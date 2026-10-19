"""The board service's thread routes (ADR 170, 7).

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
from pathlib import Path

import pytest

from board_service.app import create_app
from tests.board_service.test_app import CONFIG_YAML


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def client(tmp_path: Path, hive: Path, monkeypatch):
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", "s3cret")
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(tmp_path / "state"))
        + "thread_models: [m1, m2]\n"
    )
    (tmp_path / "config.d").mkdir()
    return create_app(str(path)).test_client()


def make(client, **body):
    return client.post("/api/threads", json={"title": "t", **body})


def test_post_creates_and_answers_201(client, hive):
    res = make(client)
    assert res.status_code == 201
    record = res.get_json()
    assert record["model"] == "m1" and record["status"] == "open"
    assert (hive.parent / "threads" / f"{record['id']}.json").is_file()


def test_post_main(client):
    res = client.post("/api/threads", json={"main": True})
    assert res.status_code == 201 and res.get_json()["id"] == "main"
    assert client.post("/api/threads", json={"main": True}).status_code == 409


def test_patch_answers_200_and_opens_a_segment(client):
    tid = make(client).get_json()["id"]
    res = client.patch(f"/api/threads/{tid}", json={"model": "m2", "title": "n"})
    assert res.status_code == 200
    record = res.get_json()
    assert (record["model"], record["title"], len(record["segments"])) == ("m2", "n", 2)


def test_a_thread_with_no_account_follows_a_primary_override_saved_after_boot(tmp_path, hive):
    """ADR 178: the default account is the primary the override names now,
    with no reload of the config the service loaded at boot."""
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_YAML.format(hive=hive)
        .replace("/data/dispatcher_state", str(tmp_path / "state"))
        .replace("agent-cuenta1\n", "agent-cuenta1\n  - name: cuenta2\n    container: agent-cuenta2\n")
        + "thread_models: [m1, m2]\nprimary_account: cuenta1\n"
    )
    (tmp_path / "config.d").mkdir()
    client = create_app(str(path)).test_client()
    assert make(client).get_json()["account"] == "cuenta1"

    (tmp_path / "config.d" / "overrides.yaml").write_text("primary_account: cuenta2\n")
    assert make(client).get_json()["account"] == "cuenta2"


def test_no_bearer_is_needed(client):
    assert make(client).status_code == 201


@pytest.mark.parametrize("method", ["post", "patch"])
def test_guards(client, method):
    tid = make(client).get_json()["id"]
    url = "/api/threads" if method == "post" else f"/api/threads/{tid}"
    call = getattr(client, method)
    body = {"title": "x"}
    assert call(url, json=body, headers={"Origin": "http://evil"}).status_code == 403
    assert call(url + "?a=1", json=body).status_code == 400
    assert call(url, json=[1]).status_code == 400
    assert call(url, data="nope", content_type="application/json").status_code == 400
    assert call(url, json={"bogus": 1}).status_code == 400
    assert call(url, json={"title": 5}).status_code == 400


def test_a_null_is_left_out_and_an_empty_patch_is_400(client):
    tid = make(client, project=None).get_json()["id"]
    assert client.patch(f"/api/threads/{tid}", json={}).status_code == 400
    assert client.patch(f"/api/threads/{tid}", json={"model": None}).status_code == 400


def test_a_main_must_be_a_boolean(client):
    assert client.post("/api/threads", json={"main": "yes"}).status_code == 400


def test_refusals_carry_the_reason(client):
    res = make(client, model="zz")
    assert res.status_code == 400 and "zz" in res.get_json()["error"]
    tid = make(client).get_json()["id"]
    res = client.patch(f"/api/threads/{tid}", json={"status": "gone"})
    assert res.status_code == 400 and "status" in res.get_json()["error"]


def test_an_unknown_id_is_404(client):
    assert client.patch("/api/threads/th-20261019T120000Z-abcdef", json={"title": "x"}).status_code == 404
    assert client.patch("/api/threads/nope", json={"title": "x"}).status_code == 404
