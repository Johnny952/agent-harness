# ia-harness Implementation Plan

**Status (2026-10-08): delivered, and a record rather than a worklist.** This is the founding plan, written on 2026-09-13 and executed to the end; its checkboxes were never ticked, so an unticked box here says nothing about what is left to do. The files each task names are the files that were planned, and several have since moved or gone — Task 10's dashboard was split out, and the board that replaced it was itself retired (`docs/decisions.md` ADR 32). The plan is deliberately left as written: rewriting it to match the tree would delete the record of what was intended. `docs/ROADMAP.md` is the live tracker.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Smart Dispatcher and its supporting infrastructure so a serial, 24/7 multi-agent Arquitecto→Implementador→Revisor→Auditor pipeline can run across multiple Claude Pro accounts in isolated Docker containers, orchestrated by `docker exec -w ...`, coordinated through Vibe Kanban's local MCP server, with quota-aware failover, dual context-transfer, hardened Docker-out-of-Docker sidecars, and hooks→HTTP→SQLite observability.

**Architecture:** A Python package (`dispatcher/`) implements a per-account disk-persisted state machine, `/usage`-based proactive quota polling with reactive-error backup, `docker exec`-based invocation of the Claude Code CLI inside per-account agent containers, and two context-transfer mechanisms (`.hive/tasks/<task-id>.md` frontmatter+lock for role handoff, `--resume <session_id>` for same-role quota/crash failover). Vibe Kanban stays the sole backlog UI and is driven only through its local MCP server — never through direct DB access. Each agent container gets a private `docker:dind` sidecar under `sysbox-runc` (no `--privileged`), sharing a registry-mirror + BuildKit registry cache. Hooks in each agent container POST events to a small Flask collector backed by SQLite (WAL), read by a separately-authenticated Flask dashboard.

**Tech Stack:** Python 3.11+, pytest, Flask, `requests`, the official `mcp` SDK, PyYAML, stdlib `subprocess`/`sqlite3`/`threading`. Docker Compose for service wiring; `sysbox-runc` runtime for DooD sidecars; `registry:2` for the pull-through mirror.

**Spec:** `docs/superpowers/specs/2026-09-13-ia-harness-design.md`

## Global Constraints

- Target scale: 2 Claude Pro accounts in immediate use; the design (config-driven account list, no hardcoded account count) must support N accounts without structural/code changes.
- Backlog lives only in Vibe Kanban. No parallel backlog source (no GitHub issues, no loose Markdown backlog).
- **Current scope is serial only** — one account/session active at a time. Do **not** implement either of the two future parallel/load-balancing variants from spec §8 (per-account subagents by quota; independent task claiming with quota-triggered relief). They are explicitly out of scope for this plan.
- `/usage` output is free text, not a documented API — parse defensively (spec §4a caveat); never assume the template is stable across CLI versions.
- The exact reactive rate-limit signal string is empirically unverified (spec §4a caveat) — implement conservative best-effort matching, not a hardcoded exact string.
- DooD hardening is single-tier: one `docker:dind` sidecar per agent under `--runtime=sysbox-runc`, **never** `--privileged`, **never** a mounted host `docker.sock` inside the agent, **never** `docker-socket-proxy`/Tecnativa.
- Vibe Kanban's MCP server is loopback-only — never expose it on a public or Tailnet-routable port.
- The observability dashboard requires its own authentication in addition to Tailscale network membership.
- Branch/worktree naming convention: `agent/<rol>/<task-id>` (e.g. `agent/implementador/task-123`).
- `--resume <session_id>` is the mechanism for same-role quota/crash handoff; `.hive/tasks/<task-id>.md` cold-start handoff is the mechanism for role-to-role transitions and the fallback when resume fails.
- CPU/RAM limits per agent container and per sidecar are required, but concrete values are an implementation/hardware-sizing detail — this plan sets explicit example values in the Compose files that operators are expected to tune to their server.

---

## File Structure

```
ia-harness/
├── pyproject.toml
├── .gitignore
├── dispatcher/
│   ├── __init__.py
│   ├── config.py              # Config/AccountConfig dataclasses, load_config()
│   ├── state_machine.py       # AccountState enum, disk-persisted get/set/list
│   ├── quota.py                # parse_usage_output(), exceeds_threshold()
│   ├── docker_exec.py          # docker exec wrapper, exec_claude(), create_worktree()
│   ├── context_transfer.py     # .hive/tasks/<id>.md read/write/lock/TTL/handoff
│   ├── vibe_kanban_client.py   # MCP client wrapper for Vibe Kanban
│   ├── dispatcher.py           # dispatch_phase(), run_task_cycle(), reap_expired_locks()
│   └── cli.py                  # `ia-harness-dispatcher run-task ...` entrypoint
├── hooks/
│   ├── __init__.py
│   └── emit_event.py           # Claude Code hook script → POSTs to collector
├── observability/
│   ├── __init__.py
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   ├── db.py                # init_db/insert_event/list_events
│   │   └── server.py            # Flask app: POST/GET /events
│   └── dashboard/
│       ├── __init__.py
│       └── app.py                # Flask app: HTTP-Basic-auth'd event viewer
├── docker/
│   ├── agent/Dockerfile
│   ├── dind-sidecar/
│   │   ├── Dockerfile
│   │   └── crontab
│   ├── registry-mirror/config.yml
│   └── compose/
│       ├── docker-compose.yml         # vibe-kanban, dispatcher, collector, dashboard, registry-mirror
│       └── docker-compose.agents.yml  # per-account agent + dind sidecar pairs
├── scripts/
│   ├── setup_volumes.sh         # creates shared + per-account shadowed volumes
│   └── prune.sh                 # `docker system prune -af --volumes`, run via cron in each sidecar
├── .hive/tasks/                 # runtime, gitignored
├── dispatcher_state/             # runtime, gitignored
└── tests/
    ├── __init__.py
    ├── dispatcher/
    │   ├── __init__.py
    │   ├── test_config.py
    │   ├── test_state_machine.py
    │   ├── test_quota.py
    │   ├── test_docker_exec.py
    │   ├── test_context_transfer.py
    │   ├── test_vibe_kanban_client.py
    │   ├── test_dispatcher.py
    │   └── test_cli.py
    ├── hooks/
    │   ├── __init__.py
    │   └── test_emit_event.py
    └── observability/
        ├── __init__.py
        ├── test_collector.py
        └── test_dashboard.py
```

---

## Task 1: Project scaffolding + config loader

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `dispatcher/__init__.py`
- Create: `dispatcher/config.py`
- Create: `tests/__init__.py`, `tests/dispatcher/__init__.py`
- Test: `tests/dispatcher/test_config.py`

**Interfaces:**
- Produces: `AccountConfig(name: str, container: str)`; `Config(accounts: list[AccountConfig], quota_threshold_pct: int, heartbeat_ttl_seconds: int, heartbeat_interval_seconds: int, projects_root: str, hive_tasks_dir: str, state_dir: str, vibe_kanban_mcp_url: str, collector_url: str)`; `load_config(path: str) -> Config`.

- [ ] **Step 1: Create the package skeleton and project metadata**

```toml
# pyproject.toml
[project]
name = "ia-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pyyaml>=6.0",
    "flask>=3.0",
    "requests>=2.31",
    "mcp>=1.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```
# .gitignore
__pycache__/
*.pyc
.pytest_cache/
.venv/
dispatcher_state/
.hive/
*.db
*.db-wal
*.db-shm
```

Create empty `dispatcher/__init__.py`, `tests/__init__.py`, `tests/dispatcher/__init__.py`.

- [ ] **Step 2: Write the failing test**

```python
# tests/dispatcher/test_config.py
from pathlib import Path

