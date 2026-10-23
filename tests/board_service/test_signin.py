"""The board service's sign-in (ADR 324): password, TOTP, sessions, CSRF, roles, audit.

State is under tmp_path, never the shared `cfg` fixture's real state dir.
"""
import datetime as dt
import json
import os
import stat
from pathlib import Path

import pytest

from board_service import signin
from board_service.app import create_app
from tests.board_service.test_app import CONFIG_YAML, task

PASSWORD = "correct horse battery"
T0 = dt.datetime(2026, 10, 22, 12, 0, 5, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(signin, "SCRYPT_N", 2**4)
    monkeypatch.setattr(signin, "SCRYPT_P", 1)


@pytest.fixture
def clock(monkeypatch):
    box = {"now": T0}
    monkeypatch.setattr(signin, "_now", lambda: box["now"])
    return box


def advance(clock, seconds: int) -> None:
    clock["now"] = clock["now"] + dt.timedelta(seconds=seconds)


@pytest.fixture
def auth_dir(tmp_path: Path) -> Path:
    return tmp_path / "auth"


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def cfg(tmp_path: Path, hive: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(tmp_path / "state")))
    (tmp_path / "config.d").mkdir()
    return path


@pytest.fixture
def secrets_(auth_dir, clock):
    """TOTP secrets by user name; an owner `ana` and a reviewer `rev`."""
    return {
        "ana": signin.create_user(str(auth_dir), "ana", PASSWORD, "owner"),
        "rev": signin.create_user(str(auth_dir), "rev", PASSWORD, "reviewer"),
    }


@pytest.fixture
def client(cfg, auth_dir, secrets_, hive, monkeypatch):
    monkeypatch.setenv("BOARD_AUTH_DIR", str(auth_dir))
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", "s3cret")
    task(hive, "T-001")
    # No cookie jar: the cookie is Secure and the test client speaks http.
    return create_app(str(cfg)).test_client(use_cookies=False)


def code_for(secrets_, clock, user="ana", offset=0):
    return signin.totp_code(secrets_[user], clock["now"] + dt.timedelta(seconds=30 * offset))


def sign_in(client, secrets_, clock, user="ana", password=PASSWORD, code=None, headers=None):
    code = code_for(secrets_, clock, user) if code is None else code
    return client.post(
        "/api/session", json={"name": user, "password": password, "code": code}, headers=headers
    )


def cookie_of(res) -> str:
    header = [h for h in res.headers.getlist("Set-Cookie") if h.startswith(signin.COOKIE + "=")][0]
    return header.split(";")[0].split("=", 1)[1]


def session(client, secrets_, clock, user="ana", step=True):
    """Sign in and return (cookie headers, csrf headers, body). `step` moves on a TOTP step first."""
    res = sign_in(client, secrets_, clock, user)
    assert res.status_code == 200, res.get_data(as_text=True)
    cookie = {"Cookie": f"{signin.COOKIE}={cookie_of(res)}"}
    return cookie, {**cookie, signin.CSRF_HEADER: res.get_json()["csrf_token"]}, res.get_json()


def log(auth_dir):
    return signin.read_audit(str(auth_dir))


def events(auth_dir):
    return [(line["event"], line.get("reason")) for line in log(auth_dir)]


# --- a good sign-in -----------------------------------------------------------


def test_a_good_sign_in_serves_the_view_and_sets_the_cookie(client, secrets_, clock, auth_dir):
    res = sign_in(client, secrets_, clock)
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"user", "role", "csrf_token", "expires_at"}
    assert (body["user"], body["role"]) == ("ana", "owner")
    header = [h for h in res.headers.getlist("Set-Cookie") if h.startswith(signin.COOKIE)][0]
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", f"Max-Age={signin.SESSION_LIFETIME}"):
        assert attr in header
    assert events(auth_dir) == [("sign_in", None)]
    assert log(auth_dir)[0]["user"] == "ana"


def test_the_session_reads_back_and_the_stored_hash_carries_its_parameters(client, secrets_, clock, auth_dir):
    cookie, _, body = session(client, secrets_, clock)
    assert client.get("/api/session", headers=cookie).get_json() == body
    stored = json.loads((auth_dir / "users.json").read_text())["ana"]["password"]
    assert stored.startswith(f"scrypt${signin.SCRYPT_N}${signin.SCRYPT_R}${signin.SCRYPT_P}$")


