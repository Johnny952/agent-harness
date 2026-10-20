"""`GET /api/projects` and `GET /api/projects/<slug>`, against a temporary hive (ADR NNN).

The mounts and the client are `test_api.py`'s `_harness`. Records are written
by `dispatcher/projects.py`'s own `create` and `change`, so a change to the
record's format shows up here as a failure rather than as two files that
disagree; the broken ones, and the one whose remote carries a credential, are
written by hand, since the writer refuses them.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from dispatcher import context_transfer
from dispatcher import projects as project_store
from tests.observability.test_api import _get, _harness

PATH = "/api/projects"

_NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)


def _setup(tmp_path: Path):
    harness = _harness(tmp_path)
    return harness, context_transfer.hive_root(harness.tasks_dir)


def _write_raw(hive: str, slug: str, text: str) -> str:
    os.makedirs(project_store.projects_dir(hive), exist_ok=True)
    path = os.path.join(project_store.projects_dir(hive), f"{slug}.json")
    Path(path).write_text(text)
    return path


def _record(slug: str, **fields) -> dict:
    return {
        "slug": slug,
        "title": slug,
        "remote": None,
        "base_branch": None,
        "key": None,
        "settings": {},
        "created": "2026-10-20T12:00:00+00:00",
        "status": "active",
        **fields,
    }


def test_no_projects_directory_is_an_empty_list(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)

    body = _get(harness, PATH).get_json()

    assert body == {"data": [], "warnings": []}
    # The read made nothing: the api never writes the hive.
    assert not os.path.exists(project_store.projects_dir(hive))


def test_the_records_are_served_by_slug_with_their_own_fields(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    project_store.create(
        hive,
        {"slug": "shop", "title": "The shop", "remote": "git@github.com:me/shop.git",
         "base_branch": "main"},
        now=_NOW,
    )
    project_store.create(hive, {"slug": "blog"}, now=_NOW)
    project_store.change(hive, "blog", {"status": "archived"})

    body = _get(harness, PATH).get_json()

    assert body["warnings"] == []
    assert [row["slug"] for row in body["data"]] == ["blog", "shop"]
    # The record's own fields and nothing else: no field the record does not define.
    for row in body["data"]:
        assert set(row) == set(project_store._FIELDS)
    blog, shop = body["data"]
    assert shop == project_store.read(hive, "shop").to_dict()
    assert shop["title"] == "The shop"
    assert shop["remote"] == "git@github.com:me/shop.git"
    # Archived records are served, with their status: the console filters them.
    assert blog["status"] == "archived"
    assert blog["title"] == "blog"


def test_a_broken_record_is_a_warning_and_the_rest_are_served(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    project_store.create(hive, {"slug": "shop"}, now=_NOW)
    not_json = _write_raw(hive, "torn", "{not json")
    wrong = _write_raw(hive, "odd", json.dumps(_record("odd", status="paused")))
    named = _write_raw(hive, "other", json.dumps(_record("elsewhere")))

    body = _get(harness, PATH).get_json()

    assert [row["slug"] for row in body["data"]] == ["shop"]
    assert len(body["warnings"]) == 3
    by_path = {w.split(": ", 1)[0]: w for w in body["warnings"]}
    assert set(by_path) == {not_json, wrong, named}
    assert "not JSON" in by_path[not_json]
    assert "status 'paused'" in by_path[wrong]
    assert "not its file's name" in by_path[named]


def test_files_that_are_not_records_are_not_listed(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    project_store.create(hive, {"slug": "shop"}, now=_NOW)
    # The record lock `change` takes, a stray file and a dotted name.
    _write_raw(hive, ".shop.lock", "")
    Path(project_store.projects_dir(hive), "notes.txt").write_text("hello")

    body = _get(harness, PATH).get_json()

    assert body == {"data": [project_store.read(hive, "shop").to_dict()], "warnings": []}


def test_a_remote_with_a_credential_never_leaves_the_route(tmp_path: Path) -> None:
    # The writer refuses this record, so it is written by hand: a file edited
    # on the host, say. `parse` refuses it as well, so it is a warning, and the
    # warning carries the remote redacted.
    harness, hive = _setup(tmp_path)
    leaky = [
        ("pw", "ssh://deploy:hunter2@git.example.com/me/shop.git", "hunter2"),
        ("tok", "https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/me/x.git",
         "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ]
    for slug, remote, _ in leaky:
        _write_raw(hive, slug, json.dumps(_record(slug, remote=remote)))

    for path in (PATH, f"{PATH}/pw", f"{PATH}/tok"):
        resp = _get(harness, path)
        text = resp.get_data(as_text=True)
        for _, _, secret in leaky:
            assert secret not in text, path
    body = _get(harness, PATH).get_json()
    assert body["data"] == []
    assert len(body["warnings"]) == 2
    assert all("***@" in w for w in body["warnings"])


def test_the_row_remote_goes_through_the_redaction(tmp_path: Path, monkeypatch) -> None:
    # The second lock: were `parse` ever to let a credential through, the row
    # would still cross redacted.
    harness, hive = _setup(tmp_path)
    monkeypatch.setattr(project_store, "_problems", lambda data: [])
    _write_raw(hive, "pw", json.dumps(_record("pw", remote="ssh://deploy:hunter2@git.example.com/r.git")))

    for path in (PATH, f"{PATH}/pw"):
        resp = _get(harness, path)
        assert "hunter2" not in resp.get_data(as_text=True)
    row = _get(harness, f"{PATH}/pw").get_json()["data"]
    assert row["remote"] == "ssh://***@git.example.com/r.git"


def test_one_record_by_slug(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    record = project_store.create(hive, {"slug": "shop", "title": "The shop"}, now=_NOW)

    resp = _get(harness, f"{PATH}/shop")

    assert resp.status_code == 200
    assert resp.get_json() == {"data": record.to_dict(), "warnings": []}


def test_an_unknown_slug_is_a_404(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    project_store.create(hive, {"slug": "shop"}, now=_NOW)

    for slug in ("nope", ".shop", "a%20b"):
        resp = _get(harness, f"{PATH}/{slug}")
        assert resp.status_code == 404, slug
        assert set(resp.get_json()) == {"error"}
    # No projects directory at all is the same 404, and makes nothing.
    other, other_hive = _setup(tmp_path / "empty")
    assert _get(other, f"{PATH}/shop").status_code == 404
    assert not os.path.exists(project_store.projects_dir(other_hive))


def test_a_broken_record_by_slug_is_null_and_the_reason(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    path = _write_raw(hive, "torn", "{not json")

    resp = _get(harness, f"{PATH}/torn")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] is None
    assert len(body["warnings"]) == 1
    assert body["warnings"][0].startswith(f"{path}: not JSON")


def test_the_detail_route_takes_no_parameters(tmp_path: Path) -> None:
    harness, hive = _setup(tmp_path)
    project_store.create(hive, {"slug": "shop"}, now=_NOW)

    resp = _get(harness, f"{PATH}/shop?status=active")

    assert resp.status_code == 400
    assert "status" in resp.get_json()["error"]
