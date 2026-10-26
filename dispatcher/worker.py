"""The action worker: drains `<state_dir>/actions/pending/` (docs/decisions.md
ADR 142 split item 2, ADR 148).

One worker at a time (a `flock` on `actions/worker.lock`), one pass every
`POLL_SECONDS`. A `cancel` runs at once on the loop thread; a `start` or a
`command_verdict` is claimed when its project is free and an account is open
and runs in its own thread; a `start` also waits as `pool` while the primary
account's free share is under its floor (the pool's floor,
docs/plans/board-service.md). The handlers are in `HANDLERS`, not in
`actions.KINDS` (ADR 148, 1). A `turn` runs a streamed `claude -p` in a thread's
container and logs it (ADR 173). A `clone`, `init`, `keygen` or `check` sets a
new project up from the console (ADR 212), spending no quota. An `approve`
merges and pushes the reviewed tip under the project's base lock, with no
account and no project lock (ADR 258, ADR 264). A `request_changes` runs the
task's cycle again from the arquitecto on its branch, holding the project and
an account as a start does (ADR 272). A `resolve_conflict` runs
`dispatch resolve-conflict` on a `conflict` task under the project's base lock,
holding an account but no project lock (ADR 285). At start and every
`CREDENTIALS_SNAPSHOT_SECONDS` a thread writes the credentials snapshot
(ADR 456, ADR 462).
Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from dispatcher import (
    actions, board_mcp, command_run, context_transfer, credentials, docker_exec, epic_runs,
    notifications, project_keys, projects, state_machine, tasks, threads,
)
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.config import Config
from dispatcher.project_lock import ProjectBusy, ProjectLock

logger = logging.getLogger(__name__)

LOCK = "worker.lock"
HEARTBEAT = "worker.json"
POLL_SECONDS = 2.0
CREDENTIALS_SNAPSHOT_SECONDS = 300
ACCOUNT_POLL_SECONDS = float(queue_mod.WAIT_POLL_SECONDS)
STOPPED_ERROR = "the worker stopped; the cycle was held at a phase boundary"
VERDICT_NOT_CANCELLABLE = (
    "a running command_verdict cannot be cancelled: its verdict is recorded "
    "before the command runs (ADR 119)"
)
# A conversation turn (ADR 173). The thread writes no code: dontAsk denies what
# no rule allows, and the three writers are denied outright.
TURN_TIMEOUT_SECONDS = 1800
TURN_PERMISSION_MODE = "dontAsk"
TURN_ALLOWED_TOOLS = (
    "Read", "Glob", "Grep", "Bash(ls*)", "Bash(pwd)", "Bash(wc*)",
    "Bash(git status*)", "Bash(git branch --list*)", "Bash(git rev-parse*)",
)
TURN_DISALLOWED_TOOLS = ("Write", "Edit", "NotebookEdit")
TURN_STOPPED_ERROR = "the worker stopped during the turn"
# What a fresh session of a thread on a task is told before the person's first
# message: the task's description, its start note and its handoffs so far, in
# at most this many characters. A resumed session already has it in its history.
TURN_SEED_MAX_CHARS = 12_000
TURN_SEED_CUT = "[... cut: the seed is kept under {max} characters]"


class WorkerLocked(Exception):
    """Another worker holds the lock."""

    def __init__(self, path: Path):
        super().__init__(f"another worker holds {path}")
        self.path = path


def _utc_now() -> datetime:
    """The module's one clock seam."""
    return datetime.now(timezone.utc)


