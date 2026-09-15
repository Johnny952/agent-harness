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
    assert cfg.default_model == "opus"
    assert cfg.max_revision_rounds == 3
    assert cfg.escalate_effort_after_round == 2
    assert cfg.escalated_effort == "high"


def test_load_config_overrides_revision_loop_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML
        + "\ndefault_model: sonnet\nmax_revision_rounds: 5\n"
        "escalate_effort_after_round: 1\nescalated_effort: max\n"
    )

    cfg = load_config(str(config_path))

    assert cfg.default_model == "sonnet"
    assert cfg.max_revision_rounds == 5
    assert cfg.escalate_effort_after_round == 1
    assert cfg.escalated_effort == "max"
