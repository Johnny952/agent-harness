import ast
import datetime as dt
import os
import stat
from pathlib import Path

import pytest

from dispatcher import tasks
from dispatcher.context_transfer import (
    read_task_file,
    task_file_path,
    write_task_file,
)
from dispatcher.handoff import SubTask

_GOAL = "Add a /healthz endpoint."
_DONE = "GET /healthz answers 200."
_DESCRIPTION = f"## Goal\n\n{_GOAL}\n\n## Done when\n\n{_DONE}\n"

#: A two-entry split, the second waiting for the first and carrying a section
#: of its own.
_SPLIT = [
    SubTask(title="The api", surface="observability/api/", description=_DESCRIPTION, after=()),
    SubTask(
        title="The screen",
        surface="front/",
        description="## Goal\n\nShow it.\n\n## Surface\n\nThe status page.\n\n## Done when\n\nIt shows.\n",
        after=(1,),
    ),
]

#: A task file from before the description moved, less the `description` key
#: itself. `write_task_file` writes exactly this for a task with a lock.
_HEAD = (
    "---\ntask_id: task-1\nstatus: in_progress\nowner: cuenta1\ndepends_on:\n- task-0\n"
    "heartbeat: '2026-10-11T16:00:36+00:00'\n"
)
_BODY = "---\n\n## arquitecto\n\n**Status:** complete\n\n## implementador\n\nDone.\n"
#: The heartbeat TTL `migrate` is given. `_HEAD`'s heartbeat is long past it,
#: so that lock is a dead cycle's and the file moves.
_TTL = 600


def _hive(tmp_path: Path) -> str:
    """The hive tasks dir, one level below the hive root as in a real config."""
    hive = tmp_path / "hive" / "tasks"
    hive.mkdir(parents=True)
    return str(hive)


def _legacy(hive: str, description_yaml: str, task_id: str = "task-1") -> Path:
    """A task file that still carries its description, written as literal text."""
    path = Path(task_file_path(hive, task_id))
    path.write_text(_HEAD.replace("task-1", task_id) + description_yaml + _BODY)
    return path


# -- parse --------------------------------------------------------------------


def test_parse_puts_each_section_in_its_field() -> None:
    parsed = tasks.parse_description(
        "## Goal\n\nThe goal.\n\n## Done when\n\n- one\n- two\n\n## Surface\n\n`a.py`\n\n"
        "## Steps\n\n1. first\n\n## Out of scope\n\nThe rest.\n"
    )

    assert parsed == tasks.Description(
        goal="The goal.",
        done_when="- one\n- two",
        surface="`a.py`",
        steps="1. first",
        out_of_scope="The rest.",
    )


def test_parse_takes_the_sections_in_any_order() -> None:
    parsed = tasks.parse_description(f"## Done when\n{_DONE}\n## Goal\n{_GOAL}\n")

    assert (parsed.goal, parsed.done_when) == (_GOAL, _DONE)


def test_parse_keeps_an_unknown_heading_as_text_of_its_section() -> None:
    parsed = tasks.parse_description(
        f"## Goal\n\n{_GOAL}\n\n## Notes\n\nAn aside.\n\n### Deeper\n\n## Done when\n\n{_DONE}\n"
    )

    assert parsed.goal == f"{_GOAL}\n\n## Notes\n\nAn aside.\n\n### Deeper"


def test_parse_reads_an_absent_or_blank_optional_section_as_none() -> None:
    parsed = tasks.parse_description(_DESCRIPTION + "\n## Steps\n\n   \n")

    assert parsed.surface is None
    assert parsed.steps is None
    assert parsed.out_of_scope is None


@pytest.mark.parametrize(
    "text, named",
    [
        (f"## Done when\n\n{_DONE}\n", ["`## Goal`"]),
        (f"## Goal\n\n{_GOAL}\n", ["`## Done when`"]),
        ("## Steps\n\n1. first\n", ["`## Goal`", "`## Done when`"]),
        (f"## Goal\n\n{_GOAL}\n\n## Done when\n\n  \n", ["`## Done when`"]),
        # Not a section: indented, lower case, or with text after the name.
        (f"## Done when\n\n{_DONE}\n ## Goal\n## goal\n## Goal:\nx\n", ["`## Goal`"]),
    ],
)
def test_parse_names_the_required_section_that_is_missing(text: str, named: list[str]) -> None:
    with pytest.raises(tasks.DescriptionError) as excinfo:
        tasks.parse_description(text)

    assert "missing" in str(excinfo.value)
    for name in named:
        assert name in str(excinfo.value)


