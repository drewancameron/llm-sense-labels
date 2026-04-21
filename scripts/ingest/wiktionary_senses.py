"""Load Wiktionary morphological forms and sense sketches for the 10
target lemmata, and populate the lemmata + lemma_forms tables.

Source: a trimmed-down copy of the parent project's wiktionary_forms.json,
containing only the 10 showcase lemmata. See config/wiktionary_forms.json.

Greek tokenisation here is deliberately simple: NFKC-normalise, strip
accents for matching, split on whitespace and punctuation. This is good
enough for exploratory matching against Perseus TEI; a production
pipeline would use morphologically-aware tokenisation (CLTK, Stanza) and
morphological disambiguation.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
import re
from pathlib import Path

from scripts.lib.db import insert_many, js

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMS_PATH = REPO_ROOT / "config" / "wiktionary_forms.json"


def strip_accents(s: str) -> str:
    """Decompose Greek polytonic accents, drop combining marks, lowercase."""
    nfd = unicodedata.normalize("NFD", s)
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.lower()


GREEK_WORD_RE = re.compile(r"[Ͱ-Ͽἀ-῿]+")


def tokenise_greek(text: str) -> list[tuple[str, int, int]]:
    """Return [(surface_form, char_start, char_end)] for each Greek word."""
    return [(m.group(), m.start(), m.end()) for m in GREEK_WORD_RE.finditer(text)]


def load_forms() -> dict:
    with FORMS_PATH.open() as f:
        return json.load(f)


def populate_lemmata_and_forms(
    conn: sqlite3.Connection, lemmata_config: list[dict]
) -> None:
    """Populate the lemmata and lemma_forms tables.

    `lemmata_config` is the parsed content of config/lemmata.yaml.
    """
    wikt = load_forms()

    lemma_rows = []
    form_rows = []

    for lem in lemmata_config:
        slug = lem["slug"]
        greek = lem["lemma_greek"]
        lemma_rows.append(
            {
                "slug": slug,
                "lemma_greek": greek,
                "pie_root": lem.get("pie_root"),
                "pie_gloss": lem.get("pie_gloss"),
                "domain_primary": lem.get("domain_primary"),
                "domain_secondary_json": js(lem.get("domain_secondary", [])),
                "expected_pattern": lem.get("expected_pattern"),
                "wiktionary_page": lem.get("wiktionary_page"),
                "lsj_entry": lem.get("lsj_entry"),
            }
        )

        # The headword itself is a form.
        form_rows.append(
            {
                "lemma_slug": slug,
                "surface_form": greek,
                "surface_norm": strip_accents(greek),
                "morph_tag": None,
            }
        )

        entry = wikt.get(greek)
        if not entry:
            print(f"[warn] no Wiktionary entry for {greek}")
            continue

        for form in entry.get("forms", []):
            # Filter out "(page does not exist)" annotations
            clean = form.split(" (")[0].strip()
            if not clean or not GREEK_WORD_RE.fullmatch(clean):
                continue
            form_rows.append(
                {
                    "lemma_slug": slug,
                    "surface_form": clean,
                    "surface_norm": strip_accents(clean),
                    "morph_tag": None,
                }
            )

    insert_many(conn, "lemmata", lemma_rows)
    # Deduplicate (lemma_slug, surface_form) since headword may repeat.
    seen = set()
    unique_form_rows = []
    for r in form_rows:
        key = (r["lemma_slug"], r["surface_form"])
        if key in seen:
            continue
        seen.add(key)
        unique_form_rows.append(r)
    insert_many(conn, "lemma_forms", unique_form_rows)
    conn.commit()
    print(f"Populated {len(lemma_rows)} lemmata, {len(unique_form_rows)} forms.")


def form_to_lemma_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Build {surface_norm: [lemma_slug, ...]} map for fast occurrence matching.

    Deduplicates slugs per norm: several Wiktionary forms (e.g. with and
    without final sigma variants) can share a normalised form and map
    back to the same lemma; we don't want to emit two occurrences for
    that case.
    """
    rows = conn.execute(
        "SELECT surface_norm, lemma_slug FROM lemma_forms"
    ).fetchall()
    buckets: dict[str, set[str]] = {}
    for r in rows:
        buckets.setdefault(r["surface_norm"], set()).add(r["lemma_slug"])
    return {k: sorted(v) for k, v in buckets.items()}
