import json
import logging
import os
import stat
import sys
import types
from pathlib import Path

import pytest
import yaml

from config_writer import app as writer
from config_writer.app import create_app, main
from dispatcher.config import CONFIG_KEYS, DRAFT_ROLE, PHASE_ROLES, load_config, refresh_primary
from dispatcher.project_docs import MAPPER_ROLE

TOKEN = "writer-token"

CONFIG_YAML = """
accounts:
  - name: cuenta1
    container: agent-cuenta1
  - name: cuenta2
    container: agent-cuenta2
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: /data/.hive/tasks
state_dir: /data/dispatcher_state
collector_url: http://127.0.0.1:8787
"""


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_YAML)
    (tmp_path / "config.d").mkdir()
    return path


@pytest.fixture
def client(cfg: Path):
    return create_app(str(cfg), TOKEN).test_client()


def d(cfg: Path) -> Path:
    return cfg.parent / "config.d"


def auth(actor: str | None = "ana") -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if actor is not None:
        headers["X-Actor"] = actor
    return headers


def get(client) -> dict:
    res = client.get("/settings", headers=auth())
    assert res.status_code == 200
    return res.get_json()["data"]


def put(client, key, value, revision, **kw):
    return client.put(
        f"/settings/{key}", json={"value": value, "revision": revision}, headers=auth(**kw)
    )


def delete(client, key, revision, **kw):
    return client.delete(f"/settings/{key}", json={"revision": revision}, headers=auth(**kw))


def log_lines(cfg: Path) -> list[dict]:
    path = d(cfg) / "changes.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_save_then_reset(client, cfg: Path) -> None:
    assert get(client)["revision"] is None
    res = put(client, "quota_threshold_pct", 80, None)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["overrides"] == {"quota_threshold_pct": 80}
    row = next(r for r in data["rows"] if r["key"] == "quota_threshold_pct")
    assert (row["value"], row["source"]) == (80, "override")
    overrides = d(cfg) / "overrides.yaml"
    first = overrides.read_bytes()
    assert first.startswith(b"# Written by the config-writer service")
    assert not (d(cfg) / "overrides.yaml.prev").exists()
    assert load_config(str(cfg)).quota_threshold_pct == 80

    res = put(client, "reserve_pct", 50, data["revision"])
    assert res.status_code == 200
    assert (d(cfg) / "overrides.yaml.prev").read_bytes() == first

    revision = res.get_json()["data"]["revision"]
    res = delete(client, "quota_threshold_pct", revision)
    assert res.status_code == 200
    assert res.get_json()["data"]["overrides"] == {"reserve_pct": 50}
    res = delete(client, "reserve_pct", res.get_json()["data"]["revision"])
    assert res.get_json()["data"]["overrides"] == {}
    assert overrides.exists()  # `{}` under the header, not an absent file
    assert load_config(str(cfg)).quota_threshold_pct == 90

    lines = log_lines(cfg)
    assert [(e["op"], e["key"]) for e in lines] == [
        ("set", "quota_threshold_pct"),
        ("set", "reserve_pct"),
        ("reset", "quota_threshold_pct"),
        ("reset", "reserve_pct"),
    ]
    assert lines[0]["old"] is None and lines[0]["new"] == 80
    assert lines[2]["old"] == 80 and lines[2]["new"] is None
    assert lines[0]["revision_before"] is None
    assert lines[1]["revision_before"] == lines[0]["revision_after"]
    assert {e["actor"] for e in lines} == {"ana"}
    assert {e["via"] for e in lines} == {"console"}
    assert lines[0]["at"].endswith("+00:00")
    for name in ("overrides.yaml", "overrides.yaml.prev", "changes.jsonl", "overrides.lock"):
        assert stat.S_IMODE(os.stat(d(cfg) / name).st_mode) == 0o644
    assert sorted(p.name for p in d(cfg).iterdir()) == [
        "changes.jsonl", "overrides.lock", "overrides.yaml", "overrides.yaml.prev",
    ]


