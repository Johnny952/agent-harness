"""Backup and restore (docs/decisions.md ADR 327, 328)."""
from __future__ import annotations

import fnmatch
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import dispatcher.cli as cli_mod
from dispatcher import backup, worker
from dispatcher.project_lock import ProjectBusy, ProjectLock

T0 = datetime(2026, 10, 22, 3, 0, 0, tzinfo=timezone.utc)
SECRET = "SECRET-MARKER"

CONFIG_YAML = """\
accounts:
  - name: cuenta1
    container: agent-cuenta1
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: {hive}/tasks
state_dir: {state}
collector_url: http://127.0.0.1:8787
"""


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def env(tmp_path: Path):
    hive, state, dest = tmp_path / "hive", tmp_path / "state", tmp_path / "backups"
    _write(hive / "board" / "T-1" / "description.md", "goal")
    _write(hive / "tasks" / "T-1.md", "task")
    _write(hive / "comments" / "T-1.jsonl", "c")
    _write(hive / "threads" / "t1.json", "t")
    _write(hive / "conversations" / "c1.jsonl", "line\n")
    _write(state / "cycles" / "T-1.json", "cycle")
    _write(state / "usage.jsonl", "u")
    _write(state / "accounts" / "cuenta1.json", "{}")
    _write(state / "actions" / "pending" / "a.json", "{}")
    return hive, state, dest


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def test_backup_copies_hive_and_state_with_a_manifest(env) -> None:
    hive, state, dest = env
    result = backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    copy = dest / "20261022T030000Z"
    assert result.name == copy.name
    assert (copy / "hive" / "board" / "T-1" / "description.md").read_text() == "goal"
    assert (copy / "hive" / "conversations" / "c1.jsonl").read_text() == "line\n"
    assert (copy / "state" / "accounts" / "cuenta1.json").exists()
    assert (copy / "state" / "usage.jsonl").exists()
    assert (copy / "manifest.json").exists()
    assert result.files == 9
    assert backup.copies(str(dest)) == [copy.name]


def test_the_cap_prunes_the_oldest_copy(env) -> None:
    hive, state, dest = env
    _write(dest / ".partial-20200101T000000Z" / "x")
    (dest / "notes").mkdir()
    for i in range(3):
        result = backup.backup(str(hive), str(state), str(dest), 2, now=T0 + timedelta(hours=i))
    assert result.pruned == ["20261022T030000Z"]
    assert backup.copies(str(dest)) == ["20261022T040000Z", "20261022T050000Z"]
    assert (dest / ".partial-20200101T000000Z").exists()
    assert (dest / "notes").exists()


def test_the_newest_copy_survives_even_when_older_names_are_newer_than_now(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 1, now=T0 + timedelta(days=1))
    result = backup.backup(str(hive), str(state), str(dest), 1, now=T0)
    assert "20261022T030000Z" in backup.copies(str(dest))
    assert result.pruned == ["20261023T030000Z"]


def test_no_credential_path_lands_in_a_copy(env, tmp_path: Path) -> None:
    hive, state, dest = env
    outside = tmp_path / "outside_creds"
    _write(outside / "token", SECRET)
    shapes = [
        "users.json", "sessions.json", "users", "claude_creds_cuenta1", "claude_shared",
        ".credentials.json", "push-subscriptions.json", "key.pem", "id_ed25519",
        "id_ed25519.pub", ".data", "x.lock", "secrets",
    ]
    for root in (hive, state):
        for where in (root, root / "auth", root / "deep" / "er"):
            for shape in shapes:
                _write(where / shape / "inner" if shape in ("users", "claude_shared", ".data", "claude_creds_cuenta1", "secrets") else where / shape, SECRET)
        (root / "link").symlink_to(outside)
        (root / "surface-locks").mkdir()
        _write(root / "surface-locks" / "s", SECRET)
    result = backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    copy = dest / result.name
    assert result.skipped_symlinks == 2
    for path in copy.rglob("*"):
        assert not any(
            fnmatch.fnmatchcase(part, pat)
            for part in path.relative_to(copy).parts
            for pat in backup.NEVER_COPIED + backup.NOT_RECORDS
        ), path
        if path.is_file():
            assert SECRET not in path.read_text(), path


