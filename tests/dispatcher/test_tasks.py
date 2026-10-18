import ast
import datetime as dt
import json
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from dispatcher import tasks
from dispatcher.context_transfer import (
    LockHeldError,
    list_task_ids,
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
    """Every file under the hive root, tasks and board both, by relative path.

    Not the write locks: taking one leaves an empty lock file that is no
    write to the task (ADR 127), and every writer takes one before it reads.
    """
    root = Path(hive).parent
    locks = Path(tasks.locks_root(hive))
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and locks not in path.parents
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


# -- phase (ADR 126) ------------------------------------------------------------


def test_acquire_lock_writes_the_phase_and_refresh_heartbeat_keeps_it(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    path = task_file_path(hive, "task-1")

    tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="arquitecto")
    assert read_task_file(path).phase == "arquitecto"
    assert "phase: arquitecto\n" in Path(path).read_text()

    tasks.refresh_heartbeat(hive, "task-1")
    assert read_task_file(path).phase == "arquitecto"


def test_acquire_lock_replaces_the_phase_and_without_one_leaves_none(tmp_path: Path) -> None:
    """A lock taken again names its own phase, never the one before it."""
    hive = _hive(tmp_path)
    path = task_file_path(hive, "task-1")
    tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="arquitecto")

    tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="implementador")
    assert read_task_file(path).phase == "implementador"

    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    assert read_task_file(path).phase is None


#: Each writer that drops the lock, as a call on (hive, task id).
_RELEASES = {
    "release_stale_lock": tasks.release_stale_lock,
    "handoff": lambda hive, tid: tasks.handoff(hive, tid, new_status="pending", body="## arquitecto"),
    "mark_blocked": tasks.mark_blocked,
}


@pytest.mark.parametrize("release", sorted(_RELEASES))
def test_every_writer_that_drops_the_lock_clears_the_phase(tmp_path: Path, release: str) -> None:
    hive = _hive(tmp_path)
    path = task_file_path(hive, "task-1")
    tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="revisor")

    _RELEASES[release](hive, "task-1")

    after = read_task_file(path)
    assert (after.owner, after.heartbeat, after.phase) == (None, None, None)
    assert "phase:" not in Path(path).read_text()


@pytest.mark.parametrize(
    "writer", ["set_resolved_debt", "mark_merged"],
)
def test_a_writer_that_leaves_the_lock_leaves_the_phase(tmp_path: Path, writer: str) -> None:
    hive = _hive(tmp_path)
    tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="integrador")

    _WRITERS[writer](hive, "task-1")

    assert read_task_file(task_file_path(hive, "task-1")).phase == "integrador"


def test_a_task_file_without_a_phase_keeps_its_bytes(tmp_path: Path) -> None:
    """Written only when set: a file from before the key round-trips unchanged."""
    hive = _hive(tmp_path)
    path = _legacy(hive, "")
    before = path.read_bytes()

    write_task_file(str(path), read_task_file(str(path)))

    assert path.read_bytes() == before


# -- close_epic, epic_problems --------------------------------------------------


def _epic(hive: str) -> None:
    """`task-1` split by `file_split` into `task-1-S1` and `task-1-S2`."""
    _legacy(hive, "")
    tasks.file_split(hive, "task-1", _SPLIT)


def _edit(hive: str, task_id: str, **fields) -> None:
    """Change a task file by hand, as an operator writing an epic would."""
    path = task_file_path(hive, task_id)
    task = read_task_file(path)
    for name, value in fields.items():
        setattr(task, name, value)
    write_task_file(path, task)


