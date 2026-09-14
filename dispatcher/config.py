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
