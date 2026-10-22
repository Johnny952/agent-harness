"""dispatcher/worker.py: the action worker (docs/decisions.md ADR 142, 148).

`run_task_cycle` and `run_command_request` are faked; the loop is driven by `Worker.tick()` and
`Worker.join()`, so nothing sleeps beyond Event waits that fail rather than hang.
"""

from __future__ import annotations

import io
import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from dispatcher import (
    actions, command_run, notifications, state_machine, tasks, threads, worker as worker_mod,
)
from dispatcher import dispatcher as core
from dispatcher.project_lock import ProjectBusy, ProjectLock
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_queue import _task

WAIT = 5


class Cycle:
    """A fake cycle. `hold(task)` makes it block until `release(task)`; it asks
    `may_start_phase` after the block, as the real one does before a phase."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.locks: list = []  # the project lock each call was handed
        self.entered: dict[str, threading.Event] = {}
        self.gates: dict[str, threading.Event] = {}
        self.raises: dict[str, Exception] = {}
        self.limits: list[tuple] = []  # (token_limit, usage_offset) per call

    def hold(self, task_id):
        self.gates[task_id] = threading.Event()

    def release(self, task_id):
        self.gates[task_id].set()

    def wait_entered(self, task_id):
        assert self.entered.setdefault(task_id, threading.Event()).wait(WAIT)

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, scheduler=None, project_lock=None,
                 token_limit=None, usage_offset=0):
        assert authorized is True
        self.locks.append(project_lock)
        self.limits.append((token_limit, usage_offset))
        self.calls.append((task_id, slug))
        self.entered.setdefault(task_id, threading.Event()).set()
        if task_id in self.raises:
            raise self.raises[task_id]
        gate = self.gates.get(task_id)
        if gate is not None:
            assert gate.wait(WAIT)
        if not may_start_phase():
            return core.CycleOutcome.HELD
        return core.CycleOutcome.FINISHED


class Verdict:
    """A fake `run_command_request`, held and raising the way `Cycle` is."""

    def __init__(self):
        self.calls: list[dict] = []
        self.entered = threading.Event()
        self.gate: threading.Event | None = None
        self.raises: Exception | None = None
        self.outcome = core.CycleOutcome.FINISHED

    def __call__(self, cfg, task_id, slug, n, verdict, comment=None, by="operator",
                 prefix=None, project_lock=None):
        self.calls.append({"task_id": task_id, "slug": slug, "n": n, "verdict": verdict,
                           "comment": comment, "by": by, "prefix": prefix})
        self.entered.set()
        if self.raises is not None:
            raise self.raises
        if self.gate is not None:
            assert self.gate.wait(WAIT)
        return self.outcome


@pytest.fixture
def cfg(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def cycle(monkeypatch):
    fake = Cycle()
    monkeypatch.setattr(core, "run_task_cycle", fake)
    return fake


@pytest.fixture
def verdicts(monkeypatch):
    fake = Verdict()
    monkeypatch.setattr(command_run, "run_command_request", fake)
    yield fake
    if fake.gate is not None:
        fake.gate.set()


@pytest.fixture
def accounts(monkeypatch):
    state = {"open": True, "probes": 0}

    def pick(cfg, exclude=None, role=None):
        return "cuenta1" if state["open"] else None

    def recheck(cfg):
        state["probes"] += 1
        return []

    monkeypatch.setattr(core, "pick_idle_account", pick)
    monkeypatch.setattr(core, "_recheck_cooling_accounts", recheck)
    return state


@pytest.fixture
def clock(monkeypatch):
    now = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(worker_mod, "_utc_now", lambda: now["t"])
    return now


@pytest.fixture
def worker(cfg, cycle, accounts):
    w = worker_mod.Worker(cfg, account_poll=0)
    yield w
    w.stop()
    for gate in cycle.gates.values():
        gate.set()
    w.join()


def start(cfg, task_id, project="proj", note=None, make_task=True):
    if make_task:
        _task(cfg, task_id)
    return actions.create(cfg.state_dir, "start", project=project, task_id=task_id,
                          note=note)


def verdict(cfg, task_id="T-1", project="proj", **params):
    params = {"n": 1, "verdict": "run", **params}
    return actions.create(cfg.state_dir, "command_verdict", project=project,
                          task_id=task_id, params=params)


def cancel(cfg, target):
    return actions.create(cfg.state_dir, "cancel", params={"target": target})


def where(cfg, aid):
    root = actions.actions_dir(cfg.state_dir)
    for state in ("pending", "claimed", "done"):
        path = root / state / f"{aid}.json"
        if path.exists():
            return state, json.loads(path.read_text())
    return None, None


def comments(cfg, task_id):
    path = tasks.comments_path(cfg.hive_tasks_dir, task_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ---- the lock ---------------------------------------------------------------


def test_a_second_worker_exits_2(cfg, capsys):
    fd = worker_mod.acquire(cfg.state_dir)
    try:
        assert worker_mod.run_worker(cfg) == 2
        assert "worker.lock" in capsys.readouterr().err
    finally:
        os.close(fd)
    os.close(worker_mod.acquire(cfg.state_dir))


# ---- serial keys ------------------------------------------------------------


def test_one_claimed_start_per_project(cfg, worker, cycle):
    cycle.hold("T-1")
    first = start(cfg, "T-1")
    second = start(cfg, "T-2")
    worker.tick()
    cycle.wait_entered("T-1")
    assert where(cfg, first["id"])[0] == "claimed"
    state, rec = where(cfg, second["id"])
    assert state == "pending" and rec["waiting"] == "project"
    cycle.release("T-1")
    worker.join()
    worker.tick()
    worker.join()
    state, rec = where(cfg, second["id"])
    assert state == "done" and rec["status"] == "done" and rec["waiting"] is None
    assert [c[0] for c in cycle.calls] == ["T-1", "T-2"]


def test_a_start_waits_while_another_process_holds_the_project(cfg, worker, cycle):
    other = ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")
    first = start(cfg, "T-1")
    worker.tick()
    worker.join()
    state, rec = where(cfg, first["id"])
    assert state == "pending" and rec["waiting"] == "project"
    assert cycle.calls == []
    other.release()
    worker.tick()
    worker.join()
    assert where(cfg, first["id"])[0] == "done"
    assert [c[0] for c in cycle.calls] == ["T-1"]


def test_the_worker_holds_the_lock_for_the_cycle_and_gives_it_back(cfg, worker, cycle):
    cycle.hold("T-1")
    start(cfg, "T-1")
    worker.tick()
    cycle.wait_entered("T-1")
    with pytest.raises(ProjectBusy) as busy:
        ProjectLock.take(cfg.state_dir, "proj", "T-2", "run-task")
    assert busy.value.holder["via"] == "worker"
    assert isinstance(cycle.locks[0], ProjectLock)
    cycle.release("T-1")
    worker.join()
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "run-task").release()


def test_two_projects_run_at_once(cfg, worker, cycle):
    cycle.hold("T-1")
    cycle.hold("T-2")
    a = start(cfg, "T-1", project="alpha")
    b = start(cfg, "T-2", project="beta")
    worker.tick()
    cycle.wait_entered("T-1")
    cycle.wait_entered("T-2")
    assert where(cfg, a["id"])[0] == "claimed" and where(cfg, b["id"])[0] == "claimed"
    cycle.release("T-1")
    cycle.release("T-2")
    worker.join()
    assert where(cfg, a["id"])[1]["status"] == "done"
    assert where(cfg, b["id"])[1]["result"]["outcome"] == "finished"


def test_an_epics_starts_run_one_at_a_time_in_the_order_queued(cfg, worker, cycle):
    # *Start epic* (ADR 207): queued at once, not in id order; each later one
    # waits as `project` while the one before holds the project.
    order = ["T-3", "T-1", "T-2"]
    for task_id in order:
        _task(cfg, task_id)
        cycle.hold(task_id)
    queued = actions.create_starts(
        cfg.state_dir, "proj", order, params={"limit": None, "epic": "T-9"}, note="go"
    )
    for n, task_id in enumerate(order):
        worker.tick()
        cycle.wait_entered(task_id)
        assert where(cfg, queued[n]["id"])[0] == "claimed"
        for later in queued[n + 1:]:
            state, rec = where(cfg, later["id"])
            assert state == "pending" and rec["waiting"] == "project"
        cycle.release(task_id)
        worker.join()
    assert [c[0] for c in cycle.calls] == order
    assert all(where(cfg, r["id"])[1]["status"] == "done" for r in queued)


def test_a_handler_that_raises_fails_the_start_and_frees_the_project(cfg, worker, cycle):
    cycle.raises["T-1"] = RuntimeError("boom\nsecond line")
    first = start(cfg, "T-1")
    second = start(cfg, "T-2")
    worker.tick()
    worker.join()
    rec = where(cfg, first["id"])[1]
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError: boom second line"
    assert worker.running == {}
    worker.tick()
    worker.join()
    assert where(cfg, second["id"])[1]["status"] == "done"


# ---- accounts ---------------------------------------------------------------


def test_a_start_waits_for_an_account(cfg, cycle, accounts, clock):
    w = worker_mod.Worker(cfg, account_poll=60)
    rec = start(cfg, "T-1")
    accounts["open"] = False
    w.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "account"
    assert cycle.calls == []
    probes = accounts["probes"]
    w.tick()
    assert accounts["probes"] == probes  # paced: no second look within account_poll
    accounts["open"] = True
    clock["t"] += timedelta(seconds=61)
    w.tick()
    w.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["waiting"] is None
    assert cycle.calls == [("T-1", "proj")]


# ---- the note ---------------------------------------------------------------


def test_the_note_is_commented_once_before_started_at(cfg, worker, cycle):
    rec = start(cfg, "T-1", note="go carefully")
    worker.tick()
    worker.join()
    lines = comments(cfg, "T-1")
    assert len(lines) == 1
    assert lines[0]["author"] == "operator" and lines[0]["text"] == "go carefully"
    done = where(cfg, rec["id"])[1]
    assert done["result"]["noted_at"] <= done["started_at"]
    assert done["result"]["outcome"] == "finished"


def test_a_start_with_a_limit_counts_from_the_usage_log_it_found(cfg, worker, cycle):
    """ADR 230: the offset is taken once, before started_at; the result says the spend."""
    from dispatcher import context_transfer
    _task(cfg, "T-1")
    for _ in range(2):
        context_transfer.append_usage(cfg.hive_tasks_dir, "T-1", {"input_tokens": 50})
    rec = actions.create(cfg.state_dir, "start", project="proj", task_id="T-1",
                         params={"limit": 500})
    worker.tick()
    worker.join()
    assert cycle.limits == [(500, 2)]
    result = where(cfg, rec["id"])[1]["result"]
    assert result["usage_offset"] == 2
    assert result["spent"] == {"limit": 500, "tokens": 0, "unmeasured": 0}


def test_a_start_without_a_limit_runs_unlimited(cfg, worker, cycle):
    rec = start(cfg, "T-1")
    worker.tick()
    worker.join()
    assert cycle.limits == [(None, 0)]
    assert "spent" not in where(cfg, rec["id"])[1]["result"]


def test_a_rerun_after_a_crash_does_not_comment_again(cfg, worker, cycle):
    rec = start(cfg, "T-1", note="once")
    claimed = actions.claim(cfg.state_dir, rec["id"])
    tasks.add_comment(cfg.hive_tasks_dir, "T-1", "operator", "once")
    claimed["result"] = {"noted_at": "2026-01-01T00:00:00+00:00"}
    actions.rewrite(cfg.state_dir, claimed)  # died before started_at
    assert worker.recover() == [(rec["id"], "back to pending")]
    worker.tick()
    worker.join()
    assert len(comments(cfg, "T-1")) == 1
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert cycle.calls == [("T-1", "proj")]


def test_a_refused_comment_fails_the_start_before_it_runs(cfg, worker, cycle):
    rec = start(cfg, "T-9", note="hello", make_task=False)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed" and got["started_at"] is None
    assert cycle.calls == []


# ---- cancel -----------------------------------------------------------------


def test_a_cancel_on_a_pending_target(cfg, worker, cycle, accounts):
    accounts["open"] = False
    target = start(cfg, "T-1")
    c = cancel(cfg, target["id"])
    worker.tick()
    state, got = where(cfg, target["id"])
    assert state == "done" and got["status"] == "cancelled"
    assert got["result"] == {"cancelled_by": c["id"]}
    state, got = where(cfg, c["id"])
    assert state == "done" and got["status"] == "done"
    assert cycle.calls == []


def test_a_cancel_on_a_running_start(cfg, worker, cycle):
    cycle.hold("T-1")
    target = start(cfg, "T-1")
    worker.tick()
    cycle.wait_entered("T-1")
    c = cancel(cfg, target["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "done" and got["result"]["target_status"] == "running"
    assert where(cfg, target["id"])[0] == "claimed"
    cycle.release("T-1")
    worker.join()
    got = where(cfg, target["id"])[1]
    assert got["status"] == "cancelled" and got["result"]["outcome"] == "held"
    assert "last_phase" in got["result"]


def test_stop_epic_drops_the_waiting_starts_and_leaves_the_running_one(cfg, worker, cycle):
    # *Stop epic* (ADR 211): the first start is running, the two after it wait
    # as `project`; the stop cancels those two and the run goes on.
    order = ["T-1", "T-2", "T-3"]
    for task_id in order:
        _task(cfg, task_id)
    cycle.hold("T-1")
    queued = actions.create_starts(
        cfg.state_dir, "proj", order, params={"limit": None, "epic": "T-9"}
    )
    worker.tick()
    cycle.wait_entered("T-1")
    made = actions.create_epic_cancels(cfg.state_dir, "T-9")
    assert [r["id"] for r in made["dropped"]] == [r["id"] for r in queued[1:]]
    assert [r["id"] for r in made["left"]] == [queued[0]["id"]]
    worker.tick()
    for later in queued[1:]:
        state, got = where(cfg, later["id"])
        assert state == "done" and got["status"] == "cancelled"
    for c in made["cancels"]:
        assert where(cfg, c["id"])[1]["status"] == "done"
    assert where(cfg, queued[0]["id"])[0] == "claimed"
    cycle.release("T-1")
    worker.join()
    assert where(cfg, queued[0]["id"])[1]["status"] == "done"
    assert [c[0] for c in cycle.calls] == ["T-1"]


def test_a_stop_cancel_whose_start_was_taken_leaves_it_running(cfg, worker, cycle):
    # Taken between the stop's read and its cancel's run: the start keeps its run.
    cycle.hold("T-1")
    target = start(cfg, "T-1")
    worker.tick()
    cycle.wait_entered("T-1")
    c = actions.create(
        cfg.state_dir, "cancel", params={"target": target["id"], "only_pending": True}
    )
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and "left running" in got["error"]
    cycle.release("T-1")
    worker.join()
    assert where(cfg, target["id"])[1]["status"] == "done"


def test_a_cancel_on_a_finished_target_fails(cfg, worker, cycle):
    done = start(cfg, "T-1")
    worker.tick()
    worker.join()
    c = cancel(cfg, done["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and "already finished as done" in got["error"]


def test_a_cancel_of_a_target_nobody_has_fails(cfg, worker, cycle):
    c = cancel(cfg, "a-20260101T000000000000Z-ffff")
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and got["error"].startswith("no action")


def test_a_cancel_rerun_after_it_cancelled_its_target_is_done(cfg, worker, cycle, accounts):
    accounts["open"] = False
    target = start(cfg, "T-1")
    c = cancel(cfg, target["id"])
    actions.finish(cfg.state_dir, target["id"], "cancelled",
                   result={"cancelled_by": c["id"]})  # then the worker died
    claimed = actions.claim(cfg.state_dir, c["id"])
    assert claimed is not None
    worker.recover()
    worker.tick()
    state, got = where(cfg, c["id"])
    assert state == "done" and got["status"] == "done"


# ---- interrupted starts -----------------------------------------------------


def test_a_start_interrupted_before_started_at_runs_again(cfg, worker, cycle):
    rec = start(cfg, "T-1")
    assert actions.claim(cfg.state_dir, rec["id"]) is not None
    assert worker.recover() == [(rec["id"], "back to pending")]
    assert where(cfg, rec["id"])[0] == "pending"
    worker.tick()
    worker.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert cycle.calls == [("T-1", "proj")]


def test_a_start_interrupted_after_started_at_is_not_run_again(cfg, worker, cycle):
    rec = start(cfg, "T-1")
    claimed = actions.claim(cfg.state_dir, rec["id"])
    claimed["started_at"] = "2026-01-01T00:00:00+00:00"
    actions.rewrite(cfg.state_dir, claimed)
    assert worker.recover() == [(rec["id"], "interrupted")]
    worker.tick()
    worker.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert cycle.calls == []


# ---- kinds ------------------------------------------------------------------


def test_an_unknown_kind_fails(cfg, worker, cycle):
    actions.ensure_dirs(cfg.state_dir)
    rec = {k: None for k in actions.FIELDS}
    rec.update(version=1, id="a-20260101T000000000000Z-0001", kind="nope",
               params={}, actor="operator", status="pending")
    root = actions.actions_dir(cfg.state_dir)
    (root / "pending" / f"{rec['id']}.json").write_text(json.dumps(rec))
    worker.tick()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "failed" and got["error"] == "unknown kind"


# ---- turn (ADR 173) ---------------------------------------------------------


class FakeProc:
    """A fake `docker exec` client: `lines` are what stdout yields, the pid line
    first. `block` holds the stream open until `kill` (or the test) sets it."""

    def __init__(self, lines, block=None, returncode=0, stderr=""):
        self.lines = list(lines)
        self.block = block
        self.returncode = returncode
        self.stderr = io.StringIO(stderr)
        self.entered = threading.Event()

    @property
    def stdout(self):
        for line in self.lines:
            yield line + "\n"
            self.entered.set()
        if self.block is not None:
            self.entered.set()
            assert self.block.wait(WAIT)

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def ev(**kw):
    return json.dumps(kw)


INIT = ev(type="system", subtype="init", session_id="s-new")
RESULT = ev(type="result", subtype="success", is_error=False, session_id="s-new",
            total_cost_usd=0.5, usage={"input_tokens": 3}, num_turns=1, result="hi")


@pytest.fixture
def execs(monkeypatch):
    class Execs:
        def __init__(self):
            self.opened = []  # (container, workdir, command)
            self.proc = FakeProc(["4242", INIT, ev(type="assistant"), RESULT])
            self.killed = []

    fake = Execs()

    def open_exec(container, workdir, command):
        fake.opened.append((container, workdir, command))
        return fake.proc

    def kill(container, pid):
        fake.killed.append((container, pid))
        if fake.proc.block is not None:
            fake.proc.block.set()

    monkeypatch.setattr(worker_mod.docker_exec, "open_docker_exec", open_exec)
    monkeypatch.setattr(worker_mod.docker_exec, "kill_in_container", kill)
    return fake


@pytest.fixture
def thread(cfg):
    cfg.thread_models = ["opus"]
    return threads.create(cfg.hive_tasks_dir, cfg, title="t", model="opus")


def turn(cfg, thread_id, text="hello"):
    return actions.create(cfg.state_dir, "turn", params={"thread": thread_id, "text": text})


def log_of(cfg, thread_id):
    with open(threads.conversation_path(cfg.hive_tasks_dir, thread_id)) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_a_turn_logs_the_message_the_events_in_order_and_ends_done(cfg, worker, thread, execs):
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    state, got = where(cfg, t["id"])
    assert state == "done" and got["status"] == "done"
    assert got["result"] == {"session_id": "s-new", "total_cost_usd": 0.5,
                             "usage": {"input_tokens": 3}, "num_turns": 1}
    lines = log_of(cfg, thread["id"])
    assert [(l["type"], l.get("event") or l.get("subtype")) for l in lines] == [
        ("ia_harness", "turn_start"), ("system", "init"), ("assistant", None),
        ("result", "success"), ("ia_harness", "turn_end"),
    ]
    assert lines[0]["text"] == "hello" and lines[0]["session_id"] is None
    assert lines[-1]["status"] == "done"


def test_a_line_that_is_not_json_is_logged_as_unparsed(cfg, worker, thread, execs):
    execs.proc = FakeProc(["4242", "garbage", INIT, RESULT])
    turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    assert log_of(cfg, thread["id"])[1] == {
        "type": "ia_harness", "event": "unparsed", "at": log_of(cfg, thread["id"])[1]["at"],
        "line": "garbage",
    }


def test_a_fresh_thread_gets_its_session_recorded_then_resumed(cfg, worker, thread, execs):
    turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    record = threads.read_thread(cfg.hive_tasks_dir, thread["id"])
    assert record["segments"][-1]["session_id"] == "s-new"
    assert "--resume" not in execs.opened[0][2]
    turn(cfg, thread["id"], "again")
    worker.tick()
    worker.join()
    command = execs.opened[1][2]
    assert command[command.index("--resume") + 1] == "s-new"


def test_the_command_is_read_only_by_flags_on_the_threads_account(cfg, worker, thread, execs):
    cfg.board_mcp_url = None
    turn(cfg, thread["id"], "what is in here")
    worker.tick()
    worker.join()
    container, workdir, command = execs.opened[0]
    assert container == "agent-cuenta1" and workdir == cfg.projects_root
    flag = command.index("--disallowed-tools")
    assert command[flag + 1:flag + 4] == ["Write", "Edit", "NotebookEdit"]
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--permission-prompts") + 1] == "none"
    allowed = command.index("--allowed-tools")
    assert command[allowed + 1:allowed + 1 + len(worker_mod.TURN_ALLOWED_TOOLS)] == list(
        worker_mod.TURN_ALLOWED_TOOLS)
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--effort") + 1] == thread["effort"]
    assert command[-2:] == ["--", "what is in here"]
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in command and "--mcp-config" not in command


@pytest.mark.parametrize("why", ["unknown", "archived", "account", "parked"])
def test_a_turn_that_cannot_run_fails_with_one_line_and_logs_nothing(
    cfg, worker, thread, execs, why
):
    tid = thread["id"]
    if why == "unknown":
        tid = "th-20260101T000000Z-abcdef"
    elif why == "archived":
        threads.update(cfg.hive_tasks_dir, cfg, tid, status="archived")
    elif why == "account":
        path = threads.thread_path(cfg.hive_tasks_dir, tid)
        rec = json.loads(open(path).read())
        rec["account"] = "nope"
        open(path, "w").write(json.dumps(rec))
    else:
        state_machine.park(cfg.state_dir, "cuenta1", state_machine.AccountState.COOLING_DOWN)
    t = turn(cfg, tid)
    worker.tick()
    worker.join()
    state, got = where(cfg, t["id"])
    assert state == "done" and got["status"] == "failed"
    assert got["error"] and "\n" not in got["error"]
    assert execs.opened == []
    assert not os.path.exists(threads.conversation_path(cfg.hive_tasks_dir, tid))


def test_an_error_result_or_no_result_fails_the_turn(cfg, worker, thread, execs):
    bad = ev(type="result", subtype="error_during_execution", is_error=True, result="boom")
    execs.proc = FakeProc(["4242", INIT, bad])
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    got = where(cfg, t["id"])[1]
    assert got["status"] == "failed" and "boom" in got["error"]
    execs.proc = FakeProc(["4242", INIT], returncode=1, stderr="no auth")
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    got = where(cfg, t["id"])[1]
    assert got["status"] == "failed" and "no auth" in got["error"]
    assert log_of(cfg, thread["id"])[-1]["status"] == "failed"


def test_a_cancel_kills_a_running_turn(cfg, worker, thread, execs):
    execs.proc = FakeProc(["4242", INIT], block=threading.Event())
    t = turn(cfg, thread["id"])
    worker.tick()
    assert execs.proc.entered.wait(WAIT)
    c = cancel(cfg, t["id"])
    worker.tick()
    assert where(cfg, c["id"])[1]["result"]["target_status"] == "running"
    worker.join()
    assert execs.killed == [("agent-cuenta1", 4242)]
    got = where(cfg, t["id"])[1]
    assert got["status"] == "cancelled"
    assert log_of(cfg, thread["id"])[-1]["status"] == "cancelled"


def test_stop_kills_a_running_turn_and_ends_it_interrupted(cfg, accounts, thread, execs):
    execs.proc = FakeProc(["4242", INIT], block=threading.Event())
    w = worker_mod.Worker(cfg, account_poll=0)
    t = turn(cfg, thread["id"])
    w.tick()
    assert execs.proc.entered.wait(WAIT)
    w.stop()
    w.recover = lambda: []  # the turn is this process's own, not a dead worker's
    w.run(interval=0.01)
    got = where(cfg, t["id"])[1]
    assert got["status"] == "interrupted" and got["error"] == worker_mod.TURN_STOPPED_ERROR
    assert execs.killed == [("agent-cuenta1", 4242)]
    assert log_of(cfg, thread["id"])[-1]["status"] == "interrupted"


def test_a_claimed_turn_is_interrupted_on_recovery_and_never_rerun(cfg, worker, thread, execs):
    t = turn(cfg, thread["id"])
    claimed = actions.claim(cfg.state_dir, t["id"])
    claimed["started_at"] = "2026-01-01T00:00:00+00:00"
    actions.rewrite(cfg.state_dir, claimed)
    threads.append_conversation(cfg.hive_tasks_dir, thread["id"], {"type": "ia_harness",
                                                                   "event": "turn_start"})
    assert worker.recover() == [(t["id"], "interrupted")]
    worker.tick()
    worker.join()
    state, got = where(cfg, t["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert execs.opened == []
    last = log_of(cfg, thread["id"])[-1]
    assert last["event"] == "turn_end" and last["status"] == "interrupted"


def turn_notices(cfg):
    path = notifications.notifications_path(cfg.hive_tasks_dir)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_a_turn_that_ends_done_failed_or_interrupted_notifies_its_thread(
    cfg, worker, accounts, thread, execs
):
    tid = thread["id"]
    turn(cfg, tid)
    worker.tick()
    worker.join()
    execs.proc = FakeProc(["4242", INIT, ev(type="result", subtype="error_during_execution",
                                            is_error=True, result="boom")])
    turn(cfg, tid)
    worker.tick()
    worker.join()
    # Stopped mid-stream, then the one a dead worker left claimed.
    execs.proc = FakeProc(["4242", INIT], block=threading.Event())
    w = worker_mod.Worker(cfg, account_poll=0)
    t = turn(cfg, tid)
    w.tick()
    assert execs.proc.entered.wait(WAIT)
    w.stop()
    w.recover = lambda: []
    w.run(interval=0.01)
    assert where(cfg, t["id"])[1]["status"] == "interrupted"
    dead = turn(cfg, tid)
    claimed = actions.claim(cfg.state_dir, dead["id"])
    claimed["started_at"] = "2026-01-01T00:00:00+00:00"
    actions.rewrite(cfg.state_dir, claimed)
    assert worker.recover() == [(dead["id"], "interrupted")]
    got = turn_notices(cfg)
    assert [(n["kind"], n["thread"], n["title"]) for n in got] == [
        ("turn_finished", tid, f"{tid} turn done: t"),
        ("turn_finished", tid, f"{tid} turn failed: t"),
        ("turn_finished", tid, f"{tid} turn interrupted: t"),
        ("turn_finished", tid, f"{tid} turn interrupted: t"),
    ]
    assert {n["url"] for n in got} == {notifications.thread_url(tid)}
    assert all("task" not in n and "account" not in n for n in got)


def test_a_cancelled_turn_notifies_nobody(cfg, worker, thread, execs):
    execs.proc = FakeProc(["4242", INIT], block=threading.Event())
    t = turn(cfg, thread["id"])
    worker.tick()
    assert execs.proc.entered.wait(WAIT)
    cancel(cfg, t["id"])
    worker.tick()
    worker.join()
    assert where(cfg, t["id"])[1]["status"] == "cancelled"
    assert turn_notices(cfg) == []


def test_a_turn_refused_before_it_starts_notifies_nobody(cfg, worker, thread, execs):
    threads.update(cfg.hive_tasks_dir, cfg, thread["id"], status="archived")
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    assert where(cfg, t["id"])[1]["status"] == "failed"
    assert turn_notices(cfg) == []


def test_a_notification_that_cannot_be_written_leaves_the_turn_done(cfg, worker, thread, execs):
    # A directory where the file goes: the append's open fails, and is logged.
    os.makedirs(notifications.notifications_path(cfg.hive_tasks_dir))
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    assert where(cfg, t["id"])[1]["status"] == "done"
    assert log_of(cfg, thread["id"])[-1]["status"] == "done"


def test_two_turns_on_one_thread_never_run_at_once(cfg, worker, thread, execs):
    execs.proc = FakeProc(["4242", INIT], block=threading.Event())
    first = turn(cfg, thread["id"])
    second = turn(cfg, thread["id"], "next")
    worker.tick()
    assert execs.proc.entered.wait(WAIT)
    worker.tick()
    state, got = where(cfg, second["id"])
    assert state == "pending" and got["waiting"] == "thread"
    assert len(execs.opened) == 1
    execs.proc.block.set()
    worker.join()
    assert where(cfg, first["id"])[1]["status"] in ("done", "failed")


def test_a_turn_gets_the_board_server_for_the_thread_role_and_its_tool_names(
    cfg, worker, thread, execs
):
    cfg.board_mcp_url = "http://board-service:8792/mcp"
    t = turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    command = execs.opened[0][2]
    server = json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]["board"]
    assert server["url"] == f"http://board-service:8792/mcp?role=thread&turn={t['id']}"
    allowed = command.index("--allowed-tools")
    names = list(worker_mod.TURN_ALLOWED_TOOLS) + [
        "mcp__board__board_list", "mcp__board__board_get", "mcp__board__task_start",
    ]
    assert command[allowed + 1:allowed + 1 + len(names)] == names
    flag = command.index("--disallowed-tools")
    assert command[flag + 1:flag + 4] == ["Write", "Edit", "NotebookEdit"]
    assert command.index("--mcp-config") < command.index("-p")


def test_a_turn_with_no_board_url_gets_no_server_and_no_tool_names(cfg, worker, thread, execs):
    cfg.board_mcp_url = None
    turn(cfg, thread["id"])
    worker.tick()
    worker.join()
    command = execs.opened[0][2]
    assert "--mcp-config" not in command
    assert not any(part.startswith("mcp__") for part in command)


# ---- the seed of a new session ----------------------------------------------


def task_thread(cfg, task_id="T-1", note="mind the lock", trail="## arquitecto\n\nPlan it."):
    """A thread on a task with a description, a start note in a finished
    `start`, and a handoff trail."""
    cfg.thread_models = ["opus"]
    _task(cfg, task_id)
    if trail:
        tasks.handoff(cfg.hive_tasks_dir, task_id, "pending", trail)
    if note is not None:
        rec = actions.create(cfg.state_dir, "start", project="proj", task_id=task_id,
                             note=note)
        actions.finish(cfg.state_dir, rec["id"], "done")
    return threads.create(cfg.hive_tasks_dir, cfg, title="t", model="opus", task=task_id)


def prompt_of(execs, n=0):
    return execs.opened[n][2][-1]


def test_a_fresh_task_thread_is_seeded_with_its_description_note_and_handoffs(
    cfg, worker, execs
):
    th = task_thread(cfg)
    turn(cfg, th["id"], "where are we")
    worker.tick()
    worker.join()
    prompt = prompt_of(execs)
    assert prompt.endswith("\n\nwhere are we")
    seed = prompt[: -len("\n\nwhere are we")]
    assert len(seed) <= worker_mod.TURN_SEED_MAX_CHARS
    assert "Do T-1." in seed and "mind the lock" in seed and "Plan it." in seed
    assert seed.index("Do T-1.") < seed.index("mind the lock") < seed.index("Plan it.")
    start = log_of(cfg, th["id"])[0]
    assert start["text"] == "where are we"
    assert start["seed"]["text"] == seed and start["seed"]["chars"] == len(seed)
    assert start["seed"]["parts"] == ["description", "note", "handoffs"]
    assert start["seed"]["truncated"] is False and start["seed"]["unread"] == []


def test_a_seed_over_its_budget_is_cut_and_says_so_and_keeps_the_latest_handoff(
    cfg, worker, execs
):
    trail = "## arquitecto\n\n" + "old " * 5000 + "\n\n## auditor\n\nThe latest."
    th = task_thread(cfg, trail=trail)
    turn(cfg, th["id"])
    worker.tick()
    worker.join()
    seed = log_of(cfg, th["id"])[0]["seed"]
    assert seed["truncated"] is True
    assert seed["chars"] == len(seed["text"]) <= worker_mod.TURN_SEED_MAX_CHARS
    marker = worker_mod.TURN_SEED_CUT.format(max=worker_mod.TURN_SEED_MAX_CHARS)
    assert marker in seed["text"]
    assert "The latest." in seed["text"] and "Do T-1." in seed["text"]
    assert prompt_of(execs) == seed["text"] + "\n\nhello"


def test_a_description_over_the_whole_budget_is_cut_and_the_rest_dropped(cfg, worker, execs):
    th = task_thread(cfg)
    tasks.write_description(cfg.hive_tasks_dir, "T-1",
                            "## Goal\n\n" + "x" * 20_000 + "\n\n## Done when\n\nok\n")
    turn(cfg, th["id"])
    worker.tick()
    worker.join()
    seed = log_of(cfg, th["id"])[0]["seed"]
    assert seed["truncated"] is True and seed["parts"] == ["description"]
    assert len(seed["text"]) <= worker_mod.TURN_SEED_MAX_CHARS


@pytest.mark.parametrize("board", [None, "http://board-service:8792/mcp"])
def test_a_thread_on_no_task_gets_no_seed(cfg, worker, thread, execs, board):
    cfg.board_mcp_url = board
    turn(cfg, thread["id"], "hi")
    worker.tick()
    worker.join()
    assert execs.opened[0][2][-2:] == ["--", "hi"]
    assert log_of(cfg, thread["id"])[0]["seed"] is None


def test_a_resumed_session_gets_no_seed(cfg, worker, execs):
    th = task_thread(cfg)
    turn(cfg, th["id"])
    worker.tick()
    worker.join()
    turn(cfg, th["id"], "again")
    worker.tick()
    worker.join()
    command = execs.opened[1][2]
    assert "--resume" in command and command[-2:] == ["--", "again"]
    assert log_of(cfg, th["id"])[-1]["event"] == "turn_end"
    second = [l for l in log_of(cfg, th["id"]) if l.get("event") == "turn_start"][1]
    assert second["seed"] is None


def test_an_unreadable_part_is_left_out_with_a_warning_and_the_turn_runs(
    cfg, worker, execs, monkeypatch, caplog
):
    th = task_thread(cfg)

    def broken(hive_dir, task_id):
        raise OSError("disk says no")

    monkeypatch.setattr(worker_mod.tasks, "read_description", broken)
    t = turn(cfg, th["id"])
    with caplog.at_level("WARNING", logger=worker_mod.__name__):
        worker.tick()
        worker.join()
    assert where(cfg, t["id"])[1]["status"] == "done"
    seed = log_of(cfg, th["id"])[0]["seed"]
    assert seed["unread"] == ["description"] and seed["parts"] == ["note", "handoffs"]
    assert "Plan it." in prompt_of(execs) and "Do T-1." not in prompt_of(execs)
    assert any("disk says no" in r.getMessage() for r in caplog.records)


def test_a_task_whose_card_is_gone_still_runs_the_turn(cfg, worker, execs):
    th = task_thread(cfg, note=None, trail=None)
    os.remove(worker_mod.context_transfer.task_file_path(cfg.hive_tasks_dir, "T-1"))
    t = turn(cfg, th["id"])
    worker.tick()
    worker.join()
    assert where(cfg, t["id"])[1]["status"] == "done"
    seed = log_of(cfg, th["id"])[0]["seed"]
    assert seed["unread"] == ["handoffs"] and seed["parts"] == ["description"]


# ---- command_verdict --------------------------------------------------------


def test_a_verdict_is_carried_with_the_actor_as_by(cfg, worker, verdicts):
    rec = verdict(cfg, verdict="allow", comment="fine", prefix=2)
    worker.tick()
    worker.join()
    assert verdicts.calls == [{"task_id": "T-1", "slug": "proj", "n": 1, "verdict": "allow",
                               "comment": "fine", "by": "operator", "prefix": 2}]
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "done" and got["error"] is None
    assert got["started_at"] is not None
    assert got["result"] == {"n": 1, "verdict": "allow", "outcome": "finished",
                             "blocked_reason": None}


def test_a_refused_verdict_fails_with_its_reason(cfg, worker, verdicts):
    verdicts.raises = command_run.VerdictRefused("task T-1: this block already\nhas a verdict")
    rec = verdict(cfg)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == "task T-1: this block already has a verdict"
    assert worker.running == {}


def test_a_verdict_that_raises_otherwise_fails(cfg, worker, verdicts):
    verdicts.raises = OSError("disk")
    rec = verdict(cfg)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed" and got["error"] == "OSError: disk"


def test_a_verdict_waits_for_an_account(cfg, worker, verdicts, accounts):
    accounts["open"] = False
    rec = verdict(cfg)
    worker.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "account"
    assert verdicts.calls == []
    accounts["open"] = True
    worker.tick()
    worker.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"


def test_a_claimed_verdict_is_interrupted_on_recovery(cfg, worker, verdicts):
    rec = verdict(cfg)
    assert actions.claim(cfg.state_dir, rec["id"]) is not None
    assert worker.recover() == [(rec["id"], "interrupted")]
    worker.tick()
    worker.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert "command-resume" in got["error"]
    assert verdicts.calls == []


def test_a_verdict_waits_for_a_start_on_its_project(cfg, worker, cycle, verdicts):
    cycle.hold("T-1")
    s = start(cfg, "T-1")
    v = verdict(cfg, task_id="T-2")
    worker.tick()
    cycle.wait_entered("T-1")
    state, got = where(cfg, v["id"])
    assert state == "pending" and got["waiting"] == "project"
    assert verdicts.calls == []
    cycle.release("T-1")
    worker.join()
    worker.tick()
    worker.join()
    assert where(cfg, s["id"])[1]["status"] == "done"
    assert where(cfg, v["id"])[1]["status"] == "done"


def test_a_start_waits_for_a_verdict_on_its_project(cfg, worker, cycle, verdicts):
    verdicts.gate = threading.Event()
    v = verdict(cfg, task_id="T-2")
    s = start(cfg, "T-1")
    other = start(cfg, "T-3", project="beta")
    worker.tick()
    assert verdicts.entered.wait(WAIT)
    state, got = where(cfg, s["id"])
    assert state == "pending" and got["waiting"] == "project"
    verdicts.gate.set()
    worker.join()
    assert where(cfg, other["id"])[1]["status"] == "done"
    worker.tick()
    worker.join()
    assert where(cfg, v["id"])[1]["status"] == "done"
    assert [c[0] for c in cycle.calls] == ["T-3", "T-1"]


def test_a_cancel_on_a_running_verdict_fails_and_changes_nothing(cfg, worker, verdicts):
    verdicts.gate = threading.Event()
    v = verdict(cfg)
    worker.tick()
    assert verdicts.entered.wait(WAIT)
    c = cancel(cfg, v["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and worker_mod.VERDICT_NOT_CANCELLABLE in got["error"]
    assert where(cfg, v["id"])[0] == "claimed"
    verdicts.gate.set()
    worker.join()
    assert where(cfg, v["id"])[1]["status"] == "done"


def test_a_cancel_on_a_pending_verdict_cancels_it(cfg, worker, verdicts, accounts):
    accounts["open"] = False
    v = verdict(cfg)
    c = cancel(cfg, v["id"])
    worker.tick()
    assert where(cfg, v["id"])[1]["status"] == "cancelled"
    assert where(cfg, c["id"])[1]["status"] == "done"
    assert verdicts.calls == []


# ---- approve ----------------------------------------------------------------

TIP = "a" * 40


class Approve:
    """A fake `core.approve_reviewed`, held and answering the way `Verdict` is."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.entered = threading.Event()
        self.gate: threading.Event | None = None
        self.outcome = "merged"
        self.comment = "approve at aaaaaaa: merged into main as ccccccc, pushed to main"
        self.push = None
        self.merge_commit = "c" * 40

    def __call__(self, cfg, task_id, slug, commit):
        self.calls.append((task_id, slug, commit))
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(WAIT)
        return core.ApprovalOutcome(
            self.outcome, commit, self.merge_commit, self.push, self.comment
        )


