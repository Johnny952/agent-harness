from __future__ import annotations

import json
import os
import sys

import requests


def main() -> None:
    event = json.load(sys.stdin)
    collector_url = os.environ["COLLECTOR_URL"]
    source_app = os.environ["SOURCE_APP"]
    body = {
        "source_app": source_app,
        "event_type": event.get("hook_event_name", "unknown"),
        "payload": event,
    }
    requests.post(f"{collector_url}/events", json=body, timeout=5)


if __name__ == "__main__":
    main()
