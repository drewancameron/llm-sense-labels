"""Extract translator and commentator notes from TEI XML, with coarse
type classification.

This is a legacy split from the older Perseus pipeline in the parent
project. The Greek-text parser in `perseus.py` already extracts notes
attached to passages it emits; this file exists for the rarer case of
standalone commentary TEI files (where notes are primary content, not
attached to a specific reading).

For the demo we don't ship any standalone commentary, but the parser is
here so the pipeline structure is complete.
"""

from __future__ import annotations

import re

from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"


def classify_note_type(note_text: str, tei_type_attr: str | None) -> str:
    """Heuristic classification of a TEI <note> element.

    Uses the @type attribute first, falling back to lexical markers in the
    note text itself. Recognised types: 'footnote', 'commentary_note',
    'lexical_note', 'apparatus', 'paratext'.
    """
    if tei_type_attr:
        t = tei_type_attr.lower()
        if "footnote" in t:
            return "footnote"
        if "commentary" in t:
            return "commentary_note"
        if "apparatus" in t or "app" in t:
            return "apparatus"
        if t in {"preface", "intro", "paratext"}:
            return "paratext"

    lexical_markers = (
        "cf.", "lit.", "meaning", "sense of",
        "translat", "render", "word", "gloss",
    )
    low = note_text.lower()
    if any(m in low for m in lexical_markers):
        return "lexical_note"
    return "footnote"


def extract_notes_from_tei(tei_bytes: bytes) -> list[dict]:
    """Return [{note_type, note_text, context_hint}] for every <note>
    in the file.
    """
    root = etree.fromstring(tei_bytes)
    out: list[dict] = []
    for note in root.iter(f"{{{TEI_NS}}}note"):
        tei_type = note.get("type")
        text = re.sub(r"\s+", " ", "".join(note.itertext())).strip()
        if not text:
            continue
        # A coarse "context hint" — the textual vicinity in which the note
        # is anchored. Useful later for alignment if no @target is set.
        parent = note.getparent()
        context_hint = None
        if parent is not None and parent.text:
            context_hint = parent.text.strip()[:120]
        out.append(
            {
                "note_type": classify_note_type(text, tei_type),
                "note_text": text,
                "context_hint": context_hint,
                "anchor_target": note.get("target"),
            }
        )
    return out