from dispatcher.config import load_config

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
vibe_kanban_mcp_url: http://127.0.0.1:9100/sse
collector_url: http://127.0.0.1:8787
"""


def test_load_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    cfg = load_config(str(config_path))

    assert [a.name for a in cfg.accounts] == ["cuenta1", "cuenta2"]
    assert cfg.accounts[0].container == "agent-cuenta1"
    assert cfg.quota_threshold_pct == 90
    assert cfg.heartbeat_ttl_seconds == 120
    assert cfg.projects_root == "/data/projects"
    assert cfg.vibe_kanban_mcp_url == "http://127.0.0.1:9100/sse"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.config'`

- [ ] **Step 4: Write minimal implementation**

```python
# dispatcher/config.py
from __future__ import annotations

import dataclasses

import yaml


@dataclasses.dataclass
class AccountConfig:
    name: str
    container: str


@dataclasses.dataclass
class Config:
    accounts: list[AccountConfig]
    quota_threshold_pct: int
    heartbeat_ttl_seconds: int
    heartbeat_interval_seconds: int
    projects_root: str
    hive_tasks_dir: str
    state_dir: str
    vibe_kanban_mcp_url: str
    collector_url: str


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    accounts = [AccountConfig(**a) for a in raw["accounts"]]
    return Config(
        accounts=accounts,
        quota_threshold_pct=raw.get("quota_threshold_pct", 90),
        heartbeat_ttl_seconds=raw.get("heartbeat_ttl_seconds", 120),
        heartbeat_interval_seconds=raw.get("heartbeat_interval_seconds", 30),
        projects_root=raw["projects_root"],
        hive_tasks_dir=raw["hive_tasks_dir"],
        state_dir=raw["state_dir"],
        vibe_kanban_mcp_url=raw["vibe_kanban_mcp_url"],
        collector_url=raw["collector_url"],
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore dispatcher/__init__.py dispatcher/config.py tests/__init__.py tests/dispatcher/__init__.py tests/dispatcher/test_config.py
git commit -m "feat: add project scaffolding and config loader"
```

---

## Task 2: Per-account state machine

**Files:**
- Create: `dispatcher/state_machine.py`
- Test: `tests/dispatcher/test_state_machine.py`

**Interfaces:**
- Consumes: `AccountConfig` from Task 1.
- Produces: `class AccountState(str, Enum)` with members `IDLE`, `BUSY`, `PRE_COOLDOWN`, `COOLING_DOWN`; `get_state(state_dir: str, account_name: str) -> AccountState`; `set_state(state_dir: str, account_name: str, state: AccountState, current_task_id: str | None = None) -> None`; `get_current_task(state_dir: str, account_name: str) -> str | None`; `list_idle_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_state_machine.py
from pathlib import Path

from dispatcher.config import AccountConfig
from dispatcher.state_machine import (
    AccountState,
    get_current_task,
    get_state,
    list_idle_accounts,
    set_state,
)


def test_get_state_defaults_to_idle(tmp_path: Path) -> None:
    assert get_state(str(tmp_path), "cuenta1") == AccountState.IDLE


def test_set_then_get_state_roundtrip(tmp_path: Path) -> None:
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    assert get_state(str(tmp_path), "cuenta1") == AccountState.BUSY
    assert get_current_task(str(tmp_path), "cuenta1") == "task-1"


def test_list_idle_accounts_filters_by_state(tmp_path: Path) -> None:
    accounts = [AccountConfig(name="cuenta1", container="c1"), AccountConfig(name="cuenta2", container="c2")]
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY)

    assert list_idle_accounts(str(tmp_path), accounts) == ["cuenta2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_state_machine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.state_machine'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/state_machine.py
from __future__ import annotations

import enum
import json
import os
from pathlib import Path

from dispatcher.config import AccountConfig


class AccountState(str, enum.Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"
    PRE_COOLDOWN = "PRE_COOLDOWN"
    COOLING_DOWN = "COOLING_DOWN"


def _state_path(state_dir: str, account_name: str) -> str:
    return os.path.join(state_dir, f"{account_name}.json")


def get_state(state_dir: str, account_name: str) -> AccountState:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return AccountState.IDLE
    data = json.loads(Path(path).read_text())
    return AccountState(data["state"])


def get_current_task(state_dir: str, account_name: str) -> str | None:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return None
    data = json.loads(Path(path).read_text())
    return data.get("current_task_id")


def set_state(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None = None,
) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, account_name)
    Path(path).write_text(json.dumps({"state": state.value, "current_task_id": current_task_id}))


def list_idle_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]:
    return [a.name for a in accounts if get_state(state_dir, a.name) == AccountState.IDLE]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_state_machine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/state_machine.py tests/dispatcher/test_state_machine.py
git commit -m "feat: add disk-persisted per-account state machine"
```

---

## Task 3: Quota parsing (`/usage` output)

**Files:**
- Create: `dispatcher/quota.py`
- Test: `tests/dispatcher/test_quota.py`

**Interfaces:**
- Produces: `UsageInfo(session_pct: int, session_reset: str, week_pct: int, week_reset: str)`; `parse_usage_output(text: str) -> UsageInfo`; `exceeds_threshold(usage: UsageInfo, threshold_pct: int) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_quota.py
import pytest

from dispatcher.quota import exceeds_threshold, parse_usage_output

USAGE_TEXT = (
    "Current session: 33% used · resets Sep 13, 10:50pm (America/Santiago)\n"
    "Current week (all models): 24% used · resets Sep 18, 11am (America/Santiago)"
)


def test_parse_usage_output() -> None:
    usage = parse_usage_output(USAGE_TEXT)

    assert usage.session_pct == 33
    assert usage.session_reset == "Sep 13, 10:50pm (America/Santiago)"
    assert usage.week_pct == 24
    assert usage.week_reset == "Sep 18, 11am (America/Santiago)"


def test_exceeds_threshold_true_when_either_window_over() -> None:
    usage = parse_usage_output(USAGE_TEXT)

    assert exceeds_threshold(usage, 20) is True
    assert exceeds_threshold(usage, 50) is False


def test_parse_usage_output_raises_on_unexpected_format() -> None:
    with pytest.raises(ValueError):
        parse_usage_output("something unexpected")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_quota.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.quota'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/quota.py
from __future__ import annotations

import dataclasses
import re

# The `/usage` command returns free text, not a documented structured API
# (design spec sec. 4a) — parse defensively and fail loudly on drift rather
# than silently misreading a changed template.
_SESSION_RE = re.compile(r"Current session:\s*(\d+)%\s*used\s*·\s*resets\s*(.+)")
_WEEK_RE = re.compile(r"Current week[^:]*:\s*(\d+)%\s*used\s*·\s*resets\s*(.+)")


@dataclasses.dataclass
class UsageInfo:
    session_pct: int
    session_reset: str
    week_pct: int
    week_reset: str


def parse_usage_output(text: str) -> UsageInfo:
    session_match = _SESSION_RE.search(text)
    week_match = _WEEK_RE.search(text)
    if not session_match or not week_match:
        raise ValueError(f"Unexpected /usage output format: {text!r}")
    return UsageInfo(
        session_pct=int(session_match.group(1)),
        session_reset=session_match.group(2).strip(),
        week_pct=int(week_match.group(1)),
        week_reset=week_match.group(2).strip(),
    )


def exceeds_threshold(usage: UsageInfo, threshold_pct: int) -> bool:
    return usage.session_pct >= threshold_pct or usage.week_pct >= threshold_pct
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_quota.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/quota.py tests/dispatcher/test_quota.py
git commit -m "feat: add defensive /usage output parsing"
```

---

## Task 4: Docker exec wrapper, Claude invocation, worktree creation

**Files:**
- Create: `dispatcher/docker_exec.py`
- Test: `tests/dispatcher/test_docker_exec.py`

**Interfaces:**
- Produces: `ClaudeResult(session_id: str | None, result_text: str, raw: dict)`; `run_docker_exec(container: str, workdir: str, command: list[str]) -> subprocess.CompletedProcess`; `exec_claude(container: str, workdir: str, prompt: str, resume_session_id: str | None = None) -> ClaudeResult`; `create_worktree(container: str, projects_root: str, slug: str, task_id: str, role: str) -> str` (branch name is `agent/<role>/<task-id>` per spec §7).

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_docker_exec.py
import json
import subprocess

import dispatcher.docker_exec as docker_exec_mod
from dispatcher.docker_exec import create_worktree, exec_claude, run_docker_exec


def test_run_docker_exec_builds_expected_command(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    run_docker_exec("agent-cuenta1", "/data/projects/foo/worktrees/task-1", ["echo", "hi"])

    assert captured["cmd"] == [
        "docker", "exec", "-w", "/data/projects/foo/worktrees/task-1", "agent-cuenta1", "echo", "hi",
    ]


def test_exec_claude_parses_session_id_and_result(monkeypatch) -> None:
    payload = {"session_id": "sess-123", "result": "done", "is_error": False}

    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/data/projects/foo/worktrees/task-1", "do it")

    assert result.session_id == "sess-123"
    assert result.result_text == "done"
    assert result.raw == payload


def test_exec_claude_passes_resume_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "continue", resume_session_id="sess-123")

    assert "--resume" in captured["cmd"]
    assert "sess-123" in captured["cmd"]


def test_create_worktree_builds_branch_name_and_tolerates_existing(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: already exists")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    path = create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert path == "/data/projects/myproj/worktrees/task-1"
    assert "agent/implementador/task-1" in captured["cmd"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_docker_exec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.docker_exec'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/docker_exec.py
from __future__ import annotations

import dataclasses
import json
import subprocess


@dataclasses.dataclass
class ClaudeResult:
    session_id: str | None
    result_text: str
    raw: dict


def run_docker_exec(container: str, workdir: str, command: list[str]) -> subprocess.CompletedProcess:
    full_command = ["docker", "exec", "-w", workdir, container, *command]
    return subprocess.run(full_command, capture_output=True, text=True)


def exec_claude(
    container: str,
    workdir: str,
    prompt: str,
    resume_session_id: str | None = None,
) -> ClaudeResult:
    command = ["claude"]
    if resume_session_id:
        command += ["--resume", resume_session_id]
    command += ["-p", prompt, "--output-format", "json"]
    proc = run_docker_exec(container, workdir, command)
    raw = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return ClaudeResult(session_id=raw.get("session_id"), result_text=raw.get("result", ""), raw=raw)


def create_worktree(container: str, projects_root: str, slug: str, task_id: str, role: str) -> str:
    project_dir = f"{projects_root}/{slug}"
    worktree_path = f"{project_dir}/worktrees/{task_id}"
    branch = f"agent/{role}/{task_id}"
    proc = run_docker_exec(container, project_dir, ["git", "worktree", "add", "-b", branch, worktree_path])
    if proc.returncode != 0 and "already exists" not in proc.stderr:
        raise RuntimeError(f"git worktree add failed: {proc.stderr}")
    return worktree_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_docker_exec.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/docker_exec.py tests/dispatcher/test_docker_exec.py
git commit -m "feat: add docker exec wrapper, claude invocation, worktree creation"
```

---

## Task 5: `.hive/tasks/<task-id>.md` context transfer, locking, TTL

**Files:**
- Create: `dispatcher/context_transfer.py`
- Test: `tests/dispatcher/test_context_transfer.py`

**Interfaces:**
- Produces: `TaskFile(task_id: str, status: str, owner: str | None, depends_on: list[str], heartbeat: str | None, body: str)`; `task_file_path(hive_dir: str, task_id: str) -> str`; `list_task_ids(hive_dir: str) -> list[str]`; `read_task_file(path: str) -> TaskFile`; `write_task_file(path: str, task: TaskFile) -> None`; `acquire_lock(hive_dir: str, task_id: str, owner: str) -> TaskFile`; `refresh_heartbeat(hive_dir: str, task_id: str) -> None`; `is_lock_expired(task: TaskFile, ttl_seconds: int, now: datetime | None = None) -> bool`; `release_stale_lock(hive_dir: str, task_id: str) -> None`; `handoff(hive_dir: str, task_id: str, new_status: str, body: str, depends_on: list[str] | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_context_transfer.py
import datetime as dt
from pathlib import Path

from dispatcher.context_transfer import (
    acquire_lock,
    handoff,
    is_lock_expired,
    list_task_ids,
    read_task_file,
    refresh_heartbeat,
    release_stale_lock,
    task_file_path,
    TaskFile,
    write_task_file,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=["task-0"], heartbeat=None, body="hello")

    write_task_file(path, task)
    result = read_task_file(path)

    assert result == task


def test_acquire_lock_sets_owner_and_moves_pending_to_in_progress(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)

    task = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    assert task.owner == "cuenta1"
    assert task.status == "in_progress"
    assert task.heartbeat is not None


def test_refresh_heartbeat_updates_timestamp(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    first = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    refresh_heartbeat(hive_dir, "task-1")

    updated = read_task_file(task_file_path(hive_dir, "task-1"))
    assert updated.heartbeat != first.heartbeat or updated.heartbeat is not None


def test_is_lock_expired(tmp_path: Path) -> None:
    stale = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=200)).isoformat(),
        body="",
    )
    fresh = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=dt.datetime.now(dt.timezone.utc).isoformat(), body="",
    )

    assert is_lock_expired(stale, ttl_seconds=120) is True
    assert is_lock_expired(fresh, ttl_seconds=120) is False


def test_release_stale_lock_clears_owner_and_heartbeat(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    release_stale_lock(hive_dir, "task-1")

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.owner is None
    assert task.heartbeat is None
    assert task.status == "in_progress"


def test_handoff_sets_status_and_body_and_clears_lock(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    handoff(hive_dir, "task-1", new_status="pending", body="handoff summary", depends_on=["task-0"])

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.status == "pending"
    assert task.body == "handoff summary"
    assert task.owner is None
    assert task.depends_on == ["task-0"]


def test_list_task_ids(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    acquire_lock(hive_dir, "task-2", owner="cuenta1")

    assert sorted(list_task_ids(hive_dir)) == ["task-1", "task-2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_context_transfer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.context_transfer'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/context_transfer.py
from __future__ import annotations

import dataclasses
import datetime as dt
import os
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


@dataclasses.dataclass
class TaskFile:
    task_id: str
    status: str
    owner: str | None
    depends_on: list[str]
    heartbeat: str | None
    body: str


def task_file_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(hive_dir, f"{task_id}.md")


def list_task_ids(hive_dir: str) -> list[str]:
    if not os.path.isdir(hive_dir):
        return []
    return [Path(f).stem for f in os.listdir(hive_dir) if f.endswith(".md")]


def read_task_file(path: str) -> TaskFile:
    text = Path(path).read_text()
    _, fm_text, body = text.split(_FRONTMATTER_DELIM, 2)
    fm = yaml.safe_load(fm_text) or {}
    return TaskFile(
        task_id=fm["task_id"],
        status=fm["status"],
        owner=fm.get("owner"),
        depends_on=fm.get("depends_on", []),
        heartbeat=fm.get("heartbeat"),
        body=body.lstrip("\n"),
    )


def write_task_file(path: str, task: TaskFile) -> None:
    fm = {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
    }
    content = f"{_FRONTMATTER_DELIM}\n{yaml.safe_dump(fm, sort_keys=False)}{_FRONTMATTER_DELIM}\n\n{task.body}"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


def acquire_lock(hive_dir: str, task_id: str, owner: str) -> TaskFile:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        task = read_task_file(path)
    else:
        task = TaskFile(task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body="")
    task.owner = owner
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    if task.status == "pending":
        task.status = "in_progress"
    write_task_file(path, task)
    return task


def refresh_heartbeat(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    write_task_file(path, task)


def is_lock_expired(task: TaskFile, ttl_seconds: int, now: dt.datetime | None = None) -> bool:
    if task.heartbeat is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    heartbeat_dt = dt.datetime.fromisoformat(task.heartbeat)
    return (now - heartbeat_dt).total_seconds() > ttl_seconds


def release_stale_lock(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.owner = None
    task.heartbeat = None
    write_task_file(path, task)


def handoff(
    hive_dir: str,
    task_id: str,
    new_status: str,
    body: str,
    depends_on: list[str] | None = None,
) -> None:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        task = read_task_file(path)
    else:
        task = TaskFile(task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body="")
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    task.body = body
    if depends_on is not None:
        task.depends_on = depends_on
    write_task_file(path, task)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_context_transfer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/context_transfer.py tests/dispatcher/test_context_transfer.py
git commit -m "feat: add .hive/tasks context-transfer, locking and TTL expiry"
```

---

## Task 6: Vibe Kanban MCP client

**Files:**
- Create: `dispatcher/vibe_kanban_client.py`
- Test: `tests/dispatcher/test_vibe_kanban_client.py`

**Interfaces:**
- Produces: `KanbanTask(id: str, project: str, title: str, status: str)`; `class VibeKanbanClient: __init__(self, mcp_url: str)` with `list_tasks(self, project: str | None = None) -> list[KanbanTask]`, `create_task(self, project: str, title: str, description: str) -> str`, `update_task_status(self, task_id: str, status: str) -> None`.
- Note: talks only to the loopback-only local MCP server Vibe Kanban exposes (spec §1) — never a public URL. The internal `_call`/`_call_async` seam wraps the `mcp` SDK's `ClientSession.call_tool`; exact tool names (`list_tasks`/`create_task`/`update_task`) must be confirmed against the running Vibe Kanban MCP server during integration testing and adjusted here if they differ.

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_vibe_kanban_client.py
from dispatcher.vibe_kanban_client import VibeKanbanClient


def test_list_tasks_parses_call_result(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    monkeypatch.setattr(
        client, "_call",
        lambda tool, args: [{"id": "t1", "project": "myproj", "title": "Do X", "status": "pending"}],
    )

    tasks = client.list_tasks(project="myproj")

    assert len(tasks) == 1
    assert tasks[0].id == "t1"
    assert tasks[0].status == "pending"


def test_create_task_returns_id(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    captured = {}

    def fake_call(tool, args):
        captured["tool"] = tool
        captured["args"] = args
        return {"id": "t2"}

    monkeypatch.setattr(client, "_call", fake_call)

    task_id = client.create_task("myproj", "Do Y", "description")

    assert task_id == "t2"
    assert captured["tool"] == "create_task"
    assert captured["args"] == {"project": "myproj", "title": "Do Y", "description": "description"}


def test_update_task_status_calls_update_tool(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    captured = {}
    monkeypatch.setattr(client, "_call", lambda tool, args: captured.update(tool=tool, args=args))

    client.update_task_status("t1", "done")

    assert captured["tool"] == "update_task"
    assert captured["args"] == {"id": "t1", "status": "done"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_vibe_kanban_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.vibe_kanban_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/vibe_kanban_client.py
from __future__ import annotations

import asyncio
import dataclasses
import json

from mcp import ClientSession
from mcp.client.sse import sse_client


@dataclasses.dataclass
class KanbanTask:
    id: str
    project: str
    title: str
    status: str


class VibeKanbanClient:
    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url

    def _call(self, tool_name: str, arguments: dict):
        return asyncio.run(self._call_async(tool_name, arguments))

    async def _call_async(self, tool_name: str, arguments: dict):
        async with sse_client(self.mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)

    def list_tasks(self, project: str | None = None) -> list[KanbanTask]:
        arguments = {"project": project} if project else {}
        items = self._call("list_tasks", arguments)
        return [
            KanbanTask(id=i["id"], project=i["project"], title=i["title"], status=i["status"])
            for i in items
        ]

    def create_task(self, project: str, title: str, description: str) -> str:
        data = self._call("create_task", {"project": project, "title": title, "description": description})
        return data["id"]

    def update_task_status(self, task_id: str, status: str) -> None:
        self._call("update_task", {"id": task_id, "status": status})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_vibe_kanban_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/vibe_kanban_client.py tests/dispatcher/test_vibe_kanban_client.py
git commit -m "feat: add Vibe Kanban MCP client wrapper"
```

---

## Task 7: Dispatcher orchestration (state + quota + docker_exec + context_transfer)

**Files:**
- Create: `dispatcher/dispatcher.py`
- Test: `tests/dispatcher/test_dispatcher.py`

**Interfaces:**
- Consumes: `Config`/`AccountConfig` (Task 1), `AccountState`/state functions (Task 2), `parse_usage_output`/`exceeds_threshold` (Task 3), `ClaudeResult`/`exec_claude`/`create_worktree` (Task 4), `TaskFile`/lock functions (Task 5), `VibeKanbanClient` (Task 6).
- Produces: `DispatchResult(success: bool, session_id: str | None, result_text: str, account: str)`; `ROLE_SEQUENCE: list[str]` (`["arquitecto", "implementador", "revisor", "auditor"]`); `pick_idle_account(cfg: Config) -> str | None`; `check_quota_ok(cfg: Config, account: str) -> bool`; `is_rate_limit_error(result: ClaudeResult) -> bool`; `reap_expired_locks(cfg: Config) -> list[str]`; `dispatch_phase(cfg: Config, task_id: str, slug: str, role: str, prompt: str, resume_session_id: str | None = None) -> DispatchResult`; `run_task_cycle(cfg: Config, task_id: str, slug: str, kanban: VibeKanbanClient) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/dispatcher/test_dispatcher.py
import dispatcher.dispatcher as dispatcher_mod
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import acquire_lock, is_lock_expired, read_task_file, task_file_path
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state


def _make_config(tmp_path):
    return Config(
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1")],
        quota_threshold_pct=90,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        vibe_kanban_mcp_url="http://127.0.0.1:9100/sse",
        collector_url="http://127.0.0.1:8787",
    )


def test_dispatch_phase_success(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    assert result.session_id == "sess-1"
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_all_accounts_over_quota(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 95% used · resets later\n"
                "Current week (all models): 20% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN


def test_reap_expired_locks_releases_stale_owner(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert not is_lock_expired(task, cfg.heartbeat_ttl_seconds)

    import datetime as dt
    from dispatcher.context_transfer import write_task_file
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)

    reaped = dispatcher_mod.reap_expired_locks(cfg)

    assert reaped == ["task-1"]
    refreshed = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert refreshed.owner is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.dispatcher'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/dispatcher.py
from __future__ import annotations

import dataclasses
import threading

from dispatcher import context_transfer, docker_exec, quota, state_machine
from dispatcher.config import Config
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import VibeKanbanClient

ROLE_SEQUENCE = ["arquitecto", "implementador", "revisor", "auditor"]


@dataclasses.dataclass
class DispatchResult:
    success: bool
    session_id: str | None
    result_text: str
    account: str


class _HeartbeatLoop:
    def __init__(self, hive_dir: str, task_id: str, interval_seconds: int):
        self._hive_dir = hive_dir
        self._task_id = task_id
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            context_transfer.refresh_heartbeat(self._hive_dir, self._task_id)

    def __enter__(self) -> "_HeartbeatLoop":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def _container_for(cfg: Config, account: str) -> str:
    for acc in cfg.accounts:
        if acc.name == account:
            return acc.container
    raise ValueError(f"Unknown account: {account}")


def pick_idle_account(cfg: Config) -> str | None:
    idle = state_machine.list_idle_accounts(cfg.state_dir, cfg.accounts)
    return idle[0] if idle else None


def check_quota_ok(cfg: Config, account: str) -> bool:
    container = _container_for(cfg, account)
    result = docker_exec.exec_claude(container, cfg.projects_root, "/usage")
    usage = quota.parse_usage_output(result.result_text)
    if quota.exceeds_threshold(usage, cfg.quota_threshold_pct):
        state_machine.set_state(cfg.state_dir, account, AccountState.PRE_COOLDOWN)
        return False
    return True


def is_rate_limit_error(result: docker_exec.ClaudeResult) -> bool:
    # Exact reactive signal strings are unverified against the real Claude
    # Code CLI (design spec sec. 4a caveat) — match conservatively and
    # tighten once confirmed empirically.
    if not result.raw.get("is_error"):
        return False
    text = (result.result_text or "").lower()
    return any(term in text for term in ("rate limit", "usage limit", "quota"))


def reap_expired_locks(cfg: Config) -> list[str]:
    reaped = []
    for task_id in context_transfer.list_task_ids(cfg.hive_tasks_dir):
        task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id))
        if task.status == "in_progress" and context_transfer.is_lock_expired(task, cfg.heartbeat_ttl_seconds):
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            reaped.append(task_id)
    return reaped


def dispatch_phase(
    cfg: Config,
    task_id: str,
    slug: str,
    role: str,
    prompt: str,
    resume_session_id: str | None = None,
) -> DispatchResult:
    tried: set[str] = set()
    while True:
        account = pick_idle_account(cfg)
        if account is None or account in tried:
            return DispatchResult(success=False, session_id=resume_session_id, result_text="no accounts available", account="")
        tried.add(account)

        if not check_quota_ok(cfg, account):
            continue

        container = _container_for(cfg, account)
        state_machine.set_state(cfg.state_dir, account, AccountState.BUSY, current_task_id=task_id)
        workdir = docker_exec.create_worktree(container, cfg.projects_root, slug, task_id, role)
        context_transfer.acquire_lock(cfg.hive_tasks_dir, task_id, owner=account)

        with _HeartbeatLoop(cfg.hive_tasks_dir, task_id, cfg.heartbeat_interval_seconds):
            result = docker_exec.exec_claude(container, workdir, prompt, resume_session_id=resume_session_id)

        if is_rate_limit_error(result):
            state_machine.set_state(cfg.state_dir, account, AccountState.COOLING_DOWN)
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            resume_session_id = result.session_id or resume_session_id
            continue

        state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
        return DispatchResult(success=True, session_id=result.session_id, result_text=result.result_text, account=account)


def run_task_cycle(cfg: Config, task_id: str, slug: str, kanban: VibeKanbanClient) -> None:
    resume_session_id: str | None = None
    for role in ROLE_SEQUENCE:
        kanban.update_task_status(task_id, f"in_progress:{role}")
        result = dispatch_phase(
            cfg, task_id, slug, role,
            prompt=f"Role: {role}. Task: {task_id}.",
            resume_session_id=resume_session_id,
        )
        if not result.success:
            kanban.update_task_status(task_id, "blocked")
            return
        context_transfer.handoff(
            cfg.hive_tasks_dir, task_id,
            new_status="done" if role == ROLE_SEQUENCE[-1] else "pending",
            body=result.result_text[:2000],
        )
        resume_session_id = None
    kanban.update_task_status(task_id, "done")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_dispatcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/dispatcher.py tests/dispatcher/test_dispatcher.py
git commit -m "feat: add dispatcher orchestration with quota failover and heartbeats"
```

---

## Task 8: CLI entrypoint

**Files:**
- Create: `dispatcher/cli.py`
- Test: `tests/dispatcher/test_cli.py`

**Interfaces:**
- Consumes: `load_config` (Task 1), `VibeKanbanClient` (Task 6), `run_task_cycle` (Task 7).
- Produces: `main() -> None` (console entrypoint, invoked as `python -m dispatcher.cli run-task --config <path> --task-id <id> --project <slug>`).

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatcher/test_cli.py
import sys
from pathlib import Path

import dispatcher.cli as cli_mod

CONFIG_YAML = """
accounts:
  - name: cuenta1
    container: agent-cuenta1
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: /data/.hive/tasks
state_dir: /data/dispatcher_state
vibe_kanban_mcp_url: http://127.0.0.1:9100/sse
collector_url: http://127.0.0.1:8787
"""


def test_cli_run_task_invokes_run_task_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    captured = {}
    monkeypatch.setattr(
        cli_mod, "run_task_cycle",
        lambda cfg, task_id, slug, kanban: captured.update(task_id=task_id, slug=slug),
    )
    monkeypatch.setattr(sys, "argv", ["ia-harness-dispatcher", "--config", str(config_path), "run-task", "--task-id", "task-1", "--project", "myproj"])

    cli_mod.main()

    assert captured == {"task_id": "task-1", "slug": "myproj"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dispatcher/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dispatcher.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# dispatcher/cli.py
from __future__ import annotations

import argparse

from dispatcher.config import load_config
from dispatcher.dispatcher import run_task_cycle
from dispatcher.vibe_kanban_client import VibeKanbanClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="ia-harness-dispatcher")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-task", help="Run the full role cycle for one task")
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--project", required=True, help="Project slug")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "run-task":
        kanban = VibeKanbanClient(cfg.vibe_kanban_mcp_url)
        run_task_cycle(cfg, args.task_id, args.project, kanban)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/dispatcher/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dispatcher/cli.py tests/dispatcher/test_cli.py
git commit -m "feat: add dispatcher CLI entrypoint"
```

---

## Task 9: Observability collector (SQLite WAL + Flask)

**Files:**
- Create: `observability/__init__.py`, `observability/collector/__init__.py`
- Create: `observability/collector/schema.sql`
- Create: `observability/collector/db.py`
- Create: `observability/collector/server.py`
- Create: `tests/observability/__init__.py`
- Test: `tests/observability/test_collector.py`

**Interfaces:**
- Produces: `init_db(db_path: str) -> None`; `insert_event(db_path: str, source_app: str, event_type: str, payload: dict) -> None`; `list_events(db_path: str, limit: int = 100, source_app: str | None = None) -> list[dict]`; `create_app(db_path: str) -> Flask` with `POST /events` and `GET /events`.

- [ ] **Step 1: Write the failing test**

```python
# tests/observability/test_collector.py
from pathlib import Path

from observability.collector.server import create_app


def test_post_then_get_events(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    app = create_app(db_path)
    client = app.test_client()

    resp = client.post(
        "/events",
        json={"source_app": "agent-cuenta1", "event_type": "PreToolUse", "payload": {"tool": "Bash"}},
    )
    assert resp.status_code == 201

    resp = client.get("/events")
    assert resp.status_code == 200
    events = resp.get_json()
    assert len(events) == 1
    assert events[0]["source_app"] == "agent-cuenta1"
    assert events[0]["event_type"] == "PreToolUse"
    assert events[0]["payload"] == {"tool": "Bash"}


def test_post_event_requires_source_app_and_event_type(tmp_path: Path) -> None:
    app = create_app(str(tmp_path / "events.db"))
    client = app.test_client()

    resp = client.post("/events", json={"payload": {}})

    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/observability/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'observability.collector.server'`

- [ ] **Step 3: Write minimal implementation**

```sql
-- observability/collector/schema.sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_events_source_app ON events(source_app);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
```

```python
# observability/collector/db.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def insert_event(db_path: str, source_app: str, event_type: str, payload: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO events (source_app, event_type, payload) VALUES (?, ?, ?)",
            (source_app, event_type, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def list_events(db_path: str, limit: int = 100, source_app: str | None = None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if source_app:
            rows = conn.execute(
                "SELECT * FROM events WHERE source_app = ? ORDER BY id DESC LIMIT ?",
                (source_app, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            {
                "id": r["id"],
                "source_app": r["source_app"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()
```

```python
# observability/collector/server.py
from __future__ import annotations

import os

from flask import Flask, jsonify, request

from observability.collector import db


def create_app(db_path: str) -> Flask:
    db.init_db(db_path)
    app = Flask(__name__)

    @app.post("/events")
    def post_event():
        body = request.get_json(force=True)
        source_app = body.get("source_app")
        event_type = body.get("event_type")
        payload = body.get("payload", {})
        if not source_app or not event_type:
            return jsonify({"error": "source_app and event_type are required"}), 400
        db.insert_event(db_path, source_app, event_type, payload)
        return jsonify({"status": "ok"}), 201

    @app.get("/events")
    def get_events():
        limit = int(request.args.get("limit", 100))
        source_app = request.args.get("source_app")
        return jsonify(db.list_events(db_path, limit=limit, source_app=source_app))

    return app


if __name__ == "__main__":
    app = create_app(os.environ.get("COLLECTOR_DB_PATH", "/data/events.db"))
    app.run(host="0.0.0.0", port=8787)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/observability/test_collector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add observability/__init__.py observability/collector tests/observability/__init__.py tests/observability/test_collector.py
git commit -m "feat: add SQLite WAL event collector"
```

---

## Task 10: Observability dashboard (auth'd viewer)

**Files:**
- Create: `observability/dashboard/__init__.py`
- Create: `observability/dashboard/app.py`
- Test: `tests/observability/test_dashboard.py`

**Interfaces:**
- Consumes: `db.list_events` from Task 9.
- Produces: `create_app(db_path: str, username: str, password_hash: str) -> Flask` with HTTP-Basic-auth'd `GET /` (per spec §6: dashboard needs its own auth beyond Tailnet membership).

- [ ] **Step 1: Write the failing test**

```python
# tests/observability/test_dashboard.py
import base64
from pathlib import Path

from observability.collector import db as collector_db
from observability.dashboard.app import create_app


def _auth_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_index_requires_auth(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d")
    client = app.test_client()

    resp = client.get("/")

    assert resp.status_code == 401


def test_index_with_valid_auth_shows_events(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    collector_db.insert_event(db_path, "agent-cuenta1", "PreToolUse", {"tool": "Bash"})
    # sha256("password") = 5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("admin", "password"))

    assert resp.status_code == 200
    assert b"agent-cuenta1" in resp.data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/observability/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'observability.dashboard.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# observability/dashboard/app.py
from __future__ import annotations

import hashlib
import os
from functools import wraps

from flask import Flask, Response, request

from observability.collector import db


def create_app(db_path: str, username: str, password_hash: str) -> Flask:
    app = Flask(__name__)

    def check_auth(user: str, password: str) -> bool:
        return user == username and hashlib.sha256(password.encode()).hexdigest() == password_hash

    def requires_auth(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return Response(
                    "Authentication required", 401,
                    {"WWW-Authenticate": 'Basic realm="ia-harness dashboard"'},
                )
            return f(*args, **kwargs)
        return wrapper

    @app.get("/")
    @requires_auth
    def index():
        events = db.list_events(db_path, limit=200)
        rows = "".join(
            f"<tr><td>{e['created_at']}</td><td>{e['source_app']}</td><td>{e['event_type']}</td></tr>"
            for e in events
        )
        return f"<table><tr><th>Time</th><th>Agent</th><th>Event</th></tr>{rows}</table>"

    return app


if __name__ == "__main__":
    app = create_app(
        os.environ.get("COLLECTOR_DB_PATH", "/data/events.db"),
        os.environ["DASHBOARD_USERNAME"],
        os.environ["DASHBOARD_PASSWORD_HASH"],
    )
    app.run(host="0.0.0.0", port=8788)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/observability/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add observability/dashboard tests/observability/test_dashboard.py
git commit -m "feat: add auth'd observability dashboard"
```

---

## Task 11: Claude Code hook → collector event emitter

**Files:**
- Create: `hooks/__init__.py`
- Create: `hooks/emit_event.py`
- Create: `tests/hooks/__init__.py`
- Test: `tests/hooks/test_emit_event.py`

**Interfaces:**
- Produces: `main() -> None` — reads a Claude Code hook JSON payload from stdin, POSTs `{source_app, event_type, payload}` to `${COLLECTOR_URL}/events` (matches `POST /events` from Task 9).

- [ ] **Step 1: Write the failing test**

```python
# tests/hooks/test_emit_event.py
import io
import json

import hooks.emit_event as emit_event_mod


def test_main_posts_event_with_source_app_tag(monkeypatch) -> None:
    stdin_payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(stdin_payload)))
    monkeypatch.setenv("COLLECTOR_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("SOURCE_APP", "agent-cuenta1")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    assert captured["url"] == "http://127.0.0.1:8787/events"
    assert captured["json"] == {
        "source_app": "agent-cuenta1",
        "event_type": "PreToolUse",
        "payload": stdin_payload,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/hooks/test_emit_event.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hooks.emit_event'`

- [ ] **Step 3: Write minimal implementation**

```python
# hooks/emit_event.py
from __future__ import annotations

import json
import os
import sys

import requests


def main() -> None:
    event = json.load(sys.stdin)
    collector_url = os.environ["COLLECTOR_URL"]
    source_app = os.environ["SOURCE_APP"]
    body = {
        "source_app": source_app,
        "event_type": event.get("hook_event_name", "unknown"),
        "payload": event,
    }
    requests.post(f"{collector_url}/events", json=body, timeout=5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/hooks/test_emit_event.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/__init__.py hooks/emit_event.py tests/hooks/__init__.py tests/hooks/test_emit_event.py
git commit -m "feat: add Claude Code hook event emitter"
```

---

## Task 12: Agent image + registry mirror config

**Files:**
- Create: `docker/agent/Dockerfile`
- Create: `docker/registry-mirror/config.yml`

**Interfaces:**
- Produces: an `agent` image (Claude Code CLI + git + `docker` CLI client, no daemon) consumed by `docker-compose.agents.yml` in Task 15; a `registry:2` pull-through config consumed by the `registry-mirror` service in Task 15 and referenced by `--registry-mirrors` in the sidecars from Task 14.

- [ ] **Step 1: Write the agent Dockerfile**

```dockerfile
# docker/agent/Dockerfile
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        docker-cli \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /data/projects
CMD ["sleep", "infinity"]
```

- [ ] **Step 2: Write the registry mirror config**

```yaml
# docker/registry-mirror/config.yml
version: 0.1
log:
  fields:
    service: registry
storage:
  cache:
    blobdescriptor: inmemory
  filesystem:
    rootdirectory: /var/lib/registry
proxy:
  remoteurl: https://registry-1.docker.io
http:
  addr: :5000
```

- [ ] **Step 3: Verify the agent image builds and the CLI is present**

Run: `docker build -t ia-harness-agent -f docker/agent/Dockerfile docker/agent`
Expected: build succeeds; `docker run --rm ia-harness-agent claude --version` prints a version string.

- [ ] **Step 4: Verify the registry config is valid**

Run: `docker run --rm -v "$(pwd)/docker/registry-mirror/config.yml:/etc/docker/registry/config.yml:ro" registry:2 --help`
Expected: no config-parse error (the `registry:2` entrypoint validates `config.yml` before printing usage).

- [ ] **Step 5: Commit**

```bash
git add docker/agent/Dockerfile docker/registry-mirror/config.yml
git commit -m "feat: add agent image and registry-mirror config"
```

---

## Task 13: Volume setup + prune scripts

**Files:**
- Create: `scripts/setup_volumes.sh`
- Create: `scripts/prune.sh`

**Interfaces:**
- Produces: `scripts/setup_volumes.sh <account1> [account2 ...]` — creates the shared `claude_shared`/`projects_data` volumes plus one `claude_creds_<account>` volume per account (spec §3: shared `~/.claude` volume, shadowed at the credentials sub-path by a per-account volume), consumed by `docker-compose.agents.yml` in Task 15 as `external: true` volumes. `scripts/prune.sh` — runs `docker system prune -af --volumes`, consumed by the sidecar cron in Task 14.

- [ ] **Step 1: Write `setup_volumes.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Creates the shared ~/.claude volume plus one shadowed credentials volume
# per account (design spec sec. 3). Run once before the first
# `docker compose -f docker-compose.agents.yml up`.

ACCOUNTS=("$@")
if [ ${#ACCOUNTS[@]} -eq 0 ]; then
    echo "Usage: $0 <account1> [account2 ...]" >&2
    exit 1
fi

docker volume inspect claude_shared >/dev/null 2>&1 || docker volume create claude_shared
docker volume inspect projects_data >/dev/null 2>&1 || docker volume create projects_data

for account in "${ACCOUNTS[@]}"; do
    vol="claude_creds_${account}"
    docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create "$vol"
    echo "Volume ready: $vol (shadow-mount at /root/.claude/credentials for ${account})"
done
```

- [ ] **Step 2: Write `prune.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Runs inside each docker:dind sidecar via cron (design spec sec. 4b) so the
# registry-mirror/BuildKit-cache disk savings aren't eaten by unused
# image/volume accumulation.

docker system prune -af --volumes
```

- [ ] **Step 3: Make both scripts executable and syntax-check them**

Run: `chmod +x scripts/setup_volumes.sh scripts/prune.sh && bash -n scripts/setup_volumes.sh && bash -n scripts/prune.sh`
Expected: no output (both scripts parse cleanly).

- [ ] **Step 4: Verify `setup_volumes.sh` against a real Docker daemon**

Run: `scripts/setup_volumes.sh cuenta1 cuenta2 && docker volume ls | grep claude_creds`
Expected: `claude_creds_cuenta1` and `claude_creds_cuenta2` listed (run this on a host with Docker available — not part of the pytest suite, since it needs a real daemon).

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_volumes.sh scripts/prune.sh
git commit -m "feat: add volume setup and prune scripts"
```

---

## Task 14: DooD sidecar image (sysbox, cron-based prune)

**Files:**
- Create: `docker/dind-sidecar/Dockerfile`
- Create: `docker/dind-sidecar/crontab`

**Interfaces:**
- Consumes: `scripts/prune.sh` from Task 13.
- Produces: a `dind-sidecar` image (stock `docker:dind` + cron running `prune.sh` daily) consumed by `docker-compose.agents.yml` in Task 15, which sets `runtime: sysbox-runc` on it (spec §4b: sidecar hardened via sysbox, never `--privileged`).

- [ ] **Step 1: Write the crontab**

```
# docker/dind-sidecar/crontab
0 3 * * * /usr/local/bin/prune.sh >> /var/log/prune.log 2>&1
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# docker/dind-sidecar/Dockerfile
FROM docker:dind

RUN apk add --no-cache dcron

COPY prune.sh /usr/local/bin/prune.sh
RUN chmod +x /usr/local/bin/prune.sh
COPY crontab /etc/crontabs/root

ENTRYPOINT ["sh", "-c", "crond -b && dockerd-entrypoint.sh"]
```

- [ ] **Step 3: Copy `prune.sh` into the build context and build the image**

Run: `cp scripts/prune.sh docker/dind-sidecar/prune.sh && docker build -t ia-harness-dind-sidecar docker/dind-sidecar`
Expected: build succeeds.

- [ ] **Step 4: Verify cron is registered inside the built image**

Run: `docker run --rm ia-harness-dind-sidecar crontab -l`
Expected: prints `0 3 * * * /usr/local/bin/prune.sh >> /var/log/prune.log 2>&1`.

- [ ] **Step 5: Commit**

```bash
git add docker/dind-sidecar/Dockerfile docker/dind-sidecar/crontab docker/dind-sidecar/prune.sh
git commit -m "feat: add hardened dind sidecar image with cron-based prune"
```

---

## Task 15: Compose wiring (services, sysbox runtime, resource limits, shadowed volumes)

**Files:**
- Create: `docker/compose/docker-compose.yml`
- Create: `docker/compose/docker-compose.agents.yml`

**Interfaces:**
- Consumes: `docker/agent/Dockerfile` and `docker/registry-mirror/config.yml` (Task 12), `docker/dind-sidecar/Dockerfile` (Task 14), volumes created by `scripts/setup_volumes.sh` (Task 13), the `dispatcher`/`observability` packages (Tasks 7, 9, 10).
- Produces: a running topology matching spec §5's diagram — Vibe Kanban, Smart Dispatcher, collector, dashboard, registry mirror in `docker-compose.yml`; per-account agent + `docker:dind` sidecar pairs (with example `--cpus`/`--memory` limits per spec §7, and `runtime: sysbox-runc` with no `--privileged` per spec §4b) in `docker-compose.agents.yml`.

- [ ] **Step 1: Write the core services compose file**

```yaml
# docker/compose/docker-compose.yml
services:
  vibe-kanban:
    image: ghcr.io/bloopai/vibe-kanban:latest
    restart: unless-stopped
    network_mode: "host"  # MCP server must stay loopback-only (spec sec. 1)
    volumes:
      - vibe_kanban_data:/data

  collector:
    build:
      context: ../..
      dockerfile: observability/collector/Dockerfile
    restart: unless-stopped
    ports:
      - "127.0.0.1:8787:8787"
    volumes:
      - observability_data:/data
    environment:
      - COLLECTOR_DB_PATH=/data/events.db

  dashboard:
    build:
      context: ../..
      dockerfile: observability/dashboard/Dockerfile
    restart: unless-stopped
    ports:
      - "127.0.0.1:8788:8788"
    volumes:
      - observability_data:/data
    environment:
      - COLLECTOR_DB_PATH=/data/events.db
      - DASHBOARD_USERNAME=${DASHBOARD_USERNAME}
      - DASHBOARD_PASSWORD_HASH=${DASHBOARD_PASSWORD_HASH}

  registry-mirror:
    image: registry:2
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"
    volumes:
      - registry_mirror_data:/var/lib/registry
      - ../registry-mirror/config.yml:/etc/docker/registry/config.yml:ro

  dispatcher:
    build:
      context: ../..
      dockerfile: docker/dispatcher/Dockerfile
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ../../dispatcher_state:/state
      - ../../.hive:/data/.hive
      - claude_shared:/root/.claude
    environment:
      - IA_HARNESS_CONFIG=/app/config.yaml
    depends_on:
      - vibe-kanban
      - collector

volumes:
  vibe_kanban_data:
  observability_data:
  registry_mirror_data:
  claude_shared:
    external: true
```

- [ ] **Step 2: Write the per-account agent compose file**

```yaml
# docker/compose/docker-compose.agents.yml
# One agent+sidecar block per Claude Pro account. Duplicate the pair for
# additional accounts — no dispatcher code changes required (design spec,
# "Decisiones de alcance": N accounts without structural changes).
services:
  agent-cuenta1:
    image: ia-harness-agent
    container_name: agent-cuenta1
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-cuenta1:2375
    volumes:
      - claude_shared:/root/.claude
      - claude_creds_cuenta1:/root/.claude/credentials
      - projects_data:/data/projects
    depends_on:
      - dind-cuenta1

  dind-cuenta1:
    image: ia-harness-dind-sidecar
    container_name: dind-cuenta1
    restart: unless-stopped
    runtime: sysbox-runc
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_TLS_CERTDIR=
    command: ["--registry-mirror=http://registry-mirror:5000"]
    volumes:
      - dind_cuenta1_data:/var/lib/docker

  agent-cuenta2:
    image: ia-harness-agent
    container_name: agent-cuenta2
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-cuenta2:2375
    volumes:
      - claude_shared:/root/.claude
      - claude_creds_cuenta2:/root/.claude/credentials
      - projects_data:/data/projects
    depends_on:
      - dind-cuenta2

  dind-cuenta2:
    image: ia-harness-dind-sidecar
    container_name: dind-cuenta2
    restart: unless-stopped
    runtime: sysbox-runc
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_TLS_CERTDIR=
    command: ["--registry-mirror=http://registry-mirror:5000"]
    volumes:
      - dind_cuenta2_data:/var/lib/docker

volumes:
  claude_shared:
    external: true
  claude_creds_cuenta1:
    external: true
  claude_creds_cuenta2:
    external: true
  projects_data:
    external: true
  dind_cuenta1_data:
  dind_cuenta2_data:
```

- [ ] **Step 3: Validate both compose files parse**

Run: `docker compose -f docker/compose/docker-compose.yml config -q && docker compose -f docker/compose/docker-compose.agents.yml config -q`
Expected: no output, exit code 0 (both files are syntactically valid and internally consistent).

- [ ] **Step 4: Verify the shadow-mount ordering on a real host**

Run (after Task 13's `scripts/setup_volumes.sh cuenta1 cuenta2` and `sysbox-runc` installed on the host): `docker compose -f docker/compose/docker-compose.agents.yml up -d && docker exec agent-cuenta1 sh -c "mount | grep /root/.claude/credentials"`
Expected: the mount table shows `claude_creds_cuenta1` mounted at `/root/.claude/credentials`, confirming the shared `claude_shared` volume's credentials sub-path is shadowed per-account (spec §3) — this step needs a real Docker host with `sysbox-runc` installed, so it is a manual verification, not part of the pytest suite.

- [ ] **Step 5: Commit**

```bash
git add docker/compose/docker-compose.yml docker/compose/docker-compose.agents.yml
git commit -m "feat: wire compose topology with sysbox sidecars and resource limits"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| §1 Vibe Kanban (control UI, MCP integration, not the orchestrator) | Task 6 (`vibe_kanban_client.py`), Task 7 (`run_task_cycle` drives it), Task 15 (`vibe-kanban` service, `network_mode: host` keeps its MCP server loopback-only) |
| §2 Multi-project routing (`docker exec -w`, subprojects via `git clone`) | Task 4 (`exec_claude`/`create_worktree` build the `-w .../worktrees/<task-id>` invocation); subproject `git clone` under `subprojects/` is a per-project repo convention, not harness code — documented here, exercised operationally when a project is onboarded |
| §3 Volume/credential isolation (nested/shadowed volumes) | Task 13 (`setup_volumes.sh`), Task 15 (`claude_shared` + per-account `claude_creds_<account>` shadow-mounted at `/root/.claude/credentials`) |
| §4a State machine, quota detection, context transfer, crash failover | Task 2 (state machine), Task 3 (quota parsing), Task 5 (`.hive/tasks` locking/TTL), Task 7 (`dispatch_phase`, `reap_expired_locks`, `--resume` handoff via `resume_session_id`) |
| §4b DooD hardening (dind sidecar, sysbox, no `--privileged`, mirror/cache/prune) | Task 12 (agent image, registry mirror config), Task 13 (`prune.sh`), Task 14 (sidecar image + cron), Task 15 (`runtime: sysbox-runc`, no `--privileged` anywhere, `--registry-mirror` flag) |
| §5 End-to-end operational flow | Task 7 (`run_task_cycle` implements the 6-step sequence: role loop, lock/heartbeat, failover, final `done` status), Task 8 (CLI entrypoint) |
| §6 Observability (hooks→HTTP→SQLite→dashboard, `source_app`, auth) | Task 9 (collector), Task 10 (auth'd dashboard), Task 11 (hook emitter tags `source_app`) |
| §7 Resource limits + branch convention | Task 15 (`cpus`/`mem_limit` on every agent+sidecar), Task 4 (`agent/<role>/<task-id>` branch name in `create_worktree`) |
| §8 Serial-only scope boundary | Stated in Global Constraints; no task implements per-account-subagents-by-quota or independent-task-claiming — `dispatch_phase`/`run_task_cycle` process exactly one account/task at a time by construction |

No spec requirement is missing a task.

**Placeholder scan:** No task contains "TBD", "TODO", "add appropriate error handling", or "similar to Task N" — every step shows real, runnable code, exact commands, and expected output. The two spec-flagged caveats (fragile `/usage` text format; unverified reactive rate-limit signal string) are implemented as concrete, defensive, conservative code with an inline comment citing the caveat — not left unimplemented.

**Type/signature consistency:** Verified across tasks — `Config`/`AccountConfig` (Task 1) fields match every consumer (Tasks 2, 4, 6, 7, 8); `AccountState` values (Task 2) match `dispatch_phase`'s transitions (Task 7); `ClaudeResult`/`exec_claude`/`create_worktree` signatures (Task 4) match their call sites in `check_quota_ok`/`dispatch_phase` (Task 7); `TaskFile` and every `context_transfer` function (Task 5) match their call sites in `dispatch_phase`/`reap_expired_locks`/`run_task_cycle` (Task 7); `VibeKanbanClient.update_task_status`/`create_task` (Task 6) match `run_task_cycle`'s calls (Task 7); `db.list_events`/`insert_event` (Task 9) match the dashboard (Task 10) and the collector's own routes.

---

Plan complete and saved to `docs/superpowers/plans/2026-09-13-ia-harness.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