@pytest.fixture
def approves(monkeypatch):
    fake = Approve()
    monkeypatch.setattr(core, "approve_reviewed", fake)
    yield fake
    if fake.gate is not None:
        fake.gate.set()


def approve(cfg, task_id="T-1", project="proj", commit=TIP):
    _task(cfg, task_id)
    return actions.create(cfg.state_dir, "approve", project=project, task_id=task_id,
                          params={"commit": commit})


def test_an_approve_comments_once_and_ends_done_with_its_result(cfg, worker, approves):
    approves.push = core.docker_exec.PushOutcome(core.docker_exec.PUSHED, "main", "pushed main")
    rec = approve(cfg)
    worker.tick()
    worker.join()
    assert approves.calls == [("T-1", "proj", TIP)]
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "done" and got["error"] is None
    assert got["started_at"] is not None
    assert got["result"] == {
        "outcome": "merged", "commit": TIP, "merge_commit": "c" * 40,
        "push": {"status": "pushed", "target": "main", "detail": "pushed main"},
    }
    [comment] = comments(cfg, "T-1")
    assert comment["author"] == "dispatcher"
    assert comment["text"].startswith("approve at aaaaaaa:")


@pytest.mark.parametrize("outcome", ["stale", "conflict", "not_run", "merge_refused"])
def test_a_refused_approve_fails_with_the_comments_first_line(cfg, worker, approves, outcome):
    approves.outcome = outcome
    approves.merge_commit = None
    approves.comment = "approve at aaaaaaa: not merged: the reason\n  second   line"
    rec = approve(cfg)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == "approve at aaaaaaa: not merged: the reason"
    assert got["result"] == {"outcome": outcome, "commit": TIP, "merge_commit": None, "push": None}
    assert [c["text"] for c in comments(cfg, "T-1")] == [approves.comment]


