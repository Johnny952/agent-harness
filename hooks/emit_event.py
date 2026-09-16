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
    # Observability is best-effort: a down collector must not fail the hook
    # (that would surface as a hook error inside the agent session), so only
    # network errors are swallowed here — config bugs (bad stdin, missing
    # env vars) should still raise and be visible.
    try:
        requests.post(f"{collector_url}/events", json=body, timeout=5)
    except requests.RequestException as exc:
        print(f"emit_event: collector unreachable: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
