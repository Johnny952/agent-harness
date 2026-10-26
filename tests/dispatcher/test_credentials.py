"""The credentials snapshot (ADR 456 decision 4, ADR 462)."""

from __future__ import annotations

import builtins
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dispatcher import credentials, projects
from dispatcher.config import AccountConfig
from tests.dispatcher.test_dispatcher import _make_config

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ISO = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def cfg(tmp_path):
    c = _make_config(tmp_path)
    (tmp_path / "secrets").mkdir()
    c.git_credentials_path = str(tmp_path / "secrets" / "git-credentials")
    c.project_keys_dir = str(tmp_path / "keys")
    c.accounts = []
    return c


class Exec:
    def __init__(self):
        self.calls = []
        self.result = subprocess.CompletedProcess([], 0, "absent\n", "")

    def __call__(self, container, workdir, command, env=None, timeout=None):
        self.calls.append((container, command, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.fixture
def fake_exec(monkeypatch):
    fake = Exec()
    monkeypatch.setattr(credentials.docker_exec, "run_docker_exec", fake)
    return fake


@pytest.fixture
def fingerprints(monkeypatch):
    seen = []

    def fake(path):
        seen.append(path)
        return "256 SHA256:abc (ED25519)"

    monkeypatch.setattr(credentials.project_keys, "fingerprint", fake)
    return seen


def _done(out, code=0):
    return subprocess.CompletedProcess([], code, out, "")


def _token(cfg):
    return credentials.snapshot(cfg, now=NOW)["git_token"]


# ---- git_token ---------------------------------------------------------------

def test_token_present(cfg):
    p = Path(cfg.git_credentials_path)
    p.write_text("x")
    os.utime(p, (1767225600, 1767225600))
    assert _token(cfg) == {"path": str(p), "state": "present", "modified_at": ISO}


def test_token_empty(cfg):
    Path(cfg.git_credentials_path).write_text("")
    got = _token(cfg)
    assert got["state"] == "empty" and got["modified_at"] is not None


def test_token_absent_when_parent_exists(cfg):
    assert _token(cfg)["state"] == "absent"
    assert _token(cfg)["modified_at"] is None


def test_token_not_mounted_when_parent_missing(cfg, tmp_path):
    cfg.git_credentials_path = str(tmp_path / "nowhere" / "git-credentials")
    assert _token(cfg)["state"] == "not_mounted"


def test_token_directory(cfg):
    os.mkdir(cfg.git_credentials_path)
    got = _token(cfg)
    assert got["state"] == "directory" and got["modified_at"] is not None


# ---- deploy_keys -------------------------------------------------------------

def _make_key(cfg, slug, pub=True):
    path = credentials.project_keys.key_path(cfg.project_keys_dir, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE")
    if pub:
        path.with_name(path.name + ".pub").write_text("ssh-ed25519 AAA")
    return path


def _record(cfg, slug):
    root = credentials.context_transfer.hive_root(cfg.hive_tasks_dir)
    d = Path(projects.projects_dir(root))
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.json").write_text("{}")


def _keys(cfg):
    return credentials.snapshot(cfg, now=NOW)["deploy_keys"]


def test_key_present_with_fingerprint(cfg, fingerprints):
    path = _make_key(cfg, "alpha")
    (row,) = _keys(cfg)
    assert row["state"] == "present" and row["fingerprint"] == "256 SHA256:abc (ED25519)"
    assert row["modified_at"] is not None
    assert fingerprints == [path.with_name(path.name + ".pub")]


def test_key_without_pub_has_null_fingerprint(cfg, fingerprints):
    _make_key(cfg, "alpha", pub=False)
    (row,) = _keys(cfg)
    assert row["state"] == "present" and row["fingerprint"] is None
    assert fingerprints == []


def test_key_fingerprint_failure_is_null(cfg, monkeypatch):
    _make_key(cfg, "alpha")

    def boom(path):
        raise subprocess.CalledProcessError(1, "ssh-keygen")

    monkeypatch.setattr(credentials.project_keys, "fingerprint", boom)
    (row,) = _keys(cfg)
    assert row["state"] == "present" and row["fingerprint"] is None


def test_record_without_key_is_absent(cfg):
    _record(cfg, "alpha")
    (row,) = _keys(cfg)
    assert row == {"project": "alpha", "state": "absent", "modified_at": None,
                   "fingerprint": None}


def test_key_dir_without_record_is_a_row_and_non_slug_ignored(cfg, fingerprints):
    _make_key(cfg, "beta")
    os.makedirs(os.path.join(cfg.project_keys_dir, ".hidden"))
    (row,) = _keys(cfg)
    assert row["project"] == "beta" and row["state"] == "present"


def test_missing_keys_dir_gives_rows_from_records_sorted(cfg):
    _record(cfg, "zeta")
    _record(cfg, "alpha")
    assert [r["project"] for r in _keys(cfg)] == ["alpha", "zeta"]


# ---- accounts ----------------------------------------------------------------

@pytest.fixture
def one_account(cfg):
    cfg.accounts = [AccountConfig(name="cuenta1", container="agent-cuenta1")]
    return cfg


def _account(cfg, **kw):
    return credentials.snapshot(cfg, now=NOW, **kw)["accounts"][0]


def test_account_present(one_account, fake_exec):
    fake_exec.result = _done("123 1767225600\n")
    row = _account(one_account)
    assert row["state"] == "present" and row["modified_at"] == ISO
    container, command, timeout = fake_exec.calls[0]
    assert container == "agent-cuenta1" and timeout == credentials.STAT_TIMEOUT_SECONDS
    assert credentials.CLAUDE_CREDENTIALS in command


def test_account_zero_size_is_absent(one_account, fake_exec):
    fake_exec.result = _done("0 1767225600\n")
    row = _account(one_account)
    assert row["state"] == "absent" and row["modified_at"] is None


def test_account_absent(one_account, fake_exec):
    fake_exec.result = _done("absent\n")
    assert _account(one_account)["state"] == "absent"


@pytest.mark.parametrize("result", [
    _done("", 1),
    subprocess.TimeoutExpired("docker", 15),
    OSError("no docker"),
    _done("what is this\n"),
    _done("12 x\n"),
])
def test_account_unreachable(one_account, fake_exec, result):
    fake_exec.result = result
    row = _account(one_account)
    assert row["state"] == "unreachable" and row["modified_at"] is None


# ---- logged_in_at ------------------------------------------------------------

def test_logged_in_at_is_carried_and_replaced(one_account, fake_exec):
    cfg = one_account
    credentials.write(cfg, logins={"cuenta1": "2025-12-31T00:00:00+00:00"}, now=NOW)
    got = credentials.write(cfg, now=NOW)
    assert got["accounts"][0]["logged_in_at"] == "2025-12-31T00:00:00+00:00"
    got = credentials.write(cfg, logins={"cuenta1": ISO}, now=NOW)
    assert got["accounts"][0]["logged_in_at"] == ISO
    assert credentials.read(cfg.state_dir) == got


def test_dropped_account_row_is_gone(one_account, fake_exec):
    credentials.write(one_account, logins={"cuenta1": ISO}, now=NOW)
    one_account.accounts = [AccountConfig(name="cuenta2", container="agent-cuenta2")]
    got = credentials.write(one_account, now=NOW)
    assert [r["account"] for r in got["accounts"]] == ["cuenta2"]
    assert got["accounts"][0]["logged_in_at"] is None


def test_non_string_previous_logged_in_at_is_null(one_account, fake_exec):
    previous = {"accounts": [{"account": "cuenta1", "logged_in_at": 5}]}
    assert _account(one_account, previous=previous)["logged_in_at"] is None


def test_unreadable_previous_file_still_writes(one_account, fake_exec):
    os.makedirs(one_account.state_dir)
    Path(one_account.state_dir, credentials.SNAPSHOT).write_text("{not json")
    got = credentials.write(one_account, now=NOW)
    assert got["accounts"][0]["logged_in_at"] is None
    assert credentials.read(one_account.state_dir) == got


# ---- read --------------------------------------------------------------------

def test_read_missing_is_none(tmp_path):
    assert credentials.read(tmp_path) is None


def test_read_refuses_bad_json_and_non_object(tmp_path):
    p = tmp_path / credentials.SNAPSHOT
    p.write_text("{nope")
    with pytest.raises(credentials.SnapshotError):
        credentials.read(tmp_path)
    p.write_text(json.dumps([1]))
    with pytest.raises(credentials.SnapshotError):
        credentials.read(tmp_path)


# ---- no secret is opened -----------------------------------------------------

def test_no_secret_file_is_ever_opened(cfg, fake_exec, fingerprints, monkeypatch):
    token = Path(cfg.git_credentials_path)
    token.write_text("SECRET")
    private = _make_key(cfg, "alpha")
    secrets = {os.path.realpath(p) for p in (token, private)}
    for p in (token, private):
        os.chmod(p, 0o000)

    def guard(real):
        def wrapper(file, *a, **kw):
            if isinstance(file, (str, bytes, os.PathLike)) and \
                    os.path.realpath(os.fsdecode(file)) in secrets:
                raise AssertionError(f"opened a secret: {file}")
            return real(file, *a, **kw)
        return wrapper

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    monkeypatch.setattr(os, "open", guard(os.open))
    got = credentials.write(cfg, now=NOW)
    assert got["git_token"]["state"] == "present"
    assert got["deploy_keys"][0]["state"] == "present"
    assert fingerprints[0].name == "alpha.pub" or str(fingerprints[0]).endswith(".pub")