def acquire(state_dir) -> int:
    """Take `actions/worker.lock` without waiting; the fd holds it until closed."""
    root = actions.ensure_dirs(state_dir)
    path = root / LOCK
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise WorkerLocked(path) from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _one_line(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Running:
    action_id: str
    thread: threading.Thread | None
    cancelled: threading.Event
    #: Set by a `turn` handler once it has the pid; stops the process.
    kill: Callable[[], None] | None = None
    #: The project's `flock` (ADR 200), held from the claim to the handler's end.
    project_lock: ProjectLock | None = None
    #: The project slug whose start group this action belongs to (ADR 306); the
    #: group, not the action, then releases `project_lock`.
    group: str | None = None


@dataclass
class _StartGroup:
    """One project's running starts: the shared `flock`, the write-window
    scheduler (None under a ceiling of 1) and the action ids inside."""

    lock: ProjectLock
    scheduler: queue_mod.WriteScheduler | None
    members: set[str]


class Worker:
    def __init__(self, cfg: Config, *, account_poll: float = ACCOUNT_POLL_SECONDS):
        self.cfg = cfg
        self.account_poll = account_poll
        self.started_at = _utc_now().isoformat()
        self.stopping = threading.Event()
        self.running: dict[str, Running] = {}
        self._lock = threading.Lock()
        #: Per project slug, the starts running under one `flock` (ADR 306);
        #: guarded by `_lock`.
        self._groups: dict[str, _StartGroup] = {}
        self._closed_until: datetime | None = None
        #: The primary's free share and when it was read (the pool's floor).
        self._pool_read: tuple[datetime, int | None] | None = None
        self._snapshot_started: datetime | None = None
        self._snapshot_thread: threading.Thread | None = None

    @property
    def state_dir(self) -> str:
        return self.cfg.state_dir

    # ---- heartbeat, lifecycle ----------------------------------------------

    def beat(self) -> None:
        root = actions.ensure_dirs(self.state_dir)
        body = {"pid": os.getpid(), "started_at": self.started_at,
                "beat_at": _utc_now().isoformat()}
        fd, tmp = tempfile.mkstemp(dir=root / actions.TMP, prefix="hb-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(body))
            os.replace(tmp, root / HEARTBEAT)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def recover(self) -> list[tuple[str, str]]:
        done = actions.recover(self.state_dir)
        for aid, what in done:
            logger.info("worker: recovered %s: %s", aid, what)
            if what == "interrupted":
                self._close_interrupted_turn(aid)
        return done

    def _close_interrupted_turn(self, aid: str) -> None:
        """A turn that died with its worker has a `turn_start` and no
        `turn_end`: write it (ADR 173, 4)."""
        path = actions.actions_dir(self.state_dir) / actions.DONE / f"{aid}.json"
        try:
            record = actions._load(path)
            if record.get("kind") != "turn" or not record.get("started_at"):
                return
            thread_id = record["params"]["thread"]
            threads.append_conversation(
                self.cfg.hive_tasks_dir, thread_id,
                _harness_line("turn_end", action=aid, status="interrupted",
                              error=record.get("error"), session_id=None),
            )
            title = threads.read_thread(self.cfg.hive_tasks_dir, thread_id).get("title")
            _notify_turn(self.cfg, thread_id, "interrupted", title)
        except (OSError, ValueError, KeyError, threads.ThreadRefused):
            logger.exception("worker: could not close the log of turn %s", aid)

    def stop(self) -> None:
        self.stopping.set()

    def join(self) -> None:
        while True:
            with self._lock:
                threads = [r.thread for r in self.running.values() if r.thread]
            if not threads:
                return
            for t in threads:
                t.join()

    def run(self, interval: float = POLL_SECONDS) -> None:
        self.recover()
        while not self.stopping.is_set():
            self.beat()
            self._maybe_snapshot()
            self.tick()
            self.stopping.wait(interval)
        with self._lock:
            kills = [r.kill for r in self.running.values() if r.kill]
        for kill in kills:  # a turn has no boundary to hold at (ADR 173, 8)
            kill()
        self.join()

    def _maybe_snapshot(self) -> None:
        """Start the credentials snapshot when it is due and none is running.
        A thread, because an exec into an agent that does not answer can take
        seconds and the loop runs a `cancel` at once (ADR 462, 7)."""
        if self._snapshot_thread is not None and self._snapshot_thread.is_alive():
            return
        now = _utc_now()
        if (self._snapshot_started is not None
                and now - self._snapshot_started
                < timedelta(seconds=CREDENTIALS_SNAPSHOT_SECONDS)):
            return
        self._snapshot_started = now
        self._snapshot_thread = threading.Thread(
            target=self._write_credentials, name="credentials-snapshot", daemon=True)
        self._snapshot_thread.start()

    def _write_credentials(self) -> None:
        try:
            credentials.write(self.cfg)
        except Exception:
            logger.exception("worker: the credentials snapshot failed")

    # ---- one pass -----------------------------------------------------------

    def tick(self) -> None:
        root = actions.ensure_dirs(self.state_dir)
        entries, warnings = actions._scan(root, (actions.PENDING,))
        for w in warnings:
            logger.warning("worker: %s", w)
        records = [e.record for e in entries]
        for record in records:
            if record.get("kind") == "cancel":
                claimed = actions.claim(self.state_dir, record["id"])
                if claimed is not None:
                    self._guarded(_cancel, claimed, None)
        for record in records:
            if self.stopping.is_set():
                return
            self._consider(record)

    def _consider(self, record: dict) -> None:
        kind = record.get("kind")
        aid = record["id"]
        if kind == "cancel":
            return
        if kind not in actions.KINDS:
            actions.finish(self.state_dir, aid, "failed", error=actions.UNKNOWN_KIND)
            return
        if kind not in HANDLERS:
            return
        key = actions.serial_key(record)
        with self._lock:
            busy = key is not None and key in self.running
        if busy:
            self._wait(record, "thread" if kind == "turn" else "project")
            return
        # The key above is this worker's memory; the project's `flock` is
        # what a `run-task` or a second worker process shows (ADR 200, 4).
        lock = None
        group = None
        if kind == "start":
            # Up to the project's ceiling of starts share one `flock` and one
            # write-window scheduler (ADR 306); a ceiling of 1 is the old lock.
            group = self._join_project(record)
            if group is None:
                self._wait(record, "project")
                return
            lock = group.lock
        elif key is not None and key.startswith("project:"):
            try:
                lock = ProjectLock.take(self.state_dir, record["project"], record.get("task_id"), "worker")
            except ProjectBusy:
                self._wait(record, "project")
                return

        def give_back() -> None:
            if group is not None:
                self._leave_project(record["project"], aid)
            elif lock is not None:
                lock.release()

        # A turn is held to its own thread's account by the handler (ADR 173, 5).
        if kind != "turn" and actions.KINDS[kind].spends_quota and not self._account_open():
            give_back()
            self._wait(record, "account")
            return
        # The pool's floor: a start waits while the primary's free share is
        # under its floor, read at the claim and paced like the account.
        if kind == "start":
            free, floor = self._pool_free(), actions.start_floor(record)
            if free is not None and free < floor:
                give_back()
                if record.get("waiting") != "pool":
                    logger.info(
                        "start %s waits for the pool: the primary account has %d%% free, "
                        "under its floor of %d%%", aid, free, floor,
                    )
                self._wait(record, "pool")
                return
        try:
            claimed = actions.claim(self.state_dir, aid)
        except BaseException:
            give_back()
            raise
        if claimed is None:
            give_back()
            return
        entry = Running(
            aid, None, threading.Event(), project_lock=lock,
            group=record["project"] if group is not None else None,
        )
        thread = threading.Thread(
            target=self._run_handler, args=(key, claimed, entry),
            name=f"worker-{aid}", daemon=False,
        )
        entry.thread = thread
        with self._lock:
            self.running[key or aid] = entry
        thread.start()

    def _join_project(self, record: dict) -> _StartGroup | None:
        """Take a place among the project's running starts, None while it has none.

        The first start takes the project's `flock`; the next ones, up to
        `cfg.parallel_for`, join the same group, so the lock is held until the
        last leaves (ADR 306). The place is reserved here and returned by
        `_leave_project` on every refusal after it.
        """
        slug, aid, task_id = record["project"], record["id"], record.get("task_id")
        ceiling = self.cfg.parallel_for(slug)
        with self._lock:
            group = self._groups.get(slug)
            if group is None:
                try:
                    lock = ProjectLock.take(self.state_dir, slug, task_id, "worker")
                except ProjectBusy:
                    return None
                scheduler = None
                if ceiling > 1:
                    scheduler = queue_mod.WriteScheduler(
                        self.cfg, slug, "worker", threading.Condition(threading.RLock()),
                    )
                group = self._groups[slug] = _StartGroup(lock, scheduler, set())
            elif len(group.members) >= ceiling:
                return None
            group.members.add(aid)
            if group.scheduler is not None and task_id:
                group.scheduler.enlist(task_id)
            return group

    def _leave_project(self, slug: str, aid: str) -> None:
        with self._lock:
            group = self._groups.get(slug)
            if group is None:
                return
            group.members.discard(aid)
            if not group.members:
                del self._groups[slug]
                group.lock.release()

    def _account_open(self) -> bool:
        now = _utc_now()
        if self._closed_until is not None and now < self._closed_until:
            return False
        if queue_mod.account_open(self.cfg):
            self._closed_until = None
            return True
        self._closed_until = now + timedelta(seconds=self.account_poll)
        return False

    def _pool_free(self) -> int | None:
        """The primary's free share, read again once `account_poll` has passed."""
        now = _utc_now()
        if self._pool_read is not None and now < self._pool_read[0] + timedelta(seconds=self.account_poll):
            return self._pool_read[1]
        free = core.primary_free_pct(self.cfg)
        self._pool_read = (now, free)
        return free

    def _wait(self, record: dict, why: str) -> None:
        if record.get("waiting") == why:
            return
        record["waiting"] = why
        try:
            actions.rewrite(self.state_dir, record)
        except FileNotFoundError:
            pass  # it moved (claimed or cancelled) since the scan

    def _run_handler(self, key: str | None, record: dict, entry: Running) -> None:
        try:
            self._guarded(HANDLERS[record["kind"]], record, entry.cancelled)
        finally:
            if entry.group is not None:
                self._leave_project(entry.group, record["id"])
            elif entry.project_lock is not None:
                entry.project_lock.release()
            with self._lock:
                if self.running.get(key or record["id"]) is entry:
                    del self.running[key or record["id"]]

    def _guarded(self, handler, record: dict, cancelled) -> None:
        try:
            handler(self, record, cancelled)
        except Exception as exc:
            logger.exception("worker: %s %s raised", record["kind"], record["id"])
            error = _one_line(f"{type(exc).__name__}: {exc}")
            try:
                actions.finish(self.state_dir, record["id"], "failed", error=error)
            except (FileNotFoundError, OSError):
                logger.exception("worker: could not finish %s", record["id"])


# ---- handlers ---------------------------------------------------------------


def _read_task(cfg: Config, task_id: str):
    try:
        return context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        )
    except (FileNotFoundError, ValueError):
        return None


def _held_project_lock(worker: Worker, record: dict) -> ProjectLock | None:
    """The lock `_consider` took for this action, None when it was run without one."""
    with worker._lock:
        entry = next((r for r in worker.running.values() if r.action_id == record["id"]), None)
    return entry.project_lock if entry is not None else None


def _held_scheduler(worker: Worker, record: dict):
    """The write-window scheduler of the start group this action is in, else None."""
    with worker._lock:
        group = worker._groups.get(record["project"])
        if group is None or record["id"] not in group.members:
            return None
        return group.scheduler


def _merge_hook(cfg, project: str):
    """The copy before the automatic approve's merge (ADR 345).

    The start holds `project`'s lock alone only when the project runs one task
    at a time; with more, a sibling cycle may be writing, so nothing is shared.
    `backup` imports this module, so it is imported here.
    """
    from dispatcher import backup
    return backup.merge_hook(cfg, shared=(project,) if cfg.parallel_for(project) == 1 else ())


def _start(worker: Worker, record: dict, cancelled: threading.Event) -> None:
    cfg, sd = worker.cfg, worker.state_dir
    result = dict(record.get("result") or {})
    note = record.get("note")
    if isinstance(note, str) and note.strip() and not result.get("noted_at"):
        try:
            tasks.add_comment(cfg.hive_tasks_dir, record["task_id"], record["actor"], note)
        except tasks.BoardWriteError as exc:  # includes NoTaskError
            actions.finish(sd, record["id"], "failed", result=result or None,
                           error=_one_line(f"{type(exc).__name__}: {exc}"))
            return
        result["noted_at"] = _utc_now().isoformat()
        record["result"] = result
        actions.rewrite(sd, record)
    # The start's own spending limit (ADR 230) counts from the usage log as it
    # is now, taken once so a resumed start keeps counting from its first.
    limit = actions.start_limit(record)
    if limit is not None and type(result.get("usage_offset")) is not int:
        result["usage_offset"] = epic_runs.usage_offset(cfg.hive_tasks_dir, record["task_id"])
        record["result"] = result
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    outcome = core.run_task_cycle(
        cfg, record["task_id"], record["project"], authorized=True,
        may_start_phase=lambda: not (cancelled.is_set() or worker.stopping.is_set()),
        project_lock=_held_project_lock(worker, record),
        scheduler=_held_scheduler(worker, record),
        token_limit=limit, usage_offset=result.get("usage_offset", 0),
        before_merge=_merge_hook(cfg, record["project"]),
    )
    task = _read_task(cfg, record["task_id"])
    result["outcome"] = outcome.value
    result["blocked_reason"] = (
        task.blocked_reason if outcome is core.CycleOutcome.BLOCKED and task else None
    )
    if limit is not None:
        tokens, unmeasured = epic_runs.task_spend(
            cfg.hive_tasks_dir, record["task_id"], result["usage_offset"]
        )
        result["spent"] = {"limit": limit, "tokens": tokens, "unmeasured": unmeasured}
    held = outcome is core.CycleOutcome.HELD
    if held and cancelled.is_set():
        result["last_phase"] = task.phase if task else None
        actions.finish(sd, record["id"], "cancelled", result=result)
    elif held and worker.stopping.is_set():
        actions.finish(sd, record["id"], "interrupted", result=result, error=STOPPED_ERROR)
    else:
        actions.finish(sd, record["id"], "done", result=result)


def _command_verdict(worker: Worker, record: dict, _cancelled=None) -> None:
    """Carry the owner's verdict (ADR 142, 8): `run_command_request` records
    it, runs the command on *run*, resumes the phase and goes on with the
    cycle. A refusal ran nothing and ends the action `failed` with its reason."""
    cfg, sd = worker.cfg, worker.state_dir
    params = record["params"]
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    try:
        outcome = command_run.run_command_request(
            cfg, record["task_id"], record["project"], params["n"], params["verdict"],
            params.get("comment"), by=record["actor"], prefix=params.get("prefix"),
            project_lock=_held_project_lock(worker, record),
        )
    except command_run.VerdictRefused as exc:
        actions.finish(sd, record["id"], "failed", error=_one_line(str(exc)))
        return
    task = _read_task(cfg, record["task_id"])
    actions.finish(sd, record["id"], "done", result={
        "n": params["n"], "verdict": params["verdict"], "outcome": outcome.value,
        "blocked_reason": (
            task.blocked_reason if outcome is core.CycleOutcome.BLOCKED and task else None
        ),
    })


def _cancel(worker: Worker, record: dict, _cancelled=None) -> None:
    sd = worker.state_dir
    root = actions.actions_dir(sd)
    target = record["params"]["target"]
    me = record["id"]

    def end(status, error=None, **result):
        actions.finish(sd, me, status, result=result or None, error=error)

    found = None
    for state in (actions.PENDING, actions.CLAIMED, actions.DONE):
        path = root / state / f"{target}.json"
        if path.exists():
            try:
                found = (state, actions._load(path))
            except (OSError, ValueError):
                found = None
            break
    if found is None:
        end("failed", f"no action {target}")
        return
    state, tgt = found
    if state == actions.PENDING:
        try:
            actions.finish(sd, target, "cancelled", result={"cancelled_by": me})
        except FileNotFoundError:
            end("failed", f"target {target} was taken before it could be cancelled")
            return
        end("done", target=target, target_status="cancelled")
    elif state == actions.CLAIMED and record["params"].get("only_pending"):
        # *Stop epic*'s cancel (ADR 211) drops a start that is still waiting and
        # leaves one that is running alone: taken in between, it keeps its run.
        end("failed", f"target {target} was taken before the stop reached it; "
                      "it is left running")
    elif state == actions.CLAIMED:
        with worker._lock:
            entry = next(
                (r for r in worker.running.values() if r.action_id == target), None
            )
        if tgt.get("kind") in ("start", "turn", "request_changes") and entry is not None:
            entry.cancelled.set()
            if entry.kill is not None:
                entry.kill()
            end("done", target=target, target_status="running")
        elif tgt.get("kind") == "command_verdict":
            end("failed", f"target {target}: {VERDICT_NOT_CANCELLABLE}")
        else:
            end("failed", f"target {target} is a running {tgt.get('kind')}; "
                          "it cannot be cancelled")
    elif tgt.get("status") == "cancelled" and (tgt.get("result") or {}).get(
        "cancelled_by"
    ) == me:
        end("done", target=target, target_status="cancelled")  # a rerun
    else:
        end("failed", f"target {target} already finished as {tgt.get('status')}")


def _harness_line(event: str, **fields) -> dict:
    return {"type": "ia_harness", "event": event, "at": _utc_now().isoformat(), **fields}


def _cut(text: str, room: int, *, keep_end: bool = False) -> tuple[str, bool]:
    """`text` in at most `room` characters, the cut marked; `keep_end` keeps
    the latest of it (a trail of handoffs) rather than the start."""
    if len(text) <= room:
        return text, False
    marker = TURN_SEED_CUT.format(max=TURN_SEED_MAX_CHARS)
    keep = room - len(marker) - 1
    return (f"{marker}\n{text[-keep:]}" if keep_end else f"{text[:keep]}\n{marker}"), True


def _start_note(cfg: Config, task_id: str) -> str | None:
    """The note of the latest `start` of this task that had one."""
    entries, _ = actions.read_all(cfg.state_dir)
    notes = sorted(
        (e.record.get("id") or "", e.record["note"]) for e in entries
        if e.record.get("kind") == "start" and e.record.get("task_id") == task_id
        and isinstance(e.record.get("note"), str) and e.record["note"].strip()
    )
    return notes[-1][1] if notes else None


def _session_seed(cfg: Config, thread: dict) -> dict | None:
    """What a fresh session is told before the person's message, or None.

    A thread on a task gets the task's description, the note of its latest
    start and the handoff trail the phases left in the task file, in that
    order and within `TURN_SEED_MAX_CHARS`: the description and the note are
    cut from the end, the trail from the start, so the latest phase stays; a
    part with no room left after those before it is dropped. A thread on no
    task gets nothing; its board tools describe themselves.

    A part that cannot be read is left out with a warning, never failing the
    turn: the seed is a convenience, and the person's message still runs.
    Returns the text with what went into it, for the `turn_start` line.
    """
    task_id = thread.get("task")
    if not task_id:
        return None
    readers = (
        ("description", "Description",
         lambda: tasks.read_description(cfg.hive_tasks_dir, task_id)),
        ("note", "Start note", lambda: _start_note(cfg, task_id)),
        ("handoffs", "Handoffs so far", lambda: context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)).body),
    )
    found, unread = [], []
    for name, heading, read in readers:
        try:
            value = read()
        except Exception as exc:  # anything: an unreadable part never fails the turn
            logger.warning("worker: the seed of thread %s left out %s's %s: %s: %s",
                           thread["id"], task_id, name, type(exc).__name__, exc)
            unread.append(name)
            continue
        if isinstance(value, str) and value.strip():
            found.append((name, heading, value.strip()))
    if not found:
        return None
    head = (f"[ia-harness: the start of a new session of thread {thread['id']}, on task "
            f"{task_id}. This is the task as the board has it, given once.]")
    tail = "[ia-harness: end of the seed; the person's message follows.]"
    room = TURN_SEED_MAX_CHARS - len(head) - len(tail) - 2
    sections, parts, truncated = [], [], False
    for name, heading, value in found:
        title = f"## {heading}\n"
        if room - len(title) - 2 <= len(TURN_SEED_CUT) + 40:
            truncated = True  # no room left for this part, not even its cut
            continue
        room -= len(title) + 2
        body, cut = _cut(value, room, keep_end=name == "handoffs")
        room -= len(body)
        truncated = truncated or cut
        sections.append(title + body)
        parts.append(name)
    text = "\n\n".join([head, *sections, tail])
    return {"text": text, "chars": len(text), "truncated": truncated,
            "parts": parts, "unread": unread}


