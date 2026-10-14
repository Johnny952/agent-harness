from __future__ import annotations

import json
import os
import re
import sys

import requests

# --- Redaction (`docs/decisions.md` ADR 74) ---------------------------------
# Every rule lives in this block, in this file: `docker/agent/Dockerfile`
# copies the hook alone into the agent image, so no sibling module and only
# the standard library. There is no setting that turns redaction off.

# A name that says token, secret, password, key or credential. `key` counts
# as a word of the name (API_KEY, ssh-key, apiKey), not inside KEYBOARD or
# monkey.
SECRET_NAME = re.compile(
    r"(?i:token|secret|passw(?:or)?d|passphrase|credential|apikey)"
    r"|(?i:(?<![a-z])key(?![a-z]))"
    r"|(?<=[a-z0-9])Key(?![a-z])"
)
# Shorter env values are skipped: `SOME_KEY=1` would redact every `1`.
MIN_ENV_SECRET_LEN = 8
ALWAYS_SECRET_ENV = ("COLLECTOR_TOKEN",)

# (rule, pattern, replacement), applied in this order. A group 1 is the
# scheme word that stays.
SHAPES = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY-----"
            r"(?:.*?-----END[A-Z0-9 ]*PRIVATE KEY-----|.*\Z)",
            re.DOTALL,
        ),
    ),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "bearer-token",
        re.compile(r"(\bbearer[ \t]+)[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE),
    ),
    (
        "basic-auth",
        re.compile(
            r"(\bauthorization[ \t]*:[ \t]*basic[ \t]+)[A-Za-z0-9+/]{4,}=*",
            re.IGNORECASE,
        ),
    ),
]
JSON_PAIR = re.compile(
    r'"([^"\\\n]*)"(\s*:\s*)"(?!\[REDACTED:)(?:[^"\\\n]|\\.)*"'
)
ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_.-]*)"
    r"([ \t]*(?<![=!<>])=(?!=)[ \t]*)"
    r"(?!\[REDACTED:)(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"'`;&|,)]+)"
)
# A file whose name marks it secret. `fullmatch`ed against each segment of
# each `tool_input` string, split on SEGMENT_SEPARATORS.
SECRET_FILE = re.compile(
    r"\.env(?:\..+)?|\.envrc|.*credential.*|.*\.pem|.*\.key"
    r"|id_(?:rsa|dsa|ecdsa|ed25519).*|\.netrc|\.pgpass|\.npmrc|\.pypirc"
    r"|.*secret.*\.(?:json|ya?ml|toml|env|txt)",
    re.IGNORECASE,
)
SEGMENT_SEPARATORS = re.compile(r"[\s'\"=:;|&<>()`,/]+")
SECRET_FILE_KEPT_KEYS = {"type", "filePath", "file_path", "path", "cwd"}
SECRET_FILE_CONTENT_KEYS = {"content", "old_string", "new_string", "new_source"}
REDACTION_FAILED_PAYLOAD = {"redaction": "[REDACTED:redaction-failed]"}


def _marker(rule: str) -> str:
    return f"[REDACTED:{rule}]"


def _env_secrets(env) -> list[str]:
    values = set()
    for name, value in env.items():
        if not value:
            continue
        if name in ALWAYS_SECRET_ENV or (
            SECRET_NAME.search(name) and len(value) >= MIN_ENV_SECRET_LEN
        ):
            values.add(value)
    return sorted(values, key=lambda v: (-len(v), v))


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _names_secret_file(tool_input) -> bool:
    for text in _strings(tool_input):
        for segment in SEGMENT_SEPARATORS.split(text):
            if segment and SECRET_FILE.fullmatch(segment):
                return True
    return False


def _redact_text(text: str, env_secrets: list[str]) -> str:
    if text.startswith("[REDACTED:"):
        return text
    for value in env_secrets:
        text = text.replace(value, _marker("env-secret"))
    for rule, pattern in SHAPES:
        if pattern.groups:
            text = pattern.sub(lambda m, r=rule: m.group(1) + _marker(r), text)
        else:
            text = pattern.sub(_marker(rule), text)

    def _pair(match):
        if SECRET_NAME.search(match.group(1)):
            return f'"{match.group(1)}"{match.group(2)}"{_marker("secret-field")}"'
        return match.group(0)

    text = JSON_PAIR.sub(_pair, text)

    def _assign(match):
        if SECRET_NAME.search(match.group(1)):
            return match.group(1) + match.group(2) + _marker("secret-assignment")
        return match.group(0)

    return ASSIGNMENT.sub(_assign, text)


def redact(event, env):
    """A copy of `event` with secrets replaced by `[REDACTED:<rule>]`.

    Same keys, same order, same non-string values; only strings change.
    """
    env_secrets = _env_secrets(env)
    secret_file = isinstance(event, dict) and _names_secret_file(
        event.get("tool_input")
    )

    def walk(node, section, key):
        if isinstance(node, dict):
            return {k: walk(v, section, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, section, key) for v in node]
        if not isinstance(node, str):
            return node
        if node.startswith("[REDACTED:"):
            return node
        if secret_file and (
            (section == "tool_response" and key not in SECRET_FILE_KEPT_KEYS)
            or (section == "tool_input" and key in SECRET_FILE_CONTENT_KEYS)
        ):
            return _marker("secret-file")
        if key is not None and SECRET_NAME.search(key):
            return _marker("secret-field")
        return _redact_text(node, env_secrets)

    if isinstance(event, dict):
        return {k: walk(v, k, k) for k, v in event.items()}
    return walk(event, None, None)


def main() -> None:
    event = json.load(sys.stdin)
    collector_url = os.environ["COLLECTOR_URL"]
    source_app = os.environ["SOURCE_APP"]
    event_type = event.get("hook_event_name", "unknown")
    # Fail closed: if redacting raises, the unredacted event is never posted.
    # The message is not printed, it may quote the secret.
    try:
        payload = redact(event, os.environ)
    except Exception as exc:
        print(f"emit_event: redaction failed: {type(exc).__name__}", file=sys.stderr)
        payload = dict(REDACTION_FAILED_PAYLOAD)
    body = {
        "source_app": source_app,
        "event_type": event_type,
        "payload": payload,
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
