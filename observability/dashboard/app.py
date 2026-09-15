from __future__ import annotations

import hashlib
import os
from functools import wraps

from flask import Flask, Response, request
from markupsafe import escape

from observability.collector import db


def create_app(db_path: str, username: str, password_hash: str) -> Flask:
    app = Flask(__name__)

    def check_auth(user: str, password: str) -> bool:
        return user == username and hashlib.sha256(password.encode()).hexdigest() == password_hash

    def requires_auth(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
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