def _turn(worker: Worker, record: dict, cancelled: threading.Event) -> None:
    """One conversation turn (ADR 173): a streamed `claude -p` on the thread's
    own account, no code written, every line of it appended to the thread's log
    as it is read."""
    cfg, sd, aid = worker.cfg, worker.state_dir, record["id"]
    thread_id, text = record["params"]["thread"], record["params"]["text"]

    def refuse(reason: str) -> None:
        actions.finish(sd, aid, "failed", error=_one_line(reason))

    try:
        thread = threads.read_thread(cfg.hive_tasks_dir, thread_id)
    except threads.ThreadRefused as exc:
        refuse(str(exc))
        return
    if thread.get("status") == "archived":
        refuse(f"thread {thread_id} is archived")
        return
    account = next((a for a in cfg.accounts if a.name == thread.get("account")), None)
    if account is None:
        refuse(f"thread {thread_id} names account {thread.get('account')!r}, which is not configured")
        return
    state = state_machine.get_state(sd, account.name)
    if state in state_machine.PARKED_STATES:
        refuse(f"account {account.name} is {state.value}; move the thread to another account")
        return
    segment = thread["segments"][-1]
    resumed = segment.get("session_id")
    project = thread.get("project")
    workdir = os.path.join(cfg.projects_root, project) if project else cfg.projects_root
    # The board server, bound to this turn, when the config has one (ADR 187, 8).
    mcp_config = None
    allowed_tools = TURN_ALLOWED_TOOLS
    if cfg.board_mcp_url:
        mcp_config = board_mcp.mcp_config(cfg.board_mcp_url, board_mcp.THREAD, turn=aid)
        allowed_tools = (*TURN_ALLOWED_TOOLS, *board_mcp.allowed_tools(board_mcp.THREAD))
    # A fresh session is seeded in its first message, so the seed is in the
    # history every `--resume` replays; the log keeps `text` as the person wrote it.
    seed = None if resumed else _session_seed(cfg, thread)
    prompt = f"{seed['text']}\n\n{text}" if seed else text
    command = docker_exec.turn_command(
        prompt, resume_session_id=resumed, model=thread["model"], effort=thread["effort"],
        permission_mode=TURN_PERMISSION_MODE, allowed_tools=allowed_tools,
        disallowed_tools=TURN_DISALLOWED_TOOLS, timeout_seconds=TURN_TIMEOUT_SECONDS,
        mcp_config=mcp_config,
    )

    def log(line) -> None:
        threads.append_conversation(cfg.hive_tasks_dir, thread_id, line)

    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    log(_harness_line(
        "turn_start", action=aid, actor=record.get("actor"), text=text,
        account=account.name, model=thread["model"], effort=thread["effort"],
        session_id=resumed, seed=seed,
    ))
    with worker._lock:
        entry = next((r for r in worker.running.values() if r.action_id == aid), None)

    status, error, result, session = "failed", None, None, None
    try:
        proc = docker_exec.open_docker_exec(account.container, workdir, command)
        try:
            first = True
            for raw in proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                if first:
                    first = False
                    if raw.isdigit():
                        pid = int(raw)
                        if entry is not None:
                            entry.kill = lambda: docker_exec.kill_in_container(
                                account.container, pid)
                            if cancelled.is_set() or worker.stopping.is_set():
                                entry.kill()
                        continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    event = None
                if not isinstance(event, dict):
                    log(_harness_line("unparsed", line=raw))
                    continue
                log(raw)
                if event.get("type") == "system" and event.get("subtype") == "init":
                    session = event.get("session_id") or session
                elif event.get("type") == "result":
                    result = event
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if cancelled.is_set():
            status = "cancelled"
        elif worker.stopping.is_set() and result is None:
            status, error = "interrupted", TURN_STOPPED_ERROR
        elif result is not None and not result.get("is_error"):
            status = "done"
        else:
            if result is not None:
                error = f"{result.get('subtype')}: {result.get('result')}"
            else:
                proc.stderr.seek(0)
                error = f"claude exited {proc.returncode} with no result: {proc.stderr.read()[-500:]}"
    except Exception as exc:
        logger.exception("worker: turn %s raised", aid)
        error = f"{type(exc).__name__}: {exc}"
    error = _one_line(error) if error else None
    if session and session != resumed:
        try:
            threads.update(cfg.hive_tasks_dir, cfg, thread_id, session_id=session)
        except threads.ThreadRefused:
            logger.exception("worker: could not record the session of %s", thread_id)
    log(_harness_line("turn_end", action=aid, status=status, error=error,
                      session_id=session or resumed))
    summary = None
    if status == "done":
        summary = {k: result.get(k) for k in
                   ("session_id", "total_cost_usd", "usage", "num_turns")}
    actions.finish(sd, aid, status, result=summary, error=error)
    _notify_turn(cfg, thread_id, status, thread.get("title"))


