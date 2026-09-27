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


def connect_read_only(db_path: str) -> sqlite3.Connection:
    """Open an existing events database, and create nothing.

    `sqlite3.connect` on a path with no database creates one there, and on a
    read-only mount it raises `unable to open database file` instead — so a
    reader that used it would either invent an empty database or fail for a
    reason that says nothing about the database it asked for. `file:<path>?mode=ro`
    with `uri=True` opens what is there and refuses to do anything else, which
    is what `observability/api/` wants: it mounts the events volume `:ro` and
    must never run the `CREATE TABLE` in `init_db`.

    Raises `sqlite3.OperationalError` when there is no database at that path.
    A caller whose contract is "empty plus a warning, never a 500" catches it.
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def list_events(
    db_path: str,
    limit: int = 100,
    source_app: str | None = None,
    since: int | None = None,
    read_only: bool = False,
) -> list[dict]:
    """The newest `limit` events, optionally by app and above an id.

    `since` is an id and not a time: the rows are ordered by `id DESC` under a
    limit, and Phase 3's live tail remembers the last id it saw rather than a
    clock it would have to trust. It composes with `limit` — the newest `limit`
    events whose id is above `since`.

    `read_only` picks `connect_read_only` over `sqlite3.connect`. It is off by
    default because the collector writes — it is the only writer left — and a
    writer should not change behaviour for a reader that arrived later. The read
    API passes it; nothing else does.
    """
    conn = connect_read_only(db_path) if read_only else sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses, params = [], []
        # `if source_app:` and not `is not None`, as this has always read: an
        # empty string is no filter rather than a filter nothing matches.
        if source_app:
            clauses.append("source_app = ?")
            params.append(source_app)
        if since is not None:
            clauses.append("id > ?")
            params.append(since)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ?", (*params, limit)
        ).fetchall()
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