def test_parse_rejects_a_section_given_twice() -> None:
    with pytest.raises(tasks.DescriptionError, match="two `## Steps` sections"):
        tasks.parse_description(_DESCRIPTION + "## Steps\n\na\n\n## Steps\n\nb\n")


def test_parse_rejects_text_before_the_first_section() -> None:
    with pytest.raises(tasks.DescriptionError, match="before its first section.*`## Goal`"):
        tasks.parse_description("Add a /healthz endpoint.\n\n" + _DESCRIPTION)


def test_a_description_error_is_a_value_error() -> None:
    # The api's `_UNREADABLE` and every `except ValueError` rest on it.
    assert issubclass(tasks.DescriptionError, ValueError)
    assert issubclass(tasks.UnmigratedTaskError, ValueError)


# -- write and read -----------------------------------------------------------


def test_the_description_lives_on_the_board_beside_the_hive_tasks_dir(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    assert tasks.board_root(hive) == str(tmp_path / "hive" / "board")
    assert tasks.description_path(hive, "task-1") == str(
        tmp_path / "hive" / "board" / "task-1" / "description.md"
    )


def test_write_then_read_round_trips_to_the_byte(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    text = _DESCRIPTION + "\n## Steps\n\ntrailing spaces   \r\nand a CRLF, no final newline"

    path = tasks.write_description(hive, "task-1", text)

    assert path == tasks.description_path(hive, "task-1")
    assert Path(path).read_bytes() == text.encode("utf-8")
    assert tasks.read_description(hive, "task-1") == text


def test_write_refuses_a_description_missing_a_section_and_writes_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(tasks.DescriptionError, match="`## Done when`"):
        tasks.write_description(hive, "task-1", f"## Goal\n\n{_GOAL}\n")

    assert not os.path.exists(tasks.board_root(hive))


def test_write_is_atomic_and_readable_by_another_uid_whatever_the_umask(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    old = os.umask(0o077)
    try:
        path = Path(tasks.write_description(hive, "task-1", _DESCRIPTION))
    finally:
        os.umask(old)

    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o755
    assert [entry.name for entry in path.parent.iterdir()] == ["description.md"]


@pytest.mark.parametrize("task_id", ["", ".", "..", "a/b", "../tasks/task-1"])
def test_a_task_id_that_is_not_one_path_component_is_refused(tmp_path: Path, task_id: str) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(ValueError):
        tasks.description_path(hive, task_id)
    with pytest.raises(ValueError):
        tasks.write_description(hive, task_id, _DESCRIPTION)


def test_read_of_a_task_with_no_description_is_none_and_creates_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    assert tasks.read_description(hive, "task-1") is None
    assert os.listdir(tmp_path / "hive") == ["tasks"]
    assert os.listdir(hive) == []


# -- the refusal of an unmigrated file ------------------------------------------


def test_read_and_write_refuse_an_unmigrated_task_file_and_leave_it_alone(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, "description: Add a /healthz endpoint.\n")
    before = legacy.read_bytes()

    with pytest.raises(tasks.UnmigratedTaskError) as read_error:
        tasks.read_description(hive, "task-1")
    with pytest.raises(tasks.UnmigratedTaskError) as write_error:
        tasks.write_description(hive, "task-1", _DESCRIPTION)

    for error in (read_error, write_error):
        assert "dispatch migrate" in str(error.value)
        assert str(legacy) in str(error.value)
    assert legacy.read_bytes() == before
    assert not os.path.exists(tasks.board_root(hive))


def test_unmigrated_ids_lists_only_the_files_that_still_have_the_key(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "description: Second.\n", "task-2")
    _legacy(hive, "description: First.\n", "task-1")
    _legacy(hive, "", "task-3")  # already migrated: no key
    # `description: null` is still the key: the next write would drop it unseen.
    _legacy(hive, "description: null\n", "task-4")
    Path(task_file_path(hive, "task-5")).write_text("TODO write this up\n")
    Path(task_file_path(hive, "task-6")).write_text("---\n- description\n---\n\n")

    assert tasks.unmigrated_ids(hive) == ["task-1", "task-2", "task-4"]
    assert tasks.is_unmigrated(hive, "task-404") is False
    assert tasks.unmigrated_ids(str(tmp_path / "no-such-dir")) == []


#: Each task-file writer, as a call on (hive, task id).
_WRITERS = {
    "acquire_lock": lambda hive, tid: tasks.acquire_lock(hive, tid, owner="cuenta1", ttl_seconds=_TTL),
    "refresh_heartbeat": tasks.refresh_heartbeat,
    "release_stale_lock": tasks.release_stale_lock,
    "handoff": lambda hive, tid: tasks.handoff(hive, tid, new_status="review", body="## revisor"),
    "set_resolved_debt": lambda hive, tid: tasks.set_resolved_debt(hive, tid, ["task-0-D1"]),
    "mark_merged": lambda hive, tid: tasks.mark_merged(hive, tid, "main"),
    "mark_blocked": tasks.mark_blocked,
    "file_split": lambda hive, tid: tasks.file_split(hive, tid, _SPLIT),
}


@pytest.mark.parametrize("writer", sorted(_WRITERS))
def test_every_task_file_writer_refuses_an_unmigrated_file_and_leaves_it_alone(
    tmp_path: Path, writer: str,
) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, "description: Add a /healthz endpoint.\n")
    before = legacy.read_bytes()

    with pytest.raises(tasks.UnmigratedTaskError) as excinfo:
        _WRITERS[writer](hive, "task-1")

    assert "dispatch migrate" in str(excinfo.value)
    assert str(legacy) in str(excinfo.value)
    assert legacy.read_bytes() == before
    assert os.listdir(hive) == ["task-1.md"]  # no temp file left behind either


@pytest.mark.parametrize("writer", sorted(_WRITERS))
def test_the_writers_refusal_is_per_file_a_legacy_neighbour_does_not_stop_it(
    tmp_path: Path, writer: str,
) -> None:
    hive = _hive(tmp_path)
    neighbour = _legacy(hive, "description: Someone else's ask.\n", "task-2")
    before = neighbour.read_bytes()
    migrated = _legacy(hive, "")

    _WRITERS[writer](hive, "task-1")

    assert migrated.read_text() != _HEAD + _BODY
    assert read_task_file(str(migrated)).task_id == "task-1"
    assert neighbour.read_bytes() == before


def test_a_writer_still_creates_a_task_file_that_is_not_there(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    tasks.acquire_lock(hive, "task-1", owner="cuenta1")

    task = read_task_file(task_file_path(hive, "task-1"))
    assert (task.status, task.owner) == ("in_progress", "cuenta1")


def test_no_module_under_dispatcher_but_tasks_calls_write_task_file() -> None:
    """Section 5 of `docs/plans/board-service.md`, "One module writes": a call
    anywhere else is a write that skips the refusal of an unmigrated file."""
    package = Path(tasks.__file__).parent
    modules = sorted(package.rglob("*.py"))
    assert package / "context_transfer.py" in modules

    callers = set()
    for module in modules:
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "write_task_file":
                callers.add(module.name)

    assert callers == {"tasks.py"}


# -- the board seam is gone (docs/decisions.md ADR 59) --------------------------


def test_a_task_file_that_still_has_kanban_issue_id_reads_and_loses_it_on_its_next_write(
    tmp_path: Path,
) -> None:
    """The key has no reader left, so there is nothing to move first: the file
    reads without error and the next writer drops the key and nothing else."""
    hive = _hive(tmp_path)
    path = _legacy(hive, "kanban_issue_id: 0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f\n")

    task = read_task_file(str(path))

    assert not hasattr(task, "kanban_issue_id")
    assert (task.status, task.owner, task.depends_on) == ("in_progress", "cuenta1", ["task-0"])

    tasks.handoff(hive, "task-1", new_status="review", body="## revisor\n\nRead it.")

    text = path.read_text()
    assert "kanban_issue_id" not in text
    assert "0e1d2c3b" not in text
    after = read_task_file(str(path))
    assert (after.status, after.depends_on) == ("review", ["task-0"])
    assert "## arquitecto" in after.body and "## implementador" in after.body
    assert after.body.rstrip().endswith("## revisor\n\nRead it.")


def test_no_module_under_dispatcher_or_observability_names_the_board_seam() -> None:
    """Neither the deleted client module nor the key it stored. The one place
    the word survives is `config.py`'s refusal of a config that still has it."""
    repo = Path(tasks.__file__).parent.parent
    modules = sorted((repo / "dispatcher").rglob("*.py")) + sorted(
        (repo / "observability").rglob("*.py")
    )
    assert repo / "observability" / "api" / "app.py" in modules
    assert not (repo / "dispatcher" / "vibe_kanban_client.py").exists()

    naming = {
        str(module.relative_to(repo))
        for module in modules
        if "kanban" in module.read_text(encoding="utf-8").lower()
    }

    assert naming == {"dispatcher/config.py"}


# -- migrate ------------------------------------------------------------------

#: The four scalar styles the live hive's descriptions come in, each with the
#: text YAML reads it as.
_STYLES = [
    ("description: Add a /healthz endpoint, and\n  keep it open.\n",
     "Add a /healthz endpoint, and keep it open."),
    ("description: |\n  Add a /healthz endpoint.\n\n  Keep it open.\n",
     "Add a /healthz endpoint.\n\nKeep it open."),
    ('description: "Add a \\"/healthz\\" endpoint.\\n\\nKeep it open.  "\n',
     'Add a "/healthz" endpoint.\n\nKeep it open.'),
    ("description: |-\n  Add a /healthz endpoint.\n  Keep it open.\n",
     "Add a /healthz endpoint.\nKeep it open."),
]


@pytest.mark.parametrize("description_yaml, goal", _STYLES)
def test_migrate_moves_the_description_and_leaves_the_rest_as_write_task_file_writes_it(
    tmp_path: Path, description_yaml: str, goal: str,
) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, description_yaml)
    # What `write_task_file` writes for the same task, description aside.
    expected = tmp_path / "expected.md"
    write_task_file(str(expected), read_task_file(str(legacy)))

    report = tasks.migrate(hive, _TTL)

    path = tasks.description_path(hive, "task-1")
    assert report == tasks.MigrateReport(
        moved=[("task-1", path)], emptied=[], refused=[], already=0
    )
    assert legacy.read_bytes() == expected.read_bytes()
    # The fixture is itself in `write_task_file`'s form, so "the rest of the
    # file" is checked against the literal too: only the key is gone.
    assert legacy.read_text() == _HEAD + _BODY
    stored = tasks.read_description(hive, "task-1")
    assert stored == f"## Goal\n\n{goal}\n\n## Done when\n\nNot stated separately; see Goal.\n"
    assert tasks.parse_description(stored) == tasks.Description(
        goal=goal, done_when=tasks.LEGACY_DONE_WHEN
    )


def test_migrate_twice_changes_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, "description: Add a /healthz endpoint.\n")
    tasks.migrate(hive, _TTL)
    path = Path(tasks.description_path(hive, "task-1"))
    before = [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in (legacy, path)]

    report = tasks.migrate(hive, _TTL)

    assert report == tasks.MigrateReport(moved=[], emptied=[], refused=[], already=1)
    assert [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in (legacy, path)] == before


def test_migrate_moves_every_task_file_in_id_order(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "description: Second.\n", "task-2")
    _legacy(hive, "description: First.\n", "task-1")
    _legacy(hive, "", "task-3")

    report = tasks.migrate(hive, _TTL)

    assert [task_id for task_id, _ in report.moved] == ["task-1", "task-2"]
    assert report.already == 1
    assert tasks.unmigrated_ids(hive) == []
    assert tasks.read_description(hive, "task-3") is None


@pytest.mark.parametrize("description_yaml", ["description: null\n", "description: '  '\n"])
def test_migrate_drops_an_empty_description_and_writes_no_file(
    tmp_path: Path, description_yaml: str,
) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, description_yaml)

    report = tasks.migrate(hive, _TTL)

    assert report == tasks.MigrateReport(moved=[], emptied=["task-1"], refused=[], already=0)
    assert legacy.read_text() == _HEAD + _BODY
    assert not os.path.exists(tasks.board_root(hive))