def _notify_turn(cfg: Config, thread_id: str, status: str, title: object) -> None:
    """A `turn_finished` notification for a turn that ended `status` (ADR 228).

    Every end but `cancelled`: a cancel is the person's own act, so there is
    nobody to tell. Written after the turn's own record, and `append` never
    raises, so the turn ends the same with or without it. A turn refused
    before it started has no `turn_start`, and no notification either.
    """
    if status == "cancelled":
        return
    notifications.append(
        cfg.hive_tasks_dir, "turn_finished", thread=thread_id,
        title=notifications.task_title(thread_id, f"turn {status}", title),
        url=notifications.thread_url(thread_id),
    )


# ---- a new project's setup (ADR 212) ----------------------------------------


def _setup_record(worker: Worker, record: dict):
    """The project's record, or None once the action is finished failed."""
    cfg, sd = worker.cfg, worker.state_dir
    slug = record["project"]
    try:
        project = projects.read(context_transfer.hive_root(cfg.hive_tasks_dir), slug)
    except projects.RecordError as exc:
        actions.finish(sd, record["id"], "failed",
                       error=_one_line(f"the record of {slug} does not read: {exc}"))
        return None
    if project is None:
        actions.finish(sd, record["id"], "failed",
                       error=f"no project record {slug}; write the record first")
        return None
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    return project