def test_close_epic_closes_the_parent_on_its_last_merged_child_and_not_before(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    parent_before = read_task_file(task_file_path(hive, "task-1"))

    tasks.mark_merged(hive, "task-1-S1", "main")
    before = _tree(hive)
    assert tasks.close_epic(hive, "task-1-S1") is None
    assert _tree(hive) == before

    tasks.mark_merged(hive, "task-1-S2", "main")
    assert tasks.close_epic(hive, "task-1-S2") == "task-1"

    parent = read_task_file(task_file_path(hive, "task-1"))
    assert parent.status == "done"
    parent.status = parent_before.status
    assert parent == parent_before  # the lock, the body and the list stay
    # Closed already: nothing is written a second time.
    before = _tree(hive)
    assert tasks.close_epic(hive, "task-1-S2") is None
    assert _tree(hive) == before


@pytest.mark.parametrize(
    "arrange",
    [
        lambda hive: _edit(hive, "task-1-S2", epic=None),
        lambda hive: _edit(hive, "task-1", split_into=["task-1-S1", "task-1-S3"]),
        lambda hive: os.remove(task_file_path(hive, "task-1")),
        lambda hive: os.remove(task_file_path(hive, "task-1-S1")),
        lambda hive: Path(task_file_path(hive, "task-1")).write_text("TODO\n"),
    ],
    ids=["no-epic", "not-in-split-into", "no-parent", "a-sibling-missing", "parent-unreadable"],
)
def test_close_epic_writes_nothing_when_the_epic_is_not_this_tasks_to_close(
    tmp_path: Path, arrange,
) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    tasks.mark_merged(hive, "task-1-S1", "main")
    tasks.mark_merged(hive, "task-1-S2", "main")
    arrange(hive)
    before = _tree(hive)

    assert tasks.close_epic(hive, "task-1-S2") is None
    assert tasks.close_epic(hive, "task-404") is None
    assert _tree(hive) == before


def test_close_epic_refuses_an_unmigrated_parent_like_every_writer(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    tasks.mark_merged(hive, "task-1-S1", "main")
    tasks.mark_merged(hive, "task-1-S2", "main")
    parent = Path(task_file_path(hive, "task-1"))
    parent.write_text(parent.read_text().replace("---\n", "---\ndescription: old\n", 1))
    before = _tree(hive)

    with pytest.raises(tasks.UnmigratedTaskError):
        tasks.close_epic(hive, "task-1-S2")

    assert _tree(hive) == before


@pytest.mark.parametrize(
    "s1, s2, closed_as",
    [
        ("merged", "dropped", "done"),
        ("dropped", "merged", "done"),
        ("dropped", "dropped", "dropped"),
    ],
)
def test_close_epic_counts_a_dropped_child_as_settled(
    tmp_path: Path, s1: str, s2: str, closed_as: str,
) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    for child, settled in (("task-1-S1", s1), ("task-1-S2", s2)):
        if settled == "merged":
            tasks.mark_merged(hive, child, "main")
        else:
            _edit(hive, child, status="dropped")

    assert tasks.close_epic(hive, "task-1-S2") == "task-1"

    # Done when one of them shipped; dropped, a final backlog status, when none did.
    assert read_task_file(task_file_path(hive, "task-1")).status == closed_as


@pytest.mark.parametrize("status", ["pending", "in_progress", "blocked", "deferred", "proposed", "done"])
def test_close_epic_waits_for_a_child_that_is_not_merged_or_dropped(
    tmp_path: Path, status: str,
) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    _edit(hive, "task-1-S1", status="dropped")
    _edit(hive, "task-1-S2", status=status)
    before = _tree(hive)

    assert tasks.close_epic(hive, "task-1-S1") is None
    assert _tree(hive) == before


def _problems(hive: str, task_id: str = "task-1-S2") -> list[str]:
    before = _tree(hive)
    problems = tasks.epic_problems(hive, read_task_file(task_file_path(hive, task_id)))
    assert _tree(hive) == before  # a reader
    return problems


def test_epic_problems_finds_none_in_a_split_that_was_filed(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive)

    assert _problems(hive, "task-1-S1") == []
    assert _problems(hive, "task-1-S2") == []
    # And a task that is in no epic has none to check.
    assert _problems(hive, "task-1") == []


@pytest.mark.parametrize(
    "arrange, expected",
    [
        (
            lambda hive: os.remove(task_file_path(hive, "task-1")),
            ["its epic task-1 has no task file"],
        ),
        (
            lambda hive: Path(task_file_path(hive, "task-1")).write_text("TODO\n"),
            ["its epic task-1: its task file cannot be read"],
        ),
        (
            lambda hive: _edit(hive, "task-1-S2", epic="../x"),
            ["its epic '../x' is not a task id"],
        ),
        (
            lambda hive: _edit(hive, "task-1", status="done"),
            ["its epic task-1 has status done, not epic"],
        ),
        (
            lambda hive: _edit(hive, "task-1", epic="task-0"),
            [
                "its epic task-1 is part of epic task-0; "
                "a task inside an epic cannot become an epic"
            ],
        ),
        (
            lambda hive: _edit(hive, "task-1", split_into="task-1-S1"),
            ["epic task-1: split_into is 'task-1-S1', not a list"],
        ),
        (
            lambda hive: _edit(hive, "task-1", split_into=["task-1-S2"]),
            [
                "epic task-1: an epic has 2 to 4 sub-tasks, this one has 1",
                # What it waits for is no longer one of the epic's.
                "task-1-S2: depends_on names task-1-S1, which is not a sibling",
            ],
        ),
        (
            lambda hive: _edit(hive, "task-1", split_into=["task-1-S1", "task-1-S1"]),
            [
                "epic task-1 does not name task-1-S2 in its split_into",
                "epic task-1: split_into names task-1-S1 more than once",
            ],
        ),
        (
            lambda hive: _edit(
                hive, "task-1", split_into=["task-1-S1", "task-1-S2", "task-1-S3", "../x"]
            ),
            ["epic task-1: task-1-S3 has no task file", "epic task-1: '../x' is not a task id"],
        ),
        (
            lambda hive: _edit(hive, "task-1-S1", epic="task-7"),
            ["task-1-S1: its epic is task-7, not task-1"],
        ),
        (
            lambda hive: _edit(hive, "task-1-S1", epic=None),
            ["task-1-S1: its epic is not set, not task-1"],
        ),
        (
            lambda hive: _edit(hive, "task-1-S2", depends_on=["task-1-S1", "task-0", "task-1-S2"]),
            [
                "task-1-S2: depends_on names task-0, which is not a sibling",
                "task-1-S2: depends_on names task-1-S2, which is not a sibling",
            ],
        ),
        (
            lambda hive: _edit(hive, "task-1-S1", depends_on="task-1-S2"),
            ["task-1-S1: depends_on is 'task-1-S2', not a list"],
        ),
    ],
    ids=[
        "no-parent", "parent-unreadable", "epic-not-an-id", "parent-not-an-epic",
        "parent-inside-an-epic", "split-into-not-a-list", "too-few", "not-named-and-twice",
        "child-missing-or-not-an-id", "child-of-another-epic", "child-of-no-epic",
        "depends-on-a-stranger", "depends-on-not-a-list",
    ],
)
def test_epic_problems_names_what_is_wrong_with_a_hand_written_epic(
    tmp_path: Path, arrange, expected: list[str],
) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    arrange(hive)

    assert _problems(hive) == expected


def test_epic_problems_reports_every_problem_at_once(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    _edit(hive, "task-1", status="pending", split_into=["task-1-S1"])

    assert _problems(hive) == [
        "its epic task-1 has status pending, not epic",
        "epic task-1 does not name task-1-S2 in its split_into",
        "epic task-1: an epic has 2 to 4 sub-tasks, this one has 1",
    ]


# --- the base lock (docs/plans/autonomy.md B-1, ADR 80) ----------------------

def _base_record(state_dir, slug="myproj"):
    path = Path(tasks.base_lock_path(str(state_dir), slug))
    return json.loads(path.read_text()) if path.exists() else None


def _plant_base_lock(state_dir, *, owner="elsewhere:1:abcd", host=None, pid=None,
                     age_seconds=0.0, slug="myproj"):
    """A lock file some other holder left, its heartbeat `age_seconds` old."""
    path = Path(tasks.base_lock_path(str(state_dir), slug))
    path.parent.mkdir(parents=True, exist_ok=True)
    beat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_seconds)).isoformat()
    path.write_text(json.dumps({
        "owner": owner, "host": host or "another-host", "pid": pid or 1,
        "operation": "merge", "acquired": beat, "heartbeat": beat,
    }))
    return path


def _dead_pid() -> int:
    """A pid nothing on this host is using."""
    pid = 4_000_000
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pass
        pid += 1


def test_the_base_lock_path_is_one_file_per_project(tmp_path) -> None:
    assert tasks.base_lock_path(str(tmp_path), "myproj") == str(tmp_path / "base-locks" / "myproj.lock")
    with pytest.raises(ValueError):
        tasks.base_lock_path(str(tmp_path), "../escape")


def test_two_holders_contend_and_the_second_is_refused(tmp_path) -> None:
    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-a", "merge", ttl_seconds=120)

    with pytest.raises(tasks.BaseLockHeldError) as excinfo:
        tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "update", ttl_seconds=120)

    # The refusal names the project, who holds it, for what, and what was refused.
    message = str(excinfo.value)
    assert "myproj" in message and "holder-a" in message
    assert "(merge," in message and "update was refused" in message
    assert _base_record(tmp_path)["owner"] == "holder-a"