def test_put_of_a_null_escalated_model_is_200_and_means_off(client, cfg: Path) -> None:
    res = put(client, "escalated_model", None, None)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["overrides"] == {"escalated_model": None}
    row = next(r for r in data["rows"] if r["key"] == "escalated_model")
    assert row["value"] is None
    assert (row["source"], row["kind"]) == ("override", "nullable_model")
    assert load_config(str(cfg)).escalated_model is None
    line = log_lines(cfg)[0]
    assert line["op"] == "set" and line["new"] is None

    res = put(client, "escalated_model", "", data["revision"])
    assert res.status_code == 422
    assert str(d(cfg) / "overrides.yaml") in res.get_json()["error"]


def test_a_stale_revision_is_409_and_writes_nothing(client, cfg: Path) -> None:
    revision = put(client, "reserve_pct", 50, None).get_json()["data"]["revision"]
    before = (d(cfg) / "overrides.yaml").read_bytes()
    assert put(client, "reserve_pct", 40, "stale").status_code == 409
    assert put(client, "reserve_pct", 40, None).status_code == 409
    assert delete(client, "reserve_pct", "stale").status_code == 409
    assert (d(cfg) / "overrides.yaml").read_bytes() == before
    assert len(log_lines(cfg)) == 1
    assert put(client, "reserve_pct", 40, revision).status_code == 200


def test_a_value_load_config_refuses_is_422_naming_the_real_file(client, cfg: Path) -> None:
    res = put(client, "reserve_pct", 95, None)  # above quota_threshold_pct 90
    assert res.status_code == 422
    message = res.get_json()["error"]
    assert str(d(cfg) / "overrides.yaml") in message
    assert ".tmp" not in message
    assert not (d(cfg) / "overrides.yaml").exists()
    assert not (d(cfg) / "changes.jsonl").exists()
    assert not [p for p in d(cfg).iterdir() if p.suffix == ".tmp"]
    res = put(client, "pace_primary_week", "yes", None)
    assert res.status_code == 422
    assert "must be true or false" in res.get_json()["error"]


def test_the_primary_is_moved_by_a_save_and_read_by_a_running_dispatcher(
    client, cfg: Path
) -> None:
    """The primary override end to end (ADR 176): the writer refuses a name
    that is no configured account, saves one that is, and a dispatcher that
    loaded its config before the save reads it on its next pick."""
    running = load_config(str(cfg))
    assert running.primary_account is None

    res = put(client, "primary_account", "cuenta3", None)
    assert res.status_code == 422
    assert "is not a configured account" in res.get_json()["error"]
    assert not (d(cfg) / "overrides.yaml").exists()

    assert put(client, "primary_account", "cuenta2", None).status_code == 200
    assert refresh_primary(running) == "cuenta2"
    assert [a.is_primary for a in running.accounts] == [False, True]

    assert delete(client, "primary_account", get(client)["revision"]).status_code == 200
    assert refresh_primary(running) is None
    assert [a.is_primary for a in running.accounts] == [False, False]


def test_a_broken_config_yaml_is_a_422_too(client, cfg: Path) -> None:
    cfg.write_text("quota_threshold_pct: 90\n")  # no accounts: KeyError in load_config
    res = put(client, "reserve_pct", 50, None)
    assert res.status_code == 422
    assert "KeyError" in res.get_json()["error"]
    assert not (d(cfg) / "overrides.yaml").exists()


@pytest.mark.parametrize("key", ["accounts", "git_credentials_path", "vibe_kanban", "nope"])
def test_put_of_a_key_that_is_not_editable_is_400(client, cfg: Path, key: str) -> None:
    assert put(client, key, "x", None).status_code == 400
    assert not (d(cfg) / "overrides.yaml").exists()


def test_delete_of_a_key_that_is_not_editable_and_not_held_is_400(client) -> None:
    for key in ("accounts", "git_credentials_path", "nope"):
        assert delete(client, key, None).status_code == 400


