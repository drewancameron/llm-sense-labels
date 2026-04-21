"""OpenAI Batch API wrapper with prompt caching and cost accounting.

Usage pattern:

    batch = BatchBuilder(stage="stage2", model="gpt-5.4")
    for occ in occurrences:
        batch.add(custom_id=f"occ-{occ.id}", system=SYS, user=render_user(occ))
    batch.estimate_cost()            # prints projected spend, refuses if > ceiling
    job = batch.submit()             # uploads JSONL, creates batch job
    responses = batch.wait(job)      # polls until complete, returns list of dicts
    ...

A --realtime flag on the stage scripts swaps submit/wait for synchronous
chat.completions.create calls, for quick iteration on small samples.

Prompt caching is engaged automatically when a system prompt is reused
across many calls (OpenAI's server-side caching kicks in at ≥1024 tokens).
This wrapper makes no explicit cache call — it just keeps the system
prompt byte-identical across requests so the server-side cache hits.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

try:
    from openai import OpenAI  # noqa: F401
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_JOBS_DIR = REPO_ROOT / "outputs" / "batch_jobs"

# Pricing per 1M tokens (USD). Kept in sync with config/models.yaml.
PRICING = {
    "gpt-5.4-pro":  {"input": 30.00, "output": 180.00, "cached_input": None},
    "gpt-5.4":      {"input":  2.50, "output":  15.00, "cached_input": 0.25},
    "gpt-5.4-mini": {"input":  0.75, "output":   4.50, "cached_input": None},
    "gpt-5.4-nano": {"input":  0.20, "output":   1.25, "cached_input": None},
    "gpt-4.1":      {"input":  2.00, "output":   8.00, "cached_input": None},
    "gpt-4.1-mini": {"input":  0.40, "output":   1.60, "cached_input": None},
    "gpt-4.1-nano": {"input":  0.10, "output":   0.40, "cached_input": None},
}
BATCH_DISCOUNT = 0.5


@dataclass
class BatchRequest:
    custom_id: str
    system: str
    user: str
    response_format: dict | None = None
    max_tokens: int = 2000
    temperature: float = 0.0


@dataclass
class BatchBuilder:
    stage: str
    model: str
    requests: list[BatchRequest] = field(default_factory=list)
    use_batch_api: bool = True
    use_cache: bool = True

    def add(
        self,
        custom_id: str,
        system: str,
        user: str,
        response_format: dict | None = None,
        max_tokens: int = 2000,
    ) -> None:
        self.requests.append(
            BatchRequest(
                custom_id=custom_id,
                system=system,
                user=user,
                response_format=response_format,
                max_tokens=max_tokens,
            )
        )

    # ─── Cost accounting ────────────────────────────────────────────

    def estimate_cost(self, assume_cache_hit_rate: float = 0.8) -> dict:
        """Rough cost projection. Assumes a high cache hit rate on the system
        prompt when caching is on.
        """
        if self.model not in PRICING:
            raise ValueError(f"Unknown model {self.model}; update PRICING table.")
        p = PRICING[self.model]

        # Character→token heuristic: ~4 chars/token for English, ~2 chars/token
        # for polytonic Greek. Round up.
        def approx_tokens(s: str) -> int:
            return max(1, (len(s) + 3) // 3)

        total_input = sum(approx_tokens(r.system) + approx_tokens(r.user) for r in self.requests)
        total_output = sum(r.max_tokens for r in self.requests) // 2  # half max is a reasonable mean

        input_cost = total_input / 1_000_000 * p["input"]
        output_cost = total_output / 1_000_000 * p["output"]

        # Cache discount on system-prompt portion
        if self.use_cache and p.get("cached_input") is not None:
            sys_tokens = approx_tokens(self.requests[0].system) if self.requests else 0
            cached_tokens = sys_tokens * (len(self.requests) - 1) * assume_cache_hit_rate
            uncached_tokens = total_input - cached_tokens
            cached_cost = cached_tokens / 1_000_000 * p["cached_input"]
            input_cost = uncached_tokens / 1_000_000 * p["input"] + cached_cost

        if self.use_batch_api:
            input_cost *= BATCH_DISCOUNT
            output_cost *= BATCH_DISCOUNT

        return {
            "model": self.model,
            "stage": self.stage,
            "n_requests": len(self.requests),
            "est_input_tokens": total_input,
            "est_output_tokens": total_output,
            "est_input_cost_usd": round(input_cost, 4),
            "est_output_cost_usd": round(output_cost, 4),
            "est_total_cost_usd": round(input_cost + output_cost, 4),
            "batch_api": self.use_batch_api,
            "cache_enabled": self.use_cache,
        }

    # ─── Submission ────────────────────────────────────────────────

    def _write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for req in self.requests:
                body: dict = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": req.system},
                        {"role": "user", "content": req.user},
                    ],
                    "max_completion_tokens": req.max_tokens,
                    "temperature": req.temperature,
                }
                if req.response_format:
                    body["response_format"] = req.response_format
                record = {
                    "custom_id": req.custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def submit(self) -> str:
        """Submit a batch job and return its ID."""
        if not self.use_batch_api:
            raise RuntimeError("Batch API disabled; use run_realtime() instead.")
        if OpenAI is None:
            raise RuntimeError("openai package not installed.")

        BATCH_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        job_tag = f"{self.stage}-{int(time.time())}"
        input_path = BATCH_JOBS_DIR / f"{job_tag}.input.jsonl"
        self._write_jsonl(input_path)

        client = OpenAI()
        with input_path.open("rb") as f:
            file_obj = client.files.create(file=f, purpose="batch")
        batch = client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={"stage": self.stage, "model": self.model},
        )
        return batch.id

    def wait(self, batch_id: str, poll_interval: int = 60) -> list[dict]:
        """Poll until the batch completes; return a list of response dicts
        with {custom_id, content, usage}.
        """
        if OpenAI is None:
            raise RuntimeError("openai package not installed.")
        client = OpenAI()
        while True:
            batch = client.batches.retrieve(batch_id)
            if batch.status in ("completed", "failed", "expired", "cancelled"):
                break
            time.sleep(poll_interval)

        if batch.status != "completed":
            raise RuntimeError(f"Batch {batch_id} ended with status {batch.status}")

        output = client.files.content(batch.output_file_id).text
        results = []
        for line in output.splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            custom_id = rec["custom_id"]
            try:
                choice = rec["response"]["body"]["choices"][0]
                content = choice["message"]["content"]
                usage = rec["response"]["body"].get("usage", {})
            except (KeyError, IndexError):
                content = None
                usage = {}
            results.append({"custom_id": custom_id, "content": content, "usage": usage})
        return results

    # ─── Realtime fallback (for quick iteration) ──────────────────

    def run_realtime(self) -> Iterator[dict]:
        """Send requests one by one via chat.completions.create. Yields
        {custom_id, content, usage}.
        """
        if OpenAI is None:
            raise RuntimeError("openai package not installed.")
        client = OpenAI()
        for req in self.requests:
            kwargs: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.user},
                ],
                "max_completion_tokens": req.max_tokens,
                "temperature": req.temperature,
            }
            if req.response_format:
                kwargs["response_format"] = req.response_format
            resp = client.chat.completions.create(**kwargs)
            yield {
                "custom_id": req.custom_id,
                "content": resp.choices[0].message.content,
                "usage": resp.usage.model_dump() if resp.usage else {},
            }


# ─── Accounting from actual usage ──────────────────────────────────

def actual_cost(model: str, usage_records: list[dict], batch_api: bool) -> float:
    """Sum real cost from usage records as returned by the API."""
    p = PRICING.get(model)
    if p is None:
        return 0.0
    inp = sum(u.get("prompt_tokens", 0) for u in usage_records)
    out = sum(u.get("completion_tokens", 0) for u in usage_records)
    cached = sum(
        (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usage_records
    )
    uncached = max(0, inp - cached)
    in_price = p["input"]
    cached_price = p.get("cached_input") or in_price
    cost = uncached / 1_000_000 * in_price + cached / 1_000_000 * cached_price + out / 1_000_000 * p["output"]
    if batch_api:
        cost *= BATCH_DISCOUNT
    return round(cost, 4)