def _end_setup(worker: Worker, record: dict, outcome) -> None:
    status = "done" if outcome.ok else "failed"
    actions.finish(
        worker.state_dir, record["id"], status,
        result={"detail": outcome.detail},
        error=None if outcome.ok else _one_line(outcome.detail),
    )


def _clone(worker: Worker, record: dict, _cancelled=None) -> None:
    """Clone the record's remote into `<projects_root>/<slug>` (ADR 212)."""
    project = _setup_record(worker, record)
    if project is None:
        return
    if not project.remote:
        actions.finish(worker.state_dir, record["id"], "failed",
                       error=f"{project.slug} has no remote to clone; init makes an empty repository")
        return
    if projects.has_credential(project.remote):
        # The record refuses one already (ADR 205); this is that refusal again,
        # at the one place a remote is handed to git, and it never names it.
        actions.finish(worker.state_dir, record["id"], "failed",
                       error=f"the remote of {project.slug} carries a credential; it is not cloned")
        return
    _end_setup(worker, record, core.make_project_checkout(
        worker.cfg, project.slug, project.remote, project.base_branch,
    ))


def _init(worker: Worker, record: dict, _cancelled=None) -> None:
    """An empty repository at `<projects_root>/<slug>`, for a record with no remote."""
    project = _setup_record(worker, record)
    if project is None:
        return
    if project.remote:
        actions.finish(worker.state_dir, record["id"], "failed",
                       error=f"{project.slug} has a remote; clone it instead")
        return
    _end_setup(worker, record, core.make_project_checkout(
        worker.cfg, project.slug, None, project.base_branch,
    ))


