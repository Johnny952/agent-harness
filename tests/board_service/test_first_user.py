"""The first-user command (ADR 519)."""
import datetime as dt
import io
import os
import stat
import subprocess
import sys

import pytest

from board_service import first_user, signin

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(signin, "SCRYPT_N", 2**4)
    monkeypatch.setattr(signin, "SCRYPT_P", 1)


@pytest.fixture
def auth(tmp_path):
    return str(tmp_path / "auth")


def run(auth, argv=(), passwords=(PASSWORD, PASSWORD), env=None):
    answers = list(passwords)
    calls = []

    def read_password(prompt):
        calls.append(prompt)
        return answers.pop(0)

    out, err = io.StringIO(), io.StringIO()
    environ = {signin.AUTH_DIR_ENV: auth} if env is None else env
    rc = first_user.main(list(argv), environ=environ, read_password=read_password,
                         out=out, err=err)
    return rc, out.getvalue(), err.getvalue(), calls


def test_creates_the_owner_and_prints_the_secret_once(auth):
    rc, out, err, _ = run(auth)
    assert rc == 0
    users = signin._read(signin._users_path(auth))
    assert list(users) == ["owner"]
    assert users["owner"]["role"] == "owner"
    secret = users["owner"]["totp_secret"]
    assert f"secret={secret}" in out
    assert "ia-harness:owner" in out
    assert out.count(secret) == 2
    assert len(signin.totp_code(secret, dt.datetime.now(dt.timezone.utc))) == 6
    assert PASSWORD not in out + err
    assert signin.verify_password(PASSWORD, users["owner"]["password"])
    lines = [e for e in signin.read_audit(auth) if e["event"] == "user_created"]
    assert len(lines) == 1 and lines[0]["user"] == "owner"
    assert secret not in open(signin._audit_path(auth)).read()


def test_name_option(auth):
    rc, _, _, _ = run(auth, ["--name", "alice"])
    assert rc == 0
    assert list(signin._read(signin._users_path(auth))) == ["alice"]


def test_refuses_when_a_user_exists(auth):
    signin.create_user(auth, "bob", PASSWORD, "reviewer")
    before = open(signin._users_path(auth), "rb").read()
    rc, out, err, calls = run(auth)
    assert rc == 1
    assert out == "" and err
    assert calls == []
    assert open(signin._users_path(auth), "rb").read() == before


@pytest.mark.parametrize("env", [{}, {signin.AUTH_DIR_ENV: ""}])
def test_refuses_when_the_variable_is_unset_or_empty(tmp_path, env):
    rc, out, err, calls = run("", env=env)
    assert rc == 2
    assert out == "" and signin.AUTH_DIR_ENV in err
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_refuses_mismatched_passwords(auth):
    rc, out, err, _ = run(auth, passwords=(PASSWORD, PASSWORD + "x"))
    assert rc == 2
    assert out == "" and err
    assert not os.path.exists(signin._users_path(auth))


def test_refuses_a_short_password(auth):
    rc, out, err, _ = run(auth, passwords=("short", "short"))
    assert rc == 2
    assert out == "" and "password" in err
    assert not os.path.exists(signin._users_path(auth))


def test_file_modes(auth):
    run(auth)
    assert stat.S_IMODE(os.stat(auth).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(signin._users_path(auth)).st_mode) == 0o600


def test_create_user_first_flag(auth):
    signin.create_user(auth, "a-user", PASSWORD, "owner")
    with pytest.raises(signin.UserExists):
        signin.create_user(auth, "b-user", PASSWORD, "owner", first=True)
    signin.create_user(auth, "b-user", PASSWORD, "reviewer")
    assert set(signin._read(signin._users_path(auth))) == {"a-user", "b-user"}


def test_module_exits_2_with_the_variable_unset():
    env = {k: v for k, v in os.environ.items() if k != signin.AUTH_DIR_ENV}
    done = subprocess.run([sys.executable, "-m", "board_service.first_user"],
                          env=env, capture_output=True, text=True)
    assert done.returncode == 2
    assert signin.AUTH_DIR_ENV in done.stderr
