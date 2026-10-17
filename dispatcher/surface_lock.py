# dispatcher/surface_lock.py
"""The surface lock: two tasks whose surfaces overlap never write at once.

`docs/plans/autonomy.md` *Decisions*, 6, task B-3, and `docs/decisions.md`
ADR 83. A task takes it before its first implementador and holds it until
it merges or leaves the cycle: the write window. Its free roles before that
(the mapping, the arquitecto) read the base and write only their own plan,
so they run in parallel whatever the surfaces say.

One record per task holding a window, under
`<state_dir>/surface-locks/<slug>/<task_id>.json`, naming the surface it
holds. A task may take the lock when no live record's surface overlaps its
own (`surface.overlaps`, so the append-only logs never collide and the whole
repo collides with everything). Every read-check-write takes a short `flock`
on the directory's guard file, so two acquirers can never both see the
surface free, and a record's liveness is the base lock's (ADR 80): a fresh
heartbeat, or a holder on this host whose pid is alive. A dead holder's
record is broken and removed on the next look.

The file is what makes the lock hold across processes. Who among one
process's waiters goes first is the queue's to decide (`dispatcher/queue.py`),
since it knows the confirmed order.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import socket
import threading
from pathlib import Path

from dispatcher import context_transfer, surface, tasks

#: Where the records live, under `state_dir`.
SURFACE_LOCK_SUBDIR = "surface-locks"

_GUARD = ".guard"


def surface_lock_dir(state_dir: str, slug: str) -> str:
    """Where a project's surface-lock records live."""
    if not context_transfer._is_single_path_component(slug):
        raise ValueError(f"project slug {slug!r} is not a single path component")
    return os.path.join(state_dir, SURFACE_LOCK_SUBDIR, slug)


def _record_path(state_dir: str, slug: str, task_id: str) -> str:
    if not context_transfer._is_single_path_component(task_id):
        raise ValueError(f"task id {task_id!r} is not a single path component")
    return os.path.join(surface_lock_dir(state_dir, slug), f"{task_id}.json")


@contextlib.contextmanager
def _guard(state_dir: str, slug: str):
    """Hold an exclusive `flock` on the project's guard file for one read-check-write."""
    directory = surface_lock_dir(state_dir, slug)
    Path(directory).mkdir(parents=True, exist_ok=True)
    fd = os.open(os.path.join(directory, _GUARD), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            record = json.loads(f.read())
    except FileNotFoundError:
        return None
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def holders(state_dir: str, slug: str, ttl_seconds: int) -> dict[str, frozenset[str]]:
    """The live holders' surfaces by task id, dropping the dead ones' records.

    Call it under `_guard`. A corrupt record is stale, as the base lock's is.
    """
    directory = surface_lock_dir(state_dir, slug)
    live: dict[str, frozenset[str]] = {}
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return live
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        record = _read(path)
        if record is None:
            continue
        if not tasks.base_lock_is_live(record, ttl_seconds):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
            continue
        live[name[: -len(".json")]] = frozenset(record.get("surface") or ())
    return live


def try_acquire(
    state_dir: str,
    slug: str,
    task_id: str,
    entries: frozenset[str],
    owner: str,
    ttl_seconds: int,
) -> list[str]:
    """Take the lock for `entries` now; the tasks in the way when it cannot.

    An empty list means it is taken. A record this task already holds under
    the same owner is taken again; one under another live owner is in the way
    like any overlapping holder (a second process running the same task).
    """
    path = _record_path(state_dir, slug, task_id)
    with _guard(state_dir, slug):
        live = holders(state_dir, slug, ttl_seconds)
        mine = _read(path)
        if task_id in live and (mine or {}).get("owner") != owner:
            return [task_id]
        live.pop(task_id, None)
        blocking = [other for other, held in live.items() if surface.overlaps(entries, held)]
        if blocking:
            return blocking
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        record = {
            "owner": owner,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "task_id": task_id,
            "surface": sorted(entries),
            "acquired": stamp,
            "heartbeat": stamp,
        }
        context_transfer._write_atomic(path, json.dumps(record, indent=2) + "\n")
    return []


def refresh(state_dir: str, slug: str, task_id: str, owner: str) -> bool:
    """Refresh the heartbeat of a record `owner` holds; False when it is not theirs."""
    path = _record_path(state_dir, slug, task_id)
    with _guard(state_dir, slug):
        record = _read(path)
        if not record or record.get("owner") != owner:
            return False
        record["heartbeat"] = dt.datetime.now(dt.timezone.utc).isoformat()
        context_transfer._write_atomic(path, json.dumps(record, indent=2) + "\n")
    return True


def release(state_dir: str, slug: str, task_id: str, owner: str) -> bool:
    """Remove the record if `owner` holds it; one someone else took is left."""
    path = _record_path(state_dir, slug, task_id)
    with _guard(state_dir, slug):
        record = _read(path)
        if not record or record.get("owner") != owner:
            return False
        os.remove(path)
    return True


class SurfaceLock:
    """One task's write window, held: the record plus a heartbeat thread.

    Built only once `try_acquire` has said yes (`take`), so it never waits on
    its own; waiting, and the order of waiters, is the caller's. `release` is
    idempotent and never raises.
    """

    def __init__(
        self,
        state_dir: str,
        slug: str,
        task_id: str,
        owner: str,
        *,
        heartbeat_interval_seconds: float,
    ) -> None:
        self.state_dir = state_dir
        self.slug = slug
        self.task_id = task_id
        self.owner = owner
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.held = True
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._beat, name=f"surface-lock-{slug}-{task_id}", daemon=True
        )
        self._thread.start()

    @classmethod
    def take(
        cls,
        state_dir: str,
        slug: str,
        task_id: str,
        entries: frozenset[str],
        *,
        ttl_seconds: int,
        heartbeat_interval_seconds: float,
    ) -> "SurfaceLock | list[str]":
        """The held lock, or the tasks in the way."""
        owner = tasks.new_base_lock_owner()
        blocking = try_acquire(state_dir, slug, task_id, entries, owner, ttl_seconds)
        if blocking:
            return blocking
        return cls(
            state_dir, slug, task_id, owner,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    def _beat(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                refresh(self.state_dir, self.slug, self.task_id, self.owner)
            except Exception:  # noqa: BLE001 — a missed beat is retried on the next one
                pass

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            release(self.state_dir, self.slug, self.task_id, self.owner)
        except Exception:  # noqa: BLE001 — a record left behind goes stale and is broken
            pass
