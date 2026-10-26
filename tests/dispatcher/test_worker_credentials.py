"""The worker's `git_token` and `account_login` handlers (docs/decisions.md
ADR 456, ADR 465), with the login's `docker exec` faked."""

from __future__ import annotations

import os
import subprocess
import threading

import pytest

from dispatcher import actions, credentials, state_machine, worker as worker_mod
from tests.dispatcher.test_worker import (  # noqa: F401  (fixtures)
    WAIT, accounts, cancel, cfg, cycle, snapshots, where, worker,
)

TOKEN = "ghp_" + "A1b2C3d4E5" * 3
CODE = "zZ9-secret-code_123"
URL = "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz"


def git_token(cfg, secret=TOKEN, username="octocat"):
    params = {"username": username}
    return actions.create(cfg.state_dir, "git_token", params=params, secret=secret)


def login(cfg, account="cuenta1"):
    return actions.create(cfg.state_dir, "account_login", params={"account": account})


def run(worker):
    worker.tick()
    worker.join()


# ---- git_token --------------------------------------------------------------


@pytest.fixture
def token_file(cfg, tmp_path):
    path = tmp_path / "creds" / "git-credentials"
    path.parent.mkdir()
    path.write_text("https://old:" + "x" * 80 + "@github.com\n")
    cfg.git_credentials_path = str(path)
    return path


def test_git_token_writes_the_line_in_place_and_keeps_the_inode(
        cfg, worker, token_file, snapshots):
    inode = os.stat(token_file).st_ino
    action = git_token(cfg)
    run(worker)
    state, got = where(cfg, action["id"])
    assert state == "done" and got["status"] == "done" and got["error"] is None
    assert token_file.read_text() == f"https://octocat:{TOKEN}@github.com\n"
    assert os.stat(token_file).st_ino == inode
    assert set(got["result"]) == {"written_at"}
    assert snapshots.calls == 1


def test_git_token_writes_into_an_empty_file(cfg, worker, token_file):
    token_file.write_text("")
    git_token(cfg)
    run(worker)
    assert token_file.read_text() == f"https://octocat:{TOKEN}@github.com\n"


