"""One cycle per project at a time (`docs/decisions.md` ADR 200).

An exclusive, non-blocking `flock` on `<state_dir>/project-locks/<slug>.lock`,
held by an open file descriptor for as long as a cycle runs. A process that
dies closes the descriptor and the kernel gives the lock back, so nothing
reaps it and the file's presence means nothing. The holder writes one JSON line
into the file once it holds the lock, only so a refused start can name who
holds the project; it is never read to decide whether the project is busy.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import socket
from pathlib import Path

from dispatcher import context_transfer


def project_lock_path(state_dir: str, slug: str) -> str:
    """Where a project's lock lives: `<state_dir>/project-locks/<slug>.lock`."""
    if not context_transfer._is_single_path_component(slug):
        raise ValueError(f"project slug {slug!r} is not a single path component")
    return os.path.join(state_dir, "project-locks", f"{slug}.lock")


class ProjectBusy(RuntimeError):
    """Another cycle holds the project. `holder` is its record, None if unreadable."""

    def __init__(self, slug: str, holder: dict | None):
        self.slug = slug
        self.holder = holder
        if holder:
            who = (
                f"task {holder.get('task_id')} holds it ({holder.get('via')}, "
                f"pid {holder.get('pid')}, since {holder.get('since')})"
            )
        else:
            who = "held by an unknown holder"
        super().__init__(f"project {slug} is busy: {who}")


class ProjectLock:
    """A held project lock. Take it with `take`; give it back with `release`."""

    def __init__(self, slug: str, fd: int):
        self.slug = slug
        self._fd: int | None = fd

    @classmethod
    def take(cls, state_dir: str, slug: str, task_id: str | None, via: str) -> "ProjectLock":
        """Hold the project or raise `ProjectBusy`; never waits."""
        path = project_lock_path(state_dir, slug)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _read_holder(fd)
            os.close(fd)
            raise ProjectBusy(slug, holder) from None
        except BaseException:
            os.close(fd)
            raise
        record = {
            "task_id": task_id,
            "via": via,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "since": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, (json.dumps(record) + "\n").encode(), 0)
        except OSError:
            pass  # the record only names the holder; the flock is the lock
        return cls(slug, fd)

    def release(self) -> None:
        """Give the project back. Idempotent, never raises."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            # Emptied while still held: truncating after the close could wipe
            # the record of the next holder. The file itself is never removed.
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "ProjectLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _read_holder(fd: int) -> dict | None:
    try:
        raw = os.pread(fd, 4096, 0)
        record = json.loads(raw.decode())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None
