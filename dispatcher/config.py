from __future__ import annotations

import dataclasses

import yaml


@dataclasses.dataclass
class AccountConfig:
    name: str
    container: str


#: What the dispatcher's own status strings become on the board. The left side
#: is this harness's vocabulary (`_update_task_status` sends "blocked", "done"
#: and "in_progress:<role>", of which only the prefix is mapped); the right
#: side has to match a status *name* on the Vibe Kanban project, which is
#: per-project and which no MCP tool lists — so these are defaults for the
#: names Vibe Kanban ships with, and an operator whose project renamed them
#: says so in `vibe_kanban.status_map`. "blocked" maps to review because the
#: dispatcher blocks a task exactly when a human has to look at it.
DEFAULT_KANBAN_STATUS_MAP = {
    "in_progress": "In Progress",
    "blocked": "In Review",
    "done": "Done",
}


@dataclasses.dataclass
class VibeKanbanConfig:
    """How to reach a Vibe Kanban board, when there is one.

    `command` is an argv the dispatcher spawns and talks MCP to over stdio —
    Vibe Kanban's server has no SSE endpoint, so there is no URL to point at.
    `project_id` is the uuid of the project new issues land in; it is optional
    because the server can infer it when it runs inside a workspace already
    linked to a remote project.
    """

    command: list[str]
    project_id: str | None = None
    status_map: dict[str, str] = dataclasses.field(
        default_factory=lambda: dict(DEFAULT_KANBAN_STATUS_MAP)
    )


#: The permission modes the CLI accepts (`claude --help`, 2.1.280). Checked at
#: load time for the same reason the integer caps are: a typo is otherwise
#: only caught by the CLI itself, which exits on an unknown choice after the
#: quota probe, the lock and the worktree have already been claimed.
PERMISSION_MODES = frozenset(
    {"acceptEdits", "auto", "bypassPermissions", "dontAsk", "manual", "plan"}
)


@dataclasses.dataclass
class Config:
    accounts: list[AccountConfig]
    quota_threshold_pct: int
    heartbeat_ttl_seconds: int
    heartbeat_interval_seconds: int
    projects_root: str
    hive_tasks_dir: str
    state_dir: str
    vibe_kanban: VibeKanbanConfig | None
    collector_url: str
    default_model: str
    permission_mode: str | None
    allowed_tools: list[str]
    max_revision_rounds: int
    escalate_effort_after_round: int
    escalated_effort: str
    phase_timeout_seconds: int
    merge_on_done: bool
    mapping_enabled: bool
    mapping_model: str
    mapping_max_turns: int
    gates_enabled: bool
    gates_test_timeout_seconds: int


def _load_vibe_kanban(raw: dict) -> VibeKanbanConfig | None:
    """Read the optional `vibe_kanban` block, or None when there is none.

    The board is a visibility aid, not a dependency: a harness with no
    `vibe_kanban` block runs tasks exactly as before and the dispatcher never
    mentions a board it was not given.
    """
    if "vibe_kanban_mcp_url" in raw:
        # This key promised an SSE endpoint that Vibe Kanban's MCP server
        # never served. Failing loudly beats ignoring it, which would leave an
        # operator believing their board is wired up when nothing reaches it.
        raise ValueError(
            "vibe_kanban_mcp_url is gone: Vibe Kanban's MCP server speaks stdio, "
            "not SSE. Replace it with a vibe_kanban block, or drop it to run "
            "without a board."
        )
    block = raw.get("vibe_kanban")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("vibe_kanban must be a mapping")
    command = block.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        raise ValueError("vibe_kanban.command must be a non-empty list of strings")
    project_id = block.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise ValueError("vibe_kanban.project_id must be a string")
    status_map = dict(DEFAULT_KANBAN_STATUS_MAP)
    overrides = block.get("status_map") or {}
    if not isinstance(overrides, dict):
        raise ValueError("vibe_kanban.status_map must be a mapping")
    for key, value in overrides.items():
        if key not in DEFAULT_KANBAN_STATUS_MAP:
            raise ValueError(
                f"vibe_kanban.status_map has no status {key!r}; known statuses are "
                + ", ".join(sorted(DEFAULT_KANBAN_STATUS_MAP))
            )
        if not isinstance(value, str) or not value:
            raise ValueError(f"vibe_kanban.status_map[{key!r}] must be a non-empty string")
        status_map[key] = value
    return VibeKanbanConfig(command=command, project_id=project_id, status_map=status_map)