def test_the_base_lock_is_per_project(tmp_path) -> None:
    tasks.acquire_base_lock(str(tmp_path), "one", "holder-a", "merge", ttl_seconds=120)

    tasks.acquire_base_lock(str(tmp_path), "two", "holder-b", "merge", ttl_seconds=120)

    assert _base_record(tmp_path, "two")["owner"] == "holder-b"


def test_only_the_holder_releases_the_base_lock(tmp_path) -> None:
    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-a", "merge", ttl_seconds=120)

    assert tasks.release_base_lock(str(tmp_path), "myproj", "holder-b") is False
    assert _base_record(tmp_path)["owner"] == "holder-a"
    assert tasks.release_base_lock(str(tmp_path), "myproj", "holder-a") is True
    assert _base_record(tmp_path) is None
    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)


def test_a_stale_base_lock_on_another_host_is_broken_after_its_ttl(tmp_path) -> None:
    _plant_base_lock(tmp_path, age_seconds=121)

    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)

    assert _base_record(tmp_path)["owner"] == "holder-b"


def test_a_stale_base_lock_whose_pid_is_dead_is_broken_after_its_ttl(tmp_path) -> None:
    _plant_base_lock(tmp_path, host=socket.gethostname(), pid=_dead_pid(), age_seconds=121)

    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)

    assert _base_record(tmp_path)["owner"] == "holder-b"


