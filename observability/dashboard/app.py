from __future__ import annotations

import hashlib
import hmac
import os
from functools import wraps

from flask import Flask, Response, request
from markupsafe import escape

from observability.collector import db


def create_app(db_path: str, username: str, password_hash: str) -> Flask:
    # Same call the collector makes: schema.sql is idempotent (CREATE TABLE IF
    # NOT EXISTS), and without it a dashboard that wins the cold-start race
    # queries a database file that has no events table yet and 500s.
    db.init_db(db_path)
    app = Flask(__name__)

    def check_auth(user: str, password: str) -> bool:
        # hmac.compare_digest on str requires ASCII (a non-ASCII Basic-Auth
        # username would otherwise raise TypeError and turn into a 500), so
        # compare encoded bytes. Both comparisons are computed unconditionally
        # before the `and` so a wrong username doesn't return faster than a
        # wrong password.
        user_ok = hmac.compare_digest(user.encode(), username.encode())
        password_ok = hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest().encode(), password_hash.encode()
        )
        return user_ok and password_ok

    def requires_auth(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.authorization
            # Bearer/Digest headers parse into an Authorization object whose
            # .username/.password are None; check auth.type first so
            # check_auth never sees None instead of a str.
            if not auth or auth.type != "basic" or not check_auth(auth.username, auth.password):
                return Response(
                    "Authentication required", 401,
                    {"WWW-Authenticate": 'Basic realm="ia-harness dashboard"'},
                )
            return f(*args, **kwargs)
        return wrapper

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
