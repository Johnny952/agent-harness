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
    # This agent's write token (`docs/decisions.md` ADR 72). The collector
    # stores the `source_app` the token is bound to, not the one in `body`, so
    # the `SOURCE_APP` above is only a label the collector may disregard.
    # `.get` and not `[...]`, unlike the two variables above: a missing token is
    # a collector that refuses the event, which the branch below reports in one
    # line, and an agent whose `.env` predates the token must keep its hooks
    # working rather than fail every tool call on a `KeyError`.
    token = os.environ.get("COLLECTOR_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Observability is best-effort: a down collector must not fail the hook
    # (that would surface as a hook error inside the agent session), so only
    # network errors are swallowed here — config bugs (bad stdin, missing
    # env vars) should still raise and be visible. A refusal (401 for a
    # missing or wrong token, 400 for a malformed event) is the same kind of
    # outcome as a collector that is down: one stderr line, never an exception.
    try:
        resp = requests.post(
            f"{collector_url}/events", json=body, headers=headers, timeout=5
        )
    except requests.RequestException as exc:
        print(f"emit_event: collector unreachable: {exc}", file=sys.stderr)
        return
    if not 200 <= resp.status_code < 300:
        print(
            f"emit_event: collector refused the event: HTTP {resp.status_code}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
