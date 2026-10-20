"""The board service's project-record routes (ADR 203).

Own config, as `test_actions_routes.py`: the hive is under tmp_path, so the
records go to `tmp_path/.hive/projects`, never the real hive.
"""
import json
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import context_transfer, projects
from dispatcher.config import load_config
from tests.board_service.test_actions_routes import AUTH, TOKEN, cfg, hive, state  # noqa: F401

REMOTE = "git@github.com:me/shop.git"


@pytest.fixture
def client(cfg: Path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", TOKEN)
    return create_app(str(cfg)).test_client()


@pytest.fixture
def root(hive: Path) -> str:  # noqa: F811
    return context_transfer.hive_root(str(hive))


def create(client, body=None, headers=AUTH, **kw):
    body = {"slug": "shop", "remote": REMOTE} if body is None else body
    return client.post("/api/projects", json=body, headers=headers, **kw)


def change(client, slug="shop", body=None, headers=AUTH, **kw):
    return client.patch(f"/api/projects/{slug}", json=body, headers=headers, **kw)


def on_disk(root: str, slug: str = "shop") -> dict:
    return json.loads(Path(projects.record_path(root, slug)).read_text())


def test_a_create_writes_a_record_the_reader_reads(client, root):
    res = create(client, {
        "slug": "shop", "title": "The shop", "remote": REMOTE, "base_branch": "main",
        "settings": {"merge_on_done": False},
    })
    assert res.status_code == 201
    record = res.get_json()
    assert record == on_disk(root)
    assert record["status"] == "active" and record["key"] is None
    assert projects.read(root, "shop").to_dict() == record


def test_a_create_defaults_what_it_leaves_out(client, root):
    res = create(client, {"slug": "shop"})
    assert res.status_code == 201
    record = on_disk(root)
    assert record["title"] == "shop"
    assert (record["remote"], record["base_branch"], record["settings"]) == (None, None, {})


def test_a_create_on_an_existing_slug_is_refused(client, root):
    assert create(client).status_code == 201
    before = on_disk(root)
    res = create(client, {"slug": "shop", "title": "Other"})
    assert res.status_code == 409
    assert res.get_json() == {"error": "project 'shop' already has a record"}
    assert on_disk(root) == before


def test_a_create_over_a_broken_record_is_refused(client, root):
    path = Path(projects.record_path(root, "shop"))
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert create(client).status_code == 409
    assert path.read_text() == "not json"


@pytest.mark.parametrize("body", [
    {"slug": "shop", "remote": "https://github.com/me/shop.git"},
    {"slug": "Not A Slug!"},
    {"slug": "shop", "settings": {"merge_on_done": "yes"}},
    {"slug": "shop", "settings": {"colour": "red"}},
    {"slug": "shop", "owner": "me"},
    {"slug": "shop", "title": ""},
])
def test_a_create_with_a_bad_field_is_refused_in_the_readers_words(client, root, body):
    data = {
        "slug": body["slug"], "title": body["slug"], "remote": None, "base_branch": None,
        "key": None, "settings": {}, "created": "2026-10-20T12:00:00Z", "status": "active",
        **body,
    }
    with pytest.raises(projects.RecordError) as reader:
        projects.parse(data)
    res = create(client, body)
    assert res.status_code == 400
    assert res.get_json() == {"error": str(reader.value)}
    assert projects.slugs(root) == []


@pytest.mark.parametrize("field, value", [
    ("key", {"fingerprint": "SHA256:abc", "created": "2026-10-20T12:00:00Z"}),
    ("created", "2026-10-20T12:00:00Z"),
    ("status", "archived"),
])
def test_a_create_takes_no_key_created_or_status(client, root, field, value):
    res = create(client, {"slug": "shop", field: value})
    assert res.status_code == 400
    assert field in res.get_json()["error"]
    assert projects.slugs(root) == []


def test_a_change_lays_its_fields_over_the_record(client, root):
    create(client, {"slug": "shop", "remote": REMOTE, "settings": {"merge_on_done": False, "gates_enabled": False}})
    created = on_disk(root)["created"]
    res = change(client, body={
        "title": "Shop", "base_branch": "develop",
        "settings": {"merge_on_done": True, "gates_enabled": None, "push_task_branch": True},
    })
    assert res.status_code == 200
    record = on_disk(root)
    assert res.get_json() == record
    assert record["title"] == "Shop" and record["base_branch"] == "develop"
    assert record["settings"] == {"merge_on_done": True, "push_task_branch": True}
    assert record["created"] == created and record["remote"] == REMOTE


def test_a_change_archives_and_restores(client, root):
    create(client)
    assert change(client, body={"status": "archived"}).get_json()["status"] == "archived"
    assert projects.read(root, "shop").status == "archived"
    assert change(client, body={"status": "active"}).get_json()["status"] == "active"
    assert change(client, body={"status": "gone"}).status_code == 400


def test_a_change_on_a_missing_slug_is_refused(client, root):
    res = change(client, "shop", {"title": "Shop"})
    assert res.status_code == 404
    assert res.get_json() == {"error": "no project record 'shop'"}
    assert change(client, "NOT_A_SLUG", {"title": "x"}).status_code == 404
    assert projects.slugs(root) == []


def test_a_change_on_a_broken_record_leaves_it(client, root):
    path = Path(projects.record_path(root, "shop"))
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    res = change(client, body={"title": "Shop"})
    assert res.status_code == 409
    assert path.read_text() == "{}"


def test_a_change_with_a_bad_field_is_refused_in_the_readers_words(client, root):
    create(client)
    before = on_disk(root)
    with pytest.raises(projects.RecordError) as reader:
        projects.parse({**before, "remote": "https://github.com/me/shop.git"})
    res = change(client, body={"remote": "https://github.com/me/shop.git"})
    assert res.status_code == 400
    assert res.get_json() == {"error": str(reader.value)}
    assert on_disk(root) == before


@pytest.mark.parametrize("body", [
    {}, {"slug": "other"}, {"key": None}, {"created": "2026-10-20T12:00:00Z"}, {"settings": None},
])
def test_a_change_refuses_what_it_does_not_change(client, root, body):
    create(client)
    before = on_disk(root)
    assert change(client, body=body).status_code == 400
    assert on_disk(root) == before


def test_the_written_record_is_what_the_dispatcher_runs_on(client, cfg, root):  # noqa: F811
    from dispatcher import dispatcher as core

    create(client, {"slug": "shop", "settings": {"merge_on_done": True, "gates_enabled": False}})
    config = load_config(str(cfg))
    project = core.project_config(config, "T-1", "shop")
    assert project.merge_on_done is True and project.gates_enabled is False
    change(client, body={"settings": {"gates_enabled": None}})
    assert core.project_config(config, "T-1", "shop").gates_enabled == config.gates_enabled


@pytest.mark.parametrize("call", [create, lambda c, **kw: change(c, body={"title": "x"}, **kw)])
def test_the_routes_want_the_bearer(client, root, call):
    res = call(client, headers={})
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert call(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert projects.slugs(root) == []


def test_no_browser_origin_writes_a_record(client, root):
    res = create(client, headers={**AUTH, "Origin": "http://localhost:5173"})
    assert res.status_code == 403
    assert projects.slugs(root) == []


def test_the_routes_are_off_without_a_token(cfg, root, monkeypatch):  # noqa: F811
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    off = create_app(str(cfg)).test_client()
    res = create(off)
    assert res.status_code == 503
    assert res.get_json() == {"error": "the project routes are off: no BOARD_ACTIONS_TOKEN is set"}
    assert change(off, body={"title": "x"}).status_code == 503


def test_a_body_that_is_not_an_object_or_a_query_is_refused(client, root):
    assert client.post("/api/projects", data="[]", headers={**AUTH, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/projects?x=1", json={"slug": "shop"}, headers=AUTH).status_code == 400
    assert projects.slugs(root) == []
