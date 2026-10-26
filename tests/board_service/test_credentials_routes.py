"""The board service's three credential routes (ADR 456 decision 3, ADR 468).

Own config: its `state_dir` is under tmp_path, never the real one.
"""
import json
import stat
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_actions_routes import AUTH, TOKEN, cfg, client, hive, pending, state  # noqa: F401
from tests.board_service.test_signin import (  # noqa: F401
    auth_dir, clock, fast_scrypt, secrets_, session,
)

GOOD = "ghp_" + "A" * 36
CODE = "Zx9-code-ABC123"
GIT = "/api/credentials/git-token"
LOGIN = "/api/accounts/cuenta1/login"
NOT_ASKING = "a-20261019T120000000000Z-abcd"


def git(client, body=None, headers=AUTH):
    return client.post(GIT, json={"token": GOOD} if body is None else body, headers=headers)


def login(client, body=None, headers=AUTH, path=LOGIN):
    return client.post(path, json={} if body is None else body, headers=headers)


def send_code(client, action_id, body=None, headers=AUTH):
    return client.post(
        f"/api/actions/{action_id}/login-code",
        json={"code": CODE} if body is None else body,
        headers=headers,
    )


def secret_files(state: Path) -> list[Path]:
    root = state / "actions" / "secrets"
    return sorted(root.iterdir()) if root.exists() else []


def awaiting(client, state, awaiting="code") -> str:
    """A claimed `account_login` whose worker is waiting for the code."""
    action_id = login(client).get_json()["id"]
    record = actions.claim(state, action_id)
    record["result"] = {"url": "https://claude.ai/x", "awaiting": awaiting, "url_at": "t"}
    actions.rewrite(state, record)
    return action_id


def nowhere(state: Path, *secrets: str) -> None:
    """No secret is in any record of the queue, on disk or read back."""
    text = json.dumps(actions.read_all(state)[0], default=str)
    for path in (state / "actions").rglob("*.json"):
        text += path.read_text()
    for secret in secrets:
        assert secret not in text


ROUTES = [
    ("post", GIT, {"token": GOOD}),
    ("post", LOGIN, {}),
    ("post", f"/api/actions/{NOT_ASKING}/login-code", {"code": CODE}),
]


# --- the guards, each route --------------------------------------------------------------


@pytest.mark.parametrize("_, path, body", ROUTES)
def test_the_guards_come_first(_, path, body, client, state):
    assert client.post(path, json=body, headers={**AUTH, "Origin": "http://x"}).status_code == 403
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"Authorization": "Bearer no"}).status_code == 401
    assert client.post(path + "?a=1", json=body, headers=AUTH).status_code == 400
    assert client.post(path, json=[body], headers=AUTH).status_code == 400
    assert pending(state) == [] and secret_files(state) == []


@pytest.mark.parametrize("_, path, body", ROUTES)
def test_no_token_turns_the_route_off(_, path, body, cfg, state, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    res = create_app(str(cfg)).test_client().post(path, json=body, headers=AUTH)
    assert res.status_code == 503
    assert pending(state) == [] and secret_files(state) == []


# --- git token ---------------------------------------------------------------------------


def test_a_git_token_is_queued_with_its_secret_file(client, state):
    res = git(client)
    assert res.status_code == 201
    record = res.get_json()
    assert (record["kind"], record["project"], record["task_id"]) == ("git_token", None, None)
    assert record["params"] == {"username": "x-access-token"}
    assert [p.stem for p in pending(state)] == [record["id"]]
    (path,) = secret_files(state)
    assert path.name == record["id"] and path.read_text() == GOOD
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert GOOD not in res.get_data(as_text=True)
    nowhere(state, GOOD)


def test_a_given_username_is_kept(client):
    assert git(client, {"token": GOOD, "username": "octo-cat"}).get_json()["params"] == {
        "username": "octo-cat"
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": "nope"},
        {"token": 12345},
        {"token": GOOD, "username": "bad name!"},
        {"token": GOOD, "username": 7},
        {"token": GOOD, "extra": 1},
    ],
)
def test_a_bad_git_token_body_is_400_and_leaves_nothing(body, client, state):
    res = git(client, body)
    assert res.status_code == 400
    assert pending(state) == [] and secret_files(state) == []
    assert GOOD not in res.get_data(as_text=True)


def test_a_second_git_token_is_409_and_keeps_the_first_secret(client, state):
    first = git(client).get_json()
    other = "ghp_" + "B" * 36
    res = git(client, {"token": other})
    assert res.status_code == 409
    assert [p.stem for p in pending(state)] == [first["id"]]
    (path,) = secret_files(state)
    assert path.read_text() == GOOD
    assert GOOD not in res.get_data(as_text=True) and other not in res.get_data(as_text=True)
    nowhere(state, GOOD, other)


