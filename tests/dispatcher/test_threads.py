"""The thread store (ADR 170)."""
import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from dispatcher import threads
from dispatcher.config import load_config

T0 = dt.datetime(2026, 10, 19, 12, 0, tzinfo=dt.timezone.utc)
T1 = T0 + dt.timedelta(minutes=5)


def make_cfg(tmp_path: Path, **extra):
    values = {
        "accounts": [
            {"name": "cuenta1", "container": "c1", "models": ["m1", "m2"]},
            {"name": "cuenta2", "container": "c2", "models": ["m3"]},
        ],
        "primary_account": "cuenta2",
        "default_model": "m3",
        "projects_root": "/p",
        "hive_tasks_dir": str(tmp_path / ".hive" / "tasks"),
        "state_dir": str(tmp_path / "state"),
        "collector_url": "http://c",
        **extra,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(values))
    return load_config(str(path))


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path)


@pytest.fixture
def hive(tmp_path) -> str:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return str(path)


def seed_session(hive: str, thread_id: str, session: str = "s-1") -> None:
    path = Path(threads.thread_path(hive, thread_id))
    record = json.loads(path.read_text())
    record["segments"][-1]["session_id"] = session
    path.write_text(json.dumps(record))


def new(hive, cfg, **kw):
    return threads.create(hive, cfg, title="t", now=T0, **kw)


def test_create_with_every_default(hive, cfg):
    record = new(hive, cfg)
    assert record["id"].startswith("th-")
    assert (record["account"], record["model"], record["effort"]) == ("cuenta2", "m3", "medium")
    assert record["status"] == "open"
    assert record["task"] is None and record["project"] is None
    assert record["created"] == T0.isoformat()
    assert record["segments"] == [
        {"session_id": None, "account": "cuenta2", "model": "m3", "effort": "medium",
         "from": T0.isoformat()}
    ]
    assert threads.read_thread(hive, record["id"]) == record
    assert list(record) == [
        "id", "title", "project", "account", "model", "effort", "segments", "created",
        "status", "task",
    ]


def test_the_first_account_is_the_default_without_a_primary(hive, tmp_path):
    cfg = make_cfg(tmp_path, primary_account=None)
    assert new(hive, cfg)["account"] == "cuenta1"


def test_the_model_defaults_to_the_lists_first_when_default_model_is_not_in_it(hive, tmp_path):
    cfg = make_cfg(tmp_path, primary_account="cuenta1")
    assert new(hive, cfg)["model"] == "m1"


def test_each_field_may_be_given(hive, cfg):
    (Path(hive) / "T-1.md").write_text("---\ntask_id: T-1\n---\n")
    record = new(hive, cfg, account="cuenta1", model="m2", effort="max", project="demo", task="T-1")
    assert (record["account"], record["model"], record["effort"]) == ("cuenta1", "m2", "max")
    assert (record["project"], record["task"]) == ("demo", "T-1")


@pytest.mark.parametrize(
    "kw",
    [
        {"account": "nope"},
        {"model": "m1"},
        {"account": "cuenta1", "model": "m3"},
        {"effort": "huge"},
        {"project": "../x"},
        {"task": "T-404"},
        {"task": "../x"},
    ],
)
def test_create_refusals(hive, cfg, kw):
    with pytest.raises(threads.ThreadRefused):
        new(hive, cfg, **kw)
    assert not Path(threads.threads_root(hive)).exists()


@pytest.mark.parametrize("title", [None, "", "  ", "x" * 201, 5])
def test_an_ordinary_thread_needs_a_title(hive, cfg, title):
    with pytest.raises(threads.ThreadRefused, match="title"):
        threads.create(hive, cfg, title=title)


@pytest.mark.parametrize("field", ["account", "model", "effort"])
def test_a_change_opens_a_segment_with_the_same_session(hive, cfg, field):
    record = new(hive, cfg, account="cuenta1", model="m1")
    seed_session(hive, record["id"])
    value = {"account": "cuenta2", "model": "m2", "effort": "high"}[field]
    kw = {field: value}
    if field == "account":
        kw["model"] = "m3"
    out = threads.update(hive, cfg, record["id"], now=T1, **kw)
    assert len(out["segments"]) == 2
    last = out["segments"][-1]
    assert last["session_id"] == "s-1" and last["from"] == T1.isoformat()
    assert (last["account"], last["model"], last["effort"]) == (
        out["account"], out["model"], out["effort"]
    )
    assert threads.read_thread(hive, record["id"]) == out


