"""scripts/backup.sh and scripts/restore.sh against a fake `docker` on PATH that
logs its argv, so the order of the compose calls is what is asserted."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BACKUP = REPO_ROOT / "scripts" / "backup.sh"
RESTORE = REPO_ROOT / "scripts" / "restore.sh"

#: Answers `ps` with FAKE_RUNNING and fails a `run` that carries `restore` when FAKE_FAIL.
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$*" in
  *" ps "*) [ -n "$FAKE_RUNNING" ] && echo abc123 ;;
  *" run "*) [ -n "$FAKE_FAIL" ] && exit 7 ;;
esac
exit 0
"""


def _run(tmp_path: Path, script: Path, *args: str, running: str = "", fail: str = "", **env: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "log"
    log.touch()
    proc = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log),
             "FAKE_RUNNING": running, "FAKE_FAIL": fail, "BACKUP_DIR": str(tmp_path / "b"), **env},
    )
    return proc, log.read_text().splitlines()


def test_backup_mounts_backup_dir_on_the_run_line(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, BACKUP, BACKUP_KEEP="3")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "b").is_dir()
    assert len(calls) == 1
    assert f"-v {tmp_path / 'b'}:/backups" in calls[0]
    assert "-e BACKUP_DIR=/backups" in calls[0]
    assert "-e BACKUP_KEEP=3" in calls[0]
    assert calls[0].endswith("dispatcher --config /app/config.yaml backup")


def test_restore_stops_runs_then_starts_the_worker(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, RESTORE, "latest", running="1")
    assert proc.returncode == 0, proc.stderr
    verbs = ("ps", "stop", "run", "up")
    assert [next(v for v in verbs if f" {v} " in c) for c in calls] == list(verbs)
    assert ":/backups:ro" in calls[2] and calls[2].endswith("restore --from latest")


def test_restore_does_not_start_a_worker_that_was_not_running(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, RESTORE, "latest")
    assert proc.returncode == 0
    assert not any(" up " in c for c in calls)


def test_a_failed_restore_leaves_the_worker_stopped(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, RESTORE, "latest", running="1", fail="1")
    assert proc.returncode == 7
    assert "left stopped" in proc.stderr
    assert not any(" up " in c for c in calls)


def test_restore_needs_a_copy_name(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, RESTORE)
    assert proc.returncode == 2 and calls == []