def test_a_corrupt_base_lock_file_counts_as_stale(tmp_path) -> None:
    path = _plant_base_lock(tmp_path)
    path.write_text("not json")

    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)

    assert _base_record(tmp_path)["owner"] == "holder-b"


def test_a_base_lock_inside_its_ttl_is_never_broken(tmp_path) -> None:
    _plant_base_lock(tmp_path, age_seconds=60)

    with pytest.raises(tasks.BaseLockHeldError):
        tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)


def test_a_live_base_lock_holder_is_never_broken_however_old_its_heartbeat(tmp_path) -> None:
    """The holder is checked before it is broken: a process on this host that
    is still alive is stuck, not dead, and a merge under it would interleave."""
    _plant_base_lock(tmp_path, host=socket.gethostname(), pid=os.getpid(), age_seconds=10_000)

    with pytest.raises(tasks.BaseLockHeldError):
        tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-b", "merge", ttl_seconds=120)


def test_base_lock_is_held_for_the_operation_only(tmp_path) -> None:
    lock = tasks.BaseLock(str(tmp_path), "myproj", "update",
                          ttl_seconds=120, heartbeat_interval_seconds=30)

    with lock:
        record = _base_record(tmp_path)
        assert record["owner"] == lock.owner
        assert record["operation"] == "update"
        assert record["host"] == socket.gethostname() and record["pid"] == os.getpid()

    assert _base_record(tmp_path) is None
    assert lock.held is False