@pytest.mark.parametrize(
    "send",
    [
        lambda c, h: c.put("/settings/reserve_pct", data="not json", headers=h),
        lambda c, h: c.put("/settings/reserve_pct", json=[1], headers=h),
        lambda c, h: c.put("/settings/reserve_pct", json={"value": 5}, headers=h),
        lambda c, h: c.put("/settings/reserve_pct", json={"revision": None}, headers=h),
        lambda c, h: c.put("/settings/reserve_pct", json={"value": 5, "revision": 3}, headers=h),
        lambda c, h: c.delete("/settings/reserve_pct", json={}, headers=h),
        lambda c, h: c.delete("/settings/reserve_pct", headers=h),
    ],
)
def test_a_malformed_body_is_400(client, cfg: Path, send) -> None:
    assert send(client, auth()).status_code == 400
    assert not (d(cfg) / "overrides.yaml").exists()


def test_a_missing_or_blank_actor_is_400(client, cfg: Path) -> None:
    assert put(client, "reserve_pct", 50, None, actor=None).status_code == 400
    assert put(client, "reserve_pct", 50, None, actor="  ").status_code == 400
    assert delete(client, "reserve_pct", None, actor=None).status_code == 400
    assert not (d(cfg) / "overrides.yaml").exists()


def test_a_save_that_repairs_an_invalid_file_is_accepted(client, cfg: Path) -> None:
    # The file is valid when written; a config.yaml change then breaks it.
    revision = put(client, "reserve_pct", 85, None).get_json()["data"]["revision"]
    cfg.write_text(CONFIG_YAML.replace("quota_threshold_pct: 90", "quota_threshold_pct: 70"))
    data = get(client)
    assert data["error"] and "reserve_pct" in data["error"]
    # Raising the threshold repairs it.
    res = put(client, "quota_threshold_pct", 90, revision)
    assert res.status_code == 200
    assert res.get_json()["data"]["error"] is None

    cfg.write_text(CONFIG_YAML.replace("quota_threshold_pct: 90", "quota_threshold_pct: 70"))
    revision = get(client)["revision"]
    res = delete(client, "reserve_pct", revision)  # so does resetting the key
    assert res.status_code == 200
    assert res.get_json()["data"]["error"] is None


def test_a_reset_of_a_non_editable_key_the_file_holds_is_accepted(client, cfg: Path) -> None:
    path = d(cfg) / "overrides.yaml"
    path.write_text("accounts: []\nreserve_pct: 50\n")
    data = get(client)
    assert data["error"] and "accounts" in data["error"]
    res = delete(client, "accounts", data["revision"])
    assert res.status_code == 200
    assert res.get_json()["data"]["overrides"] == {"reserve_pct": 50}
    assert res.get_json()["data"]["error"] is None
    assert log_lines(cfg)[0]["old"] == []


def test_a_reset_of_an_absent_key_is_200_and_writes_nothing(client, cfg: Path) -> None:
    revision = put(client, "reserve_pct", 50, None).get_json()["data"]["revision"]
    before = (d(cfg) / "overrides.yaml").read_bytes()
    res = delete(client, "default_model", revision)
    assert res.status_code == 200
    assert (d(cfg) / "overrides.yaml").read_bytes() == before
    assert not (d(cfg) / "overrides.yaml.prev").exists()
    assert len(log_lines(cfg)) == 1


def test_a_file_that_does_not_parse_is_422_on_a_write_and_200_on_a_read(client, cfg: Path) -> None:
    path = d(cfg) / "overrides.yaml"
    path.write_text("reserve_pct: [\n")
    data = get(client)
    assert data["overrides"] is None
    assert data["error"] and str(path) in data["error"]
    assert data["revision"]
    assert {r["source"] for r in data["rows"]} <= {"config.yaml", "default"}
    res = put(client, "reserve_pct", 50, data["revision"])
    assert res.status_code == 422
    assert str(path) in res.get_json()["error"]
    assert delete(client, "reserve_pct", data["revision"]).status_code == 422
    assert path.read_text() == "reserve_pct: [\n"

    path.write_text("- a\n- b\n")  # parses, not a mapping
    assert get(client)["overrides"] is None


