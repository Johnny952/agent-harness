from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def insert_event(db_path: str, source_app: str, event_type: str, payload: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO events (source_app, event_type, payload) VALUES (?, ?, ?)",
            (source_app, event_type, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def list_events(db_path: str, limit: int = 100, source_app: str | None = None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if source_app:
            rows = conn.execute(
                "SELECT * FROM events WHERE source_app = ? ORDER BY id DESC LIMIT ?",
                (source_app, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": r["id"],
                "source_app": r["source_app"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()