# --- account login -----------------------------------------------------------------------


def test_a_login_is_queued_without_a_secret(client, state):
    res = login(client)
    assert res.status_code == 201
    record = res.get_json()
    assert (record["kind"], record["params"]) == ("account_login", {"account": "cuenta1"})
    assert [p.stem for p in pending(state)] == [record["id"]]
    assert secret_files(state) == []


def test_an_unknown_account_is_404_and_queues_nothing(client, state):
    assert login(client, path="/api/accounts/nobody/login").status_code == 404
    assert pending(state) == []


def test_a_login_body_must_be_empty(client, state):
    assert login(client, {"x": 1}).status_code == 400
    assert pending(state) == []


def test_a_second_login_for_the_account_is_409(client, state):
    login(client)
    assert login(client).status_code == 409
    assert len(pending(state)) == 1


# --- login code --------------------------------------------------------------------------


def test_a_code_is_written_while_the_login_awaits_it(client, state):
    action_id = awaiting(client, state)
    res = send_code(client, action_id)
    assert res.status_code == 201
    assert res.get_json()["id"] == action_id
    (path,) = secret_files(state)
    assert path.name == action_id and path.read_text() == CODE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert CODE not in res.get_data(as_text=True)
    nowhere(state, CODE)


def test_when_the_action_has_left_the_queue_the_answer_is_the_id(client, state, monkeypatch):
    action_id = awaiting(client, state)
    monkeypatch.setattr(actions, "find", lambda *a: None)
    res = send_code(client, action_id)
    assert (res.status_code, res.get_json()) == (201, {"id": action_id})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": ""},
        {"code": "has space"},
        {"code": "x" * 513},
        {"code": 123456},
        {"code": CODE, "extra": 1},
    ],
)
def test_a_bad_code_is_400_and_writes_nothing(body, client, state):
    action_id = awaiting(client, state)
    res = send_code(client, action_id, body)
    assert res.status_code == 400
    assert secret_files(state) == []
    assert CODE not in res.get_data(as_text=True)


def test_a_code_for_a_pending_login_is_409(client, state):
    action_id = login(client).get_json()["id"]
    assert send_code(client, action_id).status_code == 409
    assert secret_files(state) == []


def test_a_code_for_a_login_not_awaiting_is_409(client, state):
    action_id = awaiting(client, state, awaiting=None)
    assert send_code(client, action_id).status_code == 409
    assert secret_files(state) == []


def test_a_code_for_a_claimed_git_token_is_409(client, state):
    action_id = git(client).get_json()["id"]
    actions.claim(state, action_id)
    res = send_code(client, action_id)
    assert res.status_code == 409
    assert [p.read_text() for p in secret_files(state)] == [GOOD]
    assert CODE not in res.get_data(as_text=True)


@pytest.mark.parametrize("action_id", [NOT_ASKING, "not-an-id", "a-1"])
def test_a_code_for_an_unknown_or_malformed_id_is_409(action_id, client, state):
    assert send_code(client, action_id).status_code == 409
    assert secret_files(state) == []


def test_a_second_code_is_409_and_keeps_the_first(client, state):
    action_id = awaiting(client, state)
    send_code(client, action_id)
    res = send_code(client, action_id, {"code": "Other-code-1"})
    assert res.status_code == 409
    assert [p.read_text() for p in secret_files(state)] == [CODE]
    assert "Other-code-1" not in res.get_data(as_text=True)


# --- owner only --------------------------------------------------------------------------


@pytest.fixture
def signed(cfg, auth_dir, secrets_, monkeypatch):  # noqa: F811
    monkeypatch.setenv("BOARD_AUTH_DIR", str(auth_dir))
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", TOKEN)
    # No cookie jar: the cookie is Secure and the test client speaks http.
    return create_app(str(cfg)).test_client(use_cookies=False)


@pytest.mark.parametrize("_, path, body", ROUTES)
def test_a_reviewer_may_not_use_the_routes(_, path, body, signed, secrets_, clock, state):
    _, csrf, _ = session(signed, secrets_, clock, user="rev")
    res = signed.post(path, json=body, headers={**csrf, **AUTH})
    assert res.status_code == 403
    assert res.get_json() == {"error": "this needs the owner's role"}
    assert pending(state) == [] and secret_files(state) == []


def test_an_owner_queues_as_themselves(signed, secrets_, clock, state):
    _, csrf, _ = session(signed, secrets_, clock)
    res = signed.post(GIT, json={"token": GOOD}, headers={**csrf, **AUTH})
    assert res.status_code == 201
    assert res.get_json()["actor"] == "ana"
    assert GOOD not in res.get_data(as_text=True)