@pytest.mark.parametrize(
    "description_yaml, why",
    [
        ("description: [not, text]\n", "list"),
        ("description: |\n  Do it.\n  ## Steps\n  1. first\n", "section heading"),
    ],
)
def test_migrate_refuses_what_it_would_have_to_guess_at(
    tmp_path: Path, description_yaml: str, why: str,
) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, description_yaml)
    before = legacy.read_bytes()

    report = tasks.migrate(hive, _TTL)

    assert report.moved == [] and report.emptied == []
    [(task_id, reason)] = report.refused
    assert task_id == "task-1" and why in reason
    assert legacy.read_bytes() == before
    assert not os.path.exists(tasks.board_root(hive))


def test_migrate_reports_a_task_file_it_cannot_read_and_moves_the_others(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    broken = Path(task_file_path(hive, "task-0"))
    broken.write_text("TODO write this up\n")
    keyless = Path(task_file_path(hive, "task-2"))
    keyless.write_text("---\nstatus: pending\ndescription: No task id.\n---\n\n")
    _legacy(hive, "description: Add a /healthz endpoint.\n")

    report = tasks.migrate(hive, _TTL)

    assert [task_id for task_id, _ in report.refused] == ["task-0", "task-2"]
    assert [task_id for task_id, _ in report.moved] == ["task-1"]
    assert broken.read_text() == "TODO write this up\n"
    assert "description: No task id." in keyless.read_text()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def test_migrate_refuses_a_task_whose_lock_is_live(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    # The heartbeat thread of the dispatcher running task-1 wrote this a moment ago.
    live = _HEAD.replace("2026-10-11T16:00:36+00:00", _now())
    legacy = Path(task_file_path(hive, "task-1"))
    legacy.write_text(live + "description: Add a /healthz endpoint.\n" + _BODY)
    before = legacy.read_bytes()
    _legacy(hive, "description: Second.\n", "task-2")

    report = tasks.migrate(hive, _TTL)

    [(task_id, reason)] = report.refused
    assert task_id == "task-1"
    assert "lock is live" in reason and "cuenta1" in reason and "dispatch migrate" in reason
    assert legacy.read_bytes() == before
    assert not os.path.exists(os.path.dirname(tasks.description_path(hive, "task-1")))
    # The dead cycle beside it still moves.
    assert [moved_id for moved_id, _ in report.moved] == ["task-2"]


def test_migrate_refuses_a_live_lock_with_an_empty_description_too(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    legacy = Path(task_file_path(hive, "task-1"))
    legacy.write_text(
        _HEAD.replace("2026-10-11T16:00:36+00:00", _now()) + "description: null\n" + _BODY
    )
    before = legacy.read_bytes()

    report = tasks.migrate(hive, _TTL)

    assert report.emptied == [] and [task_id for task_id, _ in report.refused] == ["task-1"]
    assert legacy.read_bytes() == before


@pytest.mark.parametrize(
    "head",
    [
        # Past the TTL: the cycle that wrote it is dead.
        _HEAD,
        # What `release_stale_lock` leaves: in_progress, no heartbeat to race.
        "---\ntask_id: task-1\nstatus: in_progress\nowner: null\ndepends_on: []\n"
        "heartbeat: null\n",
    ],
)
def test_migrate_moves_an_in_progress_task_whose_lock_is_not_live(
    tmp_path: Path, head: str,
) -> None:
    hive = _hive(tmp_path)
    Path(task_file_path(hive, "task-1")).write_text(
        head + "description: Add a /healthz endpoint.\n" + _BODY
    )

    report = tasks.migrate(hive, _TTL)

    assert report.refused == []
    assert [task_id for task_id, _ in report.moved] == ["task-1"]


def test_migrate_moves_a_fresh_heartbeat_on_a_task_that_is_not_in_progress(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    head = _HEAD.replace("in_progress", "review").replace("2026-10-11T16:00:36+00:00", _now())
    Path(task_file_path(hive, "task-1")).write_text(head + "description: Do it.\n" + _BODY)

    report = tasks.migrate(hive, _TTL)

    assert [task_id for task_id, _ in report.moved] == ["task-1"]


def test_migrate_refuses_to_overwrite_a_different_description_file(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, "description: Add a /healthz endpoint.\n")
    before = legacy.read_bytes()
    path = Path(tasks.description_path(hive, "task-1"))
    path.parent.mkdir(parents=True)
    path.write_text(_DESCRIPTION)

    report = tasks.migrate(hive, _TTL)

    [(task_id, reason)] = report.refused
    assert task_id == "task-1" and "different content" in reason
    assert legacy.read_bytes() == before
    assert path.read_text() == _DESCRIPTION


def test_migrate_finishes_a_run_that_died_between_its_two_writes(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    legacy = _legacy(hive, "description: Add a /healthz endpoint.\n")
    path = Path(tasks.description_path(hive, "task-1"))
    path.parent.mkdir(parents=True)
    path.write_text(
        "## Goal\n\nAdd a /healthz endpoint.\n\n## Done when\n\nNot stated separately; see Goal.\n"
    )

    report = tasks.migrate(hive, _TTL)

    assert report.moved == [("task-1", str(path))]
    assert legacy.read_text() == _HEAD + _BODY


# -- file_split: the epic format (docs/plans/task-split.md section 2) -----------

_S1_TEXT = (
    f"## Goal\n\n{_GOAL}\n\nPart of epic task-1. Surface: observability/api/.\n\n"
    f"## Done when\n\n{_DONE}\n"
)
_S2_TEXT = (
    "## Goal\n\nShow it.\n\nPart of epic task-1. Surface: front/.\n\n"
    "## Surface\n\nThe status page.\n\n## Done when\n\nIt shows.\n"
)


def _tree(hive: str) -> dict[str, bytes]:
    """Every file under the hive root, tasks and board both, by relative path."""
    root = Path(hive).parent
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _refused(hive: str, subtasks: list[SubTask], parent_id: str = "task-1") -> tasks.SplitError:
    """File a split that must be refused, and check nothing was written."""
    before = _tree(hive)
    with pytest.raises(tasks.SplitError) as excinfo:
        tasks.file_split(hive, parent_id, subtasks)
    assert _tree(hive) == before
    assert excinfo.value.task_id == parent_id
    return excinfo.value


def test_file_split_files_the_sub_tasks_and_makes_the_parent_an_epic(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    parent = _legacy(hive, "")

    assert tasks.file_split(hive, "task-1", _SPLIT) == ["task-1-S1", "task-1-S2"]

    first = read_task_file(task_file_path(hive, "task-1-S1"))
    second = read_task_file(task_file_path(hive, "task-1-S2"))
    for sub in (first, second):
        assert (sub.status, sub.owner, sub.heartbeat, sub.body) == ("pending", None, None, "")
        assert sub.epic == "task-1"
        assert sub.split_into == [] and sub.merged is None
    assert first.depends_on == []
    assert second.depends_on == ["task-1-S1"]
    # The ask is on the board, never in the frontmatter.
    for task_id in ("task-1-S1", "task-1-S2"):
        assert "description" not in Path(task_file_path(hive, task_id)).read_text()
    assert tasks.read_description(hive, "task-1-S1") == _S1_TEXT
    assert tasks.read_description(hive, "task-1-S2") == _S2_TEXT
    parsed = tasks.parse_description(_S2_TEXT)
    assert parsed.goal == "Show it.\n\nPart of epic task-1. Surface: front/."
    assert (parsed.surface, parsed.done_when) == ("The status page.", "It shows.")

    epic = read_task_file(str(parent))
    assert epic.status == "epic"
    assert epic.split_into == ["task-1-S1", "task-1-S2"]
    assert (epic.owner, epic.depends_on, epic.epic) == ("cuenta1", ["task-0"], None)
    assert epic.body == _BODY.split("---\n\n", 1)[1]


def test_file_split_puts_the_epic_line_under_a_goal_that_comes_last(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    last = SubTask(
        title="t", surface="docs/\n## Steps\nand  tests", after=(),
        description="## Done when\n\nDone.\n\n## Goal\n\nDo it.\n\n\n",
    )

    tasks.file_split(hive, "task-1", [_SPLIT[0], last])

    text = tasks.read_description(hive, "task-1-S2")
    assert text == (
        "## Done when\n\nDone.\n\n## Goal\n\nDo it.\n\n"
        "Part of epic task-1. Surface: docs/ ## Steps and tests.\n\n\n"
    )
    # A newline in the surface opened no section.
    assert tasks.parse_description(text).steps is None


def test_file_split_refuses_a_description_that_is_not_in_sections(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    bad = SubTask(title="t", surface="front/", description="Just do it.", after=())

    error = _refused(hive, [_SPLIT[0], bad])

    assert error.problems == [
        "sub-task 2: its description has text before its first section; start with `## Goal`"
    ]
    assert "task task-1: the split cannot be filed: sub-task 2" in str(error)


def test_file_split_refuses_what_split_problems_refuses(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")

    error = _refused(hive, _SPLIT[:1])

    assert error.problems == ["a split has 2 to 4 sub-tasks, this one has 1"]


def test_file_split_refuses_a_parent_that_is_inside_an_epic(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    tasks.file_split(hive, "task-1", _SPLIT)

    error = _refused(hive, _SPLIT, parent_id="task-1-S1")

    assert error.problems == [
        "task task-1-S1 is part of epic task-1; a task inside an epic cannot become an epic"
    ]


def test_file_split_refuses_a_sub_task_that_exists_with_another_description(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    tasks.write_description(hive, "task-1-S2", _DESCRIPTION)
    tasks.acquire_lock(hive, "task-1-S1", owner="cuenta1")  # a task file, no description

    error = _refused(hive, _SPLIT)

    assert error.problems == [
        "task-1-S1 already exists with no description",
        "task-1-S2 already exists with a different description",
    ]


def test_file_split_refuses_a_parent_with_no_task_file(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    error = _refused(hive, _SPLIT)

    assert error.problems == ["task task-1 has no task file"]
    assert not os.path.exists(tasks.board_root(hive))


def test_file_split_refuses_a_parent_already_split_into_other_ids(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    third = SubTask(title="t", surface="docs/", description=_DESCRIPTION, after=(2,))
    tasks.file_split(hive, "task-1", _SPLIT + [third])

    error = _refused(hive, _SPLIT)

    assert error.problems == ["task task-1 is already split into task-1-S1, task-1-S2, task-1-S3"]


def test_file_split_names_every_problem_in_one_error(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    blank = SubTask(title="", surface="front/", description="## Goal\n\nOnly a goal.\n", after=(2,))

    error = _refused(hive, [blank, _SPLIT[0]])

    assert error.problems == [
        "sub-task 1: title is blank",
        "sub-task 1: after names 2, which is not an entry before it",
        "task task-1 has no task file",
        "sub-task 1: its description is missing its `## Done when` section",
    ]
    for problem in error.problems:
        assert problem in str(error)


def test_file_split_refuses_a_parent_id_that_is_not_one_path_component(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        tasks.file_split(hive, "../task-1", _SPLIT)

    assert not isinstance(excinfo.value, tasks.SplitError)
    assert _tree(hive) == {}


def test_filing_the_same_split_again_writes_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    tasks.file_split(hive, "task-1", _SPLIT)
    # The first sub-task has run since: its file is not the one that was filed.
    tasks.acquire_lock(hive, "task-1-S1", owner="cuenta1")
    tasks.handoff(hive, "task-1-S1", new_status="done", body="## arquitecto\n\nDone.")
    before = _tree(hive)
    mtimes = {name: os.stat(Path(hive).parent / name).st_mtime_ns for name in before}

    assert tasks.file_split(hive, "task-1", _SPLIT) == ["task-1-S1", "task-1-S2"]

    assert _tree(hive) == before
    assert {name: os.stat(Path(hive).parent / name).st_mtime_ns for name in before} == mtimes


def test_file_split_finishes_a_run_that_died_after_its_first_write(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    tasks.write_description(hive, "task-1-S1", _S1_TEXT)

    tasks.file_split(hive, "task-1", _SPLIT)

    assert read_task_file(task_file_path(hive, "task-1-S1")).epic == "task-1"
    assert read_task_file(task_file_path(hive, "task-1-S2")).depends_on == ["task-1-S1"]
    assert tasks.read_description(hive, "task-1-S2") == _S2_TEXT
    assert read_task_file(task_file_path(hive, "task-1")).status == "epic"


def test_filing_again_does_not_reopen_an_epic_that_was_closed(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    parent = _legacy(hive, "")
    tasks.file_split(hive, "task-1", _SPLIT)
    closed = read_task_file(str(parent))
    closed.status = "done"
    write_task_file(str(parent), closed)
    before = _tree(hive)

    tasks.file_split(hive, "task-1", _SPLIT)

    assert _tree(hive) == before
    assert read_task_file(str(parent)).status == "done"


# -- mark_merged, unmet_dependencies, mark_blocked ------------------------------


def test_mark_merged_writes_the_block_and_leaves_the_rest(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    path = _legacy(hive, "")
    before = read_task_file(str(path))

    tasks.mark_merged(hive, "task-1", "main")

    after = read_task_file(str(path))
    assert after.merged["into"] == "main"
    assert isinstance(after.merged["at"], str)
    assert dt.datetime.fromisoformat(after.merged["at"]).utcoffset() == dt.timedelta(0)
    assert sorted(after.merged) == ["at", "into"]
    after.merged = None
    assert after == before
    # The rest of the file to the byte: the block is appended to the frontmatter.
    head, block = path.read_text().split("merged:\n", 1)
    assert head + block.split("---\n", 1)[1] == _HEAD + _BODY.removeprefix("---\n")


def test_a_second_mark_merged_replaces_the_block(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")

    tasks.mark_merged(hive, "task-1", "main")
    tasks.mark_merged(hive, "task-1", "release")

    assert read_task_file(task_file_path(hive, "task-1")).merged["into"] == "release"


@pytest.mark.parametrize("writer", [lambda h: tasks.mark_merged(h, "task-1", "main"), lambda h: tasks.mark_blocked(h, "task-1")])
def test_marking_a_task_with_no_file_raises_and_creates_nothing(tmp_path: Path, writer) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(FileNotFoundError):
        writer(hive)

    assert _tree(hive) == {}


def test_unmet_dependencies_says_which_are_missing_and_which_are_not_merged(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    tasks.handoff(hive, "task-a", new_status="done", body="## auditor")  # done, not merged
    tasks.handoff(hive, "task-b", new_status="done", body="## auditor")
    tasks.mark_merged(hive, "task-b", "main")
    Path(task_file_path(hive, "task-c")).write_text("TODO write this up\n")
    task = _legacy(hive, "")
    waiting = read_task_file(str(task))
    waiting.depends_on = ["task-a", "task-404", "task-b", "task-c", "../x", 7]
    before = _tree(hive)

    assert tasks.unmet_dependencies(hive, waiting) == [
        "task-a is not merged",
        "task-404 has no task file",
        "task-c: its task file cannot be read",
        "'../x' is not a task id",
        "7 is not a task id",
    ]
    assert _tree(hive) == before  # a reader

    tasks.mark_merged(hive, "task-a", "main")
    waiting.depends_on = ["task-a", "task-b"]
    assert tasks.unmet_dependencies(hive, waiting) == []
    waiting.depends_on = []
    assert tasks.unmet_dependencies(hive, waiting) == []
    waiting.depends_on = "task-a"
    assert tasks.unmet_dependencies(hive, waiting) == ["depends_on is 'task-a', not a list"]


def test_a_sub_task_waits_for_the_one_it_comes_after_until_that_is_merged(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")
    tasks.file_split(hive, "task-1", _SPLIT)
    second = read_task_file(task_file_path(hive, "task-1-S2"))

    assert tasks.unmet_dependencies(hive, second) == ["task-1-S1 is not merged"]
    tasks.mark_merged(hive, "task-1-S1", "main")
    assert tasks.unmet_dependencies(hive, second) == []


def test_mark_blocked_sets_the_status_and_releases_the_lock_keeping_the_body(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    path = _legacy(hive, "")
    before = read_task_file(str(path))
    assert (before.status, before.owner) == ("in_progress", "cuenta1")

    tasks.mark_blocked(hive, "task-1")

    after = read_task_file(str(path))
    assert (after.status, after.owner, after.heartbeat) == ("blocked", None, None)
    assert (after.body, after.depends_on) == (before.body, before.depends_on)
