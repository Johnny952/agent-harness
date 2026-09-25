from pathlib import Path

import pytest

from dispatcher.config import DEFAULT_KANBAN_STATUS_MAP, load_config

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


def test_load_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    cfg = load_config(str(config_path))

    assert [a.name for a in cfg.accounts] == ["cuenta1", "cuenta2"]
    assert cfg.accounts[0].container == "agent-cuenta1"
    assert cfg.quota_threshold_pct == 90
    assert cfg.heartbeat_ttl_seconds == 120
    assert cfg.projects_root == "/data/projects"
    assert cfg.vibe_kanban is None
    assert cfg.default_model == "opus"
    assert cfg.permission_mode == "acceptEdits"
    assert cfg.max_revision_rounds == 3
    assert cfg.escalate_effort_after_round == 2
    assert cfg.escalated_effort == "high"
    assert cfg.phase_timeout_seconds == 7200


def test_load_config_overrides_revision_loop_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML
        + "\ndefault_model: sonnet\nmax_revision_rounds: 5\n"
        "escalate_effort_after_round: 1\nescalated_effort: max\n"
        "phase_timeout_seconds: 3600\n"
    )

    cfg = load_config(str(config_path))

    assert cfg.default_model == "sonnet"
    assert cfg.max_revision_rounds == 5
    assert cfg.escalate_effort_after_round == 1
    assert cfg.escalated_effort == "max"
    assert cfg.phase_timeout_seconds == 3600


def test_the_permission_mode_defaults_to_the_one_this_harness_can_use(tmp_path: Path) -> None:
    """Not left to the CLI's own default, which is the measured failure: with
    no mode a phase's every prompting tool call is denied, its own worktree
    included. `acceptEdits` is as permissive as this harness can go —
    `bypassPermissions` is refused outright when the CLI runs as root, which
    the agent image does."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    assert load_config(str(config_path)).permission_mode == "acceptEdits"


def test_load_config_honours_a_permission_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\npermission_mode: plan\n")

    assert load_config(str(config_path)).permission_mode == "plan"


def test_an_explicit_null_permission_mode_sends_no_flag(tmp_path: Path) -> None:
    """The way back to the unflagged call, kept deliberately: the denial it
    produces is the baseline every measurement of this is compared against,
    and a default nobody can turn off is a default nobody can check."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\npermission_mode: null\n")

    assert load_config(str(config_path)).permission_mode is None


@pytest.mark.parametrize("bad_value", ["acceptedits", "yolo", "44", "true"])
def test_load_config_rejects_an_unknown_permission_mode(tmp_path: Path, bad_value: str) -> None:
    """Caught here rather than by the CLI, which exits on an unknown choice
    only after the quota probe has run and the lock and the worktree have
    been claimed — so a typo costs a task's setup before it says anything."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\npermission_mode: {bad_value}\n")

    with pytest.raises(ValueError, match="permission_mode must be null or one of"):
        load_config(str(config_path))


def test_allowed_tools_is_empty_until_the_project_names_something(tmp_path: Path) -> None:
    """Unlike the permission mode, this has no safe generic default: what a
    phase may run is a fact about the project, and a guess that happened to
    match would be a grant nobody decided on."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    assert load_config(str(config_path)).allowed_tools == []


def test_load_config_honours_allowed_tools(tmp_path: Path) -> None:
    """A pattern with a space in it survives YAML and reaches the flag whole —
    the shape measured to let a phase run `node --test` at all."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML + '\nallowed_tools:\n  - "Bash(node --test*)"\n  - "Bash(npm test*)"\n'
    )

    assert load_config(str(config_path)).allowed_tools == [
        "Bash(node --test*)", "Bash(npm test*)",
    ]


def test_an_explicit_null_allowed_tools_is_the_same_as_none(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\nallowed_tools: null\n")

    assert load_config(str(config_path)).allowed_tools == []


def test_load_config_rejects_allowed_tools_written_as_one_string(tmp_path: Path) -> None:
    """The flag calls itself "comma or space-separated", so a single string
    looks plausible and would load — as a list of its characters everywhere
    this is iterated. Refusing it names the mistake instead."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + '\nallowed_tools: "Bash(node --test*)"\n')

    with pytest.raises(ValueError, match="allowed_tools must be a list"):
        load_config(str(config_path))