def test_base_lock_is_released_when_the_operation_raises(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        with tasks.BaseLock(str(tmp_path), "myproj", "merge",
                            ttl_seconds=120, heartbeat_interval_seconds=30):
            raise RuntimeError("docker exec died")

    assert _base_record(tmp_path) is None


def test_base_lock_refuses_after_its_bounded_wait(tmp_path) -> None:
    tasks.acquire_base_lock(str(tmp_path), "myproj", "holder-a", "merge", ttl_seconds=120)
    lock = tasks.BaseLock(str(tmp_path), "myproj", "update", ttl_seconds=120,
                          heartbeat_interval_seconds=30, wait_seconds=0.2, poll_seconds=0.05)

    start = time.monotonic()
    with pytest.raises(tasks.BaseLockHeldError) as excinfo:
        lock.acquire()

    assert time.monotonic() - start >= 0.2
    assert "after waiting" in str(excinfo.value)
    assert lock.held is False
    assert _base_record(tmp_path)["owner"] == "holder-a"


def test_base_lock_waits_for_the_holder_to_let_go(tmp_path) -> None:
    first = tasks.BaseLock(str(tmp_path), "myproj", "merge",
                           ttl_seconds=120, heartbeat_interval_seconds=30).acquire()
    second = tasks.BaseLock(str(tmp_path), "myproj", "update", ttl_seconds=120,
                            heartbeat_interval_seconds=30, wait_seconds=5, poll_seconds=0.05)
    timer = threading.Timer(0.2, first.release)
    timer.start()
    try:
        with second:
            assert _base_record(tmp_path)["owner"] == second.owner
    finally:
        timer.join()

    assert _base_record(tmp_path) is None


def test_base_lock_heartbeat_keeps_a_long_operation_fresh(tmp_path) -> None:
    lock = tasks.BaseLock(str(tmp_path), "myproj", "merge",
                          ttl_seconds=120, heartbeat_interval_seconds=0.05)
    with lock:
        first = _base_record(tmp_path)["heartbeat"]
        deadline = time.monotonic() + 5
        while _base_record(tmp_path)["heartbeat"] == first and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _base_record(tmp_path)["heartbeat"] > first


def test_a_base_lock_holder_whose_lock_was_broken_leaves_the_new_one(tmp_path) -> None:
    lock = tasks.BaseLock(str(tmp_path), "myproj", "merge",
                          ttl_seconds=120, heartbeat_interval_seconds=30).acquire()
    # Someone broke it and took it in between.
    _plant_base_lock(tmp_path, owner="newcomer")

    lock.release()

    assert _base_record(tmp_path)["owner"] == "newcomer"


# -- the backlog's fields and the write lock (ADR 127) ------------------------


def _file(hive: str, task_id: str = "task-1", **frontmatter) -> Path:
    """A migrated task file with these frontmatter keys, written as literal YAML."""
    lines = [f"task_id: {task_id}", "status: pending"] + [
        f"{key}: {json.dumps(value)}" for key, value in frontmatter.items()
    ]
    path = Path(task_file_path(hive, task_id))
    path.write_text("---\n" + "\n".join(lines) + "\n---\n\nbody\n")
    return path


def test_priority_and_source_round_trip_and_a_writer_keeps_them(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    path = _file(hive, status="proposed", priority="high", source="arquitecto")

    task = read_task_file(str(path))
    assert (task.status, task.priority, task.source) == ("proposed", "high", "arquitecto")

    tasks.set_resolved_debt(hive, "task-1", ["task-0-D1"])
    after = read_task_file(str(path))
    assert (after.status, after.priority, after.source) == ("proposed", "high", "arquitecto")


def test_a_task_with_neither_field_gets_neither_key_written(tmp_path: Path) -> None:
    """Every task filed before ADR 127 keeps its bytes' shape: no new keys."""
    hive = _hive(tmp_path)
    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    text = Path(task_file_path(hive, "task-1")).read_text()
    assert "priority" not in text and "source" not in text
    task = read_task_file(task_file_path(hive, "task-1"))
    assert (task.priority, task.source) == (None, None)
    assert tasks.effective_priority(task) == tasks.DEFAULT_PRIORITY == "normal"


def test_a_bad_hand_edited_field_is_kept_by_the_next_write_and_named(tmp_path: Path) -> None:
    """Validation on read: a writer does not drop or refuse what a hand edit put there."""
    hive = _hive(tmp_path)
    path = _file(hive, priority=0, source="someone")

    tasks.mark_blocked(hive, "task-1", "stuck")

    task = read_task_file(str(path))
    assert (task.priority, task.source) == (0, "someone")
    problems = tasks.field_problems(task)
    assert len(problems) == 2
    assert "priority is 0" in problems[0] and "high, normal, low" in problems[0]
    assert "source is 'someone'" in problems[1]
    assert tasks.dispatch_problems(task) == problems


@pytest.mark.parametrize("priority", tasks.PRIORITIES)
@pytest.mark.parametrize("source", tasks.SOURCES)
def test_every_listed_value_is_fine(tmp_path: Path, priority: str, source: str) -> None:
    task = read_task_file(str(_file(_hive(tmp_path), priority=priority, source=source)))
    assert tasks.field_problems(task) == []
    assert tasks.dispatch_problems(task) == []
    assert tasks.effective_priority(task) == priority


@pytest.mark.parametrize("status", tasks.BACKLOG_STATUSES)
def test_a_backlog_task_is_not_dispatchable(tmp_path: Path, status: str) -> None:
    task = read_task_file(str(_file(_hive(tmp_path), status=status)))
    assert tasks.is_backlog(task)
    [problem] = tasks.dispatch_problems(task)
    assert f"in the backlog (status {status})" in problem
    assert "pending" in problem


@pytest.mark.parametrize("status", ["pending", "in_progress", "blocked", "done", "epic"])
def test_a_board_task_is_not_refused_for_its_status(tmp_path: Path, status: str) -> None:
    task = read_task_file(str(_file(_hive(tmp_path), status=status)))
    assert not tasks.is_backlog(task)
    assert tasks.dispatch_problems(task) == []


@pytest.mark.parametrize("status", tasks.BACKLOG_STATUSES)
def test_acquire_lock_refuses_a_backlog_task_and_writes_nothing(tmp_path: Path, status: str) -> None:
    hive = _hive(tmp_path)
    path = _file(hive, status=status)
    before = path.read_bytes()

    with pytest.raises(tasks.BacklogTaskError) as excinfo:
        tasks.acquire_lock(hive, "task-1", owner="cuenta1", phase="arquitecto")

    assert isinstance(excinfo.value, ValueError)
    assert (excinfo.value.task_id, excinfo.value.status) == ("task-1", status)
    assert path.read_bytes() == before


def test_acquire_lock_still_takes_a_blocked_task(tmp_path: Path) -> None:
    """A retry is the operator's decision (ADR 117): only the backlog is refused."""
    hive = _hive(tmp_path)
    _file(hive, status="blocked", blocked_reason="stuck")
    task = tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    assert (task.status, task.owner, task.blocked_reason) == ("in_progress", "cuenta1", None)


def test_the_write_lock_lives_under_the_hive_root_not_beside_the_tasks(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    assert tasks.task_lock_path(hive, "T-7") == str(tmp_path / "hive" / "locks" / "T-7.lock")
    with pytest.raises(ValueError):
        tasks.task_lock_path(hive, "../T-7")

    old = os.umask(0o077)
    try:
        tasks.acquire_lock(hive, "T-7", owner="cuenta1")
    finally:
        os.umask(old)

    lock = Path(tasks.task_lock_path(hive, "T-7"))
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644
    assert lock.read_bytes() == b""
    assert os.listdir(hive) == ["T-7.md"]
    assert list(list_task_ids(hive)) == ["T-7"]
    assert not (Path(tasks.board_root(hive)) / "T-7.lock").exists()


def test_the_write_lock_is_exclusive_against_another_open_file(tmp_path: Path) -> None:
    """`flock` is per open file: what another process would meet."""
    import fcntl

    hive = _hive(tmp_path)
    with tasks.task_lock(hive, "task-1"):
        fd = os.open(tasks.task_lock_path(hive, "task-1"), os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    fd = os.open(tasks.task_lock_path(hive, "task-1"), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # let go, so free
    finally:
        os.close(fd)


def test_the_write_lock_is_re_entrant_in_one_thread(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    with tasks.task_lock(hive, "task-1"):
        with tasks.task_lock(hive, "task-1"):
            tasks.acquire_lock(hive, "task-1", owner="cuenta1")  # a writer inside: no wait on itself
        tasks.refresh_heartbeat(hive, "task-1")
    assert read_task_file(task_file_path(hive, "task-1")).owner == "cuenta1"


def test_another_thread_waits_for_the_write_lock(tmp_path: Path) -> None:
    """The heartbeat thread and the phase under it: the second writer waits."""
    hive = _hive(tmp_path)
    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    done = threading.Event()

    def beat() -> None:
        tasks.refresh_heartbeat(hive, "task-1")
        done.set()

    with tasks.task_lock(hive, "task-1"):
        thread = threading.Thread(target=beat)
        thread.start()
        assert not done.wait(0.3)
    thread.join(timeout=5)
    assert done.is_set()


def test_two_takers_of_a_free_task_cannot_both_have_it(tmp_path: Path) -> None:
    """The owner check and the write are one step: one taker wins, one is refused."""
    hive = _hive(tmp_path)
    _file(hive)
    start = threading.Barrier(2)
    won, refused = [], []

    def take(owner: str) -> None:
        start.wait()
        try:
            tasks.acquire_lock(hive, "task-1", owner=owner)
            won.append(owner)
        except LockHeldError:
            refused.append(owner)

    threads = [threading.Thread(target=take, args=(o,)) for o in ("cuenta1", "cuenta2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(won) == 1 and len(refused) == 1
    assert read_task_file(task_file_path(hive, "task-1")).owner == won[0]