def test_a_merged_but_unpushed_approve_is_done(cfg, worker, approves):
    approves.outcome = "merged_not_pushed"
    approves.push = core.docker_exec.PushOutcome(core.docker_exec.PUSH_REFUSED, "main", "no")
    approves.comment = "approve at aaaaaaa: merged locally into main as ccccccc\nno"
    rec = approve(cfg)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "done" and got["error"] is None
    assert got["result"]["outcome"] == "merged_not_pushed"
    assert got["result"]["push"]["status"] == "push-refused"


def test_an_approve_whose_comment_cannot_be_written_still_ends_with_its_result(
    cfg, worker, approves, monkeypatch,
):
    def refuse(*args, **kwargs):
        raise tasks.BoardWriteError("task T-1", ["no such task"])

    monkeypatch.setattr(tasks, "add_comment", refuse)
    rec = approve(cfg)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "done" and got["result"]["outcome"] == "merged"


def test_an_approve_waits_on_no_account_and_takes_no_project_lock(
    cfg, worker, approves, accounts,
):
    accounts["open"] = False
    held = ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")
    try:
        rec = approve(cfg)
        worker.tick()
        worker.join()
    finally:
        held.release()
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert accounts["probes"] == 0


def test_an_approve_runs_beside_a_running_start_of_its_project(cfg, worker, cycle, approves):
    cycle.hold("T-1")
    s = start(cfg, "T-1")
    a = approve(cfg, "T-2")
    worker.tick()
    cycle.wait_entered("T-1")
    assert approves.entered.wait(WAIT)
    cycle.release("T-1")
    worker.join()
    assert where(cfg, s["id"])[1]["status"] == "done"
    assert where(cfg, a["id"])[1]["status"] == "done"


