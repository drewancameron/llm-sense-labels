"""Fetch and parse a curated set of public-domain Greek texts from the
Perseus Digital Library (via the PerseusDL/canonical-greekLit GitHub
mirror), then emit passage and note records ready to be written to the
demo database.

The curated list is deliberately small: five works spanning Archaic
epic, Classical tragedy, Classical historiography, Classical philosophy,
and Koine religious prose. This is enough to exhibit register / metre /
source-nature variation across the pipeline.

All works listed here are in the public domain in the United States and
the United Kingdom (original composition predates 1925 by more than 2000
years; Perseus TEI encoding is released under a CC-BY-SA licence).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from lxml import etree

from scripts.lib.db import insert_many

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "demo_db" / "cache"

TEI_NS = "http://www.tei-c.org/ns/1.0"
NSMAP = {"tei": TEI_NS}


@dataclass
class PerseusWork:
    work_id: str
    author: str
    title: str
    date_estimate: str
    period: str
    genre: str
    license_status: str
    tei_url: str


# ─── Curated source list ────────────────────────────────────────────

PERSEUS_WORKS: list[PerseusWork] = [
    PerseusWork(
        work_id="tlg0012.tlg001.perseus-grc2",
        author="Homer",
        title="Iliad, Book 1",
        date_estimate="c. 750 BCE",
        period="archaic",
        genre="epic",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0012/tlg001/tlg0012.tlg001.perseus-grc2.xml",
    ),
    PerseusWork(
        work_id="tlg0011.tlg002.perseus-grc2",
        author="Sophocles",
        title="Antigone",
        date_estimate="c. 441 BCE",
        period="classical",
        genre="tragic",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0011/tlg002/tlg0011.tlg002.perseus-grc2.xml",
    ),
    PerseusWork(
        work_id="tlg0016.tlg001.perseus-grc2",
        author="Herodotus",
        title="Histories, Book 1",
        date_estimate="c. 440 BCE",
        period="classical",
        genre="historiographical",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0016/tlg001/tlg0016.tlg001.perseus-grc2.xml",
    ),
    PerseusWork(
        work_id="tlg0059.tlg002.perseus-grc2",
        author="Plato",
        title="Apology",
        date_estimate="c. 399 BCE",
        period="classical",
        genre="philosophical",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0059/tlg002/tlg0059.tlg002.perseus-grc2.xml",
    ),
    PerseusWork(
        work_id="tlg0031.tlg001.perseus-grc2",
        author="Anonymous (Gospel of Matthew)",
        title="Gospel of Matthew, Ch. 5-7 (Sermon on the Mount)",
        date_estimate="c. 80-90 CE",
        period="koine",
        genre="religious_NT",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0031/tlg001/tlg0031.tlg001.perseus-grc2.xml",
    ),
]


# ─── Fetch with on-disk cache ──────────────────────────────────────

def fetch_tei(work: PerseusWork) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(work.tei_url.encode()).hexdigest()[:12]
    cache_path = CACHE_DIR / f"{work.work_id}.{digest}.xml"
    if cache_path.exists():
        return cache_path.read_bytes()
    print(f"  fetching {work.tei_url}")
    with urlopen(work.tei_url, timeout=30) as r:
        data = r.read()
    cache_path.write_bytes(data)
    return data


# ─── TEI parsing ────────────────────────────────────────────────────

def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _text_of(el: etree._Element, ignore_notes: bool = True) -> str:
    """Serialise an element's text content, optionally excluding <note>
    children (to keep note text out of the passage body).
    """
    parts: list[str] = []

    def walk(node: etree._Element) -> None:
        tag = etree.QName(node).localname
        if ignore_notes and tag == "note":
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return _clean_text("".join(parts))


def _extract_notes(el: etree._Element) -> list[tuple[str, str]]:
    """Return [(note_type, note_text)] for every <note> in the subtree."""
    out = []
    for note in el.iter("{%s}note" % TEI_NS):
        note_type = note.get("type") or "footnote"
        txt = _clean_text("".join(note.itertext()))
        if txt:
            out.append((note_type, txt))
    return out


def parse_work(work: PerseusWork, tei_bytes: bytes) -> tuple[dict, list[dict], list[dict]]:
    """Return (work_row, passage_rows, note_rows) for one TEI file.

    Passage granularity: one <div>[@subtype='section'|'chapter'] OR one
    <l> line for verse works. Tuning this is the biggest single lever
    on pipeline behaviour — finer passages give more occurrences but
    less context; coarser passages give richer context per Stage-1 call
    but fewer decision points.
    """
    root = etree.fromstring(tei_bytes)

    # Strip namespaces from XPath queries for legibility.
    def findall(xpath: str, node=root):
        return node.xpath(xpath, namespaces=NSMAP)

    work_row = {
        "work_id": work.work_id,
        "author": work.author,
        "title": work.title,
        "date_estimate": work.date_estimate,
        "period": work.period,
        "genre": work.genre,
        "language": "grc",
        "license_status": work.license_status,
        "source_url": work.tei_url,
        "notes": None,
    }

    passage_rows: list[dict] = []
    note_rows: list[dict] = []

    # Strategy: gather every leaf <div> (no further <div> children) or <l>,
    # emit one passage per such unit. Reference = ordered ancestry of @n
    # attributes, e.g. '1.1.1' or '1.33'.
    body = findall("//tei:text//tei:body")
    if not body:
        return work_row, [], []
    body = body[0]

    sequence = 0
    is_verse = work.genre in ("epic", "tragic", "lyric", "comic")

    def ref_for(el: etree._Element) -> str:
        chain = []
        cur: etree._Element | None = el
        while cur is not None:
            n = cur.get("n")
            if n:
                chain.append(n)
            cur = cur.getparent()
        chain.reverse()
        return ".".join(chain) if chain else f"seq{sequence}"

    # Verse: passages are <l>.
    # Prose: passages are innermost <div> containing direct text content,
    # or <p> / <seg> if present.
    if is_verse:
        units = findall(".//tei:l", body)
    else:
        units = findall(".//tei:p", body)
        if not units:
            # fallback: leaf divs
            units = [
                d for d in findall(".//tei:div", body)
                if not d.findall(f"{{{TEI_NS}}}div")
            ]

    passages_by_id: dict[str, dict] = {}
    for unit in units:
        sequence += 1
        ref = ref_for(unit)
        passage_id = f"{work.work_id.split('.')[0]}.{ref}".strip(".")
        greek = _text_of(unit, ignore_notes=True)
        if not greek or len(greek) < 5:
            continue
        row = {
            "passage_id": passage_id,
            "work_id": work.work_id,
            "reference": ref,
            "sequence": sequence,
            "greek_text": greek,
            "context_before": None,  # filled in later
            "context_after": None,
            "word_count": len(greek.split()),
        }
        passages_by_id[passage_id] = row
        passage_rows.append(row)

        for note_type, note_text in _extract_notes(unit):
            note_rows.append(
                {
                    "passage_id": passage_id,
                    "translation_id": None,
                    "note_author": "Perseus TEI",
                    "note_type": note_type,
                    "note_text": note_text,
                    "license_status": work.license_status,
                }
            )

    # Populate context_before / context_after (one passage on either side)
    for i, row in enumerate(passage_rows):
        if i > 0:
            row["context_before"] = passage_rows[i - 1]["greek_text"]
        if i < len(passage_rows) - 1:
            row["context_after"] = passage_rows[i + 1]["greek_text"]

    return work_row, passage_rows, note_rows


def fetch_and_load_all(conn: sqlite3.Connection) -> None:
    """Fetch every curated work, parse, and insert rows into the DB."""
    all_work_rows: list[dict] = []
    all_passage_rows: list[dict] = []
    all_note_rows: list[dict] = []

    for work in PERSEUS_WORKS:
        print(f"{work.author}: {work.title}")
        try:
            tei_bytes = fetch_tei(work)
        except Exception as e:
            print(f"  [skip] could not fetch: {e}")
            continue
        try:
            work_row, passages, notes = parse_work(work, tei_bytes)
        except Exception as e:
            print(f"  [skip] could not parse: {e}")
            continue
        print(f"  parsed {len(passages)} passages, {len(notes)} notes")
        all_work_rows.append(work_row)
        all_passage_rows.extend(passages)
        all_note_rows.extend(notes)

    insert_many(conn, "works", all_work_rows)
    insert_many(conn, "passages", all_passage_rows)
    insert_many(conn, "notes", all_note_rows)
    conn.commit()
    print(
        f"Loaded {len(all_work_rows)} works, {len(all_passage_rows)} passages, "
        f"{len(all_note_rows)} notes."
    )
