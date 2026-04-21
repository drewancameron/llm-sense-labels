"""Attach published English translations to Greek passages, where a
public-domain translation is available for the same canonical reference.

For the demo we ship a small curated set of Perseus TEI English
translations paired with the Greek works already fetched. Where the
reference schema lines up (book.chapter.section or line number), we
perform a direct ref-match; where it doesn't, the alignment is left to
Perseus-bundled <alignment> links or to the user's own data.

This is a deliberately minimal implementation. The parent research uses
a richer alignment pipeline, including translator-specific heuristics,
passage-level fuzzy matching, and commentator cross-references. The
demo shows the mechanism; the production code handles the edge cases.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from lxml import etree

from scripts.ingest.perseus import (
    CACHE_DIR, NSMAP, TEI_NS, _clean_text, _text_of,
)
from scripts.lib.db import insert_many


@dataclass
class PerseusTranslation:
    """An English translation on Perseus corresponding to a Greek work."""

    translation_work_id: str           # e.g. 'tlg0012.tlg001.perseus-eng4'
    source_greek_work_id: str          # which PerseusWork.work_id this pairs with
    translator: str
    date_estimate: str
    license_status: str
    tei_url: str


TRANSLATIONS: list[PerseusTranslation] = [
    PerseusTranslation(
        translation_work_id="tlg0012.tlg001.perseus-eng4",
        source_greek_work_id="tlg0012.tlg001.perseus-grc2",
        translator="Samuel Butler",
        date_estimate="1898",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0012/tlg001/tlg0012.tlg001.perseus-eng4.xml",
    ),
    PerseusTranslation(
        translation_work_id="tlg0011.tlg002.perseus-eng2",
        source_greek_work_id="tlg0011.tlg002.perseus-grc2",
        translator="Francis Storr",
        date_estimate="1912",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0011/tlg002/tlg0011.tlg002.perseus-eng2.xml",
    ),
    PerseusTranslation(
        translation_work_id="tlg0016.tlg001.perseus-eng2",
        source_greek_work_id="tlg0016.tlg001.perseus-grc2",
        translator="A. D. Godley",
        date_estimate="1920",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0016/tlg001/tlg0016.tlg001.perseus-eng2.xml",
    ),
    PerseusTranslation(
        translation_work_id="tlg0059.tlg002.perseus-eng2",
        source_greek_work_id="tlg0059.tlg002.perseus-grc2",
        translator="Benjamin Jowett",
        date_estimate="1871",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0059/tlg002/tlg0059.tlg002.perseus-eng2.xml",
    ),
    PerseusTranslation(
        translation_work_id="tlg0031.tlg001.perseus-eng2",
        source_greek_work_id="tlg0031.tlg001.perseus-grc2",
        translator="King James (KJV)",
        date_estimate="1611",
        license_status="public_domain",
        tei_url="https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/tlg0031/tlg001/tlg0031.tlg001.perseus-eng2.xml",
    ),
]


def _fetch(url: str) -> bytes:
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    cache_path = CACHE_DIR / f"translation.{digest}.xml"
    if cache_path.exists():
        return cache_path.read_bytes()
    print(f"  fetching {url}")
    with urlopen(url, timeout=30) as r:
        data = r.read()
    cache_path.write_bytes(data)
    return data


def _english_passages(tei_bytes: bytes) -> dict[str, str]:
    """Return {reference_string: english_text} for every leaf paragraph
    or line in an English TEI file. Reference chain identical to the
    Greek parser (ordered @n ancestry).
    """
    root = etree.fromstring(tei_bytes)
    body = root.xpath("//tei:text//tei:body", namespaces=NSMAP)
    if not body:
        return {}
    body = body[0]

    def ref_for(el: etree._Element) -> str:
        chain = []
        cur: etree._Element | None = el
        while cur is not None:
            n = cur.get("n")
            if n and ":" not in n:  # skip URN-style outer @n
                chain.append(n)
            cur = cur.getparent()
        chain.reverse()
        return ".".join(chain)

    # Try <p> first, then <l>, then leaf <div>.
    units = body.xpath(".//tei:p", namespaces=NSMAP)
    if not units:
        units = body.xpath(".//tei:l", namespaces=NSMAP)
    if not units:
        units = [
            d for d in body.xpath(".//tei:div", namespaces=NSMAP)
            if not d.findall(f"{{{TEI_NS}}}div")
        ]

    out: dict[str, str] = {}
    for u in units:
        ref = ref_for(u)
        if not ref:
            continue
        text = _text_of(u, ignore_notes=True)
        if not text:
            continue
        out[ref] = text
    return out


def _ref_tuple(ref: str) -> tuple:
    """Parse a reference like '1.40' into a tuple for ordered comparison.

    Non-integer segments (e.g. Stephanus '17b') are returned as-is so
    that segments stay comparable within a book. The tuple is used to
    find the nearest lower English reference when the English corpus is
    at a coarser granularity than the Greek.
    """
    out = []
    for seg in ref.split("."):
        try:
            out.append((0, int(seg)))  # numeric segments sort first
        except ValueError:
            out.append((1, seg))  # string segments after numerics
    return tuple(out)


def align_all(conn: sqlite3.Connection) -> int:
    """For each translation, match its refs against Greek passage refs
    and insert translation rows.

    Three match strategies, in order of decreasing confidence:
      1. Exact ref match: Greek '1.1' == English '1.1'.
      2. Prefix fallback: Greek '1.1.1' → English '1.1'.
      3. Range fallback: for verse works where the English is at every-
         N-lines granularity (e.g. Homer Butler's Iliad has English refs
         '1.1', '1.40', '1.80', …), match each Greek line to the
         largest English ref ≤ it within the same first-level book.
    """
    total = 0
    for tr in TRANSLATIONS:
        try:
            data = _fetch(tr.tei_url)
        except Exception as e:
            print(f"  [skip] translation {tr.translation_work_id}: {e}")
            continue
        en_by_ref = _english_passages(data)
        if not en_by_ref:
            continue

        # Pre-compute a sorted list of (tuple, ref, text) for each
        # top-level book (first ref segment) for range fallback.
        en_by_book: dict[str, list[tuple]] = {}
        for ref, text in en_by_ref.items():
            book = ref.split(".")[0] if "." in ref else ref
            en_by_book.setdefault(book, []).append((_ref_tuple(ref), ref, text))
        for book in en_by_book:
            en_by_book[book].sort()

        greek_rows = conn.execute(
            "SELECT passage_id, reference FROM passages WHERE work_id = ?",
            (tr.source_greek_work_id,),
        ).fetchall()

        rows = []
        for g in greek_rows:
            gref = g["reference"]
            english = en_by_ref.get(gref)
            confidence = 1.0

            if not english:
                parts = gref.split(".")
                for depth in range(len(parts) - 1, 0, -1):
                    english = en_by_ref.get(".".join(parts[:depth]))
                    if english:
                        confidence = 0.6
                        break

            if not english:
                # Range fallback (verse works with coarse English).
                book = gref.split(".")[0] if "." in gref else gref
                candidates = en_by_book.get(book, [])
                gt = _ref_tuple(gref)
                best = None
                for etpl, eref, etext in candidates:
                    if etpl <= gt:
                        best = (eref, etext)
                    else:
                        break
                if best:
                    english = best[1]
                    confidence = 0.4

            if not english:
                continue
            rows.append(
                {
                    "passage_id": g["passage_id"],
                    "translator": tr.translator,
                    "translation_work_id": None,
                    "aligned_text": english,
                    "alignment_confidence": confidence,
                    "license_status": tr.license_status,
                }
            )
        inserted = insert_many(conn, "translations", rows)
        total += inserted
        print(f"  {tr.translator}: {inserted} alignments")
    conn.commit()
    print(f"Inserted {total} aligned translations.")
    return total
