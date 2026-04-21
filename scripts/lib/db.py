"""SQLite helpers for the llm-sense-labels demo database.

Thin wrapper around sqlite3 with row_factory set, json helpers, and a
small number of convenience queries used by multiple stages.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "demo_db" / "demo.db"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "demo_db" / "schema.sql"


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(db_path: Path = DEFAULT_DB_PATH, schema_path: Path = DEFAULT_SCHEMA_PATH) -> None:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    with schema_path.open() as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def insert_many(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    """Insert a batch of rows, using the keys of the first row as columns."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.executemany(sql, [[r[c] for c in cols] for r in rows])
    return len(rows)


def js(value: Any) -> str:
    """Dump a value to JSON, ensuring non-ASCII Greek is preserved as UTF-8."""
    return json.dumps(value, ensure_ascii=False)


def jl(value: str | None) -> Any:
    """Load a JSON string from the DB; tolerate NULL."""
    if value is None:
        return None
    return json.loads(value)


# ─── Convenience queries ────────────────────────────────────────────

def fetch_occurrences_for_lemma(
    conn: sqlite3.Connection, lemma_slug: str, limit: int | None = None
) -> list[sqlite3.Row]:
    sql = """
      SELECT * FROM occurrence_context
      WHERE lemma_slug = ?
      ORDER BY passage_id, occurrence_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (lemma_slug,)).fetchall()


def fetch_passage_by_id(conn: sqlite3.Connection, passage_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM passages WHERE passage_id = ?", (passage_id,)
    ).fetchone()


def fetch_translations_for_passage(
    conn: sqlite3.Connection, passage_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM translations WHERE passage_id = ? ORDER BY alignment_confidence DESC",
        (passage_id,),
    ).fetchall()


def fetch_notes_for_passage(conn: sqlite3.Connection, passage_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notes WHERE passage_id = ?", (passage_id,)
    ).fetchall()


def fetch_inventory(conn: sqlite3.Connection, lemma_slug: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sense_inventory WHERE lemma_slug = ? ORDER BY sense_index",
        (lemma_slug,),
    ).fetchall()


def fetch_context_record(conn: sqlite3.Connection, passage_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM context_records WHERE passage_id = ?", (passage_id,)
    ).fetchone()
