"""The action worker: drains `<state_dir>/actions/pending/` (docs/decisions.md
ADR 142 split item 2, ADR 148).

One worker at a time (a `flock` on `actions/worker.lock`), one pass every
`POLL_SECONDS`. A `cancel` runs at once on the loop thread; a `start` or a
`command_verdict` is claimed when its project is free and an account is open
and runs in its own thread. The handlers are in `HANDLERS`, not in
`actions.KINDS` (ADR 148, 1). `turn` has none yet: it stays pending, untouched.
Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dispatcher import actions, command_run, context_transfer, tasks
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.config import Config

logger = logging.getLogger(__name__)

LOCK = "worker.lock"
HEARTBEAT = "worker.json"
POLL_SECONDS = 2.0
ACCOUNT_POLL_SECONDS = float(queue_mod.WAIT_POLL_SECONDS)
STOPPED_ERROR = "the worker stopped; the cycle was held at a phase boundary"
VERDICT_NOT_CANCELLABLE = (
    "a running command_verdict cannot be cancelled: its verdict is recorded "
    "before the command runs (ADR 119)"
)


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


class Worker:
    def __init__(self, cfg: Config, *, account_poll: float = ACCOUNT_POLL_SECONDS):
        self.cfg = cfg
        self.account_poll = account_poll
        self.started_at = _utc_now().isoformat()
        self.stopping = threading.Event()
        self.running: dict[str, Running] = {}
        self._lock = threading.Lock()
        self._closed_until: datetime | None = None

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
        return done

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
            self.tick()
            self.stopping.wait(interval)
        self.join()

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
            self._wait(record, "project")
            return
        if actions.KINDS[kind].spends_quota and not self._account_open():
            self._wait(record, "account")
            return
        claimed = actions.claim(self.state_dir, aid)
        if claimed is None:
            return
        entry = Running(aid, None, threading.Event())
        thread = threading.Thread(
            target=self._run_handler, args=(key, claimed, entry),
            name=f"worker-{aid}", daemon=False,
        )
        entry.thread = thread
        with self._lock:
            self.running[key or aid] = entry
        thread.start()

    def _account_open(self) -> bool:
        now = _utc_now()
        if self._closed_until is not None and now < self._closed_until:
            return False
        if queue_mod.account_open(self.cfg):
            self._closed_until = None
            return True
        self._closed_until = now + timedelta(seconds=self.account_poll)
        return False

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
    record["started_at"] = _utc_now().isoformat()
    actions.rewrite(sd, record)
    outcome = core.run_task_cycle(
        cfg, record["task_id"], record["project"], authorized=True,
        may_start_phase=lambda: not (cancelled.is_set() or worker.stopping.is_set()),
    )
    task = _read_task(cfg, record["task_id"])
    result["outcome"] = outcome.value
    result["blocked_reason"] = (
        task.blocked_reason if outcome is core.CycleOutcome.BLOCKED and task else None
    )
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
    elif state == actions.CLAIMED:
        with worker._lock:
            entry = next(
                (r for r in worker.running.values() if r.action_id == target), None
            )
        if tgt.get("kind") == "start" and entry is not None:
            entry.cancelled.set()
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


HANDLERS = {"start": _start, "cancel": _cancel, "command_verdict": _command_verdict}


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