def test_a_waiting_action_secret_is_not_in_a_copy(env) -> None:
    hive, state, dest = env
    _write(state / "actions" / "secrets" / "a", SECRET)
    result = backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    copy = dest / result.name / "state" / "actions"
    assert (copy / "pending" / "a.json").exists()
    assert not (copy / "secrets").exists()


def test_a_held_project_lock_refuses_and_writes_nothing(env) -> None:
    hive, state, dest = env
    _write(hive / "projects" / "alpha.json", "{}")
    _write(hive / "projects" / "beta.json", "{}")
    held = ProjectLock.take(str(state), "beta", "T-9", "run-task")
    try:
        with pytest.raises(ProjectBusy):
            backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    finally:
        held.release()
    assert not dest.exists() or os.listdir(dest) == []
    # alpha was released again: nothing keeps it
    ProjectLock.take(str(state), "alpha", None, "t").release()


def test_backup_dir_inside_a_source_is_refused(env) -> None:
    hive, state, _ = env
    with pytest.raises(backup.BackupError):
        backup.backup(str(hive), str(state), str(hive / "b"), 7, now=T0)
    with pytest.raises(backup.BackupError):
        backup.backup(str(hive), str(state), str(state.parent), 7, now=T0)


def test_a_name_that_exists_is_refused(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    with pytest.raises(backup.BackupError):
        backup.backup(str(hive), str(state), str(dest), 7, now=T0)


@pytest.mark.parametrize("keep", ["0", "-1", "x", "1.5"])
def test_a_bad_backup_keep_is_refused(keep: str) -> None:
    with pytest.raises(backup.BackupError):
        backup.settings_from_env({"BACKUP_DIR": "/b", "BACKUP_KEEP": keep})


def test_settings_default_and_required() -> None:
    assert backup.settings_from_env({"BACKUP_DIR": "/b"}) == ("/b", 7)
    with pytest.raises(backup.BackupError):
        backup.settings_from_env({"BACKUP_DIR": " "})


def test_before_merge_returns_the_copys_line(env) -> None:
    hive, state, dest = env
    line = backup.before_merge(str(hive), str(state), {"BACKUP_DIR": str(dest)}, now=T0)
    name = T0.strftime(backup.NAME_FORMAT)
    assert line == f"backup before the merge: {name}"
    assert backup.copies(str(dest)) == [name]


def test_before_merge_without_a_backup_dir_says_so_and_does_not_raise(env) -> None:
    hive, state, dest = env
    line = backup.before_merge(str(hive), str(state), {}, now=T0)
    assert line == "no backup before the merge: BACKUP_DIR is not set"
    assert not dest.exists()


def test_before_merge_under_a_held_project_lock_says_busy_and_writes_nothing(env) -> None:
    hive, state, dest = env
    _write(hive / "projects" / "alpha.json", "{}")
    held = ProjectLock.take(str(state), "alpha", "T-9", "run-task")
    try:
        line = backup.before_merge(str(hive), str(state), {"BACKUP_DIR": str(dest)}, now=T0)
    finally:
        held.release()
    assert line.startswith("no backup before the merge: ")
    assert not dest.exists() or os.listdir(dest) == []


def test_a_shared_project_lock_is_not_retaken(env) -> None:
    hive, state, dest = env
    _write(hive / "projects" / "p.json", "{}")
    held = ProjectLock.take(str(state), "p", "T-9", "run-task")
    try:
        result = backup.backup(str(hive), str(state), str(dest), 7, now=T0, shared=("p",))
        line = backup.before_merge(
            str(hive), str(state), {"BACKUP_DIR": str(dest)},
            now=T0 + timedelta(seconds=1), shared=("p",),
        )
    finally:
        held.release()
    assert backup.copies(str(dest)) == [result.name, "20261022T030001Z"]
    assert line == "backup before the merge: 20261022T030001Z"


def test_a_shared_lock_does_not_cover_another_projects_busy_lock(env) -> None:
    hive, state, dest = env
    _write(hive / "projects" / "p.json", "{}")
    _write(hive / "projects" / "q.json", "{}")
    mine = ProjectLock.take(str(state), "p", "T-9", "run-task")
    other = ProjectLock.take(str(state), "q", "T-8", "run-task")
    try:
        with pytest.raises(ProjectBusy):
            backup.backup(str(hive), str(state), str(dest), 7, now=T0, shared=("p",))
    finally:
        other.release()
        mine.release()
    assert not dest.exists() or os.listdir(dest) == []


def test_merge_hook_takes_the_copy_with_the_shared_lock(env) -> None:
    hive, state, dest = env
    _write(hive / "projects" / "p.json", "{}")

    class Cfg:
        hive_tasks_dir = str(hive / "tasks")
        state_dir = str(state)

    held = ProjectLock.take(str(state), "p", "T-9", "run-task")
    try:
        line = backup.merge_hook(Cfg, {"BACKUP_DIR": str(dest)}, shared=("p",))()
    finally:
        held.release()
    assert line.startswith("backup before the merge: ")


def test_restore_round_trip_sets_aside_and_keeps_directories(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    want_hive, want_state = _tree(hive), _tree(state)
    inode = (hive / "board").stat().st_ino
    (hive / "tasks" / "T-1.md").write_text("changed")
    (hive / "comments" / "T-1.jsonl").unlink()
    _write(hive / "tasks" / "T-2.md", "added")
    _write(state / "extra" / "e.json", "e")
    live_cred = _write(state / "claude_creds_a" / "token", SECRET)

    result = backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    aside = ".pre-restore-20261022T040000Z"
    live_hive = {k: v for k, v in _tree(hive).items() if not k.startswith(aside)}
    assert live_hive == want_hive
    assert (hive / aside / "tasks" / "T-2.md").read_text() == "added"
    assert (state / aside / "extra" / "e.json").exists()
    assert not (state / "extra").exists()
    assert (hive / "board").stat().st_ino == inode
    assert live_cred.read_text() == SECRET
    assert {k: v for k, v in _tree(state).items() if not k.startswith(aside) and not k.startswith("claude_creds_a")
            and not k.startswith("actions/worker.lock")} == want_state
    assert len(result.asides) == 2
    assert (hive / aside / "tasks" / "T-1.md").read_text() == "changed"


def test_restore_sets_aside_the_live_version_of_a_changed_file_only(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    (hive / "tasks" / "T-1.md").write_text("changed")

    result = backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    aside = hive / ".pre-restore-20261022T040000Z"
    assert (hive / "tasks" / "T-1.md").read_text() == "task"
    assert (aside / "tasks" / "T-1.md").read_text() == "changed"
    assert not (aside / "board").exists()
    assert str(aside) in result.asides


def test_restore_of_an_unchanged_tree_sets_nothing_aside(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)

    result = backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    assert str(hive / ".pre-restore-20261022T040000Z") not in result.asides
    assert not list(hive.glob(".pre-restore-*"))
    # the worker lock the restore itself holds is the only thing the state sets aside
    assert _tree(state / ".pre-restore-20261022T040000Z").keys() <= {"actions/worker.lock"}


def test_restore_never_writes_through_a_live_symlink_to_a_directory(env, tmp_path: Path) -> None:
    hive, state, dest = env
    outside = tmp_path / "outside"
    _write(outside / "keep.txt", "mine")
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    before = _tree(outside)
    import shutil
    shutil.rmtree(hive / "board")
    (hive / "board").symlink_to(outside)

    backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    assert _tree(outside) == before
    assert not (hive / "board").is_symlink()
    assert (hive / "board" / "T-1" / "description.md").read_text() == "goal"
    link = hive / ".pre-restore-20261022T040000Z" / "board"
    assert link.is_symlink() and os.path.realpath(link) == str(outside.resolve())


def test_restore_replaces_a_live_symlink_to_a_file_without_touching_its_target(env, tmp_path: Path) -> None:
    hive, state, dest = env
    target = _write(tmp_path / "target.md", "mine")
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    (hive / "tasks" / "T-1.md").unlink()
    (hive / "tasks" / "T-1.md").symlink_to(target)

    backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    assert target.read_text() == "mine"
    live = hive / "tasks" / "T-1.md"
    assert not live.is_symlink() and live.read_text() == "task"
    assert (hive / ".pre-restore-20261022T040000Z" / "tasks" / "T-1.md").is_symlink()


def test_restore_leaves_a_live_symlink_the_copy_lacks_in_place(env, tmp_path: Path) -> None:
    hive, state, dest = env
    outside = tmp_path / "outside"
    _write(outside / "keep.txt", "mine")
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    (hive / "extra").symlink_to(outside)

    result = backup.restore(str(hive), str(state), str(dest), "latest", now=T0 + timedelta(hours=1))

    assert (hive / "extra").is_symlink()
    assert _tree(outside) == {"keep.txt": "mine"}
    assert not list(hive.glob(".pre-restore-*"))


def test_restore_refuses_unknown_and_broken_copies(env) -> None:
    hive, state, dest = env
    with pytest.raises(backup.BackupError):
        backup.restore(str(hive), str(state), str(dest), "latest", now=T0)
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    with pytest.raises(backup.BackupError):
        backup.restore(str(hive), str(state), str(dest), "../x", now=T0)
    (dest / "20261022T030000Z" / "manifest.json").unlink()
    with pytest.raises(backup.BackupError):
        backup.restore(str(hive), str(state), str(dest), "20261022T030000Z", now=T0)


def test_restore_refuses_while_the_worker_runs_and_touches_nothing(env) -> None:
    hive, state, dest = env
    backup.backup(str(hive), str(state), str(dest), 7, now=T0)
    (hive / "tasks" / "T-1.md").write_text("changed")
    fd = worker.acquire(str(state))
    try:
        with pytest.raises(worker.WorkerLocked):
            backup.restore(str(hive), str(state), str(dest), "latest", now=T0)
    finally:
        os.close(fd)
    assert (hive / "tasks" / "T-1.md").read_text() == "changed"


def _cli(tmp_path: Path, monkeypatch, capsys, *argv: str, **environ: str):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML.format(hive=tmp_path / "hive", state=tmp_path / "state"))
    for key in ("BACKUP_DIR", "BACKUP_KEEP"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", str(config), *argv])
    code = 0
    try:
        cli_mod.main()
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr()


def test_cli_backup_and_restore_exit_codes_and_lines(env, tmp_path, monkeypatch, capsys) -> None:
    _, state, dest = env
    code, out = _cli(tmp_path, monkeypatch, capsys, "backup")
    assert code == 2 and "BACKUP_DIR" in out.err
    code, out = _cli(tmp_path, monkeypatch, capsys, "backup", BACKUP_DIR=str(dest))
    assert code == 0 and out.out.startswith("backup ")
    code, out = _cli(tmp_path, monkeypatch, capsys, "restore", "--from", "latest", BACKUP_DIR=str(dest))
    assert code == 0
    lines = out.out.splitlines()
    assert lines[0].startswith("restored ")
    assert tuple(lines[1:]) == backup.RESTORE_LINES
    fd = worker.acquire(str(state))
    try:
        code, out = _cli(tmp_path, monkeypatch, capsys, "restore", "--from", "latest", BACKUP_DIR=str(dest))
    finally:
        os.close(fd)
    assert code == 75
    code, out = _cli(tmp_path, monkeypatch, capsys, "restore", "--from", "nope", BACKUP_DIR=str(dest))
    assert code == 2