def test_the_default_scrypt_parameters(monkeypatch):
    monkeypatch.undo()
    assert (signin.SCRYPT_N, signin.SCRYPT_R, signin.SCRYPT_P) == (2**14, 8, 5)
    assert signin.hash_password("some long password").startswith("scrypt$16384$8$5$")


def test_the_store_is_private(client, secrets_, clock, auth_dir):
    sign_in(client, secrets_, clock)
    assert stat.S_IMODE(os.stat(auth_dir).st_mode) == 0o700
    for name in ("users.json", "sessions.json", "audit.jsonl"):
        assert stat.S_IMODE(os.stat(auth_dir / name).st_mode) == 0o600


# --- failures -------------------------------------------------------------------


def test_a_wrong_password_and_an_unknown_name_answer_the_same(client, secrets_, clock, auth_dir):
    wrong = sign_in(client, secrets_, clock, password="not the password")
    unknown = client.post("/api/session", json={"name": "nobody", "password": PASSWORD, "code": "123456"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json() == {"error": "sign-in failed"}
    lines = log(auth_dir)
    assert [(l["event"], l["reason"], l["user"]) for l in lines] == [
        ("sign_in_failed", "wrong_password", "ana"),
        ("sign_in_failed", "unknown_user", None),
    ]


def test_a_wrong_code_is_refused_and_logged(client, secrets_, clock, auth_dir):
    res = sign_in(client, secrets_, clock, code="000000" if code_for(secrets_, clock) != "000000" else "111111")
    assert res.status_code == 401
    assert res.get_json() == {"error": "sign-in failed"}
    assert events(auth_dir) == [("sign_in_failed", "wrong_code")]


def test_a_replayed_code_is_refused(client, secrets_, clock, auth_dir):
    code = code_for(secrets_, clock)
    assert sign_in(client, secrets_, clock, code=code).status_code == 200
    advance(clock, 5)
    again = sign_in(client, secrets_, clock, code=code)
    assert again.status_code == 401
    assert events(auth_dir) == [("sign_in", None), ("sign_in_failed", "replayed_code")]
    # A code from before the stored step is a replay too; the next one is fine.
    assert sign_in(client, secrets_, clock, code=code_for(secrets_, clock, offset=-1)).status_code == 401
    advance(clock, 60)
    assert sign_in(client, secrets_, clock).status_code == 200


def test_a_code_one_step_off_is_accepted_and_three_are_not(client, secrets_, clock):
    assert sign_in(client, secrets_, clock, code=code_for(secrets_, clock, offset=1)).status_code == 200
    advance(clock, 600)
    assert sign_in(client, secrets_, clock, code=code_for(secrets_, clock, offset=3)).status_code == 401


def test_five_failures_lock_the_user_until_the_wait_is_over(client, secrets_, clock, auth_dir):
    for _ in range(signin.MAX_FAILURES):
        assert sign_in(client, secrets_, clock, password="wrong wrong wrong").status_code == 401
    locked = sign_in(client, secrets_, clock)
    assert locked.status_code == 429
    assert "try again after" in locked.get_json()["error"]
    assert events(auth_dir)[-1] == ("sign_in_failed", "locked")
    advance(clock, signin.LOCK_SECONDS - 1)
    assert sign_in(client, secrets_, clock).status_code == 429
    advance(clock, 2)
    assert sign_in(client, secrets_, clock).status_code == 200


# --- failures per address (ADR 381) ----------------------------------------------

ADDR = {"X-Forwarded-For": "203.0.113.7"}


def spray(client, count, headers=ADDR, prefix="ghost"):
    """`count` failed sign-ins, each with a different unknown name."""
    for i in range(count):
        res = client.post(
            "/api/session",
            json={"name": f"{prefix}{i}", "password": PASSWORD, "code": "123456"},
            headers=headers,
        )
        assert res.status_code == 401


def test_twenty_unknown_names_lock_the_address(client, secrets_, clock, auth_dir):
    assert signin.MAX_ADDRESS_FAILURES == 20
    spray(client, 20)
    res = sign_in(client, secrets_, clock, headers=ADDR)
    assert res.status_code == 429
    assert "try again after" in res.get_json()["error"]
    last = log(auth_dir)[-1]
    assert (last["reason"], last["user"], last["forwarded_for"]) == ("address_locked", "ana", "203.0.113.7")


def test_wrong_passwords_over_two_users_count_toward_the_address(client, secrets_, clock):
    for i in range(20):
        sign_in(client, secrets_, clock, user="ana" if i % 2 else "rev", password="wrong wrong wrong", headers=ADDR)
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 429


def test_another_address_and_no_header_are_unaffected(client, secrets_, clock):
    spray(client, 20)
    other = {"X-Forwarded-For": "198.51.100.1"}
    assert sign_in(client, secrets_, clock, headers=other).status_code == 200
    advance(clock, 60)
    assert sign_in(client, secrets_, clock).status_code == 200
    spray(client, 25, headers=None, prefix="bare")
    advance(clock, 60)
    assert sign_in(client, secrets_, clock).status_code == 200


def test_the_first_hop_is_the_key(client, secrets_, clock):
    spray(client, 20, headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 429


def test_the_address_lock_ends_with_its_time(client, secrets_, clock):
    spray(client, 20)
    advance(clock, signin.LOCK_SECONDS - 1)
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 429
    advance(clock, 2)
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 200


def test_failures_spread_past_the_window_do_not_lock(client, secrets_, clock):
    spray(client, 19)
    advance(clock, signin.ADDRESS_WINDOW + 1)
    spray(client, 19, prefix="late")
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 200


def test_a_good_sign_in_does_not_reset_the_address_count(client, secrets_, clock):
    spray(client, 19)
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 200
    spray(client, 1, prefix="one-more")
    advance(clock, 60)
    assert sign_in(client, secrets_, clock, headers=ADDR).status_code == 429


def test_a_locked_address_answers_alike_for_a_known_and_an_unknown_name(client, secrets_, clock):
    spray(client, 20)
    known = sign_in(client, secrets_, clock, headers=ADDR)
    unknown = client.post(
        "/api/session", json={"name": "nobody", "password": PASSWORD, "code": "123456"}, headers=ADDR
    )
    assert known.status_code == unknown.status_code == 429
    assert known.get_json() == unknown.get_json()


def test_the_address_store_is_private_and_holds_no_secret(client, secrets_, clock, auth_dir):
    spray(client, 1)
    path = auth_dir / "addresses.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    text = path.read_text()
    assert PASSWORD not in text and "123456" not in text
    assert set(json.loads(text)["203.0.113.7"]) == {"failures", "since", "locked_until"}


def test_an_expired_address_is_pruned_on_the_next_write(client, secrets_, clock, auth_dir):
    spray(client, 1)
    advance(clock, signin.ADDRESS_WINDOW + 1)
    spray(client, 1, headers={"X-Forwarded-For": "198.51.100.1"})
    assert set(json.loads((auth_dir / "addresses.json").read_text())) == {"198.51.100.1"}


def test_a_good_sign_in_resets_the_failure_count(client, secrets_, clock):
    for _ in range(signin.MAX_FAILURES - 1):
        sign_in(client, secrets_, clock, password="wrong wrong wrong")
    assert sign_in(client, secrets_, clock).status_code == 200
    advance(clock, 60)
    for _ in range(signin.MAX_FAILURES - 1):
        sign_in(client, secrets_, clock, password="wrong wrong wrong")
    assert sign_in(client, secrets_, clock).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"name": "ana", "password": PASSWORD},
        {"name": "ana", "password": PASSWORD, "code": 123456},
        {"name": "ana", "password": PASSWORD, "code": "123456", "extra": "x"},
        [],
    ],
)
def test_a_malformed_sign_in_body_is_400(client, body):
    assert client.post("/api/session", json=body).status_code == 400


