"""The credentials snapshot: what the dispatcher's side can tell about the
secrets without opening one (docs/decisions.md ADR 456 decision 4, ADR 462, ADR 480).

A snapshot says, per region, whether a credential is there and when it last
changed: the git token, each project's deploy key, each account's Claude
login. It is made from `stat` and directory listings, and from one `stat` run
inside each agent container; no token, private key or login file is ever
opened or read (charter C-5). The deploy key's fingerprint comes from its
public half, through `project_keys.fingerprint`. `write` puts the record in
`<state_dir>/credentials.json` for the console's route to read.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone

from dispatcher import context_transfer, docker_exec, project_keys, projects

logger = logging.getLogger(__name__)

SNAPSHOT = "credentials.json"
VERSION = 1
CLAUDE_CREDENTIALS = "/root/.claude-account/.credentials.json"
STAT_TIMEOUT_SECONDS = 15
# Files Docker and Podman put at a container's root; a missing git token path
# is `not_mounted` where one exists, `absent` elsewhere (ADR 480).
CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")
_STAT_SCRIPT = 'if [ -f "$1" ]; then stat -c \'%s %Y\' "$1"; else echo absent; fi'

_LOCK = threading.Lock()


class SnapshotError(ValueError):
    """The snapshot file does not parse or is not a JSON object."""


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")


def _in_container() -> bool:
    return any(os.path.exists(p) for p in CONTAINER_MARKERS)


def git_token_row(path: str) -> dict:
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        state = "not_mounted" if _in_container() else "absent"
        return {"path": path, "state": state, "modified_at": None}
    except OSError:
        return {"path": path, "state": "absent", "modified_at": None}
    import stat as stat_mod
    if stat_mod.S_ISDIR(st.st_mode):
        state = "directory"
    elif stat_mod.S_ISREG(st.st_mode):
        state = "present" if st.st_size > 0 else "empty"
    else:
        return {"path": path, "state": "absent", "modified_at": None}
    return {"path": path, "state": state, "modified_at": _iso(st.st_mtime)}


def _key_slugs(cfg) -> list[str]:
    found = set(projects.slugs(context_transfer.hive_root(cfg.hive_tasks_dir)))
    try:
        with os.scandir(cfg.project_keys_dir) as it:
            for entry in it:
                try:
                    if entry.is_dir() and projects.is_slug(entry.name):
                        found.add(entry.name)
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(found)


def _regular_stat(path) -> os.stat_result | None:
    import stat as stat_mod
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st if stat_mod.S_ISREG(st.st_mode) else None


def _deploy_key(cfg, slug: str) -> dict:
    row = {"project": slug, "state": "absent", "modified_at": None, "fingerprint": None}
    path = project_keys.key_path(cfg.project_keys_dir, slug)
    st = _regular_stat(path)
    if st is None:
        return row
    row["state"] = "present"
    row["modified_at"] = _iso(st.st_mtime)
    public = path.with_name(path.name + ".pub")
    if _regular_stat(public) is not None:
        try:
            row["fingerprint"] = project_keys.fingerprint(public)
        except (OSError, subprocess.CalledProcessError):
            pass
    return row


def account_row(account, previous_row: dict | None = None) -> dict:
    row = {"account": account.name, "container": account.container,
           "state": "unreachable", "modified_at": None, "logged_in_at": None}
    if previous_row is not None and isinstance(previous_row.get("logged_in_at"), str):
        row["logged_in_at"] = previous_row["logged_in_at"]
    try:
        done = docker_exec.run_docker_exec(
            account.container, "/",
            ["sh", "-c", _STAT_SCRIPT, "sh", CLAUDE_CREDENTIALS],
            timeout=STAT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return row
    if done.returncode != 0:
        return row
    out = (done.stdout or "").split()
    if out == ["absent"]:
        row["state"] = "absent"
        return row
    if len(out) == 2:
        try:
            size, mtime = int(out[0]), int(out[1])
        except ValueError:
            return row
        if size > 0:
            row["state"] = "present"
            row["modified_at"] = _iso(mtime)
        else:
            row["state"] = "absent"
    return row


def snapshot(cfg, previous: dict | None = None, *, logins: dict | None = None,
             now: datetime | None = None) -> dict:
    """The record, built from `stat` and one exec per account; writes nothing."""
    now = now or datetime.now(timezone.utc)
    prior = {}
    if isinstance(previous, dict) and isinstance(previous.get("accounts"), list):
        prior = {r.get("account"): r for r in previous["accounts"] if isinstance(r, dict)}
    accounts = []
    for account in cfg.accounts:
        row = account_row(account, prior.get(account.name))
        if logins and account.name in logins:
            row["logged_in_at"] = logins[account.name]
        accounts.append(row)
    return {
        "version": VERSION,
        "written_at": now.isoformat(),
        "git_token": git_token_row(cfg.git_credentials_path),
        "deploy_keys": [_deploy_key(cfg, slug) for slug in _key_slugs(cfg)],
        "accounts": accounts,
    }


def read(state_dir) -> dict | None:
    """The last snapshot, None when there is none; `SnapshotError` when unreadable."""
    path = os.path.join(os.fspath(state_dir), SNAPSHOT)
    try:
        with open(path) as f:
            body = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"{path}: {exc}") from exc
    if not isinstance(body, dict):
        raise SnapshotError(f"{path}: not a JSON object")
    return body


def write(cfg, *, logins: dict | None = None, now: datetime | None = None) -> dict:
    """Build the snapshot and replace `<state_dir>/credentials.json` atomically."""
    with _LOCK:
        try:
            previous = read(cfg.state_dir)
        except SnapshotError:
            logger.exception("credentials: the last snapshot is unreadable; starting over")
            previous = None
        record = snapshot(cfg, previous, logins=logins, now=now)
        os.makedirs(cfg.state_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cfg.state_dir, prefix=".credentials-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(record, indent=2))
            os.replace(tmp, os.path.join(cfg.state_dir, SNAPSHOT))
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        return record
