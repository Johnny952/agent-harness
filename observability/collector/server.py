from __future__ import annotations

import os

from flask import Flask, jsonify, request

from observability.collector import db


def create_app(db_path: str) -> Flask:
    db.init_db(db_path)
    app = Flask(__name__)

    @app.post("/events")
    def post_event():
        body = request.get_json(force=True)
        source_app = body.get("source_app")
        event_type = body.get("event_type")
        payload = body.get("payload", {})
        if not source_app or not event_type:
            return jsonify({"error": "source_app and event_type are required"}), 400
        db.insert_event(db_path, source_app, event_type, payload)
        return jsonify({"status": "ok"}), 201

    @app.get("/events")
    def get_events():
        limit = int(request.args.get("limit", 100))
        source_app = request.args.get("source_app")
        return jsonify(db.list_events(db_path, limit=limit, source_app=source_app))

    return app


if __name__ == "__main__":
    app = create_app(os.environ.get("COLLECTOR_DB_PATH", "/data/events.db"))
    app.run(host="0.0.0.0", port=8787)