def test_get_with_a_merge_that_fails_is_200_with_error(client, cfg: Path) -> None:
    path = d(cfg) / "overrides.yaml"
    path.write_text("reserve_pct: 99\nrole_model: {reviewer: opus}\n")
    res = client.get("/settings", headers=auth())
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["error"] and str(path) in data["error"]
    assert data["overrides"] == {"reserve_pct": 99, "role_model": {"reviewer": "opus"}}
    rows = {r["key"]: r for r in data["rows"]}
    assert (rows["reserve_pct"]["value"], rows["reserve_pct"]["source"]) == (99, "override")
    assert (rows["quota_threshold_pct"]["value"], rows["quota_threshold_pct"]["source"]) == (
        90, "config.yaml",
    )
    assert rows["default_model"]["source"] == "default"


@pytest.mark.parametrize(
    "send",
    [
        lambda c, h: c.get("/settings", headers=h),
        lambda c, h: c.put("/settings/reserve_pct", json={"value": 5, "revision": None}, headers=h),
        lambda c, h: c.delete("/settings/reserve_pct", json={"revision": None}, headers=h),
    ],
)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic Zm9vOmJhcg=="}])
def test_every_route_needs_the_token(client, cfg: Path, send, headers) -> None:
    headers = {**headers, "X-Actor": "ana"}
    res = send(client, headers)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == 'Bearer realm="ia-harness-config-writer"'
    assert res.get_json() == {"error": "a valid bearer token is required"}
    assert not (d(cfg) / "overrides.yaml").exists()


def test_a_log_failure_puts_the_file_back(client, cfg: Path, monkeypatch) -> None:
    def boom(path, entry):
        raise OSError("disk full")

    revision = put(client, "reserve_pct", 50, None).get_json()["data"]["revision"]
    path = d(cfg) / "overrides.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(writer, "_append_change", boom)
    res = put(client, "reserve_pct", 40, revision)
    assert res.status_code == 500
    assert "disk full" in res.get_json()["error"]
    assert path.read_bytes() == before
    assert len(log_lines(cfg)) == 1
    assert not [p for p in d(cfg).iterdir() if p.suffix == ".tmp"]

    # A first save has no previous file: the rollback removes the new one.
    path.unlink()
    res = put(client, "reserve_pct", 40, None)
    assert res.status_code == 500
    assert not path.exists()


def test_startup_is_refused_without_the_token(capsys) -> None:
    for environ in ({}, {"CONFIG_WRITER_TOKEN": "  "}):
        with pytest.raises(SystemExit) as exit_info:
            main(environ)
        assert exit_info.value.code == 2
        assert "CONFIG_WRITER_TOKEN" in capsys.readouterr().err
    assert main({"CONFIG_WRITER_TOKEN": "t"}).name  # builds an app


def test_run_serves_from_waitress_on_the_same_host_and_port(cfg, monkeypatch) -> None:
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        types.SimpleNamespace(serve=lambda app, **kwargs: calls.append((app, kwargs))),
    )

    writer.run({"CONFIG_WRITER_TOKEN": TOKEN, "CONFIG_PATH": str(cfg)})

    app, kwargs = calls[0]
    # The literals pin ADR 420: a change to any is a decision to revisit.
    assert kwargs == {"host": "0.0.0.0", "port": 8791, "threads": 4}
    assert (writer.HOST, writer.PORT, writer.THREADS) == ("0.0.0.0", 8791, 4)
    assert app.test_client().get("/settings", headers=auth()).status_code == 200


def test_run_refuses_a_missing_token_before_it_touches_waitress(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "waitress", None)  # an import would raise

    with pytest.raises(SystemExit) as exit_info:
        writer.run({})

    assert exit_info.value.code == 2
    assert "CONFIG_WRITER_TOKEN" in capsys.readouterr().err