def _keygen(worker: Worker, record: dict, _cancelled=None) -> None:
    """The project's deploy key (`project-keygen`'s function) and its record's
    fingerprint. The result holds the public half only: the private half is
    named by the path it is written to and never read here."""
    cfg, sd = worker.cfg, worker.state_dir
    project = _setup_record(worker, record)
    if project is None:
        return
    replace = bool(record["params"].get("replace"))
    try:
        key = project_keys.generate(cfg.project_keys_dir, project.slug, replace=replace)
    except project_keys.KeyExists:
        actions.finish(sd, record["id"], "failed",
                       error=f"{project.slug} already has a deploy key; replacing it was not asked")
        return
    except (OSError, subprocess.CalledProcessError) as exc:
        # ssh-keygen's stderr says nothing of the key it did not make (cli's
        # _run_project_keygen shows it for the same reason).
        why = getattr(exc, "stderr", None) or exc
        actions.finish(sd, record["id"], "failed",
                       error=_one_line(f"could not make a key for {project.slug}: {why}"))
        return
    projects.record_key(
        context_transfer.hive_root(cfg.hive_tasks_dir), project.slug, cfg.project_keys_dir,
    )
    actions.finish(sd, record["id"], "done", result={
        "public": key.public, "fingerprint": key.fingerprint, "replaced": replace,
    })


