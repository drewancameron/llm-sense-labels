#!/usr/bin/env python3
"""Stage 3 — Philological inventory synthesis.

For each lemma, assemble all Stage 2 candidate labels plus scholarly
reference material (Wiktionary senses, PIE root, gold polysemy notes)
and ask gpt-5.4-pro to synthesise a clean canonical sense inventory.

Records are written to `sense_inventory`, one row per canonical sense.

Usage:
    python scripts/stage3_build_inventory.py --lemma logos
    python scripts/stage3_build_inventory.py --all-lemmata --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.lib.db import connect, js, jl  # noqa: E402
from scripts.lib.openai_batch import BatchBuilder, actual_cost  # noqa: E402
from scripts.lib.schemas import SenseInventoryResponse  # noqa: E402

STAGE = "stage3"
PROMPT_PATH = REPO_ROOT / "config" / "prompts" / "stage3_inventory.md"
MODELS_PATH = REPO_ROOT / "config" / "models.yaml"
LEMMATA_PATH = REPO_ROOT / "config" / "lemmata.yaml"
WIKT_PATH = REPO_ROOT / "config" / "wiktionary_forms.json"


def load_prompt_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text()
    parts = text.split("## User template", 1)
    system = parts[0].split("## System", 1)[1].strip()
    user_template = parts[1].split("---", 1)[1].strip() if "---" in parts[1] else parts[1].strip()
    return system, user_template


def load_stage_cfg() -> dict:
    with MODELS_PATH.open() as f:
        return yaml.safe_load(f)["stages"]["stage3_build_inventory"]


def load_lemma_config(slug: str) -> dict:
    with LEMMATA_PATH.open() as f:
        data = yaml.safe_load(f)
    for lem in data["lemmata"]:
        if lem["slug"] == slug:
            return lem
    raise KeyError(slug)


def load_wiktionary_senses(greek: str) -> list[str]:
    with WIKT_PATH.open() as f:
        data = json.load(f)
    entry = data.get(greek, {})
    return entry.get("definitions", [])


def gather_candidates(conn, lemma_slug: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.candidate_id, c.sense_label, c.proposed_gloss, c.confidence,
               c.is_primary, c.evidence_json, c.register_override,
               c.metrical_constraint_acknowledged, c.gold_note_relied_upon,
               c.notes, c.extraction_confidence,
               o.surface_form, p.reference, p.greek_text,
               cr.register_label, cr.metre_label, cr.source_nature,
               cr.period_inferred
        FROM candidate_labels c
        JOIN occurrences o ON c.occurrence_id = o.occurrence_id
        JOIN passages p ON o.passage_id = p.passage_id
        LEFT JOIN context_records cr ON p.passage_id = cr.passage_id
        WHERE o.lemma_slug = ?
        ORDER BY c.candidate_id
        """,
        (lemma_slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def gather_gold_notes(conn, lemma_slug: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT cr.gold_notes_json
        FROM context_records cr
        JOIN passages p ON cr.passage_id = p.passage_id
        JOIN occurrences o ON o.passage_id = p.passage_id
        WHERE o.lemma_slug = ?
        """,
        (lemma_slug,),
    ).fetchall()
    out: list[dict] = []
    seen = set()
    for r in rows:
        for note in jl(r["gold_notes_json"]) or []:
            if note.get("target_lemma") and note["target_lemma"] not in (None, ""):
                key = (note.get("quote") or "")[:200]
                if key in seen:
                    continue
                seen.add(key)
                out.append(note)
    return out


def candidate_to_jsonl_line(c: dict) -> str:
    return json.dumps(
        {
            "candidate_id": c["candidate_id"],
            "sense_label": c["sense_label"],
            "proposed_gloss": c["proposed_gloss"],
            "confidence": c["confidence"],
            "is_primary": bool(c["is_primary"]),
            "evidence": jl(c["evidence_json"]),
            "passage": {
                "reference": c["reference"],
                "surface_form": c["surface_form"],
                "greek_excerpt": (c["greek_text"] or "")[:200],
            },
            "context": {
                "register": c["register_label"],
                "metre": c["metre_label"],
                "source_nature": c["source_nature"],
                "period": c["period_inferred"],
            },
            "extraction_confidence": c["extraction_confidence"],
        },
        ensure_ascii=False,
    )


def write_inventory(conn, lemma_slug: str, parsed: SenseInventoryResponse,
                    raw: str, model_id: str) -> None:
    conn.execute("DELETE FROM sense_inventory WHERE lemma_slug = ?", (lemma_slug,))
    for i, sense in enumerate(parsed.canonical_senses):
        conn.execute(
            """
            INSERT INTO sense_inventory (
              lemma_slug, sense_index, label, gloss, confidence,
              period_notes, attested_periods_json,
              register_affinities_json, domain_affinities_json,
              merged_candidate_ids_json, representative_evidence_json,
              notes, raw_response_json, model_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lemma_slug, i, sense.label, sense.gloss, sense.confidence,
                sense.period_notes, js(sense.attested_periods),
                js(sense.register_affinities), js(sense.domain_affinities),
                js(sense.merged_candidate_ids),
                js([e.model_dump() for e in sense.representative_evidence]),
                sense.notes, raw, model_id,
            ),
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lemma", action="append")
    ap.add_argument("--all-lemmata", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.lemma and not args.all_lemmata:
        ap.error("--lemma or --all-lemmata required")

    cfg = load_stage_cfg()
    system, user_template = load_prompt_template()

    conn = connect()

    if args.all_lemmata:
        with LEMMATA_PATH.open() as f:
            slugs = [L["slug"] for L in yaml.safe_load(f)["lemmata"]]
    else:
        slugs = args.lemma

    # Stage 3 is always realtime: low volume, single-call-per-lemma.
    builder = BatchBuilder(stage=STAGE, model=cfg["model"], use_batch_api=False, use_cache=cfg["cache"])

    slug_by_custom_id: dict[str, str] = {}
    for slug in slugs:
        lemma_cfg = load_lemma_config(slug)
        candidates = gather_candidates(conn, slug)
        if not candidates:
            print(f"[skip] no Stage 2 candidates for {slug}")
            continue

        user = user_template.format(
            lemma_greek=lemma_cfg["lemma_greek"],
            lemma_slug=slug,
            pie_root=lemma_cfg.get("pie_root", ""),
            pie_gloss=lemma_cfg.get("pie_gloss", ""),
            expected_pattern=lemma_cfg.get("expected_pattern", ""),
            lsj_sketch="(none provided for demo)",
            wiktionary_senses="\n".join(f"- {s}" for s in load_wiktionary_senses(lemma_cfg["lemma_greek"])),
            gold_notes_digest="\n".join(
                f"- [{n['type']}] {n['translator_or_commentator']}: {n['quote']}"
                for n in gather_gold_notes(conn, slug)
            ) or "(none surfaced by Stage 1)",
            candidate_senses_jsonl="\n".join(candidate_to_jsonl_line(c) for c in candidates),
        )
        cid = f"inv-{slug}"
        slug_by_custom_id[cid] = slug
        builder.add(
            custom_id=cid, system=system, user=user,
            response_format={"type": "json_object"}, max_tokens=4000,
        )

    if not builder.requests:
        print("Nothing to do.")
        return

    est = builder.estimate_cost()
    print(json.dumps(est, indent=2))
    if args.dry_run:
        return

    responses = list(builder.run_realtime())
    ok, bad = 0, 0
    usages = []
    for resp in responses:
        slug = slug_by_custom_id.get(resp["custom_id"])
        if slug is None or resp["content"] is None:
            bad += 1
            continue
        try:
            parsed = SenseInventoryResponse.model_validate_json(resp["content"])
        except Exception as e:
            print(f"[quarantine] {resp['custom_id']}: {e}")
            bad += 1
            continue
        write_inventory(conn, slug, parsed, resp["content"], cfg["model"])
        usages.append(resp.get("usage", {}))
        ok += 1
        print(f"  {slug}: {len(parsed.canonical_senses)} canonical senses")

    conn.commit()
    cost = actual_cost(cfg["model"], usages, batch_api=False)
    print(f"Stage 3 complete. ok={ok} quarantined={bad} cost=${cost:.4f}")


if __name__ == "__main__":
    main()
