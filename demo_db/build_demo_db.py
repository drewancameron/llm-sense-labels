#!/usr/bin/env python3
"""Build the showcase demo database from public-domain Perseus TEI.

Orchestrates:

  1. Initialise schema.sql in a fresh demo.db
  2. Populate lemmata and lemma_forms tables from config/
  3. Fetch + parse Perseus Greek TEI for five curated works
  4. Attach public-domain English translations to passages by reference
  5. Match lemma forms against passage text to populate occurrences

After this script runs, the demo DB contains everything the four
pipeline stages need as input.

Expected runtime: 10-30 seconds depending on network. Output size:
~1-3 MB SQLite file.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python demo_db/build_demo_db.py` from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.lib.db import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH, connect, init_schema  # noqa: E402
from scripts.ingest.wiktionary_senses import populate_lemmata_and_forms  # noqa: E402
from scripts.ingest.perseus import fetch_and_load_all  # noqa: E402
from scripts.ingest.align_translations import align_all  # noqa: E402
from scripts.ingest.match_occurrences import find_all_occurrences  # noqa: E402


def main() -> None:
    print(f"Initialising schema at {DEFAULT_DB_PATH} …")
    init_schema(DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH)

    lemmata_cfg_path = REPO_ROOT / "config" / "lemmata.yaml"
    with lemmata_cfg_path.open() as f:
        lemmata_cfg = yaml.safe_load(f)["lemmata"]

    with connect(DEFAULT_DB_PATH) as conn:
        print("\nStep 1 — lemmata + Wiktionary forms")
        populate_lemmata_and_forms(conn, lemmata_cfg)

        print("\nStep 2 — fetch + parse Perseus Greek TEI")
        fetch_and_load_all(conn)

        print("\nStep 3 — align English translations")
        align_all(conn)

        print("\nStep 4 — match occurrences of target lemmata")
        find_all_occurrences(conn)

        print("\nSummary")
        for table in (
            "works", "passages", "translations", "notes",
            "lemmata", "lemma_forms", "occurrences",
        ):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<20s} {n}")

    print(f"\nDone. Demo DB at {DEFAULT_DB_PATH}")


if __name__ == "__main__":
    main()