def test_run_sends_waitress_start_line_and_queue_warnings_to_stderr(
    cfg, monkeypatch, capsys
) -> None:
    root = logging.getLogger()

    def serve(app, **kwargs):
        # waitress.serve() runs logging.basicConfig(): a stderr handler on root
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(handler)
        try:
            logging.getLogger("waitress").info("Serving on http://0.0.0.0:8791")
            logging.getLogger("waitress.queue").warning("Task queue depth is 1")
        finally:
            root.removeHandler(handler)

    monkeypatch.setitem(sys.modules, "waitress", types.SimpleNamespace(serve=serve))
    before = (root.level, list(root.handlers))

    writer.run({"CONFIG_WRITER_TOKEN": TOKEN, "CONFIG_PATH": str(cfg)})

    err = capsys.readouterr().err
    assert err.count("INFO:waitress:Serving on") == 1  # ADR 423
    assert err.count("WARNING:waitress.queue:Task queue depth") == 1
    waitress_logger = logging.getLogger("waitress")
    assert waitress_logger.propagate is False
    assert len(waitress_logger.handlers) == 1
    assert (root.level, list(root.handlers)) == before  # only `waitress` changed


def test_rows_are_the_key_table_in_order(client) -> None:
    rows = get(client)["rows"]
    assert [r["key"] for r in rows] == list(CONFIG_KEYS)
    by_key = {r["key"]: r for r in rows}
    assert by_key["accounts"]["class"] == "read-only"
    assert by_key["accounts"]["value"][0]["name"] == "cuenta1"
    assert by_key["quota_threshold_pct"]["source"] == "config.yaml"
    assert by_key["quota_threshold_pct"]["value"] == 90
    assert by_key["quota_threshold_pct"]["kind"] == "positive_int"
    assert by_key["reserve_pct"]["source"] == "default"
    assert by_key["reserve_pct"]["value"] == 60
    assert by_key["primary_account"]["value"] is None
    assert by_key["vibe_kanban"]["value"] is None
    assert by_key["git_credentials_path"]["class"] == "credential"


def test_roles_are_served_from_the_dispatcher_constants(client) -> None:
    roles = get(client)["roles"]
    assert roles["role_model"] == sorted((PHASE_ROLES - {MAPPER_ROLE}) | {DRAFT_ROLE})
    assert roles["role_effort"] == sorted(PHASE_ROLES)
    assert roles["fallback_roles"] == sorted(PHASE_ROLES)


def test_every_served_role_loads_in_an_override(client, cfg: Path) -> None:
    for role in get(client)["roles"]["role_model"]:
        revision = get(client)["revision"]
        assert put(client, "role_model", {role: "some-model"}, revision).status_code == 200, role
    for role in get(client)["roles"]["role_effort"]:
        revision = get(client)["revision"]
        assert put(client, "role_effort", {role: "high"}, revision).status_code == 200, role


def test_recent_changes_are_newest_first_and_a_bad_line_is_a_warning(client, cfg: Path) -> None:
    revision = None
    for value in (50, 51, 52):
        revision = put(client, "reserve_pct", value, revision).get_json()["data"]["revision"]
    log = d(cfg) / "changes.jsonl"
    with open(log, "a") as f:
        f.write("not json\n")
    res = client.get("/settings", headers=auth())
    body = res.get_json()
    assert [e["new"] for e in body["data"]["recent_changes"]] == [52, 51, 50]
    assert len(body["warnings"]) == 1
    assert str(log) in body["warnings"][0] and "line 4" in body["warnings"][0]


def test_recent_changes_stops_at_twenty(client, cfg: Path) -> None:
    log = d(cfg) / "changes.jsonl"
    log.write_text("".join(json.dumps({"n": n}) + "\n" for n in range(30)))
    changes = get(client)["recent_changes"]
    assert [c["n"] for c in changes] == list(range(29, 9, -1))


def test_no_file_means_a_null_revision_and_no_changes(client) -> None:
    data = get(client)
    assert data["revision"] is None
    assert data["overrides"] == {}
    assert data["recent_changes"] == []
    assert data["error"] is None
    assert data["base"]["quota_threshold_pct"] == 90


def test_the_overrides_file_is_plain_yaml_under_the_header(client, cfg: Path) -> None:
    put(client, "reserve_pct", 50, None)
    text = (d(cfg) / "overrides.yaml").read_text()
    assert text.splitlines()[:2] == writer.HEADER.splitlines()
    assert yaml.safe_load(text) == {"reserve_pct": 50}