def test_the_session_routes_refuse_a_browser_origin_and_a_query(client, secrets_, clock):
    assert client.get("/api/session", headers={"Origin": "https://x.example"}).status_code == 403
    assert client.get("/api/session?x=1").status_code == 400


# --- sessions ---------------------------------------------------------------------


def test_an_idle_session_expires(client, secrets_, clock, auth_dir):
    cookie, _, _ = session(client, secrets_, clock)
    advance(clock, signin.SESSION_IDLE - 1)
    assert client.get("/api/session", headers=cookie).status_code == 200
    advance(clock, signin.SESSION_IDLE + 1)
    res = client.get("/api/session", headers=cookie)
    assert res.status_code == 401
    assert "Expires=Thu, 01 Jan 1970" in "; ".join(res.headers.getlist("Set-Cookie"))
    assert events(auth_dir)[-1] == ("session_refused", "expired")
    assert client.get("/api/session", headers=cookie).status_code == 401


def test_a_session_ends_at_its_fixed_lifetime_whatever_the_activity(client, secrets_, clock, auth_dir):
    cookie, _, _ = session(client, secrets_, clock)
    for _ in range(signin.SESSION_LIFETIME // 3000):
        advance(clock, 3000)
        assert client.get("/api/session", headers=cookie).status_code == 200
    advance(clock, 3000)
    assert client.get("/api/session", headers=cookie).status_code == 401
    assert events(auth_dir)[-1] == ("session_refused", "expired")


def test_an_unknown_cookie_is_refused(client, auth_dir):
    res = client.get("/api/tasks", headers={"Cookie": f"{signin.COOKIE}=nope"})
    assert res.status_code == 401
    assert events(auth_dir) == [("session_refused", "unknown")]
    assert log(auth_dir)[0]["user"] is None


# --- CSRF, roles, sign-out -------------------------------------------------------------


def comment(client, headers):
    return client.post("/api/tasks/T-001/comments", json={"text": "hello"}, headers=headers)


def test_a_write_without_the_csrf_token_is_refused(client, secrets_, clock, auth_dir):
    cookie, csrf, _ = session(client, secrets_, clock)
    res = comment(client, cookie)
    assert res.status_code == 403
    assert res.get_json() == {"error": "a CSRF token is required"}
    wrong = comment(client, {**cookie, signin.CSRF_HEADER: "not-the-token"})
    assert wrong.status_code == 403
    assert events(auth_dir)[1:] == [("csrf_refused", "missing"), ("csrf_refused", "mismatch")]
    assert log(auth_dir)[1]["path"] == "/api/tasks/T-001/comments"
    assert comment(client, csrf).status_code == 201
    last = log(auth_dir)[-1]
    assert (last["event"], last["user"], last["method"]) == ("action", "ana", "POST")


def test_a_read_needs_no_csrf_token(client, secrets_, clock):
    cookie, _, _ = session(client, secrets_, clock)
    assert client.get("/api/session", headers=cookie).status_code == 200


def test_a_reviewer_may_comment_and_read_but_not_edit(client, secrets_, clock, auth_dir):
    _, csrf, body = session(client, secrets_, clock, user="rev")
    assert body["role"] == "reviewer"
    assert comment(client, csrf).status_code == 201
    res = client.patch("/api/tasks/T-001", json={"title": "x"}, headers=csrf)
    assert res.status_code == 403
    assert res.get_json() == {"error": "this needs the owner's role"}
    assert events(auth_dir)[-1] == ("role_refused", None)
    assert client.get("/api/session", headers=csrf).status_code == 200


def test_an_owner_may_edit(client, secrets_, clock):
    _, csrf, _ = session(client, secrets_, clock)
    assert client.patch("/api/tasks/T-001", json={"title": "x"}, headers=csrf).status_code != 403


def test_sign_out_ends_the_session(client, secrets_, clock, auth_dir):
    cookie, csrf, _ = session(client, secrets_, clock)
    assert client.delete("/api/session", headers=cookie).status_code == 403
    res = client.delete("/api/session", headers=csrf)
    assert res.status_code == 204
    assert client.get("/api/session", headers=cookie).status_code == 401
    assert [e for e in events(auth_dir) if e[0] != "session_refused"] == [
        ("sign_in", None), ("csrf_refused", "missing"), ("sign_out", None),
    ]


# --- the machine path and sign-in off ---------------------------------------------------


def test_a_request_with_no_cookie_behaves_as_before(client):
    bearer = {"Authorization": "Bearer s3cret"}
    assert client.post("/api/tasks/T-1/start", json={"project": "demo"}, headers=bearer).status_code != 401
    assert client.post("/api/tasks/T-001/comments", json={"text": "hi"}).status_code == 201
    assert client.post("/api/tasks/T-1/start", json={}, headers={"Authorization": "Bearer no"}).status_code == 401


def test_with_no_auth_dir_the_routes_say_off_and_a_cookie_is_ignored(cfg, hive, monkeypatch):
    monkeypatch.delenv("BOARD_AUTH_DIR", raising=False)
    task(hive, "T-001")
    client = create_app(str(cfg)).test_client(use_cookies=False)
    res = client.post("/api/session", json={"name": "a", "password": "b", "code": "c"})
    assert res.status_code == 503
    assert "BOARD_AUTH_DIR" in res.get_json()["error"]
    assert client.get("/api/session").status_code == 503
    cookie = {"Cookie": f"{signin.COOKIE}=whatever"}
    assert client.post("/api/tasks/T-001/comments", json={"text": "hi"}, headers=cookie).status_code == 201


def test_building_the_app_does_not_create_the_auth_dir(cfg, tmp_path, monkeypatch):
    target = tmp_path / "never"
    monkeypatch.setenv("BOARD_AUTH_DIR", str(target))
    create_app(str(cfg))
    assert not target.exists()


# --- create_user -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, password, role",
    [
        ("Ana", PASSWORD, "owner"),
        ("", PASSWORD, "owner"),
        ("-ana", PASSWORD, "owner"),
        ("bob", PASSWORD, "admin"),
        ("bob", "short", "owner"),
        ("bob", "x" * 1025, "owner"),
        ("ana", PASSWORD, "owner"),  # exists
    ],
)
def test_create_user_refuses(auth_dir, secrets_, name, password, role):
    with pytest.raises(ValueError):
        signin.create_user(str(auth_dir), name, password, role)


