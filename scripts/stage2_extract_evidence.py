#!/usr/bin/env python3
"""Stage 2 — Per-occurrence sense extraction.

For each occurrence of a target lemma, build a prompt that includes:

  - The Greek passage (with surrounding context)
  - All aligned English translations
  - All attached translator / commentator notes
  - The Stage 1 context record (register / metre / source / gold notes)
  - The current sense inventory (or seed senses from config/lemmata.yaml
    during the inventory-building phase)

Ask gpt-5.4 to identify which sense(s) of the lemma are instantiated and
to cite the exact translation spans or notes that support the decision.
Write candidate labels with full provenance to `candidate_labels`.

Usage:
    python scripts/stage2_extract_evidence.py --lemma logos
    python scripts/stage2_extract_evidence.py --lemma logos --limit 5 --realtime
    python scripts/stage2_extract_evidence.py --all-lemmata --dry-run
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
    connect, fetch_context_record, fetch_inventory, fetch_notes_for_passage,
    fetch_translations_for_passage, js, jl,
)
from scripts.lib.openai_batch import BatchBuilder, actual_cost  # noqa: E402
from scripts.lib.schemas import CandidateLabel  # noqa: E402

STAGE = "stage2"
PROMPT_PATH = REPO_ROOT / "config" / "prompts" / "stage2_extraction.md"
MODELS_PATH = REPO_ROOT / "config" / "models.yaml"
LEMMATA_PATH = REPO_ROOT / "config" / "lemmata.yaml"


def load_prompt_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text()
    parts = text.split("## User template", 1)
    system = parts[0].split("## System", 1)[1].strip()
    user_template = parts[1].split("---", 1)[1].strip() if "---" in parts[1] else parts[1].strip()
    return system, user_template


def load_stage_cfg() -> dict:
    with MODELS_PATH.open() as f:
        return yaml.safe_load(f)["stages"]["stage2_extract_evidence"]


def load_seed_senses() -> dict[str, list[str]]:
    with LEMMATA_PATH.open() as f:
        data = yaml.safe_load(f)
    return {L["slug"]: L.get("seed_senses", []) for L in data["lemmata"]}


def render_inventory_block(conn, lemma_slug: str, seed_senses: list[str]) -> str:
    rows = fetch_inventory(conn, lemma_slug)
    if rows:
        lines = []
        for r in rows:
            lines.append(f"- **{r['label']}** — {r['gloss']} (confidence: {r['confidence']})")
        return "\n".join(lines)
    lines = [
        "(No canonical inventory yet — the pipeline is in the inventory-building phase.",
        " Seed senses from the pilot config follow. You may emit __NEW__ candidates",
        " if the evidence genuinely does not fit any seed.)",
        "",
    ]
    for s in seed_senses:
        lines.append(f"- {s}")
    return "\n".join(lines)


def render_translations_block(translations) -> str:
    if not translations:
        return "(No aligned English translation available for this passage.)"
    parts = []
    for t in translations:
        parts.append(
            f"### {t['translator']} (confidence {t['alignment_confidence']:.2f})\n{t['aligned_text']}"
        )
    return "\n\n".join(parts)


def render_notes_block(notes) -> str:
    if not notes:
        return "(No notes attached to this passage.)"
    return "\n\n".join(
        f"- **{n['note_type']}** by {n['note_author']}: {n['note_text']}"
        for n in notes
    )


def render_context_json(ctx_row) -> str:
    if not ctx_row:
        return json.dumps({"note": "no Stage 1 record available for this passage"})
    return json.dumps(
        {
            "register": {"label": ctx_row["register_label"], "confidence": ctx_row["register_confidence"]},
            "metre": {"label": ctx_row["metre_label"], "metrical_pressure": ctx_row["metrical_pressure"]},
            "source_nature": {"label": ctx_row["source_nature"], "confidence": ctx_row["source_confidence"]},
            "author_stance": ctx_row["author_stance"],
            "gold_notes": jl(ctx_row["gold_notes_json"]) or [],
            "period_inferred": ctx_row["period_inferred"],
        },
        ensure_ascii=False,
    )


def write_candidate_labels(conn, occurrence_id: int, parsed: CandidateLabel, raw: str,
                           model_id: str, batch_job_id: str | None) -> None:
    for sense in parsed.candidate_senses:
        conn.execute(
            """
            INSERT INTO candidate_labels (
              occurrence_id, sense_label, proposed_gloss, confidence, is_primary,
              evidence_json, register_override, metrical_constraint_acknowledged,
              gold_note_relied_upon, notes, extraction_confidence,
              raw_response_json, model_id, batch_job_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurrence_id,
                sense.sense_label,
                sense.proposed_gloss,
                sense.confidence,
                int(sense.is_primary),
                js([e.model_dump() for e in sense.evidence]),
                parsed.register_override,
                int(parsed.metrical_constraint_acknowledged),
                parsed.gold_note_relied_upon,
                parsed.notes,
                parsed.extraction_confidence,
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
                    help="Re-extract even for occurrences that already have candidate_labels rows.")
    args = ap.parse_args()

    if not args.lemma and not args.all_lemmata:
        ap.error("--lemma or --all-lemmata required")

    cfg = load_stage_cfg()
    system, user_template = load_prompt_template()
    seed_senses = load_seed_senses()

    conn = connect()

    clauses = []
    params: list = []
    if args.lemma:
        clauses.append(f"o.lemma_slug IN ({','.join('?' for _ in args.lemma)})")
        params = list(args.lemma)
    if not args.force:
        clauses.append("o.occurrence_id NOT IN (SELECT occurrence_id FROM candidate_labels)")
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
    occurrences = conn.execute(sql, params).fetchall()
    if not occurrences:
        print("No occurrences found.")
        return

    builder = BatchBuilder(
        stage=STAGE, model=cfg["model"],
        use_batch_api=(cfg["batch"] and not args.realtime),
        use_cache=(cfg["cache"] and not args.no_cache),
    )

    custom_id_to_occurrence: dict[str, int] = {}

    for occ in occurrences:
        translations = fetch_translations_for_passage(conn, occ["passage_id"])
        notes = fetch_notes_for_passage(conn, occ["passage_id"])
        ctx = fetch_context_record(conn, occ["passage_id"])
        inventory_block = render_inventory_block(
            conn, occ["lemma_slug"], seed_senses.get(occ["lemma_slug"], [])
        )

        user = user_template.format(
            occurrence_id=str(occ["occurrence_id"]),
            reference=occ["reference"],
            lemma_greek=occ["lemma_greek"],
            lemma_slug=occ["lemma_slug"],
            surface_form=occ["surface_form"],
            greek_text=occ["greek_text"],
            aligned_translations_block=render_translations_block(translations),
            attached_notes_block=render_notes_block(notes),
            context_record_json=render_context_json(ctx),
            sense_inventory_block=inventory_block,
        )

        cid = f"occ-{occ['occurrence_id']}"
        custom_id_to_occurrence[cid] = occ["occurrence_id"]
        builder.add(
            custom_id=cid,
            system=system,
            user=user,
            response_format={"type": "json_object"},
            max_tokens=1500,
        )

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
            parsed = CandidateLabel.model_validate_json(resp["content"])
        except Exception as e:
            print(f"[quarantine] {resp['custom_id']}: {e}")
            bad += 1
            continue
        write_candidate_labels(conn, occ_id, parsed, resp["content"], cfg["model"], batch_job_id)
        usages.append(resp.get("usage", {}))
        ok += 1

    conn.commit()
    cost = actual_cost(cfg["model"], usages, batch_api=not args.realtime)
    print(f"Stage 2 complete. ok={ok} quarantined={bad} cost=${cost:.4f}")


if __name__ == "__main__":
    main()
