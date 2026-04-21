#!/usr/bin/env python3
"""Stage 4 — Bulk classification against the stable sense inventory.

Once Stage 3 has produced a canonical inventory for a lemma, Stage 4
classifies every occurrence of that lemma quickly and cheaply against
the inventory, using the Stage 1 context record as an additional
signal. Uses gpt-5.4-mini by default.

Occurrences without a Stage 2 pass are welcome here: Stage 4 can re-read
from scratch and does not depend on Stage 2 labels. However, Stage 1
context records are strongly recommended; if missing, register / metre
fields in the prompt are populated with 'uncertain'.

Usage:
    python scripts/stage4_bulk_classify.py --lemma logos
    python scripts/stage4_bulk_classify.py --all-lemmata --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.lib.db import (  # noqa: E402
    connect, fetch_context_record, fetch_inventory,
    fetch_translations_for_passage,
)
from scripts.lib.openai_batch import BatchBuilder, actual_cost  # noqa: E402
from scripts.lib.schemas import BulkLabel  # noqa: E402

STAGE = "stage4"
PROMPT_PATH = REPO_ROOT / "config" / "prompts" / "stage4_bulk.md"
MODELS_PATH = REPO_ROOT / "config" / "models.yaml"


def load_prompt_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text()
    parts = text.split("## User template", 1)
    system = parts[0].split("## System", 1)[1].strip()
    user_template = parts[1].split("---", 1)[1].strip() if "---" in parts[1] else parts[1].strip()
    return system, user_template


def load_stage_cfg() -> dict:
    with MODELS_PATH.open() as f:
        return yaml.safe_load(f)["stages"]["stage4_bulk_classify"]


def render_inventory_numbered(rows) -> str:
    lines = []
    for r in rows:
        lines.append(f"[{r['sense_index']}] {r['label']} — {r['gloss']}")
    return "\n".join(lines)


def render_ctx_fields(ctx) -> dict[str, str]:
    if not ctx:
        return {
            "register": "uncertain", "metre": "uncertain",
            "metrical_pressure": "none", "source_nature": "uncertain",
        }
    return {
        "register": ctx["register_label"] or "uncertain",
        "metre": ctx["metre_label"] or "uncertain",
        "metrical_pressure": ctx["metrical_pressure"] or "none",
        "source_nature": ctx["source_nature"] or "uncertain",
    }


def write_bulk_label(conn, occurrence_id: int, parsed: BulkLabel,
                     raw: str, model_id: str, batch_job_id: str | None) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO bulk_labels (
          occurrence_id, chosen_sense_label, chosen_sense_index, confidence,
          register_applied, reason, fallback_sense_label, fallback_confidence,
          raw_response_json, model_id, batch_job_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurrence_id, parsed.chosen_sense_label, parsed.chosen_sense_index,
            parsed.confidence, parsed.register_applied, parsed.reason,
            parsed.fallback_sense_label, parsed.fallback_confidence,
            raw, model_id, batch_job_id,
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lemma", action="append")
    ap.add_argument("--all-lemmata", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="Re-classify even for occurrences that already have bulk_labels rows.")
    args = ap.parse_args()

    if not args.lemma and not args.all_lemmata:
        ap.error("--lemma or --all-lemmata required")

    cfg = load_stage_cfg()
    system, user_template = load_prompt_template()

    conn = connect()

    clauses = []
    params: list = []
    if args.lemma:
        clauses.append(f"o.lemma_slug IN ({','.join('?' for _ in args.lemma)})")
        params = list(args.lemma)
    if not args.force:
        clauses.append("o.occurrence_id NOT IN (SELECT occurrence_id FROM bulk_labels)")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
      SELECT o.occurrence_id, o.lemma_slug, o.surface_form,
             p.passage_id, p.reference, p.greek_text,
             l.lemma_greek
      FROM occurrences o
      JOIN passages p ON o.passage_id = p.passage_id
      JOIN lemmata l ON o.lemma_slug = l.slug
      {where}
      ORDER BY o.occurrence_id
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    occs = conn.execute(sql, params).fetchall()
    if not occs:
        print("No occurrences.")
        return

    inventories_by_slug: dict[str, list] = {}

    builder = BatchBuilder(
        stage=STAGE, model=cfg["model"],
        use_batch_api=(cfg["batch"] and not args.realtime),
        use_cache=(cfg["cache"] and not args.no_cache),
    )
    custom_id_to_occurrence: dict[str, int] = {}

    for occ in occs:
        slug = occ["lemma_slug"]
        if slug not in inventories_by_slug:
            inventories_by_slug[slug] = fetch_inventory(conn, slug)
        inv_rows = inventories_by_slug[slug]
        if not inv_rows:
            print(f"[skip] no inventory for {slug}; run Stage 3 first.")
            continue

        translations = fetch_translations_for_passage(conn, occ["passage_id"])
        best_en = translations[0]["aligned_text"] if translations else "(no English translation available)"
        ctx = fetch_context_record(conn, occ["passage_id"])
        ctx_fields = render_ctx_fields(ctx)

        user = user_template.format(
            occurrence_id=str(occ["occurrence_id"]),
            reference=occ["reference"],
            lemma_greek=occ["lemma_greek"],
            lemma_slug=occ["lemma_slug"],
            surface_form=occ["surface_form"],
            greek_text=occ["greek_text"],
            aligned_english_short=best_en[:500],
            register=ctx_fields["register"],
            metre=ctx_fields["metre"],
            metrical_pressure=ctx_fields["metrical_pressure"],
            source_nature=ctx_fields["source_nature"],
            inventory_numbered_list=render_inventory_numbered(inv_rows),
        )

        cid = f"occ-{occ['occurrence_id']}"
        custom_id_to_occurrence[cid] = occ["occurrence_id"]
        builder.add(
            custom_id=cid, system=system, user=user,
            response_format={"type": "json_object"}, max_tokens=400,
        )

    if not builder.requests:
        print("Nothing to classify.")
        return

    est = builder.estimate_cost()
    print(json.dumps(est, indent=2))
    if args.dry_run:
        return

    if args.realtime:
        responses = list(builder.run_realtime())
        batch_job_id = None
    else:
        batch_job_id = builder.submit()
        print(f"Submitted batch {batch_job_id}. Polling …")
        responses = builder.wait(batch_job_id)
        conn.execute(
            "INSERT OR REPLACE INTO batch_jobs (batch_job_id, stage, model_id, status) VALUES (?, ?, ?, 'completed')",
            (batch_job_id, STAGE, cfg["model"]),
        )

    ok, bad = 0, 0
    usages = []
    for resp in responses:
        occ_id = custom_id_to_occurrence.get(resp["custom_id"])
        if occ_id is None or resp["content"] is None:
            bad += 1
            continue
        try:
            parsed = BulkLabel.model_validate_json(resp["content"])
        except Exception as e:
            print(f"[quarantine] {resp['custom_id']}: {e}")
            bad += 1
            continue
        write_bulk_label(conn, occ_id, parsed, resp["content"], cfg["model"], batch_job_id)
        usages.append(resp.get("usage", {}))
        ok += 1

    conn.commit()
    cost = actual_cost(cfg["model"], usages, batch_api=not args.realtime)
    print(f"Stage 4 complete. ok={ok} quarantined={bad} cost=${cost:.4f}")


if __name__ == "__main__":
    main()
