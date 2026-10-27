"""SubagentStop hook: cap how long a subagent's return may be (ADR 510).

An overlong return is blocked once, asking for the detail on disk and a short
summary; the retry is let through and logged. ``--report`` reads the log back,
one line per role, to tune the caps.

Stdlib only and self-contained: the script is copied alone into the agent image
(docs/learnings/the-hook-is-copied-alone-into-the-agent-image.md). It fails
open: any error writes one stderr line and exits 0 with no decision.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

DEFAULT_CAP = 4000

#: Per-role overrides of DEFAULT_CAP, keyed by role name. Tuned from --report.
CAPS: dict[str, int] = {}

DEFAULT_LOG_PATH = "/root/.claude/ia-harness/subagent-returns.jsonl"

SENTINEL = re.compile(r"<!--\s*ia-harness-role:\s*([\w-]+)\s*-->")
SESSION_ROLE = re.compile(r"^Role:\s*([\w-]+)\.", re.MULTILINE)
SESSION_TASK = re.compile(r"\bTask:\s*([\w-]+)\.")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _entries(path):
    if not isinstance(path, str) or not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def _message_text(entry: dict) -> str:
    message = entry.get("message")
    return _text_of(message.get("content")) if isinstance(message, dict) else ""


def _first_user_text(path) -> str:
    for entry in _entries(path):
        if entry.get("type") == "user":
            return _message_text(entry)
    return ""


def _last_assistant_text(path) -> str:
    text = ""
    for entry in _entries(path):
        if entry.get("type") == "assistant":
            text = _message_text(entry)
    return text


def role_of(payload: dict):
    """Return ``(role, source, task)``: subagent sentinel, session line, none."""
    session = _first_user_text(payload.get("transcript_path"))
    task_match = SESSION_TASK.search(session)
    task = task_match.group(1) if task_match else None
    match = SENTINEL.search(_first_user_text(payload.get("agent_transcript_path")))
    if match:
        return match.group(1), "subagent", task
    match = SESSION_ROLE.search(session)
    if match:
        return match.group(1), "session", task
    return "unknown", "none", task


def _read_log(log_path: str):
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def already_blocked(log_path: str, agent_id) -> bool:
    if not agent_id:
        return False
    return any(
        e.get("outcome") == "blocked" and e.get("agent_id") == agent_id
        for e in _read_log(log_path)
    )


def _append(log_path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def decide(payload: dict, log_path: str, now=None):
    """Return the block decision for an overlong first return, else ``None``."""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        text = _last_assistant_text(payload.get("agent_transcript_path"))
    role, source, task = role_of(payload)
    cap = CAPS.get(role, DEFAULT_CAP)
    chars = len(text)
    agent_id = payload.get("agent_id")
    retry = bool(payload.get("stop_hook_active")) or already_blocked(log_path, agent_id)
    if not retry and chars <= cap:
        return None
    when = now or datetime.now(timezone.utc)
    _append(log_path, {
        "ts": when.isoformat(),
        "outcome": "retried" if retry else "blocked",
        "role": role,
        "role_source": source,
        "task": task,
        "session_id": payload.get("session_id"),
        "agent_id": agent_id,
        "agent_type": payload.get("agent_type"),
        "chars": chars,
        "cap": cap,
    })
    if retry:
        return None
    return {
        "decision": "block",
        "reason": (
            f"Your return is {chars} characters; the cap for role {role} is "
            f"{cap}. Write the detail to a file, then reply with at most {cap} "
            "characters that name that file's path. This is asked once."
        ),
    }


def report(log_path: str) -> list[str]:
    rows: dict[str, dict] = {}
    for e in _read_log(log_path):
        role = str(e.get("role", "unknown"))
        row = rows.setdefault(
            role, {"cap": e.get("cap"), "blocked": 0, "retried": 0, "over": 0, "max": 0}
        )
        chars = e.get("chars") if isinstance(e.get("chars"), int) else 0
        row["cap"] = e.get("cap", row["cap"])
        row["max"] = max(row["max"], chars)
        if e.get("outcome") == "blocked":
            row["blocked"] += 1
        elif e.get("outcome") == "retried":
            row["retried"] += 1
            if isinstance(e.get("cap"), int) and chars > e["cap"]:
                row["over"] += 1
    return [
        f"role={r} cap={v['cap']} blocked={v['blocked']} retried={v['retried']} "
        f"retried_over={v['over']} max_chars={v['max']}"
        for r, v in sorted(rows.items())
    ]


def main(argv=None, stdin=None, stdout=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    log_path = os.environ.get("IA_HARNESS_RETURN_LOG", DEFAULT_LOG_PATH)
    try:
        if "--report" in argv:
            for line in report(log_path):
                print(line, file=stdout)
            return
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        decision = decide(payload, log_path)
        if decision is not None:
            print(json.dumps(decision), file=stdout)
    except Exception as exc:  # fail open: a broken cap must not stop a return
        print(f"ia-harness: cap_subagent_return: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