def test_two_changes_at_once_open_one_segment(hive, cfg):
    record = new(hive, cfg, account="cuenta1", model="m1")
    out = threads.update(hive, cfg, record["id"], model="m2", effort="low")
    assert len(out["segments"]) == 2


def test_the_same_value_opens_none(hive, cfg):
    record = new(hive, cfg, account="cuenta1", model="m1")
    out = threads.update(hive, cfg, record["id"], model="m1", effort="medium")
    assert len(out["segments"]) == 1


def test_an_account_change_rechecks_the_model(hive, cfg):
    record = new(hive, cfg, account="cuenta1", model="m1")
    path = Path(threads.thread_path(hive, record["id"]))
    before = path.read_text()
    with pytest.raises(threads.ThreadRefused, match="m1"):
        threads.update(hive, cfg, record["id"], account="cuenta2")
    assert path.read_text() == before
    out = threads.update(hive, cfg, record["id"], account="cuenta2", model="m3")
    assert out["account"] == "cuenta2" and out["model"] == "m3"


@pytest.mark.parametrize("kw", [{"model": "zz"}, {"effort": "huge"}, {"account": "nope"}, {"status": "gone"}])
def test_update_refusals(hive, cfg, kw):
    record = new(hive, cfg)
    with pytest.raises(threads.ThreadRefused):
        threads.update(hive, cfg, record["id"], **kw)


def test_title_and_status_open_no_segment(hive, cfg):
    record = new(hive, cfg)
    out = threads.update(hive, cfg, record["id"], title="new", status="archived")
    assert (out["title"], out["status"], len(out["segments"])) == ("new", "archived", 1)


def test_update_of_an_unknown_or_malformed_id(hive, cfg):
    with pytest.raises(threads.NoThreadError):
        threads.update(hive, cfg, "th-20261019T120000Z-abcdef", title="x")
    with pytest.raises(threads.NoThreadError):
        threads.read_thread(hive, "../x")
    with pytest.raises(threads.NoThreadError):
        threads.read_thread(hive, "th-1")


def test_the_main_thread_is_single(hive, cfg):
    record = threads.create(hive, cfg, main=True, now=T0)
    assert record["id"] == "main" and record["title"] == "Main thread"
    assert record["task"] is None and record["project"] is None
    with pytest.raises(threads.ThreadExistsError):
        threads.create(hive, cfg, main=True)
    assert [p.name for p in Path(threads.threads_root(hive)).glob("*.json")] == ["main.json"]


def test_the_main_thread_takes_no_task_or_project_and_cannot_be_archived(hive, cfg):
    for kw in ({"task": "T-1"}, {"project": "demo"}):
        with pytest.raises(threads.ThreadRefused):
            threads.create(hive, cfg, main=True, **kw)
    threads.create(hive, cfg, main=True)
    with pytest.raises(threads.ThreadRefused, match="archived"):
        threads.update(hive, cfg, "main", status="archived")


def test_read_threads_warns_on_a_bad_file_and_lists_main_first(hive, cfg):
    a = new(hive, cfg)
    threads.create(hive, cfg, main=True, now=T1)
    root = Path(threads.threads_root(hive))
    (root / "bad.json").write_text("{nope")
    (root / "list.json").write_text("[]")
    (root / "x.json.tmp").write_text("{")
    records, warnings = threads.read_threads(hive)
    assert [r["id"] for r in records] == ["main", a["id"]]
    assert len(warnings) == 2
    assert any("bad.json" in w for w in warnings) and any("list.json" in w for w in warnings)


def test_read_threads_without_a_directory(hive):
    assert threads.read_threads(hive) == ([], [])


def test_session_id_goes_in_the_last_segment_and_opens_none(hive, cfg):
    record = new(hive, cfg)
    got = threads.update(hive, cfg, record["id"], session_id="s-9")
    assert got["segments"][-1]["session_id"] == "s-9" and len(got["segments"]) == 1


def test_the_conversation_log_appends_lines_and_refuses_a_foreign_id(hive, cfg):
    record = new(hive, cfg)
    threads.append_conversation(hive, record["id"], {"a": 1})
    threads.append_conversation(hive, record["id"], '{"b": 2}')
    path = threads.conversation_path(hive, record["id"])
    assert path.endswith(f"conversations/{record['id']}.jsonl")
    assert [json.loads(l) for l in Path(path).read_text().splitlines()] == [{"a": 1}, {"b": 2}]
    with pytest.raises(threads.NoThreadError):
        threads.append_conversation(hive, "../x", {})
