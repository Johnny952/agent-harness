from __future__ import annotations

import os

from flask import Flask
from markupsafe import escape

from observability import auth
from observability.collector import db


def create_app(db_path: str, username: str, password_hash: str) -> Flask:
    # Same call the collector makes: schema.sql is idempotent (CREATE TABLE IF
    # NOT EXISTS), and without it a dashboard that wins the cold-start race
    # queries a database file that has no events table yet and 500s.
    db.init_db(db_path)
    app = Flask(__name__)

    # The same check this file used to define inline, now shared with
    # `observability/api/app.py`. The realm is passed rather than defaulted so
    # this service's 401 header stays the string it has always been.
    requires_auth = auth.requires_auth(username, password_hash, realm="ia-harness dashboard")

    @app.get("/")
    @requires_auth
    def index():
        events = db.list_events(db_path, limit=200)
        rows = "".join(
            f"<tr><td>{escape(e['created_at'])}</td><td>{escape(e['source_app'])}</td><td>{escape(e['event_type'])}</td></tr>"
            for e in events
        )
        return f"<table><tr><th>Time</th><th>Agent</th><th>Event</th></tr>{rows}</table>"

    return app


if __name__ == "__main__":
    app = create_app(
        os.environ.get("COLLECTOR_DB_PATH", "/data/events.db"),
        os.environ["DASHBOARD_USERNAME"],
        os.environ["DASHBOARD_PASSWORD_HASH"],
    )
    app.run(host="0.0.0.0", port=8788)