def test_verify_password_refuses_a_malformed_hash():
    assert signin.verify_password("x", "garbage") is False
    assert signin.verify_password("x", "bcrypt$1$2$3$4$5") is False


def test_the_audit_reader_skips_a_line_that_will_not_parse(auth_dir, secrets_):
    with open(auth_dir / "audit.jsonl", "a") as handle:
        handle.write("not json\n[1]\n")
    signin.audit(str(auth_dir), "sign_out", "ana")
    assert [l["event"] for l in log(auth_dir)] == ["sign_out"]


# --- nothing secret leaves ----------------------------------------------------------------


def test_no_response_or_audit_line_holds_a_secret(client, secrets_, clock, auth_dir):
    bodies = []

    def see(res):
        bodies.append(res.get_data(as_text=True) + str(res.headers.getlist("Set-Cookie")) * 0)
        return res

    code = code_for(secrets_, clock)
    see(sign_in(client, secrets_, clock, password="the wrong password!!"))
    see(sign_in(client, secrets_, clock, code="999999"))
    good = see(sign_in(client, secrets_, clock, code=code))
    sid = cookie_of(good)
    cookie = {"Cookie": f"{signin.COOKIE}={sid}"}
    csrf = {**cookie, signin.CSRF_HEADER: good.get_json()["csrf_token"]}
    advance(clock, 40)
    see(sign_in(client, secrets_, clock, code=code))  # replay
    see(client.get("/api/session", headers=cookie))
    see(comment(client, cookie))
    see(comment(client, csrf))
    see(client.delete("/api/session", headers=csrf))
    see(client.get("/api/session", headers=cookie))

    users = json.loads((auth_dir / "users.json").read_text())
    stored_hash = users["ana"]["password"]
    forbidden = [PASSWORD, "the wrong password!!", code, "999999", stored_hash, secrets_["ana"], sid]
    audit_text = (auth_dir / "audit.jsonl").read_text()
    for text in bodies + [audit_text]:
        for secret in forbidden:
            assert secret not in text
    for name in ("users.json", "sessions.json"):
        assert sid not in (auth_dir / name).read_text()
    assert PASSWORD not in (auth_dir / "users.json").read_text()
