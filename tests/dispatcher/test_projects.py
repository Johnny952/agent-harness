"""The project record, `project` on every card, and the migration that writes them.

`docs/plans/projects.md` step 1 (ADR NNN): `<hive>/projects/<slug>.json`, one
reader that validates it and one writer, `project` on the task file, the
`dispatch migrate` part that writes one record per clone and stamps the cards,
and the dispatcher reading a project's settings over `config.yaml`'s.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from dispatcher import context_transfer, projects, tasks
from dispatcher import docker_exec as docker_exec_mod
from dispatcher.context_transfer import TaskFile, read_task_file, task_file_path, write_task_file
from tests.dispatcher.test_premerge import _CONTAINER, _config, _git, _local_exec

_NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)
_TTL = 600


def _record(**overrides) -> dict:
    record = {
        "slug": "shop",
        "title": "Shop",
        "remote": "git@github.com:acme/shop.git",
        "base_branch": "main",
        "key": None,
        "settings": {},
        "created": "2026-10-20T12:00:00Z",
        "status": "active",
    }
    record.update(overrides)
    return record


def _hive(tmp_path: Path) -> str:
    hive = tmp_path / "hive" / "tasks"
    hive.mkdir(parents=True)
    return str(hive)


def _card(hive: str, task_id: str, **fields) -> str:
    path = task_file_path(hive, task_id)
    write_task_file(path, TaskFile(
        task_id=task_id, status=fields.pop("status", "pending"), owner=fields.pop("owner", None),
        depends_on=[], heartbeat=fields.pop("heartbeat", None), body="", **fields,
    ))
    return path


# -- the record ----------------------------------------------------------------


def test_a_whole_record_parses() -> None:
    record = projects.parse(_record(
        key={"fingerprint": "SHA256:abc", "created": "2026-10-11T09:00:00Z"},
        settings={
            "allowed_tools": ["Read", "Bash(git status)"],
            "merge_on_done": True,
            "gates_enabled": False,
            "gates_test_timeout_seconds": 300,
            "push_task_branch": False,
        },
    ))

    assert record.slug == "shop"
    assert record.remote == "git@github.com:acme/shop.git"
    assert record.key == projects.KeyRecord(fingerprint="SHA256:abc", created="2026-10-11T09:00:00Z")
    assert record.settings["allowed_tools"] == ["Read", "Bash(git status)"]
    assert record.status == "active"


@pytest.mark.parametrize("change, problem", [
    ({"slug": "../etc"}, "slug"),
    ({"slug": ".hidden"}, "slug"),
    ({"slug": ""}, "slug"),
    ({"title": ""}, "title"),
    ({"remote": "https://github.com/acme/shop.git"}, "SSH"),
    ({"base_branch": 3}, "base_branch"),
    ({"key": "ssh-ed25519 AAAA..."}, "key"),
    ({"key": {"fingerprint": "SHA256:abc", "created": "x", "private": "-----BEGIN"}}, "key"),
    ({"settings": {"allowed_tools": "Read"}}, "allowed_tools"),
    ({"settings": {"allowed_tools": [""]}}, "allowed_tools"),
    ({"settings": {"merge_on_done": "yes"}}, "merge_on_done"),
    ({"settings": {"gates_test_timeout_seconds": 0}}, "gates_test_timeout_seconds"),
    ({"settings": {"gates_test_timeout_seconds": True}}, "gates_test_timeout_seconds"),
    ({"settings": {"auto_merge": True}}, "auto_merge"),
    ({"status": "deleted"}, "status"),
    ({"created": None}, "created"),
    ({"owner": "me"}, "owner"),
])
def test_a_bad_record_is_refused_and_named(change, problem) -> None:
    with pytest.raises(projects.RecordError) as raised:
        projects.parse(_record(**change))

    assert problem in str(raised.value)


def test_a_record_with_no_remote_or_branch_is_kept() -> None:
    # A clone with no `origin`, or a detached checkout, still counts.
    record = projects.parse(_record(remote=None, base_branch=None))

    assert (record.remote, record.base_branch) == (None, None)


def test_the_file_name_must_be_the_slug(tmp_path) -> None:
    root = tmp_path / "hive"
    (root / "projects").mkdir(parents=True)
    (root / "projects" / "other.json").write_text(json.dumps(_record()))

    with pytest.raises(projects.RecordError, match="slug"):
        projects.read(str(root), "other")


def test_the_writer_writes_a_readable_file_and_read_gives_it_back(tmp_path) -> None:
    root = str(tmp_path / "hive")
    record = projects.parse(_record())

    path = projects.write(root, record)

    assert path == os.path.join(root, "projects", "shop.json")
    assert projects.read(root, "shop") == record
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o755
    assert projects.read(root, "absent") is None
    assert [r.slug for r in projects.read_all(root)] == ["shop"]


def test_the_writer_never_writes_over_a_record(tmp_path) -> None:
    root = str(tmp_path / "hive")
    projects.write(root, projects.parse(_record(title="First")))

    with pytest.raises(projects.RecordExists):
        projects.write(root, projects.parse(_record(title="Second")))

    assert projects.read(root, "shop").title == "First"
    assert os.listdir(os.path.join(root, "projects")) == ["shop.json"]


def test_replace_is_asked_for_by_name(tmp_path) -> None:
    root = str(tmp_path / "hive")
    projects.write(root, projects.parse(_record(title="First")))

    projects.write(root, projects.parse(_record(title="Second")), replace=True)

    assert projects.read(root, "shop").title == "Second"


@pytest.mark.parametrize("remote, ssh", [
    ("https://github.com/acme/shop.git", "git@github.com:acme/shop.git"),
    ("https://github.com/acme/shop", "git@github.com:acme/shop.git"),
    ("https://token@github.com/acme/shop.git", "git@github.com:acme/shop.git"),
    ("git@github.com:acme/shop.git", "git@github.com:acme/shop.git"),
    ("ssh://git@github.com/acme/shop.git", "ssh://git@github.com/acme/shop.git"),
    ("/srv/git/shop.git", None),
])
def test_a_remote_is_recorded_in_ssh_form(remote, ssh) -> None:
    assert projects.ssh_remote(remote) == ssh


# -- `project` on the card -------------------------------------------------------


def test_project_is_written_and_read_on_a_card(tmp_path) -> None:
    hive = _hive(tmp_path)
    path = _card(hive, "task-1", project="shop")

    assert read_task_file(path).project == "shop"
    assert "project: shop" in Path(path).read_text()


def test_a_card_with_no_project_writes_no_key(tmp_path) -> None:
    hive = _hive(tmp_path)
    path = _card(hive, "task-1")

    assert read_task_file(path).project is None
    assert "project" not in Path(path).read_text()


def test_a_bad_project_on_a_card_is_a_field_problem(tmp_path) -> None:
    hive = _hive(tmp_path)
    task = read_task_file(_card(hive, "task-1", project="../x"))

    assert any("project" in p for p in tasks.field_problems(task))
    assert tasks.field_problems(read_task_file(_card(hive, "task-2", project="shop"))) == []


def test_a_split_hands_its_project_to_its_subtasks(tmp_path) -> None:
    from tests.dispatcher.test_tasks import _SPLIT

    hive = _hive(tmp_path)
    _card(hive, "task-1", status="in_progress", project="shop")
    tasks.write_description(hive, "task-1", "## Goal\n\nG.\n\n## Done when\n\nD.\n")

    ids = tasks.file_split(hive, "task-1", _SPLIT)

    assert [read_task_file(task_file_path(hive, i)).project for i in ids] == ["shop", "shop"]


# -- the migration -------------------------------------------------------------


def _clone(projects_root: Path, slug: str, remote: str | None, branch: str = "main") -> Path:
    path = projects_root / slug
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", branch)
    if remote is not None:
        _git(path, "remote", "add", "origin", remote)
    return path


@pytest.fixture
def local_exec(monkeypatch):
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)


def test_discover_lists_each_clone_with_its_remote_and_branch(tmp_path, local_exec) -> None:
    root = tmp_path / "projects"
    _clone(root, "shop", "https://github.com/acme/shop.git")
    _clone(root, "blog", None, branch="trunk")
    (root / ".cache").mkdir()
    (root / "notes.txt").write_text("not a project")

    clones = projects.discover(_CONTAINER, str(root))

    assert clones == [
        projects.Clone(slug="blog", remote=None, base_branch="trunk"),
        projects.Clone(slug="shop", remote="https://github.com/acme/shop.git", base_branch="main"),
    ]


def _migrate(tmp_path, hive, clones, **kwargs):
    return projects.migrate(
        hive, clones, keys_dir=str(tmp_path / "keys"), ttl_seconds=_TTL, now=_NOW, **kwargs
    )


def test_migrate_writes_one_record_per_clone_with_no_settings(tmp_path) -> None:
    hive = _hive(tmp_path)
    clones = [projects.Clone("shop", "https://github.com/acme/shop.git", "main")]

    report = _migrate(tmp_path, hive, clones)

    root = context_transfer.hive_root(hive)
    assert report.written == ["shop"]
    record = projects.read(root, "shop")
    assert record == projects.ProjectRecord(
        slug="shop", title="shop", remote="git@github.com:acme/shop.git", base_branch="main",
        key=None, settings={}, created="2026-10-20T12:00:00Z", status="active",
    )


def test_migrate_never_writes_over_a_record_and_twice_changes_nothing(tmp_path) -> None:
    hive = _hive(tmp_path)
    root = context_transfer.hive_root(hive)
    projects.write(root, projects.parse(_record(title="Hand made", settings={"merge_on_done": True})))
    path = Path(root) / "projects" / "shop.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
    clones = [projects.Clone("shop", None, "main"), projects.Clone("blog", None, "main")]
    _card(hive, "task-1")

    first = _migrate(tmp_path, hive, clones, default="shop")
    blog = (Path(root) / "projects" / "blog.json").read_bytes()
    second = _migrate(tmp_path, hive, clones, default="shop")

    assert (first.written, first.existing) == (["blog"], ["shop"])
    assert (second.written, second.existing) == ([], ["blog", "shop"])
    assert (second.stamped, second.refused) == ([], [])
    assert second.already == 1
    assert (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) == before
    assert (Path(root) / "projects" / "blog.json").read_bytes() == blog


def test_migrate_stamps_every_card_with_the_one_project(tmp_path) -> None:
    hive = _hive(tmp_path)
    _card(hive, "task-1")
    _card(hive, "task-2", status="epic", split_into=["task-3"])
    _card(hive, "task-3", epic="task-2")
    _card(hive, "task-4", status="proposed", source="operator", title="An idea")
    _card(hive, "task-5", project="other")

    report = _migrate(tmp_path, hive, [projects.Clone("shop", None, "main")])

    assert report.default == "shop"
    assert report.stamped == ["task-1", "task-2", "task-3", "task-4"]
    assert report.already == 1
    projects_of = {
        i: read_task_file(task_file_path(hive, i)).project
        for i in ("task-1", "task-2", "task-3", "task-4", "task-5")
    }
    assert projects_of == {
        "task-1": "shop", "task-2": "shop", "task-3": "shop", "task-4": "shop", "task-5": "other",
    }
    # Only the key is added: the rest is what `write_task_file` left.
    task4 = read_task_file(task_file_path(hive, "task-4"))
    assert (task4.status, task4.source, task4.title) == ("proposed", "operator", "An idea")


def test_with_several_projects_and_no_default_no_card_is_stamped(tmp_path) -> None:
    hive = _hive(tmp_path)
    path = _card(hive, "task-1")
    before = Path(path).read_bytes()

    report = _migrate(
        tmp_path, hive, [projects.Clone("shop", None, "main"), projects.Clone("blog", None, "main")]
    )

    assert report.written == ["blog", "shop"]
    assert report.default is None
    assert report.stamped == []
    assert report.refused and "--project" in report.refused[0][1]
    assert Path(path).read_bytes() == before


def test_a_default_that_is_no_project_is_refused(tmp_path) -> None:
    hive = _hive(tmp_path)
    _card(hive, "task-1")

    report = _migrate(tmp_path, hive, [projects.Clone("shop", None, "main")], default="nope")

    assert report.stamped == []
    assert report.refused and "nope" in report.refused[0][1]


def test_a_card_whose_lock_is_live_is_not_stamped(tmp_path) -> None:
    hive = _hive(tmp_path)
    live = dt.datetime.now(dt.timezone.utc).isoformat()
    path = _card(hive, "task-1", status="in_progress", owner="cuenta1", heartbeat=live)
    before = Path(path).read_bytes()

    report = _migrate(tmp_path, hive, [projects.Clone("shop", None, "main")])

    assert report.stamped == []
    assert report.refused[0][0] == "task-1" and "live" in report.refused[0][1]
    assert Path(path).read_bytes() == before


def test_a_card_that_still_carries_its_description_is_not_stamped(tmp_path) -> None:
    hive = _hive(tmp_path)
    path = Path(task_file_path(hive, "task-1"))
    path.write_text("---\ntask_id: task-1\nstatus: pending\ndescription: Old.\n---\n\n")
    before = path.read_bytes()

    report = _migrate(tmp_path, hive, [projects.Clone("shop", None, "main")])

    assert report.stamped == []
    assert report.refused[0][0] == "task-1"
    assert path.read_bytes() == before


def test_a_clone_whose_name_is_no_slug_is_skipped(tmp_path) -> None:
    hive = _hive(tmp_path)

    report = _migrate(tmp_path, hive, [projects.Clone("bad name", None, "main")])

    assert report.written == []
    assert report.skipped and report.skipped[0][0] == "bad name"


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_migrate_records_the_keys_fingerprint_never_the_key(tmp_path) -> None:
    from dispatcher import project_keys

    hive = _hive(tmp_path)
    key = project_keys.generate(str(tmp_path / "keys"), "shop")

    _migrate(tmp_path, hive, [projects.Clone("shop", None, "main")])

    text = (Path(context_transfer.hive_root(hive)) / "projects" / "shop.json").read_text()
    record = projects.read(context_transfer.hive_root(hive), "shop")
    assert record.key is not None
    assert record.key.fingerprint.startswith("SHA256:")
    assert record.key.fingerprint in key.fingerprint
    assert key.public.split()[1] not in text
    assert "PRIVATE" not in text


# -- the settings precedence -----------------------------------------------------


def test_with_no_record_the_config_is_used_unchanged(tmp_path) -> None:
    cfg = _config(tmp_path, str(tmp_path / "projects"))

    assert projects.settings_for(cfg, "shop") is cfg


def test_a_record_with_no_settings_leaves_the_config_as_it_is(tmp_path) -> None:
    cfg = _config(tmp_path, str(tmp_path / "projects"))
    projects.write(context_transfer.hive_root(cfg.hive_tasks_dir), projects.parse(_record()))

    assert projects.settings_for(cfg, "shop") == cfg


def test_the_records_settings_win_and_what_it_leaves_out_falls_back(tmp_path) -> None:
    cfg = _config(tmp_path, str(tmp_path / "projects"))
    projects.write(
        context_transfer.hive_root(cfg.hive_tasks_dir),
        projects.parse(_record(settings={"allowed_tools": ["Read"], "merge_on_done": False})),
    )

    got = projects.settings_for(cfg, "shop")

    assert got.allowed_tools == ["Read"]
    assert got.merge_on_done is False
    assert got.gates_enabled is cfg.gates_enabled is True
    assert got.gates_test_timeout_seconds == cfg.gates_test_timeout_seconds == 900
    # Another project's cycle is not touched.
    assert projects.settings_for(cfg, "blog") is cfg


def test_a_broken_record_stops_rather_than_falling_back(tmp_path) -> None:
    cfg = _config(tmp_path, str(tmp_path / "projects"))
    directory = Path(context_transfer.hive_root(cfg.hive_tasks_dir)) / "projects"
    directory.mkdir(parents=True)
    (directory / "shop.json").write_text("{not json")

    with pytest.raises(projects.RecordError):
        projects.settings_for(cfg, "shop")


def test_a_cycle_on_a_project_with_a_broken_record_is_blocked(tmp_path, caplog) -> None:
    from dispatcher import dispatcher as core

    cfg = _config(tmp_path, str(tmp_path / "projects"))
    directory = Path(context_transfer.hive_root(cfg.hive_tasks_dir)) / "projects"
    directory.mkdir(parents=True)
    (directory / "shop.json").write_text(json.dumps(_record(status="gone")))

    with caplog.at_level("ERROR"):
        assert core.run_task_cycle(cfg, "task-1", "shop") == core.CycleOutcome.BLOCKED
        assert core.file_saved_split(cfg, "task-1", "shop") == core.CycleOutcome.BLOCKED
        assert core.run_single_phase(cfg, "task-1", "shop", "arquitecto") is None
    # Each stopped on the record, before anything else was looked at.
    assert sum("project shop" in r.getMessage() for r in caplog.records) == 3
    assert not any("arquitecto handoff" in r.getMessage() for r in caplog.records)
    verdict = core.pre_merge_check(cfg, _CONTAINER, "task-1", "shop")
    assert not verdict.passed and "project shop" in verdict.detail


def test_the_project_config_is_the_record_over_the_config(tmp_path) -> None:
    from dispatcher import dispatcher as core

    cfg = _config(tmp_path, str(tmp_path / "projects"))
    projects.write(
        context_transfer.hive_root(cfg.hive_tasks_dir),
        projects.parse(_record(settings={"gates_enabled": False})),
    )

    assert core.project_config(cfg, "task-1", "shop").gates_enabled is False
    assert core.project_config(cfg, "task-1", "blog") is cfg
