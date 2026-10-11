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

_GOAL = "Add a /healthz endpoint."
_DONE = "GET /healthz answers 200."
_DESCRIPTION = f"## Goal\n\n{_GOAL}\n\n## Done when\n\n{_DONE}\n"

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
    "set_kanban_issue_id": lambda hive, tid: tasks.set_kanban_issue_id(hive, tid, "an-issue"),
    "set_resolved_debt": lambda hive, tid: tasks.set_resolved_debt(hive, tid, ["task-0-D1"]),
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