def test_a_second_approve_on_a_project_waits_as_project_while_the_first_runs(
    cfg, worker, approves,
):
    approves.gate = threading.Event()
    first = approve(cfg, "T-1")
    second = approve(cfg, "T-2")
    other = approve(cfg, "T-3", project="beta")
    worker.tick()
    assert approves.entered.wait(WAIT)
    state, got = where(cfg, second["id"])
    assert state == "pending" and got["waiting"] == "project"
    approves.gate.set()
    worker.join()
    assert where(cfg, first["id"])[1]["status"] == "done"
    assert where(cfg, other["id"])[1]["status"] == "done"
    worker.tick()
    worker.join()
    assert where(cfg, second["id"])[1]["status"] == "done"
    assert sorted(c[0] for c in approves.calls) == ["T-1", "T-2", "T-3"]


def test_a_cancel_on_a_pending_approve_cancels_it_and_a_running_one_is_left(
    cfg, worker, approves,
):
    approves.gate = threading.Event()
    running = approve(cfg, "T-1")
    waiting = approve(cfg, "T-2")
    worker.tick()
    assert approves.entered.wait(WAIT)
    c1, c2 = cancel(cfg, waiting["id"]), cancel(cfg, running["id"])
    worker.tick()
    assert where(cfg, waiting["id"])[1]["status"] == "cancelled"
    assert where(cfg, c1["id"])[1]["status"] == "done"
    got = where(cfg, c2["id"])[1]
    assert got["status"] == "failed" and "cannot be cancelled" in got["error"]
    approves.gate.set()
    worker.join()
    assert where(cfg, running["id"])[1]["status"] == "done"