@pytest.mark.parametrize("bad_entry", ['""', '"   "', "44", "null"])
def test_load_config_rejects_an_empty_allowed_tools_entry(tmp_path: Path, bad_entry: str) -> None:
    """An empty pattern reaches the CLI as a bare argument after the variadic
    flag, where it is not ignored — it is read as the next thing in the list."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\nallowed_tools:\n  - {bad_entry}\n")

    with pytest.raises(ValueError, match="allowed_tools entries must be non-empty strings"):
        load_config(str(config_path))


def test_mapping_is_off_until_it_is_asked_for(tmp_path: Path) -> None:
    """Mapping a project spends quota on a phase that ships no code, so it is
    opt-in per harness — never something a first run discovers by being
    billed for it."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    cfg = load_config(str(config_path))

    assert cfg.mapping_enabled is False
    assert cfg.mapping_model == "sonnet"
    assert cfg.mapping_max_turns == 40


def test_load_config_reads_the_mapping_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML + "\nmapping_enabled: true\nmapping_model: haiku\nmapping_max_turns: 12\n"
    )

    cfg = load_config(str(config_path))

    assert cfg.mapping_enabled is True
    assert cfg.mapping_model == "haiku"
    assert cfg.mapping_max_turns == 12


@pytest.mark.parametrize("bad_value", ["0", "-5", "many", "true"])
def test_load_config_rejects_an_invalid_mapping_turn_budget(tmp_path: Path, bad_value: str) -> None:
    """Caught at load rather than at dispatch: the budget is the only thing
    bounding what this phase spends, and a run-task is a bad place to find out
    it was a string."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\nmapping_max_turns: {bad_value}\n")

    with pytest.raises(ValueError, match="mapping_max_turns must be a positive integer"):
        load_config(str(config_path))


def test_the_gates_are_on_until_they_are_turned_off(tmp_path: Path) -> None:
    """The other two optional phases are opt-in because they spend quota. The
    gates spend none — they are `docker exec` with no model in the loop — and
    what they catch costs a revisor call plus another round when it reaches
    review instead, so the default that saves money is on."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    cfg = load_config(str(config_path))

    assert cfg.gates_enabled is True
    assert cfg.gates_test_timeout_seconds == 900


def test_load_config_reads_the_gate_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML + "\ngates_enabled: false\ngates_test_timeout_seconds: 120\n"
    )

    cfg = load_config(str(config_path))

    assert cfg.gates_enabled is False
    assert cfg.gates_test_timeout_seconds == 120


@pytest.mark.parametrize("bad_value", ["0", "-5", "15m", "true"])
def test_load_config_rejects_an_invalid_test_timeout(tmp_path: Path, bad_value: str) -> None:
    """The timeout is what keeps a hung suite from holding the task's lock for
    the rest of the afternoon, so a bad one is caught before anything is
    claimed."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\ngates_test_timeout_seconds: {bad_value}\n")

    with pytest.raises(ValueError, match="gates_test_timeout_seconds must be a positive integer"):
        load_config(str(config_path))


@pytest.mark.parametrize("bad_value", ["0", "-5", "2h", "true"])
def test_load_config_rejects_invalid_phase_timeout_seconds(tmp_path: Path, bad_value: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\nphase_timeout_seconds: {bad_value}\n")

    with pytest.raises(ValueError, match="phase_timeout_seconds must be a positive integer"):
        load_config(str(config_path))


KANBAN_YAML = """
vibe_kanban:
  command: ["npx", "vibe-kanban@0.1.44", "mcp"]
  project_id: 0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f
