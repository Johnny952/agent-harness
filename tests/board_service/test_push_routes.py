"""The board service's Web Push routes (ADR NNN).

Own config, as `test_actions_routes.py`: the hive is under tmp_path, so the
subscriptions go to `tmp_path/.hive/push-subscriptions.json`, and the VAPID key
is one made here. No thread is started and nothing reaches the network.
"""
import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from board_service.app import create_app
from dispatcher import push
from tests.board_service.test_actions_routes import AUTH, TOKEN, cfg, hive, state  # noqa: F401


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


ENDPOINT = "https://push.example.com/send/abc"
SUB = {
    "endpoint": ENDPOINT,
    "expirationTime": None,
    "keys": {"p256dh": b64(b"\x04" + b"\x01" * 64), "auth": b64(b"\x02" * 16)},
}


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "vapid-private.pem"
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return path


@pytest.fixture
def client(cfg: Path, key_path: Path, monkeypatch):  # noqa: F811
    cfg.write_text(cfg.read_text() + f"\npush_vapid_key_path: {key_path}\n")
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", TOKEN)
    return create_app(str(cfg)).test_client()


def stored(hive: Path) -> list[dict]:  # noqa: F811
    return push.read_subscriptions(str(hive))


def subscribe(client, body=SUB, headers=AUTH, **kw):
    return client.post("/api/push/subscriptions", json=body, headers=headers, **kw)


def unsubscribe(client, body=None, headers=AUTH, **kw):
    return client.delete(
        "/api/push/subscriptions", json={"endpoint": ENDPOINT} if body is None else body,
        headers=headers, **kw,
    )


def test_a_subscription_is_stored_and_the_same_endpoint_replaces_it(client, hive):  # noqa: F811
    res = subscribe(client)
    assert res.status_code == 201
    assert res.get_json()["endpoint"] == ENDPOINT
    [row] = stored(hive)
    assert row["endpoint"] == ENDPOINT and row["keys"] == SUB["keys"]
    assert "expirationTime" not in row

    other = {**SUB, "keys": {**SUB["keys"], "auth": b64(b"\x03" * 16)}}
    assert subscribe(client, other).status_code == 200
    [row] = stored(hive)
    assert row["keys"]["auth"] == other["keys"]["auth"]


def test_the_store_is_private_and_written_whole(client, hive):  # noqa: F811
    subscribe(client)
    path = Path(push.subscriptions_path(str(hive)))
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["subscriptions"][0]["endpoint"] == ENDPOINT
    leftovers = [p.name for p in path.parent.iterdir() if p.name.endswith(".new")]
    assert leftovers == []


@pytest.mark.parametrize("body", [
    {"keys": SUB["keys"]},
    {**SUB, "endpoint": "http://push.example.com/x"},
    {**SUB, "endpoint": "https:///nohost"},
    {**SUB, "endpoint": "https://user:pw@push.example.com/x"},
    {**SUB, "endpoint": "https://push.example.com/a b"},
    {**SUB, "endpoint": "https://push.example.com/" + "a" * 3000},
    {**SUB, "endpoint": 7},
    {**SUB, "keys": None},
    {**SUB, "keys": {"p256dh": SUB["keys"]["p256dh"]}},
    {**SUB, "keys": {**SUB["keys"], "extra": "x"}},
    {**SUB, "keys": {**SUB["keys"], "p256dh": b64(b"\x04" * 10)}},
    {**SUB, "keys": {**SUB["keys"], "p256dh": b64(b"\x05" + b"\x01" * 64)}},
    {**SUB, "keys": {**SUB["keys"], "auth": "not base64 !!"}},
    {**SUB, "keys": {**SUB["keys"], "auth": b64(b"\x02" * 8)}},
    {**SUB, "expirationTime": "soon"},
    {**SUB, "expirationTime": True},
    {**SUB, "topic": "x"},
])
def test_junk_is_refused_and_nothing_is_stored(client, hive, body):  # noqa: F811
    res = subscribe(client, body)
    assert res.status_code == 400
    assert res.get_json()["error"]
    assert stored(hive) == []


def test_the_store_is_bounded(client, hive, monkeypatch):  # noqa: F811
    monkeypatch.setattr(push, "MAX_SUBSCRIPTIONS", 1)
    assert subscribe(client).status_code == 201
    other = {**SUB, "endpoint": "https://push.example.com/send/other"}
    assert subscribe(client, other).status_code == 409
    # The same endpoint again is a replace, not a new one.
    assert subscribe(client).status_code == 200
    assert len(stored(hive)) == 1


def test_a_delete_drops_the_subscription(client, hive):  # noqa: F811
    subscribe(client)
    res = unsubscribe(client)
    assert res.status_code == 200 and res.get_json()["removed"] is True
    assert stored(hive) == []
    assert unsubscribe(client).status_code == 404


@pytest.mark.parametrize("body", [{}, {"endpoint": "ftp://x"}, {"endpoint": ENDPOINT, "x": 1}])
def test_a_delete_with_junk_is_refused(client, body):
    assert unsubscribe(client, body).status_code == 400


CALLS = [subscribe, unsubscribe]


@pytest.mark.parametrize("call", CALLS)
def test_the_routes_want_the_bearer(client, hive, call):  # noqa: F811
    assert call(client, headers={}).status_code == 401
    assert call(client, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert stored(hive) == []


@pytest.mark.parametrize("call", CALLS)
def test_no_browser_origin_writes(client, call):
    res = call(client, headers={**AUTH, "Origin": "https://evil.example"})
    assert res.status_code == 403


@pytest.mark.parametrize("call", CALLS)
def test_the_routes_are_off_without_a_token(cfg, call, monkeypatch):  # noqa: F811
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    res = call(create_app(str(cfg)).test_client())
    assert res.status_code == 503
    assert res.get_json() == {"error": "the push routes are off: no BOARD_ACTIONS_TOKEN is set"}


@pytest.mark.parametrize("call", CALLS)
def test_a_query_or_a_body_that_is_not_an_object_is_refused(client, call):
    assert call(client, query_string={"x": "1"}).status_code == 400
    res = client.open(
        "/api/push/subscriptions", method="POST" if call is subscribe else "DELETE",
        data="[1]", content_type="application/json", headers=AUTH,
    )
    assert res.status_code == 400


def test_the_public_key_is_served_open(client, key_path):
    res = client.get("/api/push/key")
    assert res.status_code == 200
    key = res.get_json()["publicKey"]
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    assert len(raw) == 65 and raw[0] == 4
    assert key == push.public_key(str(key_path))
    assert client.get("/api/push/key?x=1").status_code == 400


def test_no_key_is_a_404(cfg, tmp_path, monkeypatch):  # noqa: F811
    cfg.write_text(cfg.read_text() + f"\npush_vapid_key_path: {tmp_path / 'none.pem'}\n")
    res = create_app(str(cfg)).test_client().get("/api/push/key")
    assert res.status_code == 404
    assert "no VAPID key" in res.get_json()["error"]


def test_a_key_that_is_not_one_is_a_404(cfg, tmp_path):  # noqa: F811
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key\n")
    cfg.write_text(cfg.read_text() + f"\npush_vapid_key_path: {bad}\n")
    assert create_app(str(cfg)).test_client().get("/api/push/key").status_code == 404


def test_the_app_builds_the_notifier_and_starts_no_thread(client, key_path):
    notifier = client.application.config["PUSH_NOTIFIER"]
    assert isinstance(notifier, push.Notifier)
    assert notifier.key_path == str(key_path)
    assert notifier.subject == push.DEFAULT_SUBJECT