@pytest.mark.parametrize("make,sentence", [
    ("not_mounted", "the worker has no token file mounted: create "
     ".data/credentials/git-credentials on the host, then recreate the worker"),
    ("absent", "the token file is missing: create .data/credentials/git-credentials "
     "on the host, then recreate the worker"),
    ("directory", "the token file is a directory: Docker made one for a missing "
     ".data/credentials/git-credentials; replace it with a file on the host, then "
     "recreate the worker"),
])
def test_git_token_fixed_failures_for_the_file(cfg, worker, tmp_path, monkeypatch, make, sentence):
    markers = (str(tmp_path / "no-marker"),)
    if make == "not_mounted":
        (tmp_path / ".dockerenv").write_text("")
        markers = (str(tmp_path / ".dockerenv"),)
    monkeypatch.setattr(credentials, "CONTAINER_MARKERS", markers)
    if make in ("not_mounted", "absent"):
        cfg.git_credentials_path = str(tmp_path / "git-credentials")
    else:
        (tmp_path / "git-credentials").mkdir()
        cfg.git_credentials_path = str(tmp_path / "git-credentials")
    action = git_token(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed" and got["error"] == sentence
    assert TOKEN not in str(got)


def test_git_token_without_its_secret_fails_with_the_fixed_sentence(cfg, worker, token_file):
    action = git_token(cfg)
    actions.take_secret(cfg.state_dir, action["id"])
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the token was not found; renew it again"


def test_git_token_refuses_a_bad_shape_without_naming_it(cfg, worker, token_file, snapshots):
    action = git_token(cfg)
    bad = "not-a-token-" + "q" * 30
    root = actions.ensure_dirs(cfg.state_dir)
    (root / actions.SECRETS / action["id"]).write_text(bad)
    before = token_file.read_text()
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == "the token is not a GitHub personal access token"
    assert bad not in str(got) and token_file.read_text() == before
    assert snapshots.calls == 1


def test_git_token_write_error_quotes_strerror_only(cfg, worker, token_file, monkeypatch):
    def boom(path, username, token):
        raise OSError(30, "Read-only file system", str(token_file))

    monkeypatch.setattr(worker_mod, "_write_token_file", boom)
    action = git_token(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the token file could not be written: Read-only file system"


def test_a_running_git_token_cannot_be_cancelled_and_is_still_written(
        cfg, worker, token_file, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    real = worker_mod._write_token_file

    def slow(path, username, token):
        entered.set()
        assert gate.wait(WAIT)
        real(path, username, token)

    monkeypatch.setattr(worker_mod, "_write_token_file", slow)
    action = git_token(cfg)
    worker.tick()
    assert entered.wait(WAIT)
    c = cancel(cfg, action["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and "cannot be cancelled" in got["error"]
    gate.set()
    worker.join()
    assert token_file.read_text() == f"https://octocat:{TOKEN}@github.com\n"


def _everything_under(*roots):
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as f:
                    yield os.path.join(dirpath, name), f.read().decode("utf-8", "replace")


def test_the_token_is_in_no_record_log_or_state_file(cfg, worker, token_file, caplog):
    caplog.set_level("DEBUG")
    git_token(cfg)
    run(worker)
    for path, body in _everything_under(cfg.state_dir, cfg.hive_tasks_dir):
        assert TOKEN not in body, path
    assert TOKEN not in caplog.text


# ---- account_login ----------------------------------------------------------


class LoginProc:
    """A fake `docker exec -i` of the login. `lines` come first (the pid line
    among them); after that stdout stays open until the code is written (then it
    echoes it back, as `script` does, and ends with `returncode`), a kill, or
    `finish`."""

    def __init__(self, lines, returncode=0, exit_on_code=True):
        self.lines = list(lines)
        self.exit_returncode = returncode
        self.exit_on_code = exit_on_code
        self.finish = threading.Event()
        self.returncode = None
        self.writes: list[str] = []
        self.echo: list[str] = []
        self.killed = False
        self.stdin = self
        self.stdin_closed = False
        self.fail_write = False

    # stdin
    def write(self, text):
        if self.fail_write:
            raise BrokenPipeError()
        self.writes.append(text)
        self.echo.append(text.strip() + "\n")

    def flush(self):
        pass

    def close(self):
        self.stdin_closed = True

    # stdout
    @property
    def stdout(self):
        for line in self.lines:
            yield line + "\n"
        while True:
            if self.echo:
                yield self.echo.pop(0)
                continue
            if self.writes and self.exit_on_code and self.returncode is None:
                self.returncode = self.exit_returncode
                self.finish.set()
            if self.finish.is_set():
                return
            self.finish.wait(0.01)

    def poll(self):
        return self.returncode if self.finish.is_set() else None

    def wait(self, timeout=None):
        if not self.finish.wait(timeout if timeout is not None else WAIT):
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        self.finish.set()


@pytest.fixture
def exec_login(monkeypatch, cfg):
    class Fake:
        def __init__(self):
            self.opened = []
            self.killed = []
            self.proc = LoginProc(["4242", "Opening browser to sign in...", URL])
            self.rewritten = {"state": "present", "modified_at": "2999-01-01T00:00:00+00:00"}

    fake = Fake()

    def open_exec(container, workdir, command, *, stdin=False):
        fake.opened.append((container, workdir, command, stdin))
        return fake.proc

    def kill(container, pid):
        fake.killed.append((container, pid))
        fake.proc.kill()

    monkeypatch.setattr(worker_mod.docker_exec, "open_docker_exec", open_exec)
    monkeypatch.setattr(worker_mod.docker_exec, "kill_in_container", kill)
    monkeypatch.setattr(worker_mod.credentials, "account_row", lambda account, prev=None: dict(
        fake.rewritten, account=account.name))
    monkeypatch.setattr(worker_mod, "LOGIN_URL_SECONDS", 0.4)
    monkeypatch.setattr(worker_mod, "LOGIN_CODE_SECONDS", 3)
    monkeypatch.setattr(worker_mod, "LOGIN_EXIT_SECONDS", 0.4)
    monkeypatch.setattr(worker_mod, "LOGIN_CODE_POLL_SECONDS", 0.02)
    return fake


def wait_for_result(cfg, aid, key="awaiting", value="code"):
    deadline = threading.Event()
    for _ in range(int(WAIT / 0.02)):
        record = where(cfg, aid)[1]
        if record and (record.get("result") or {}).get(key) == value:
            return record
        deadline.wait(0.02)
    raise AssertionError("the login never reached " + value)


def sign_in(cfg, worker, exec_login):
    """Start a login, wait for the URL, hand it the code, let it end."""
    action = login(cfg)
    worker.tick()
    wait_for_result(cfg, action["id"])
    actions.put_login_code(cfg.state_dir, action["id"], CODE)
    worker.join()
    return action


def test_the_url_is_taken_shown_and_the_code_written_once(cfg, worker, exec_login, snapshots):
    exec_login.proc = LoginProc([
        "4242", "see https://example.com/not-this", "\x1b[1mOpen \x1b]8;;x\x07"
        f"{URL}\x1b[0m\r",
    ])
    action = login(cfg)
    worker.tick()
    shown = wait_for_result(cfg, action["id"])
    assert shown["status"] == "running"
    assert shown["result"]["url"] == URL and shown["result"]["awaiting"] == "code"
    assert shown["started_at"]
    actions.put_login_code(cfg.state_dir, action["id"], CODE)
    worker.join()
    state, got = where(cfg, action["id"])
    assert state == "done" and got["status"] == "done"
    assert exec_login.proc.writes == [CODE + "\r"]
    container, workdir, command, stdin = exec_login.opened[0]
    assert (container, workdir, command, stdin) == (
        "agent-cuenta1", "/", worker_mod.LOGIN_COMMAND, True)
    assert command[-4:] == ["script", "-qfec", "claude auth login", "/dev/null"][-4:]
    assert set(got["result"]) == {"logged_in_at", "released"} and got["result"]["released"] is False
    assert not os.path.exists(os.path.join(cfg.state_dir, "actions", "secrets", action["id"]))
    assert snapshots.calls == 1


def test_the_snapshot_after_a_login_carries_its_time(cfg, worker, exec_login, monkeypatch):
    seen = []
    monkeypatch.setattr(worker_mod.credentials, "write",
                        lambda cfg, **kw: seen.append(kw))
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert seen == [{"logins": {"cuenta1": got["result"]["logged_in_at"]}}]


def test_a_login_releases_the_account_from_auth_expired(cfg, worker, exec_login):
    state_machine.park(cfg.state_dir, "cuenta1", state_machine.AccountState.AUTH_EXPIRED)
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "done" and got["result"]["released"] is True
    assert state_machine.get_state(cfg.state_dir, "cuenta1") is state_machine.AccountState.IDLE


def test_a_login_on_an_account_holding_a_phase_is_refused(cfg, worker, exec_login):
    assert state_machine.claim(cfg.state_dir, "cuenta1", "T-9")
    action = login(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the account is running a phase; sign in again once it is idle"
    assert exec_login.opened == []


def test_an_unconfigured_account_is_refused(cfg, worker, exec_login, snapshots):
    action = login(cfg, "cuenta9")
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed" and got["error"] == "account cuenta9 is not configured"
    assert snapshots.calls == 1


def test_an_exec_that_cannot_start_is_a_fixed_failure(cfg, worker, monkeypatch):
    def boom(*a, **k):
        raise OSError(2, "no docker /secret/path")

    monkeypatch.setattr(worker_mod.docker_exec, "open_docker_exec", boom)
    action = login(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the login could not be started in agent-cuenta1"


def test_no_url_in_time_ends_the_login_and_kills_the_exec(cfg, worker, exec_login):
    exec_login.proc = LoginProc(["4242", "waiting"])
    action = login(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed"
    assert got["error"] == "the login printed no sign-in URL within 60 seconds"
    assert exec_login.killed == [("agent-cuenta1", 4242)] and exec_login.proc.killed


def test_the_stream_ending_before_a_url_names_the_exit(cfg, worker, exec_login):
    exec_login.proc = LoginProc(["4242", "nothing"], returncode=3)
    exec_login.proc.finish.set()
    exec_login.proc.returncode = 3
    action = login(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the login exited 3 before printing a sign-in URL"


def test_no_code_in_time_ends_the_login_and_kills_the_exec(cfg, worker, exec_login, monkeypatch):
    monkeypatch.setattr(worker_mod, "LOGIN_CODE_SECONDS", 0.3)
    action = login(cfg)
    run(worker)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "no code arrived within 10 minutes; start the sign-in again"
    assert exec_login.killed == [("agent-cuenta1", 4242)]
    assert exec_login.proc.writes == []


def test_the_login_exiting_while_waiting_for_the_code(cfg, worker, exec_login):
    exec_login.proc = LoginProc(["4242", URL], returncode=1, exit_on_code=False)
    action = login(cfg)
    worker.tick()
    wait_for_result(cfg, action["id"])
    exec_login.proc.returncode = 1
    exec_login.proc.finish.set()
    worker.join()
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the login exited 1 before the code was sent"


def test_a_nonzero_exit_after_the_code_fails(cfg, worker, exec_login):
    exec_login.proc = LoginProc(["4242", URL], returncode=2)
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed" and got["error"] == "the login exited 2 after the code was sent"


def test_exit_zero_with_a_stale_credentials_file_fails(cfg, worker, exec_login):
    exec_login.rewritten["modified_at"] = "2000-01-01T00:00:00+00:00"
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["error"] == (
        "the login exited 0 but the account's credentials file was not rewritten")


def test_exit_zero_with_no_credentials_file_fails(cfg, worker, exec_login):
    exec_login.rewritten.update(state="absent", modified_at=None)
    action = sign_in(cfg, worker, exec_login)
    assert where(cfg, action["id"])[1]["status"] == "failed"


def test_no_exit_after_the_code_ends_the_login_and_kills_the_exec(cfg, worker, exec_login):
    exec_login.proc = LoginProc(["4242", URL], exit_on_code=False)
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the login did not exit within 120 seconds after the code was sent"
    assert exec_login.killed == [("agent-cuenta1", 4242)]


def test_a_closed_pipe_is_a_fixed_failure(cfg, worker, exec_login):
    exec_login.proc.fail_write = True
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["error"] == "the code could not be sent to the login"


def test_a_cancel_while_awaiting_the_code_kills_the_exec(cfg, worker, exec_login):
    action = login(cfg)
    worker.tick()
    wait_for_result(cfg, action["id"])
    c = cancel(cfg, action["id"])
    worker.tick()
    worker.join()
    state, got = where(cfg, action["id"])
    assert state == "done" and got["status"] == "cancelled" and got["error"] is None
    assert exec_login.killed and exec_login.proc.killed
    cancel_got = where(cfg, c["id"])[1]
    assert cancel_got["status"] == "done" and cancel_got["result"]["target_status"] == "running"


def test_the_worker_stopping_interrupts_the_login(cfg, worker, exec_login):
    action = login(cfg)
    worker.tick()
    wait_for_result(cfg, action["id"])
    worker.stop()
    worker.join()
    got = where(cfg, action["id"])[1]
    assert got["status"] == "interrupted"
    assert got["error"] == "the worker stopped during the sign-in"
    assert exec_login.proc.killed


def test_the_code_is_in_no_record_log_or_state_file(cfg, worker, exec_login, caplog):
    caplog.set_level("DEBUG")
    sign_in(cfg, worker, exec_login)
    assert exec_login.proc.writes == [CODE + "\r"]
    for path, body in _everything_under(cfg.state_dir, cfg.hive_tasks_dir):
        assert CODE not in body, path
    assert CODE not in caplog.text


def test_an_unexpected_error_names_only_its_type(cfg, worker, exec_login, caplog, monkeypatch):
    def boom(account, previous=None):
        raise RuntimeError(f"leak {CODE}")

    monkeypatch.setattr(worker_mod.credentials, "account_row", boom)
    action = sign_in(cfg, worker, exec_login)
    got = where(cfg, action["id"])[1]
    assert got["status"] == "failed" and CODE not in str(got) and CODE not in caplog.text


def test_open_docker_exec_adds_dash_i_and_a_pipe_only_when_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_mod.docker_exec.subprocess, "Popen",
                        lambda argv, **kw: calls.append((argv, kw)))
    worker_mod.docker_exec.open_docker_exec("c", "/", ["x"])
    worker_mod.docker_exec.open_docker_exec("c", "/", ["x"], stdin=True)
    (plain, plain_kw), (piped, piped_kw) = calls
    assert plain == ["docker", "exec", "-w", "/", "c", "x"] and plain_kw["stdin"] is None
    assert piped == ["docker", "exec", "-i", "-w", "/", "c", "x"]
    assert piped_kw["stdin"] == subprocess.PIPE


# ---- the sweep and the keygen snapshot ---------------------------------------


def test_a_tick_sweeps_an_orphan_secret(cfg, worker):
    root = actions.ensure_dirs(cfg.state_dir)
    (root / actions.SECRETS).mkdir(exist_ok=True)
    orphan = root / actions.SECRETS / "a-20260101T000000-orphan"
    orphan.write_text("x")
    worker.tick()
    assert not orphan.exists()


def test_keygen_refreshes_the_snapshot(cfg, worker, snapshots):
    action = actions.create(cfg.state_dir, "keygen", project="nope", params={"replace": False})
    run(worker)
    assert where(cfg, action["id"])[1]["status"] == "failed"
    assert snapshots.calls == 1