def test_a_claimed_approve_is_interrupted_on_recovery_and_never_run_again(cfg, worker, approves):
    rec = approve(cfg)
    assert actions.claim(cfg.state_dir, rec["id"]) is not None
    assert worker.recover() == [(rec["id"], "interrupted")]
    worker.tick()
    worker.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert "read the task's comments and the base" in got["error"]
    assert approves.calls == [] and comments(cfg, "T-1") == []


# ---- request changes --------------------------------------------------------


class ChangesCycle:
    """A fake `core.run_task_cycle` for a request for changes: it keeps what it
    was handed and the task's status when it was called, and can be held."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.calls: list[dict] = []
        self.entered = threading.Event()
        self.gate: threading.Event | None = None
        self.outcome = core.CycleOutcome.FINISHED

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, scheduler=None, project_lock=None,
                 token_limit=None, usage_offset=0, changes_requested=""):
        status = worker_mod._read_task(cfg, task_id).status
        self.calls.append({"task_id": task_id, "slug": slug, "authorized": authorized,
                           "changes_requested": changes_requested, "status": status,
                           "project_lock": project_lock})
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(WAIT)
        if not may_start_phase():
            return core.CycleOutcome.HELD
        tasks.handoff(cfg.hive_tasks_dir, task_id, "done", "## auditor\n\nPassed.")
        tasks.mark_awaiting_review(cfg.hive_tasks_dir, task_id)
        return self.outcome


@pytest.fixture
def changes(cfg, monkeypatch):
    fake = ChangesCycle(cfg)
    monkeypatch.setattr(core, "run_task_cycle", fake)
    yield fake
    if fake.gate is not None:
        fake.gate.set()


def awaiting(cfg, task_id="T-1"):
    _task(cfg, task_id)
    tasks.handoff(cfg.hive_tasks_dir, task_id, "done", "## auditor\n\nPassed.")
    assert tasks.mark_awaiting_review(cfg.hive_tasks_dir, task_id)


def request_changes(cfg, task_id="T-1", project="proj", commit=TIP, text="Rename it.",
                    make_task=True):
    if make_task:
        awaiting(cfg, task_id)
    tasks.add_comment(cfg.hive_tasks_dir, task_id, "operator", "The name is off.",
                      file="src/app.py", line=12, commit=commit)
    tasks.record_verdict(cfg.hive_tasks_dir, task_id, "operator", "request_changes", text, commit)
    return actions.create(cfg.state_dir, "request_changes", project=project, task_id=task_id,
                          params={"commit": commit})


def test_a_request_for_changes_reruns_the_cycle_unauthorized_with_the_comments(
    cfg, worker, changes,
):
    rec = request_changes(cfg)
    worker.tick()
    worker.join()
    [call] = changes.calls
    assert (call["task_id"], call["slug"], call["authorized"]) == ("T-1", "proj", False)
    assert call["status"] == "pending"  # reopened before the cycle took it
    assert call["project_lock"] is not None
    note = call["changes_requested"]
    assert f"at commit {TIP}" in note
    assert "- operator on src/app.py:12: The name is off." in note
    assert note.rstrip().endswith("- operator: Rename it.")
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "done" and got["error"] is None
    assert got["started_at"] is not None
    assert got["result"] == {"commit": TIP, "outcome": "finished", "blocked_reason": None}
    assert worker_mod._read_task(cfg, "T-1").status == "awaiting_review"


def test_a_request_for_changes_on_a_conflict_reopens_it_and_reruns_the_cycle(
    cfg, worker, changes,
):
    rec = request_changes(cfg)
    tasks.handoff(cfg.hive_tasks_dir, "T-1", tasks.CONFLICT_STATUS, "## integrador\n\nBlocked.")
    worker.tick()
    worker.join()
    [call] = changes.calls
    assert call["status"] == "pending"
    assert "- operator: Rename it." in call["changes_requested"]
    assert where(cfg, rec["id"])[1]["status"] == "done"


def test_a_request_for_changes_waits_for_an_account(cfg, worker, changes, accounts):
    accounts["open"] = False
    rec = request_changes(cfg)
    worker.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "account"
    assert changes.calls == []


def test_a_request_for_changes_waits_for_a_start_on_its_project(cfg, worker, cycle):
    cycle.hold("T-2")
    s = start(cfg, "T-2")
    worker.tick()
    cycle.wait_entered("T-2")
    awaiting(cfg)
    tasks.record_verdict(cfg.hive_tasks_dir, "T-1", "operator", "request_changes", "x", TIP)
    rec = actions.create(cfg.state_dir, "request_changes", project="proj", task_id="T-1",
                         params={"commit": TIP})
    worker.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "project"
    cycle.release("T-2")
    worker.join()
    assert where(cfg, s["id"])[1]["status"] == "done"


@pytest.mark.parametrize("status, why", [
    ("pending", "it is pending"), ("done", "it is done"),
])
def test_a_request_for_changes_on_a_task_not_under_review_fails_and_runs_nothing(
    cfg, worker, changes, status, why,
):
    rec = request_changes(cfg)
    tasks.handoff(cfg.hive_tasks_dir, "T-1", status, "")
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == f"task T-1 is not under review: {why}"
    assert got["started_at"] is None
    assert changes.calls == []


def test_a_request_for_changes_with_no_verdict_on_its_commit_fails(cfg, worker, changes):
    rec = request_changes(cfg)
    other = "b" * 40
    actions.finish(cfg.state_dir, rec["id"], "cancelled")
    rec = actions.create(cfg.state_dir, "request_changes", project="proj", task_id="T-1",
                         params={"commit": other})
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == f"task T-1 has no request for changes on {other}"
    assert changes.calls == []
    assert worker_mod._read_task(cfg, "T-1").status == "awaiting_review"


def test_a_cancel_on_a_running_request_for_changes_holds_it(cfg, worker, changes):
    changes.gate = threading.Event()
    rec = request_changes(cfg)
    worker.tick()
    assert changes.entered.wait(WAIT)
    c = cancel(cfg, rec["id"])
    worker.tick()
    assert where(cfg, c["id"])[1]["result"]["target_status"] == "running"
    changes.gate.set()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "cancelled" and got["result"]["outcome"] == "held"
    assert "last_phase" in got["result"]


def test_a_request_for_changes_interrupted_before_started_at_runs_again(cfg, worker, changes):
    rec = request_changes(cfg)
    assert actions.claim(cfg.state_dir, rec["id"]) is not None
    assert worker.recover() == [(rec["id"], "back to pending")]
    worker.tick()
    worker.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert len(changes.calls) == 1


def test_a_request_for_changes_interrupted_after_started_at_is_not_run_again(
    cfg, worker, changes,
):
    rec = request_changes(cfg)
    claimed = actions.claim(cfg.state_dir, rec["id"])
    claimed["started_at"] = "2026-01-01T00:00:00+00:00"
    actions.rewrite(cfg.state_dir, claimed)
    assert worker.recover() == [(rec["id"], "interrupted")]
    worker.tick()
    worker.join()
    assert where(cfg, rec["id"])[1]["status"] == "interrupted"
    assert changes.calls == []


# ---- heartbeat, shutdown ----------------------------------------------------


def test_the_heartbeat_is_written(cfg, clock):
    w = worker_mod.Worker(cfg)
    w.beat()
    path = actions.actions_dir(cfg.state_dir) / worker_mod.HEARTBEAT
    first = json.loads(path.read_text())
    assert set(first) == {"pid", "started_at", "beat_at"} and first["pid"] == os.getpid()
    clock["t"] += timedelta(seconds=5)
    w.beat()
    second = json.loads(path.read_text())
    assert second["started_at"] == first["started_at"]
    assert second["beat_at"] != first["beat_at"]


def test_run_beats_and_returns_once_stopped(cfg, cycle, accounts):
    w = worker_mod.Worker(cfg)
    timer = threading.Timer(0.1, w.stop)
    timer.start()
    w.run(interval=0.01)
    timer.join()
    assert (actions.actions_dir(cfg.state_dir) / worker_mod.HEARTBEAT).exists()


def test_stop_holds_the_running_start_and_claims_nothing_more(cfg, worker, cycle):
    cycle.hold("T-1")
    running = start(cfg, "T-1", project="alpha")
    worker.tick()
    cycle.wait_entered("T-1")
    waiting = start(cfg, "T-2", project="beta")
    worker.stop()
    cycle.release("T-1")
    worker.join()
    state, got = where(cfg, running["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert got["error"] == worker_mod.STOPPED_ERROR
    worker.tick()
    assert where(cfg, waiting["id"])[0] == "pending"
    assert [c[0] for c in cycle.calls] == ["T-1"]
