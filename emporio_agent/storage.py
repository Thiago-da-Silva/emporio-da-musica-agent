"""SQLite persistence: conversation history per session and support tickets."""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("EMPORIO_DB", Path(__file__).resolve().parent.parent / "emporio.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    contact TEXT NOT NULL,
    order_id INTEGER,
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def load_messages(conn: sqlite3.Connection, session_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT content_json FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    return [r[0] for r in rows]


def append_messages(conn: sqlite3.Connection, session_id: str, contents_json: list[str]) -> None:
    conn.executemany(
        "INSERT INTO messages (session_id, content_json) VALUES (?, ?)",
        [(session_id, c) for c in contents_json],
    )
    conn.commit()


def create_ticket(conn: sqlite3.Connection, reason: str, customer_name: str, contact: str,
                  summary: str, order_id: int | None) -> int:
    cur = conn.execute(
        "INSERT INTO tickets (reason, customer_name, contact, order_id, summary) VALUES (?, ?, ?, ?, ?)",
        (reason, customer_name, contact, order_id, summary),
    )
    conn.commit()
    return cur.lastrowid
