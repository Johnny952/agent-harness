"""The board service's four project-setup routes (ADR 212).

Own config, as `test_projects_routes.py`: the hive and the action queue are
under tmp_path. The routes only queue; what the worker does with each action
is `tests/dispatcher/test_project_setup.py`'s.
"""
import json

import pytest

from tests.board_service.test_actions_routes import AUTH, cfg, hive, pending, state  # noqa: F401
from tests.board_service.test_projects_routes import REMOTE, client, create, root  # noqa: F401

KINDS = ("clone", "init", "keygen", "check")


def setup(client, kind, slug="shop", body=None, headers=AUTH, **kw):  # noqa: F811
    return client.post(
        f"/api/projects/{slug}/{kind}", json={} if body is None else body, headers=headers, **kw
    )


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_is_queued_for_the_project_with_no_remote_in_it(client, state, kind):  # noqa: F811
    assert create(client).status_code == 201
    res = setup(client, kind)
    assert res.status_code == 201
    record = res.get_json()
    assert (record["kind"], record["project"], record["task_id"]) == (kind, "shop", None)
    assert record["params"] == {} and record["actor"] == "operator"
    files = pending(state)
    assert [p.stem for p in files] == [record["id"]]
    assert REMOTE not in files[0].read_text()


def test_a_keygen_carries_replace(client, state):  # noqa: F811
    create(client)
    res = setup(client, "keygen", body={"replace": True})
    assert res.status_code == 201 and res.get_json()["params"] == {"replace": True}


def test_a_replace_that_is_not_a_bool_is_refused(client, state):  # noqa: F811
    create(client)
    res = setup(client, "keygen", body={"replace": "yes"})
    assert res.status_code == 400 and "replace" in res.get_json()["error"]
    assert pending(state) == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_body_field_is_refused(client, state, kind):  # noqa: F811
    create(client)
    field = "remote" if kind != "keygen" else "url"
    res = setup(client, kind, body={field: REMOTE})
    assert res.status_code == 400
    assert res.get_json() == {"error": f"unknown field {field!r}"}
    assert pending(state) == []


def test_a_project_with_no_record_is_404(client, state):  # noqa: F811
    res = setup(client, "clone", slug="nobody")
    assert res.status_code == 404
    assert res.get_json() == {"error": "project 'nobody' has no record"}
    assert pending(state) == []


def test_a_bad_slug_is_refused_by_the_queue(client, state):  # noqa: F811
    res = setup(client, "init", slug=".hidden")
    assert res.status_code == 400
    assert pending(state) == []


def test_a_second_of_the_same_kind_is_409(client, state):  # noqa: F811
    create(client)
    assert setup(client, "check").status_code == 201
    res = setup(client, "check")
    assert res.status_code == 409
    assert res.get_json() == {"error": "a check for project shop is already queued"}
    assert setup(client, "keygen").status_code == 201
    assert len(pending(state)) == 2


def test_the_routes_need_the_bearer_and_no_origin(client, state):  # noqa: F811
    create(client)
    assert setup(client, "clone", headers={}).status_code == 401
    browser = {**AUTH, "Origin": "http://evil.example"}
    assert setup(client, "clone", headers=browser).status_code == 403
    assert pending(state) == []


def test_without_a_token_the_routes_are_off(cfg, state, monkeypatch):  # noqa: F811
    from board_service.app import create_app

    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    res = create_app(str(cfg)).test_client().post(
        "/api/projects/shop/keygen", json={}, headers=AUTH
    )
    assert res.status_code == 503
    assert json.loads(res.data)["error"].startswith("the action routes are off")
