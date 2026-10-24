"""The config writer: the one service that changes the dispatcher's overrides.

The console edits the dispatcher's settings through `config.d/overrides.yaml`
(`docs/decisions.md` ADR 75). This service is the only writer of that file. It
imports `dispatcher.config` for the key table and for `load_config`, so a save
is validated by the loader the dispatcher runs, and `observability.auth` for the
token check. It holds one read-write mount, `config.d`, and no docker socket.

Three routes, one bearer token (`CONFIG_WRITER_TOKEN`). The edges ADR 75 leaves
open are ADR 110's; the network and port are ADR 109's.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

import yaml
from flask import Flask, jsonify, request

from dispatcher.config import (
    CONFIG_KEYS,
    DRAFT_ROLE,
    EDITABLE,
    PHASE_ROLES,
    REMOVED_BOARD_KEYS,
    Config,
    load_config,
)
from dispatcher.project_docs import MAPPER_ROLE
from observability.auth import _bearer, check_token

CONFIG_PATH = "/app/config.yaml"
HOST = "0.0.0.0"
PORT = 8791

#: Request threads (`docs/decisions.md` ADR 420): saves are rare and each holds
#: a file lock briefly, so four is ample.
THREADS = 4
TOKEN_VAR = "CONFIG_WRITER_TOKEN"
REALM = "ia-harness-config-writer"
RECENT_CHANGES = 20

OVERRIDES = "overrides.yaml"
PREVIOUS = "overrides.yaml.prev"
CHANGES = "changes.jsonl"
LOCK = "overrides.lock"

HEADER = (
    "# Written by the config-writer service (docs/decisions.md ADR 75).\n"
    "# Delete a key to reset it to config.yaml's value or the default.\n"
)

#: What `load_config` can raise on a file that does not load: a `ValueError`
#: for a refusal it words, but a missing `accounts` key or a wrong shape in
#: `config.yaml` comes out as `KeyError`/`TypeError`, and a bad file as
#: `yaml.YAMLError`. A route must answer for all of them, not 500.
LOAD_ERRORS = (OSError, ValueError, KeyError, TypeError, yaml.YAMLError)


def _sha256(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _mapping(data: bytes | None) -> dict | None:
    """The file's bytes as a mapping (`{}` for none/empty), or None when they
    do not parse or are not a mapping."""
    try:
        loaded = yaml.safe_load(data) if data is not None else None
    except yaml.YAMLError:
        return None
    if loaded is None:
        return {}
    return loaded if isinstance(loaded, dict) else None


def _load_message(config_path: str, overrides_path: str) -> str | None:
    """Why `load_config` refuses this pair, or None when it loads."""
    try:
        load_config(config_path, overrides_path=overrides_path)
    except ValueError as err:
        return str(err)
    except LOAD_ERRORS as err:
        return f"{config_path}: {type(err).__name__}: {err}"
    return None


def _load(config_path: str, overrides_path: str) -> tuple[Config | None, str | None]:
    try:
        return load_config(config_path, overrides_path=overrides_path), None
    except ValueError as err:
        return None, str(err)
    except LOAD_ERRORS as err:
        return None, f"{config_path}: {type(err).__name__}: {err}"


def _open(path: str, flags: int) -> int:
    """`os.open` with mode 0644 whatever the umask: the dispatcher and the api
    read what this service writes."""
    fd = os.open(path, flags | os.O_CREAT, 0o644)
    try:
        os.fchmod(fd, 0o644)
    except OSError:
        pass
    return fd


def _write_temp(directory: str, data: bytes, prefix: str = ".overrides.") -> str:
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    try:
        os.fchmod(fd, 0o644)
        os.write(fd, data)
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise
    os.close(fd)
    return tmp


def _replace_with(directory: str, data: bytes, target: str) -> None:
    """`data` onto `target` by temp file and `os.replace`."""
    tmp = _write_temp(directory, data)
    try:
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


def _append_change(path: str, entry: dict) -> None:
    """One JSON line onto the log.

    `O_APPEND` and a single `os.write`, not a temp file and a replace: a line is
    added to the end of the file and nothing before it is rewritten, so there is
    no copy to swap in and a replace would only drop concurrent lines
    (`docs/learnings/an-append-only-log-is-not-the-atomic-write-case.md`).
    """
    line = (json.dumps(entry) + "\n").encode()
    fd = _open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _unauthorized():
    return (
        jsonify({"error": "a valid bearer token is required"}),
        401,
        {"WWW-Authenticate": f'Bearer realm="{REALM}"'},
    )


def _bad(message: str, status: int = 400):
    return jsonify({"error": message}), status


def create_app(config_path: str, token: str) -> Flask:
    """The writer for `config_path`, guarded by `token`.

    Both are required, no default: a caller that forgets the token gets a
    `TypeError`, not an open writer. `check_token` refuses an empty token, so an
    empty one closes every route.
    """
    config_d = os.path.join(os.path.dirname(config_path), "config.d")
    overrides_path = os.path.join(config_d, OVERRIDES)
    previous_path = os.path.join(config_d, PREVIOUS)
    changes_path = os.path.join(config_d, CHANGES)
    lock_path = os.path.join(config_d, LOCK)
    app = Flask(__name__)

    def authorized() -> bool:
        return check_token(_bearer(request.headers.get("Authorization", "")), token)

    def recent_changes(warnings: list[str]) -> list[dict]:
        raw = _read(changes_path)
        if raw is None:
            return []
        entries = []
        for number, line in enumerate(raw.decode(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                warnings.append(f"{changes_path} line {number} is not a JSON object; skipped")
                continue
            entries.append(entry)
        return entries[-RECENT_CHANGES:][::-1]

    def payload() -> tuple[dict, list[str]]:
        warnings: list[str] = []
        raw = _read(overrides_path)
        overrides = _mapping(raw)
        try:
            with open(config_path) as f:
                base = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            base = None
        if not isinstance(base, dict):
            base = None
        cfg, error = _load(config_path, overrides_path)
        fields = {f.name for f in dataclasses.fields(Config)}
        rows = []
        for key, spec in CONFIG_KEYS.items():
            if overrides is not None and key in overrides:
                source = "override"
            elif base is not None and key in base:
                source = "config.yaml"
            else:
                source = "default"
            if key in REMOVED_BOARD_KEYS:
                value = None
            elif cfg is not None and key in fields:
                value = getattr(cfg, key)
                if key == "accounts":
                    value = [dataclasses.asdict(a) for a in value]
            elif source == "override":
                value = overrides[key]
            elif source == "config.yaml":
                value = base[key]
            else:
                value = None
            rows.append({
                "key": key,
                "class": spec.key_class,
                "kind": spec.kind,
                "reason": spec.reason,
                "value": value,
                "source": source,
            })
        data = {
            "base": base,
            "overrides": overrides,
            "revision": _sha256(raw),
            "rows": rows,
            "roles": {
                "role_model": sorted((PHASE_ROLES - {MAPPER_ROLE}) | {DRAFT_ROLE}),
                "role_effort": sorted(PHASE_ROLES),
                "fallback_roles": sorted(PHASE_ROLES),
            },
            "recent_changes": recent_changes(warnings),
            "error": error,
        }
        return data, warnings

    def settings_response():
        data, warnings = payload()
        return jsonify({"data": data, "warnings": warnings})

    @app.get("/settings")
    def get_settings():
        if not authorized():
            return _unauthorized()
        return settings_response()

    @app.put("/settings/<key>")
    def put_setting(key):
        return change("set", key)

    @app.delete("/settings/<key>")
    def delete_setting(key):
        return change("reset", key)

    def change(op: str, key: str):
        if not authorized():
            return _unauthorized()
        spec = CONFIG_KEYS.get(key)
        if op == "set" and (spec is None or spec.key_class != EDITABLE):
            what = "not a config key" if spec is None else f"{spec.key_class} and cannot be set"
            return _bad(f"{key} is {what}")
        body = request.get_json(silent=True)
        needed = ("value", "revision") if op == "set" else ("revision",)
        if not isinstance(body, dict) or any(name not in body for name in needed):
            return _bad("the body must be a JSON object with " + " and ".join(needed))
        revision = body["revision"]
        if revision is not None and not isinstance(revision, str):
            return _bad("revision must be a string or null")
        actor = request.headers.get("X-Actor", "").strip()
        if not actor:
            return _bad("the X-Actor header is required")

        lock_fd = _open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return write(op, key, spec, body.get("value"), revision, actor)
        finally:
            os.close(lock_fd)  # closing releases the flock

    def write(op, key, spec, value, revision, actor):
        current = _read(overrides_path)
        revision_before = _sha256(current)
        if revision != revision_before:
            return _bad("the overrides changed since this revision; reload and try again", 409)
        mapping = _mapping(current)
        if mapping is None:
            return _bad(
                _load_message(config_path, overrides_path)
                or f"{overrides_path} does not parse or is not a mapping",
                422,
            )
        old = mapping.get(key)
        if op == "reset":
            if key not in mapping:
                if spec is None or spec.key_class != EDITABLE:
                    return _bad(f"{key} is not an editable config key")
                return settings_response()
            new_mapping = {k: v for k, v in mapping.items() if k != key}
            new = None
        else:
            new_mapping = {**mapping, key: value}
            new = value

        candidate = (HEADER + yaml.safe_dump(new_mapping, sort_keys=True)).encode()
        try:
            tmp = _write_temp(config_d, candidate)
        except OSError as err:
            return _bad(f"cannot write {config_d}: {err}", 500)
        try:
            message = _load_message(config_path, tmp)
            if message is not None:
                return _bad(message.replace(tmp, overrides_path), 422)
            if current is not None:
                _replace_with(config_d, current, previous_path)
            os.replace(tmp, overrides_path)
        except OSError as err:
            return _bad(f"cannot write {config_d}: {err}", 500)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor,
            "via": "console",
            "key": key,
            "op": op,
            "old": old,
            "new": new,
            "revision_before": revision_before,
            "revision_after": _sha256(candidate),
        }
        try:
            _append_change(changes_path, entry)
        except OSError as err:
            # No change stands without its log line (ADR 75 part 3).
            try:
                if current is None:
                    os.unlink(overrides_path)
                else:
                    _replace_with(config_d, current, overrides_path)
            except OSError as undo:
                return _bad(
                    f"cannot log the change ({err}) and cannot put the previous "
                    f"{OVERRIDES} back ({undo}); restore it from {PREVIOUS}",
                    500,
                )
            return _bad(f"cannot log the change, so it was not saved: {err}", 500)
        return settings_response()

    return app


def main(environ=None) -> Flask:
    """The app built from the environment, or exit 2 naming what is missing."""
    environ = os.environ if environ is None else environ
    token = (environ.get(TOKEN_VAR) or "").strip()
    if not token:
        print(
            f"config-writer: refusing to start: {TOKEN_VAR} is not set: "
            "the writer accepts no save without it",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return create_app(environ.get("CONFIG_PATH", CONFIG_PATH), token)


def _log_waitress() -> None:
    """Send waitress's start line and queue warnings to stderr once (ADR 420, 423)."""
    logger = logging.getLogger("waitress")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # waitress.serve() gives root a handler too (ADR 423)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        logger.addHandler(handler)


def run(environ=None) -> None:
    """Serve the config writer from waitress (`docs/decisions.md` ADR 420)."""
    app = main(environ)
    # Imported after `main`: a refused configuration exits 2 whether or not
    # waitress is installed.
    from waitress import serve

    _log_waitress()
    serve(app, host=HOST, port=PORT, threads=THREADS)


if __name__ == "__main__":
    run()