def _check(worker: Worker, record: dict, _cancelled=None) -> None:
    """Whether the record's remote answers the project's deploy key.

    Done either way, with `reachable` saying which: a remote that does not
    answer yet is the expected answer before the key is pasted on GitHub, not
    a failure of the check. `writable` is whether the remote took a dry-run
    push from the key (ADR 214), `None` when that was not tried because the
    read did not answer.
    """
    project = _setup_record(worker, record)
    if project is None:
        return
    if not project.remote:
        actions.finish(worker.state_dir, record["id"], "failed",
                       error=f"{project.slug} has no remote to check")
        return
    outcome = core.check_project_remote(worker.cfg, project.slug, project.remote)
    actions.finish(worker.state_dir, record["id"], "done", result={
        "reachable": outcome.ok, "detail": outcome.detail, "writable": outcome.writable,
    })


def _approve(worker: Worker, record: dict, _cancelled=None) -> None:
    """Merge and push the reviewed tip, or say why not (ADR 258, ADR 264).

    `core.approve_reviewed` does the work under the base lock; this writes its
    one comment on the task and ends the action with the outcome. A comment
    that cannot be written is logged: the action's result still says it.
    """
    cfg, sd = worker.cfg, worker.state_dir
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    from dispatcher import backup  # backup imports this module

    out = core.approve_reviewed(
        cfg, record["task_id"], record["project"], record["params"]["commit"],
        before_merge=lambda: backup.before_merge(
            context_transfer.hive_root(cfg.hive_tasks_dir), cfg.state_dir, os.environ,
            now=_utc_now(),
        ),
    )
    try:
        tasks.add_comment(cfg.hive_tasks_dir, record["task_id"], "dispatcher", out.comment)
    except tasks.BoardWriteError as exc:
        logger.warning("task %s: the approve's comment was not written: %s", record["task_id"], exc)
    push = out.push
    actions.finish(
        sd, record["id"], "done" if out.landed else "failed",
        result={
            "outcome": out.outcome, "commit": out.commit, "merge_commit": out.merge_commit,
            "push": (
                {"status": push.status, "target": push.target, "detail": push.detail}
                if push is not None else None
            ),
        },
        error=None if out.landed else _one_line(out.comment.splitlines()[0]),
    )