def _load_permission_mode(raw: dict) -> str | None:
    """Which permission mode every phase command carries.

    With no mode the CLI runs `-p` under `--permission-prompts host` with no
    host to ask, so everything that would prompt is denied instead — Write and
    Edit inside the phase's own worktree included. Measured end to end on
    2026-09-23: 43 of 62 tool calls denied, not one file written, all three
    phases blocked.

    `acceptEdits` is the default because it is the most permissive mode this
    harness can actually use. `bypassPermissions` is refused at startup when
    the CLI runs as root, which the agent image does; `dontAsk` is precisely
    the denial above; and `auto`, the one listed mode that would also cover
    Bash, is a server-side classifier the CLI can report as unavailable, so it
    is not something to depend on before it has been measured on these
    accounts. Move this to `auto` once it has been.

    `permission_mode: null` drops the flag again, which is how the unflagged
    behaviour above stays measurable.
    """
    mode = raw.get("permission_mode", "acceptEdits")
    if mode is None:
        return None
    if not isinstance(mode, str) or mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode must be null or one of: {', '.join(sorted(PERMISSION_MODES))}"
        )
    return mode


def _load_allowed_tools(raw: dict) -> list[str]:
    """Tool patterns every phase is allowed outright, on top of the mode.

    The mode and this list are two independent grants, measured separately on
    2026-09-23: `acceptEdits` covers the file tools and the CLI's own
    read-only Bash set, but not running a program, and a phase asked to run
    `node --test` was refused six times across three phases — so the task's
    own acceptance criterion was unreachable and nothing in the loop ever
    executed the code. The same prompt with `Bash(node --test*)` allowed ran
    it in two turns. An allowlist with no mode alongside it ran it too, which
    is why this is a separate setting rather than a property of the mode.

    It has to come from here rather than from the phase, because a phase
    cannot grant itself the permission: writing its own
    `.claude/settings.local.json` is refused even under `acceptEdits`, and a
    CLI flag is not on disk for it to reach at all. Narrow entries are the
    point — prefer `Bash(npm test*)` over `Bash(npm*)`, and note that the
    trailing `*` still lets arguments through.

    Empty by default, which sends no flag: what a project's phases may run is
    a decision about that project, and guessing it would be worse than asking.
    """
    tools = raw.get("allowed_tools", [])
    if tools is None:
        return []
    if isinstance(tools, str) or not isinstance(tools, list):
        raise ValueError("allowed_tools must be a list of tool patterns")
    for tool in tools:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("allowed_tools entries must be non-empty strings")
    return [tool.strip() for tool in tools]


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
    mapping_max_turns = raw.get("mapping_max_turns", 40)
    if isinstance(mapping_max_turns, bool) or not isinstance(mapping_max_turns, int) or mapping_max_turns <= 0:
        raise ValueError("mapping_max_turns must be a positive integer")
    gates_test_timeout_seconds = raw.get("gates_test_timeout_seconds", 900)
    if (
        isinstance(gates_test_timeout_seconds, bool)
        or not isinstance(gates_test_timeout_seconds, int)
        or gates_test_timeout_seconds <= 0
    ):
        raise ValueError("gates_test_timeout_seconds must be a positive integer")
    return Config(
        accounts=accounts,
        quota_threshold_pct=raw.get("quota_threshold_pct", 90),
        heartbeat_ttl_seconds=raw.get("heartbeat_ttl_seconds", 120),
        heartbeat_interval_seconds=raw.get("heartbeat_interval_seconds", 30),
        projects_root=raw["projects_root"],
        hive_tasks_dir=raw["hive_tasks_dir"],
        state_dir=raw["state_dir"],
        vibe_kanban=_load_vibe_kanban(raw),
        collector_url=raw["collector_url"],
        default_model=raw.get("default_model", "opus"),
        permission_mode=_load_permission_mode(raw),
        allowed_tools=_load_allowed_tools(raw),
        max_revision_rounds=raw.get("max_revision_rounds", 3),
        escalate_effort_after_round=raw.get("escalate_effort_after_round", 2),
        escalated_effort=raw.get("escalated_effort", "high"),
        phase_timeout_seconds=phase_timeout_seconds,
        # Off by default: merging is the one thing a run does to the branch the
        # human works from, so it waits to be asked for. `dispatch merge-task`
        # does the same merge by hand whenever this stays false.
        merge_on_done=bool(raw.get("merge_on_done", False)),
        # Also off by default, and for the same kind of reason: mapping an
        # unmapped project spends quota on a phase the operator did not ask
        # for. Turned on, it runs once per project — before the arquitecto,
        # only when the project has no docs index — and never blocks the task
        # if it fails.
        mapping_enabled=bool(raw.get("mapping_enabled", False)),
        # Cheaper than the roles that decide things: reading a tree and
        # writing down what is there does not need the expensive model.
        mapping_model=raw.get("mapping_model", "sonnet"),
        mapping_max_turns=mapping_max_turns,
        # On by default, unlike the two above: the gates run through
        # `docker exec` with no model in the loop, so they spend no quota, and
        # what they catch — a change with no test, a suite that is already
        # failing — costs a revisor call plus another implementador round when
        # it reaches review instead. They net quota back.
        gates_enabled=bool(raw.get("gates_enabled", True)),
        # The phase timeout is hours long because a phase is a model working;
        # a test suite that has not finished in fifteen minutes is telling the
        # gate something else, and the gate says so and lets review proceed
        # rather than holding the task open for the rest of the afternoon.
        gates_test_timeout_seconds=gates_test_timeout_seconds,
    )