"""


def test_load_config_reads_the_vibe_kanban_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + KANBAN_YAML)

    cfg = load_config(str(config_path))

    assert cfg.vibe_kanban is not None
    assert cfg.vibe_kanban.command == ["npx", "vibe-kanban@0.1.44", "mcp"]
    assert cfg.vibe_kanban.project_id == "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"
    assert cfg.vibe_kanban.status_map == DEFAULT_KANBAN_STATUS_MAP


def test_load_config_leaves_project_id_unset(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + '\nvibe_kanban:\n  command: ["vibe-kanban", "mcp"]\n')

    cfg = load_config(str(config_path))

    assert cfg.vibe_kanban is not None
    assert cfg.vibe_kanban.project_id is None


def test_load_config_overrides_one_status_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + KANBAN_YAML + '  status_map:\n    done: Shipped\n')

    cfg = load_config(str(config_path))

    assert cfg.vibe_kanban is not None
    # A project that renamed one column keeps the defaults for the rest.
    assert cfg.vibe_kanban.status_map["done"] == "Shipped"
    assert cfg.vibe_kanban.status_map["blocked"] == DEFAULT_KANBAN_STATUS_MAP["blocked"]


def test_load_config_rejects_the_dead_sse_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\nvibe_kanban_mcp_url: http://vibe-kanban:9100/sse\n")

    with pytest.raises(ValueError, match="speaks stdio"):
        load_config(str(config_path))


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("vibe_kanban: http://vibe-kanban:9100\n", "must be a mapping"),
        ("vibe_kanban:\n  project_id: abc\n", "command must be a non-empty list"),
        ("vibe_kanban:\n  command: []\n", "command must be a non-empty list"),
        ('vibe_kanban:\n  command: "npx vibe-kanban mcp"\n', "command must be a non-empty list"),
        ('vibe_kanban:\n  command: ["npx", 44]\n', "command must be a non-empty list"),
        ('vibe_kanban:\n  command: ["vk"]\n  project_id: 44\n', "project_id must be a string"),
        ('vibe_kanban:\n  command: ["vk"]\n  status_map:\n    doing: Doing\n', "no status 'doing'"),
        ('vibe_kanban:\n  command: ["vk"]\n  status_map:\n    done: 44\n', "must be a non-empty string"),
    ],
)
def test_load_config_rejects_a_malformed_vibe_kanban_block(
    tmp_path: Path, block: str, message: str
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\n" + block)

    with pytest.raises(ValueError, match=message):
        load_config(str(config_path))


LOCAL_BOARD_YAML = """
local_board:
  dir: /state/board
"""


def test_load_config_has_no_local_board_by_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    # Opt-in: a harness that has been running with no cards must not start
    # writing them because it was upgraded.
    assert load_config(str(config_path)).local_board is None


def test_load_config_reads_the_local_board_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + LOCAL_BOARD_YAML)

    cfg = load_config(str(config_path))

    assert cfg.local_board is not None
    assert cfg.local_board.dir == "/state/board"
    assert cfg.vibe_kanban is None


def test_load_config_refuses_two_boards(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + KANBAN_YAML + LOCAL_BOARD_YAML)

    # Not a precedence rule: an operator who wrote both meant one of them, and
    # the message has to say which one to drop.
    with pytest.raises(ValueError, match="Drop local_board"):
        load_config(str(config_path))


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("local_board: /state/board\n", "must be a mapping"),
        ("local_board: {}\n", "dir must be a non-empty string"),
        ("local_board:\n  dir: 44\n", "dir must be a non-empty string"),
        ('local_board:\n  dir: ""\n', "dir must be a non-empty string"),
    ],
)
def test_load_config_rejects_a_malformed_local_board_block(
    tmp_path: Path, block: str, message: str
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\n" + block)

    with pytest.raises(ValueError, match=message):
        load_config(str(config_path))


def test_the_refusal_cooldown_defaults_to_half_an_hour(tmp_path: Path) -> None:
    """The knob is optional: a config written before the cooldown existed still
    loads, and still holds a refused account out of the pool."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    cfg = load_config(str(config_path))

    assert cfg.quota_cooldown_seconds == 1800


def test_load_config_reads_the_refusal_cooldown(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + "\nquota_cooldown_seconds: 600\n")

    cfg = load_config(str(config_path))

    assert cfg.quota_cooldown_seconds == 600


@pytest.mark.parametrize("bad_value", ["0", "-5", "30m", "true"])
def test_load_config_rejects_an_invalid_refusal_cooldown(tmp_path: Path, bad_value: str) -> None:
    """Zero or a negative would make the floor no floor at all, and silently
    put back the behaviour it exists to stop: a refused account recovered on
    local counters that cannot see the refusal."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML + f"\nquota_cooldown_seconds: {bad_value}\n")

    with pytest.raises(ValueError, match="quota_cooldown_seconds must be a positive integer"):
        load_config(str(config_path))
