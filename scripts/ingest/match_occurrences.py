"""Find occurrences of the 10 target lemmata in the ingested Greek
passages, using the Wiktionary forms table.

Matching is surface-level: accent-stripped, lowercased token match. For
an unambiguous lemma this works fine; for forms shared across lemmata
we record *all* candidate lemmata and leave disambiguation to a later
morphological pass (not in scope for the demo).
"""

from __future__ import annotations

import sqlite3

from scripts.ingest.wiktionary_senses import form_to_lemma_map, strip_accents, tokenise_greek
from scripts.lib.db import insert_many


def find_all_occurrences(conn: sqlite3.Connection) -> int:
    form_map = form_to_lemma_map(conn)
    passages = conn.execute(
        "SELECT passage_id, greek_text FROM passages"
    ).fetchall()

    rows: list[dict] = []
    for p in passages:
        passage_id = p["passage_id"]
        text = p["greek_text"]
        for surface, start, end in tokenise_greek(text):
            norm = strip_accents(surface)
            lemmata = form_map.get(norm)
            if not lemmata:
                continue
            for lemma_slug in lemmata:
                rows.append(
                    {
                        "passage_id": passage_id,
                        "lemma_slug": lemma_slug,
                        "surface_form": surface,
                        "char_offset_start": start,
                        "char_offset_end": end,
                        "morph_tag": None,
                    }
                )

    # The UNIQUE constraint covers (passage_id, lemma_slug, char_offset_start)
    # so repeated forms at the same span are collapsed (shouldn't happen).
    n = insert_many(conn, "occurrences", rows)
    conn.commit()
    print(f"Matched {n} occurrences.")
    return n