def _request_changes(worker: Worker, record: dict, cancelled: threading.Event) -> None:
    """Run the task's cycle again from the arquitecto on its branch (ADR 272).

    The person's request for changes is on the task's comments, written by
    the board service before this was queued; it and their review comments
    before it go to the arquitecto as its note. The task moves from
    `awaiting_review` (or `conflict`) to `pending` first, so the cycle takes it as any start
    does and an approve on the old tip reads as stale. A `conflict` task is
    taken too (ADR 278): the person's answer to a design conflict the
    integrador would not settle. Not authorized: the
    cycle ends in `awaiting_review` again, for the person to review the new
    tip. Ended like a start: cancelled or stopped while held, done otherwise.
    """
    cfg, sd = worker.cfg, worker.state_dir
    task_id, commit = record["task_id"], record["params"]["commit"]
    task = _read_task(cfg, task_id)
    if (
        task is None
        or task.status not in (tasks.AWAITING_REVIEW_STATUS, tasks.CONFLICT_STATUS)
        or task.merged
    ):
        why = (
            "it has no task file" if task is None
            else "it is merged" if task.merged
            else f"it is {task.status}"
        )
        actions.finish(sd, record["id"], "failed",
                       error=f"task {task_id} is not under review: {why}")
        return
    try:
        comments = tasks.review_comments(cfg.hive_tasks_dir, task_id, commit)
    except OSError as exc:
        actions.finish(sd, record["id"], "failed",
                       error=_one_line(f"the task's comments could not be read: {exc}"))
        return
    if not comments:
        actions.finish(sd, record["id"], "failed",
                       error=f"task {task_id} has no request for changes on {commit}")
        return
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    if not tasks.reopen_for_changes(cfg.hive_tasks_dir, task_id):
        actions.finish(sd, record["id"], "failed",
                       error=f"task {task_id} left review before it was reopened")
        return
    outcome = core.run_task_cycle(
        cfg, task_id, record["project"], authorized=False,
        may_start_phase=lambda: not (cancelled.is_set() or worker.stopping.is_set()),
        project_lock=_held_project_lock(worker, record),
        changes_requested=core.changes_requested_note(commit, comments),
    )
    task = _read_task(cfg, task_id)
    result = {
        "commit": commit, "outcome": outcome.value,
        "blocked_reason": (
            task.blocked_reason if outcome is core.CycleOutcome.BLOCKED and task else None
        ),
    }
    held = outcome is core.CycleOutcome.HELD
    if held and cancelled.is_set():
        result["last_phase"] = task.phase if task else None
        actions.finish(sd, record["id"], "cancelled", result=result)
    elif held and worker.stopping.is_set():
        actions.finish(sd, record["id"], "interrupted", result=result, error=STOPPED_ERROR)
    else:
        actions.finish(sd, record["id"], "done", result=result)


def _resolve_conflict(worker: Worker, record: dict, _cancelled=None) -> None:
    """Resolve a `conflict` task's merge conflict, or say why not (ADR 285).

    The task is read again first: one no longer in `conflict` is `stale` and
    nothing runs. Every outcome writes one dispatcher comment naming who asked;
    a comment that cannot be written is logged, as an approve's is.
    """
    cfg, sd = worker.cfg, worker.state_dir
    task_id, actor = record["task_id"], record["actor"]
    task = _read_task(cfg, task_id)
    if task is None or task.merged or task.status != tasks.CONFLICT_STATUS:
        why = (
            "it has no task file" if task is None
            else "it is merged" if task.merged
            else f"it is {task.status}"
        )
        outcome, detail = "stale", f"not run: task {task_id} is not in conflict: {why}"
    else:
        record["started_at"] = _utc_now().isoformat()
        actions.rewrite(sd, record)
        outcome, detail = core.resolve_conflict_locked(cfg, task_id, record["project"])
    comment = f"resolve conflict, asked by {actor}: {detail}"
    try:
        tasks.add_comment(cfg.hive_tasks_dir, task_id, "dispatcher", comment)
    except tasks.BoardWriteError as exc:
        logger.warning("task %s: the resolve conflict's comment was not written: %s", task_id, exc)
    resolved = outcome == "resolved"
    actions.finish(
        sd, record["id"], "done" if resolved else "failed",
        result={"outcome": outcome, "detail": detail},
        error=None if resolved else _one_line(comment.splitlines()[0]),
    )


HANDLERS = {
    "start": _start, "cancel": _cancel, "command_verdict": _command_verdict, "turn": _turn,
    "clone": _clone, "init": _init, "keygen": _keygen, "check": _check,
    "approve": _approve, "request_changes": _request_changes,
    "resolve_conflict": _resolve_conflict,
}


# ---- the verb ---------------------------------------------------------------


def run_worker(cfg: Config, *, interval: float = POLL_SECONDS) -> int:
    try:
        fd = acquire(cfg.state_dir)
    except WorkerLocked as exc:
        print(f"another worker holds {exc.path}; exiting", file=sys.stderr)
        return 2
    worker = Worker(cfg)
    old = {
        sig: signal.signal(sig, lambda *_: worker.stop())
        for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        worker.run(interval)
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        os.close(fd)
    return 0
