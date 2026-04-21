#!/usr/bin/env python3
"""Stage 1 — Context analysis.

For each passage in the demo DB that contains at least one occurrence of
a target lemma, query the LLM for a structured context record:

  - Register  (epic, historiographical, philosophical, …)
  - Metre     (prose, dactylic hexameter, iambic trimeter, …)
                + metrical pressure (none / low / moderate / high)
  - Source nature (original, archaising, mock-archaic, koine-vernacular, …)
  - Author stance
  - Gold notes (translator remarks on polysemy, diachronic shift,
                register, metre, etymology, or lexical choice)
  - Inferred period

Records are written to the `context_records` table. Downstream stages
read them as a structured side-channel that conditions sense selection.

Usage:
    python scripts/stage1_analyze_context.py --lemma logos
    python scripts/stage1_analyze_context.py --lemma logos --realtime --limit 3
    python scripts/stage1_analyze_context.py --all-lemmata --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.lib.db import connect, fetch_notes_for_passage, js  # noqa: E402
from scripts.lib.openai_batch import BatchBuilder, actual_cost  # noqa: E402
from scripts.lib.schemas import ContextRecord  # noqa: E402

STAGE = "stage1"
PROMPT_PATH = REPO_ROOT / "config" / "prompts" / "stage1_context.md"
MODELS_PATH = REPO_ROOT / "config" / "models.yaml"


def load_prompt_template() -> tuple[str, str]:
    """Split the prompt file into (system, user_template)."""
    text = PROMPT_PATH.read_text()
    parts = text.split("## User template", 1)
    system = parts[0].split("## System", 1)[1].strip()
    user_template = parts[1].split("---", 1)[1].strip() if "---" in parts[1] else parts[1].strip()
    return system, user_template


def load_stage_cfg() -> dict:
    with MODELS_PATH.open() as f:
        return yaml.safe_load(f)["stages"]["stage1_analyze_context"]


def render_user(template: str, passage_row, work_row, lemma_row, notes) -> str:
    attached = "\n\n".join(
        f"- **{n['note_type']}** by {n['note_author']}: {n['note_text']}"
        for n in notes
    ) or "(none)"

    return template.format(
        reference=passage_row["reference"],
        work_title=work_row["title"],
        author=work_row["author"],
        date_estimate=work_row["date_estimate"] or "",
        genre_tag=work_row["genre"] or "",
        greek_text=passage_row["greek_text"],
        context_before=passage_row["context_before"] or "",
        context_after=passage_row["context_after"] or "",
        lemma_greek=lemma_row["lemma_greek"],
        lemma_slug=lemma_row["slug"],
        surface_form="(multiple occurrences possible; see DB)",
        attached_notes_block=attached,
    )


def fetch_target_passages(conn, lemma_slugs: list[str] | None):
    """Return passages that contain at least one target-lemma occurrence."""
    if lemma_slugs:
        placeholders = ",".join("?" for _ in lemma_slugs)
        sql = f"""
          SELECT DISTINCT p.passage_id, p.reference, p.greek_text,
                          p.context_before, p.context_after,
                          w.work_id, w.author, w.title, w.date_estimate,
                          w.period, w.genre
          FROM passages p
          JOIN occurrences o ON p.passage_id = o.passage_id
          JOIN works w ON p.work_id = w.work_id
          WHERE o.lemma_slug IN ({placeholders})
          ORDER BY p.sequence
        """
        return conn.execute(sql, lemma_slugs).fetchall()
    sql = """
      SELECT DISTINCT p.passage_id, p.reference, p.greek_text,
                      p.context_before, p.context_after,
                      w.work_id, w.author, w.title, w.date_estimate,
                      w.period, w.genre
      FROM passages p
      JOIN occurrences o ON p.passage_id = o.passage_id
      JOIN works w ON p.work_id = w.work_id
      ORDER BY p.sequence
    """
    return conn.execute(sql).fetchall()


def get_representative_lemma(conn, passage_id: str) -> dict:
    """Pick one target lemma from the passage to anchor the prompt."""
    row = conn.execute(
        """
        SELECT l.* FROM lemmata l
        JOIN occurrences o ON o.lemma_slug = l.slug
        WHERE o.passage_id = ?
        LIMIT 1
        """,
        (passage_id,),
    ).fetchone()
    return dict(row) if row else {}


def write_record(conn, passage_id: str, parsed: ContextRecord, raw: str, model_id: str, batch_job_id: str | None) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO context_records (
          passage_id, register_label, register_confidence, register_evidence,
          metre_label, metrical_pressure, metre_evidence,
          source_nature, source_confidence, source_evidence,
          author_stance, period_inferred,
          gold_notes_json, notes, raw_response_json,
          model_id, batch_job_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            passage_id,
            parsed.register_info.label, parsed.register_info.confidence, parsed.register_info.evidence,
            parsed.metre.label, parsed.metre.metrical_pressure, parsed.metre.evidence,
            parsed.source_nature.label, parsed.source_nature.confidence, parsed.source_nature.evidence,
            parsed.author_stance, parsed.period_inferred,
            js([n.model_dump() for n in parsed.gold_notes]),
            parsed.notes, raw,
            model_id, batch_job_id,
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lemma", action="append", help="Lemma slug (repeatable).")
    ap.add_argument("--all-lemmata", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--realtime", action="store_true", help="Use sync API instead of Batch.")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build prompts and print cost estimate, but do not call API.")
    args = ap.parse_args()

    if not args.lemma and not args.all_lemmata:
        ap.error("--lemma (repeatable) or --all-lemmata required")

    cfg = load_stage_cfg()
    system, user_template = load_prompt_template()

    conn = connect()
    lemma_slugs = args.lemma if not args.all_lemmata else None
    passages = fetch_target_passages(conn, lemma_slugs)
    if args.limit:
        passages = passages[: args.limit]

    if not passages:
        print("No matching passages; did you run build_demo_db.py?")
        return

    builder = BatchBuilder(
        stage=STAGE,
        model=cfg["model"],
        use_batch_api=(cfg["batch"] and not args.realtime),
        use_cache=(cfg["cache"] and not args.no_cache),
    )

    for p in passages:
        lemma = get_representative_lemma(conn, p["passage_id"])
        notes = [dict(n) for n in fetch_notes_for_passage(conn, p["passage_id"])]
        user = render_user(user_template, p, p, lemma, notes)
        builder.add(
            custom_id=p["passage_id"],
            system=system,
            user=user,
            response_format={"type": "json_object"},
            max_tokens=1200,
        )

    est = builder.estimate_cost()
    print(json.dumps(est, indent=2))
    if args.dry_run:
        return

    if args.realtime:
        responses = list(builder.run_realtime())
        batch_job_id = None
    else:
        job_id = builder.submit()
        print(f"Submitted batch {job_id}. Polling …")
        responses = builder.wait(job_id)
        batch_job_id = job_id
        conn.execute(
            "INSERT OR REPLACE INTO batch_jobs (batch_job_id, stage, model_id, status) VALUES (?, ?, ?, 'completed')",
            (job_id, STAGE, cfg["model"]),
        )

    ok, bad = 0, 0
    usages = []
    for resp in responses:
        if resp["content"] is None:
            bad += 1
            continue
        try:
            parsed = ContextRecord.model_validate_json(resp["content"])
        except Exception as e:
            print(f"[quarantine] {resp['custom_id']}: {e}")
            bad += 1
            continue
        write_record(conn, resp["custom_id"], parsed, resp["content"], cfg["model"], batch_job_id)
        usages.append(resp.get("usage", {}))
        ok += 1

    conn.commit()
    cost = actual_cost(cfg["model"], usages, batch_api=not args.realtime)
    print(f"Stage 1 complete. ok={ok} quarantined={bad} cost=${cost:.4f}")


if __name__ == "__main__":
    main()
