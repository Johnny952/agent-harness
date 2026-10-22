"""Backup and restore of the hive and `/state` (`docs/decisions.md` ADR 327, 328).

A backup is a plain directory, `<BACKUP_DIR>/<UTC time>/{hive,state,manifest.json}`,
built under a `.partial-` name and renamed once complete. It is taken with every
project lock held, so no cycle writes while it runs. The two sources are the only
things ever read, and `NEVER_COPIED` keeps a credential out even if one sits inside
them. A restore syncs a copy back in place (both roots are mount points) and sets
aside whatever it replaces; it deletes nothing.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dispatcher import context_transfer, projects, worker
from dispatcher.project_lock import ProjectLock, ProjectBusy

NEVER_COPIED = (
    "users", "users.*", "sessions", "sessions.*", "claude_creds_*", "claude_shared",
    ".credentials.json", "push-subscriptions.json", "*.pem", "id_ed25519*", ".data",
)
NOT_RECORDS = ("*.lock", "surface-locks")
NAME_FORMAT = "%Y%m%dT%H%M%SZ"
NAME_RE = re.compile(r"^\d{8}T\d{6}Z$")
KEEP_DEFAULT = 7
FORMAT = 1
RESTORE_LINES = (
    "each account must sign in again from /pool: no Claude login is in a backup",
    "recreate the first user: the users store is not in a backup",
    "turn push on again on each device: push subscriptions are not in a backup",
)
ASIDE = ".pre-restore-"
PARTIAL = ".partial-"


class BackupError(ValueError):
    """A refusal that touches nothing; the CLI exits 2."""


@dataclass
class Result:
    name: str
    files: int = 0
    skipped_symlinks: int = 0
    pruned: list[str] = field(default_factory=list)
    asides: list[str] = field(default_factory=list)


def settings_from_env(environ) -> tuple[str, int]:
    backup_dir = (environ.get("BACKUP_DIR") or "").strip()
    if not backup_dir:
        raise BackupError("BACKUP_DIR is not set")
    raw = (environ.get("BACKUP_KEEP") or "").strip()
    if not raw:
        return backup_dir, KEEP_DEFAULT
    if not raw.isdigit() or int(raw) < 1:
        raise BackupError(f"BACKUP_KEEP must be a positive integer, got {raw!r}")
    return backup_dir, int(raw)


def _skipped(name: str) -> bool:
    if name.startswith((ASIDE, PARTIAL)):
        return True
    return any(fnmatch.fnmatchcase(name, pat) for pat in NEVER_COPIED + NOT_RECORDS)


def copies(backup_dir: str) -> list[str]:
    """Completed copies, oldest first. Partial directories and strangers are not copies."""
    try:
        names = os.listdir(backup_dir)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if NAME_RE.match(n) and os.path.isdir(os.path.join(backup_dir, n)))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _lock_slugs(hive_root: str, state_dir: str) -> list[str]:
    found = set(projects.slugs(hive_root))
    try:
        found.update(n[: -len(".lock")] for n in os.listdir(os.path.join(state_dir, "project-locks"))
                     if n.endswith(".lock") and projects.is_slug(n[: -len(".lock")]))
    except FileNotFoundError:
        pass
    return sorted(found)


class _Locks:
    """Every project lock, taken without waiting; one busy releases the rest and re-raises."""

    def __init__(self, hive_root: str, state_dir: str, via: str):
        self._held: list[ProjectLock] = []
        try:
            for slug in _lock_slugs(hive_root, state_dir):
                self._held.append(ProjectLock.take(state_dir, slug, None, via))
        except ProjectBusy:
            self.release()
            raise

    def release(self) -> None:
        for lock in self._held:
            lock.release()
        self._held = []


def _copy_tree(src: str, dst: str, result: Result) -> None:
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        path = os.path.join(src, name)
        if os.path.islink(path):
            result.skipped_symlinks += 1
        elif _skipped(name):
            continue
        elif os.path.isdir(path):
            _copy_tree(path, os.path.join(dst, name), result)
        elif os.path.isfile(path):
            shutil.copy2(path, os.path.join(dst, name))
            result.files += 1


def backup(hive_root: str, state_dir: str, backup_dir: str, keep: int, *, now: datetime) -> Result:
    hive_root, state_dir = os.path.realpath(hive_root), os.path.realpath(state_dir)
    target = os.path.realpath(backup_dir)
    for source in (hive_root, state_dir):
        if _inside(target, source) or _inside(source, target):
            raise BackupError(f"BACKUP_DIR {backup_dir} and {source} must not contain one another")
    name = now.strftime(NAME_FORMAT)
    final, partial = os.path.join(target, name), os.path.join(target, PARTIAL + name)
    if os.path.exists(final):
        raise BackupError(f"a copy named {name} already exists")
    locks = _Locks(hive_root, state_dir, "backup")  # ProjectBusy: nothing written
    result = Result(name)
    try:
        os.makedirs(target, exist_ok=True)
        shutil.rmtree(partial, ignore_errors=True)
        _copy_tree(hive_root, os.path.join(partial, "hive"), result)
        _copy_tree(state_dir, os.path.join(partial, "state"), result)
        manifest = {"format": FORMAT, "created_at": now.isoformat(), "hive_root": hive_root,
                    "state_dir": state_dir, "files": result.files}
        Path(partial, "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        locks.release()
    old = [n for n in copies(target) if n != name]
    for victim in old[: max(0, len(old) + 1 - keep)]:
        shutil.rmtree(os.path.join(target, victim))
        result.pruned.append(victim)
    return result


def _resolve_copy(backup_dir: str, which: str) -> str:
    names = copies(backup_dir)
    if which == "latest":
        if not names:
            raise BackupError(f"no copy under {backup_dir}")
        which = names[-1]
    elif which not in names:
        raise BackupError(f"no copy named {which!r} under {backup_dir}")
    path = os.path.join(backup_dir, which)
    try:
        manifest = json.loads(Path(path, "manifest.json").read_text())
    except (OSError, ValueError):
        raise BackupError(f"copy {which} has no readable manifest.json") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError(f"copy {which} is not format {FORMAT}")
    if not (os.path.isdir(os.path.join(path, "hive")) and os.path.isdir(os.path.join(path, "state"))):
        raise BackupError(f"copy {which} lacks hive/ or state/")
    return path


def _set_aside(live: str, aside_root: str, rel: str, result: Result) -> None:
    dest = os.path.join(aside_root, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(live, dest)
    if aside_root not in result.asides:
        result.asides.append(aside_root)


def _sync(src: str, dst: str, root: str, aside_root: str, result: Result) -> None:
    """Make `dst` hold what `src` holds, in place; what `src` lacks is moved aside."""
    os.makedirs(dst, exist_ok=True)
    wanted = set(os.listdir(src))
    for name in sorted(os.listdir(dst)):
        live = os.path.join(dst, name)
        if _skipped(name) or os.path.islink(live) or (name in wanted and _same_kind(live, os.path.join(src, name))):
            continue
        _set_aside(live, aside_root, os.path.relpath(live, root), result)
    for name in sorted(wanted):
        theirs, live = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(theirs):
            _sync(theirs, live, root, aside_root, result)
        else:
            fd, tmp = tempfile.mkstemp(dir=dst, prefix=".restore-")
            os.close(fd)
            shutil.copy2(theirs, tmp)
            os.replace(tmp, live)
            result.files += 1


def _same_kind(live: str, theirs: str) -> bool:
    return os.path.isdir(live) == os.path.isdir(theirs)


def restore(hive_root: str, state_dir: str, backup_dir: str, which: str, *, now: datetime) -> Result:
    hive_root, state_dir = os.path.realpath(hive_root), os.path.realpath(state_dir)
    source = _resolve_copy(os.path.realpath(backup_dir), which)
    result = Result(os.path.basename(source))
    fd = worker.acquire(state_dir)  # WorkerLocked: nothing touched
    try:
        locks = _Locks(hive_root, state_dir, "restore")
        try:
            stamp = ASIDE + now.strftime(NAME_FORMAT)
            for sub, root in (("hive", hive_root), ("state", state_dir)):
                _sync(os.path.join(source, sub), root, root, os.path.join(root, stamp), result)
        finally:
            locks.release()
    finally:
        os.close(fd)
    return result
