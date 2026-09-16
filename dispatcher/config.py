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
    default_model: str
    max_revision_rounds: int
    escalate_effort_after_round: int
    escalated_effort: str
    phase_timeout_seconds: int


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    accounts = [AccountConfig(**a) for a in raw["accounts"]]
    phase_timeout_seconds = raw.get("phase_timeout_seconds", 7200)
    # A bad value here (0, negative, or the wrong type) previously loaded
    # fine and only surfaced inside exec_claude, after a quota probe, BUSY,
    # the lock and the worktree were already claimed — catch it up front
    # instead. bool is an int subclass in Python, so it needs its own check.
    if isinstance(phase_timeout_seconds, bool) or not isinstance(phase_timeout_seconds, int) or phase_timeout_seconds <= 0:
        raise ValueError("phase_timeout_seconds must be a positive integer")
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
        default_model=raw.get("default_model", "opus"),
        max_revision_rounds=raw.get("max_revision_rounds", 3),
        escalate_effort_after_round=raw.get("escalate_effort_after_round", 2),
        escalated_effort=raw.get("escalated_effort", "high"),
        phase_timeout_seconds=phase_timeout_seconds,
    )
