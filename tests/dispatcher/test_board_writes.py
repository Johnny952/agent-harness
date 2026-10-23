"""The board's write tools in `dispatcher.tasks` (T-084, ADR 129): propose,
edit_task, add_comment and accept, under the per-task write lock."""

import json
import os
import threading
from pathlib import Path

import pytest

from dispatcher import tasks
from dispatcher.context_transfer import (
    TaskFile,
    read_task_file,
    task_file_path,
    write_task_file,
)

_DESCRIPTION = "## Goal\n\nAdd a /healthz endpoint.\n\n## Done when\n\nGET /healthz answers 200.\n"
_OTHER = "## Goal\n\nSomething else.\n\n## Done when\n\nIt is done.\n"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
DROP = {"reason": "no longer needed", "by": "operator"}


def _hive(tmp_path: Path) -> str:
    hive = tmp_path / "hive" / "tasks"
    hive.mkdir(parents=True)
    return str(hive)


def _task(hive: str, task_id: str, status: str = "pending", **fields) -> None:
    write_task_file(
        task_file_path(hive, task_id),
        TaskFile(
            task_id=task_id, status=status, owner=fields.pop("owner", None),
            depends_on=fields.pop("depends_on", []), heartbeat=None,
            body=fields.pop("body", ""), **fields,
        ),
    )
    tasks.write_description(hive, task_id, _DESCRIPTION)


def _read(hive: str, task_id: str) -> TaskFile:
    return read_task_file(task_file_path(hive, task_id))


def _epic(hive: str, n: int = 2) -> dict:
    entries = [
        {"title": f"Part {i}", "description": _DESCRIPTION, **({"after": [i - 1]} if i > 1 else {})}
        for i in range(1, n + 1)
    ]
    return tasks.propose(
        hive, title="The epic", description=_DESCRIPTION, source="arquitecto", tasks=entries
    )


def _problems(excinfo) -> str:
    return "; ".join(excinfo.value.problems)


def _blocks_while_locked(hive: str, lock_id: str, write) -> None:
    """`write` waits while another holder has `lock_id`, and runs once it is let go."""
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            write()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            done.set()

    with tasks.task_lock(hive, lock_id):
        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.3)
    thread.join(5)
    assert done.is_set()
    assert errors == []


# -- propose -------------------------------------------------------------------


def test_propose_files_a_proposed_task_with_the_next_number(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-007")
    _task(hive, "T-007-S1")  # a split's id does not count

    result = tasks.propose(
        hive, title="  Health check  ", description=_DESCRIPTION, source="arquitecto",
        priority="high", depends_on=["T-007"],
    )

    assert result == {"id": "T-008", "created": True, "tasks": []}
    task = _read(hive, "T-008")
    assert (task.status, task.title, task.source, task.priority) == (
        "proposed", "Health check", "arquitecto", "high",
    )
    assert task.depends_on == ["T-007"]
    assert (task.owner, task.epic, task.split_into) == (None, None, [])
    assert tasks.read_description(hive, "T-008") == _DESCRIPTION


def test_propose_on_an_empty_hive_starts_at_one(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    assert tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")["id"] == "T-001"


def test_propose_is_idempotent_on_its_title(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    first = tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")
    again = tasks.propose(hive, title="A ", description=_DESCRIPTION, source="operator")

    assert again == {"id": first["id"], "created": False, "tasks": []}
    assert sorted(os.listdir(hive)) == [f"{first['id']}.md"]


def test_propose_again_with_another_ask_is_refused_pointing_at_task_edit(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")

    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.propose(hive, title="A", description=_OTHER, source="operator")

    assert "task_edit" in str(excinfo.value)
    assert "description" in str(excinfo.value)


def test_propose_again_finishes_a_proposal_whose_description_was_lost(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    first = tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")
    os.remove(tasks.description_path(hive, first["id"]))

    again = tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")

    assert again["created"] is False
    assert tasks.read_description(hive, first["id"]) == _DESCRIPTION


def test_a_task_with_the_title_inside_an_epic_does_not_count(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)

    result = tasks.propose(hive, title="Part 1", description=_DESCRIPTION, source="operator")

    assert result["created"] is True
    assert result["id"] != epic["tasks"][0]


@pytest.mark.parametrize(
    "fields, needle",
    [
        ({"title": ""}, "its title is blank"),
        ({"title": "a\nb"}, "more than one line"),
        ({"title": "x" * (tasks.MAX_TITLE_CHARS + 1)}, "longer than"),
        ({"description": "no sections"}, "its description"),
        ({"description": "## Goal\n\nOnly a goal.\n"}, "its description"),
        ({"source": "revisor"}, "its source is 'revisor'"),
        ({"source": None}, "its source is None"),
        ({"priority": "urgent"}, "priority is 'urgent'"),
        ({"depends_on": ["T-404"]}, "depends_on"),
        ({"depends_on": "T-001"}, "not a list"),
        ({"depends_on": ["T-001", "T-001"]}, "more than once"),
        ({"depends_on": ["d-20261018T120000Z-abcd"]}, "drafts"),
    ],
)
def test_propose_refuses_and_writes_nothing(tmp_path: Path, fields: dict, needle: str) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    before = sorted(os.listdir(hive))
    args = {"title": "A", "description": _DESCRIPTION, "source": "operator", **fields}

    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.propose(hive, **args)

    assert needle in str(excinfo.value)
    assert sorted(os.listdir(hive)) == before


def test_propose_reports_every_problem_at_once(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.propose(hive, title="", description="x", source="nobody", priority="urgent")

    assert len(excinfo.value.problems) == 4


def test_propose_an_epic(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-004")

    result = tasks.propose(
        hive, title="The epic", description=_DESCRIPTION, source="arquitecto", priority="low",
        tasks=[
            {"title": "One", "description": _DESCRIPTION},
            {"title": "Two", "description": _OTHER, "after": [1]},
            {"title": "Three", "description": _DESCRIPTION, "after": [1, 2]},
        ],
    )

    assert result == {
        "id": "T-005", "created": True, "tasks": ["T-005-S1", "T-005-S2", "T-005-S3"],
    }
    parent = _read(hive, "T-005")
    assert (parent.status, parent.split_into, parent.epic) == ("proposed", result["tasks"], None)
    for child_id, title, deps in [
        ("T-005-S1", "One", []), ("T-005-S2", "Two", ["T-005-S1"]),
        ("T-005-S3", "Three", ["T-005-S1", "T-005-S2"]),
    ]:
        child = _read(hive, child_id)
        assert (child.status, child.title, child.epic, child.depends_on) == ("proposed", title, "T-005", deps)
        assert (child.source, child.priority) == ("arquitecto", "low")
    assert tasks.read_description(hive, "T-005-S2") == _OTHER


@pytest.mark.parametrize(
    "entries, needle",
    [
        ([{"title": "One", "description": _DESCRIPTION}], "2 to 4 tasks"),
        ([{"title": f"P{i}", "description": _DESCRIPTION} for i in range(5)], "2 to 4 tasks"),
        ([{"title": "One", "description": _DESCRIPTION}, {"title": "One", "description": _DESCRIPTION}],
         "another task of the epic"),
        ([{"title": "One", "description": _DESCRIPTION},
          {"title": "Two", "description": _DESCRIPTION, "after": [2]}], "not a task before it"),
        ([{"title": "One", "description": _DESCRIPTION},
          {"title": "Two", "description": _DESCRIPTION, "after": [True]}], "not an integer"),
        ([{"title": "One", "description": _DESCRIPTION},
          {"title": "Two", "description": "bad"}], "task 2: its description"),
        ([{"title": "One", "description": _DESCRIPTION, "rank": 1},
          {"title": "Two", "description": _DESCRIPTION}], "unknown field 'rank'"),
        ("not a list", "tasks is not a list"),
    ],
)
def test_an_epic_is_all_or_nothing(tmp_path: Path, entries, needle: str) -> None:
    hive = _hive(tmp_path)

    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.propose(hive, title="E", description=_DESCRIPTION, source="operator", tasks=entries)

    assert needle in str(excinfo.value)
    assert os.listdir(hive) == []


def test_an_epic_takes_no_depends_on(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")

    with pytest.raises(tasks.BoardWriteError, match="an epic takes no depends_on"):
        tasks.propose(
            hive, title="E", description=_DESCRIPTION, source="operator", depends_on=["T-001"],
            tasks=[{"title": "A", "description": _DESCRIPTION}, {"title": "B", "description": _DESCRIPTION}],
        )


def test_propose_an_epic_again_answers_it_and_finishes_a_missing_task(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    first = _epic(hive, 3)
    os.remove(task_file_path(hive, first["tasks"][2]))

    again = _epic(hive, 3)

    assert again == {**first, "created": False}
    child = _read(hive, first["tasks"][2])
    assert (child.status, child.epic, child.depends_on) == ("proposed", first["id"], [first["tasks"][1]])


def test_propose_an_epic_again_with_other_tasks_is_refused(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive, 2)

    with pytest.raises(tasks.BoardWriteError, match="3 tasks, not 2|2 tasks, not 3"):
        _epic(hive, 3)


def test_a_task_and_an_epic_do_not_share_a_title(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive)
    with pytest.raises(tasks.BoardWriteError, match="an epic already has the title"):
        tasks.propose(hive, title="The epic", description=_DESCRIPTION, source="operator")

    tasks.propose(hive, title="Single", description=_DESCRIPTION, source="operator")
    with pytest.raises(tasks.BoardWriteError, match="not an epic already has the title"):
        tasks.propose(
            hive, title="Single", description=_DESCRIPTION, source="operator",
            tasks=[{"title": "A", "description": _DESCRIPTION}, {"title": "B", "description": _DESCRIPTION}],
        )


def test_propose_waits_for_the_create_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _blocks_while_locked(
        hive, tasks.CREATE_LOCK,
        lambda: tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator"),
    )
    assert _read(hive, "T-001").status == "proposed"


def test_concurrent_proposals_get_distinct_ids(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    results: list[dict] = []

    def run(i: int) -> None:
        results.append(tasks.propose(hive, title=f"Task {i}", description=_DESCRIPTION, source="operator"))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert sorted(r["id"] for r in results) == [f"T-{n:03d}" for n in range(1, 9)]


def test_accept_drafts_waits_for_the_create_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    draft_id = tasks.new_draft_id()
    text = _DESCRIPTION + "\n## Surface\n\n`api/`\n\n## Out of scope\n\nThe rest.\n"
    tasks.write_draft(hive, draft_id, tasks.Draft(name="1", description=text, depends_on=[]))
    _blocks_while_locked(hive, tasks.CREATE_LOCK, lambda: tasks.accept_drafts(hive, draft_id))


def test_the_title_round_trips_and_an_old_file_has_none(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    assert "title:" not in Path(task_file_path(hive, "T-001")).read_text()
    assert _read(hive, "T-001").title is None

    tasks.propose(hive, title="Has one", description=_DESCRIPTION, source="operator")
    assert "title: Has one" in Path(task_file_path(hive, "T-002")).read_text()


# -- edit_task -----------------------------------------------------------------


def test_edit_changes_only_what_differs(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")

    result = tasks.edit_task(
        hive, "T-002", title="B", description=_OTHER, depends_on=["T-001"], priority="normal",
    )

    assert result == {"id": "T-002", "changed": ["description", "title", "depends_on", "priority"]}
    task = _read(hive, "T-002")
    assert (task.title, task.depends_on, task.priority, task.status) == ("B", ["T-001"], "normal", "proposed")
    assert tasks.read_description(hive, "T-002") == _OTHER

    assert tasks.edit_task(hive, "T-002", title="B", description=_OTHER)["changed"] == []


def test_edit_with_nothing_to_change_is_refused(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    with pytest.raises(tasks.BoardWriteError, match="nothing to change"):
        tasks.edit_task(hive, "T-001")


def test_edit_a_missing_task_is_no_task(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    with pytest.raises(tasks.NoTaskError):
        tasks.edit_task(hive, "T-404", priority="low")


@pytest.mark.parametrize("task_id", ["../x", "a/b", "", 7, "d-20261018T120000Z-abcd"])
def test_a_write_refuses_what_is_not_a_task_id(tmp_path: Path, task_id) -> None:
    hive = _hive(tmp_path)
    for write in (
        lambda: tasks.edit_task(hive, task_id, priority="low"),
        lambda: tasks.add_comment(hive, task_id, "operator", "hi"),
        lambda: tasks.accept(hive, task_id),
    ):
        with pytest.raises(tasks.BoardWriteError):
            write()


@pytest.mark.parametrize(
    "status, fields, needle",
    [
        ("pending", {"owner": "cuenta1"}, "a dispatcher holds it"),
        ("pending", {"merged": "main"}, "it is merged"),
        ("pending", {"body": "## arquitecto\n\nDone.\n"}, "handoffs"),
        ("in_progress", {}, "its status is in_progress"),
        ("review", {}, "its status is review"),
        ("blocked", {}, "its status is blocked"),
    ],
)
def test_edit_refuses_a_task_that_has_started(tmp_path: Path, status, fields, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status, **fields)
    before = Path(task_file_path(hive, "T-001")).read_bytes()

    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.edit_task(hive, "T-001", priority="high", description=_OTHER)

    assert needle in str(excinfo.value)
    assert Path(task_file_path(hive, "T-001")).read_bytes() == before
    assert tasks.read_description(hive, "T-001") == _DESCRIPTION


@pytest.mark.parametrize("status", ["pending", "proposed", "deferred", "dropped"])
def test_edit_takes_a_task_not_yet_started(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    # Into another bucket, so at its end too (ADR 130).
    assert tasks.edit_task(hive, "T-001", priority="low")["changed"] == ["priority", "rank"]


def test_an_epic_is_editable_until_one_of_its_tasks_starts(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    tasks.accept(hive, epic["id"])
    assert tasks.edit_task(hive, epic["id"], priority="high")["changed"] == ["priority"]

    child = _read(hive, epic["tasks"][0])
    child.status = "in_progress"
    write_task_file(task_file_path(hive, epic["tasks"][0]), child)

    with pytest.raises(tasks.BoardWriteError, match=f"its task {epic['tasks'][0]} has started"):
        tasks.edit_task(hive, epic["id"], priority="low")


def test_edit_keeps_the_title_unique_within_its_scope(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")
    tasks.propose(hive, title="B", description=_DESCRIPTION, source="operator")
    epic = _epic(hive)

    with pytest.raises(tasks.BoardWriteError, match="T-001 already has the title 'A'"):
        tasks.edit_task(hive, "T-002", title="A")
    with pytest.raises(tasks.BoardWriteError, match=f"in epic {epic['id']}"):
        tasks.edit_task(hive, epic["tasks"][1], title="Part 1")
    # A title taken inside an epic is free outside it, and the reverse.
    assert tasks.edit_task(hive, "T-002", title="Part 1")["changed"] == ["title"]
    assert tasks.edit_task(hive, epic["tasks"][1], title="A")["changed"] == ["title"]


def test_edit_with_a_title_waits_for_the_create_lock_and_the_task_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    tasks.propose(hive, title="A", description=_DESCRIPTION, source="operator")
    _blocks_while_locked(hive, tasks.CREATE_LOCK, lambda: tasks.edit_task(hive, "T-001", title="B"))
    _blocks_while_locked(hive, "T-001", lambda: tasks.edit_task(hive, "T-001", priority="low"))
    task = _read(hive, "T-001")
    assert (task.title, task.priority) == ("B", "low")


def test_edit_depends_on_refuses_a_cycle_and_unknown_tasks(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    _task(hive, "T-002", status="proposed", depends_on=["T-001"])
    _task(hive, "T-003", status="proposed", depends_on=["T-002"])

    with pytest.raises(tasks.BoardWriteError, match="that is a cycle"):
        tasks.edit_task(hive, "T-001", depends_on=["T-003"])
    with pytest.raises(tasks.BoardWriteError, match="the task itself"):
        tasks.edit_task(hive, "T-001", depends_on=["T-001"])
    with pytest.raises(tasks.BoardWriteError, match="depends_on"):
        tasks.edit_task(hive, "T-001", depends_on=["T-404"])
    assert tasks.edit_task(hive, "T-003", depends_on=[])["changed"] == ["depends_on"]
    assert tasks.edit_task(hive, "T-001", depends_on=["T-003"])["changed"] == ["depends_on"]


def test_edit_depends_on_inside_an_epic(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-100", status="proposed")
    epic = _epic(hive, 3)
    s1, s2, s3 = epic["tasks"]

    with pytest.raises(tasks.BoardWriteError, match="an epic takes no depends_on"):
        tasks.edit_task(hive, epic["id"], depends_on=["T-100"])
    with pytest.raises(tasks.BoardWriteError, match="not a sibling"):
        tasks.edit_task(hive, s2, depends_on=["T-100"])
    with pytest.raises(tasks.BoardWriteError, match="that is a cycle"):
        tasks.edit_task(hive, s1, depends_on=[s3])
    with pytest.raises(tasks.BoardWriteError, match="more than once"):
        tasks.edit_task(hive, s3, depends_on=[s1, s1])
    assert tasks.edit_task(hive, s3, depends_on=[s1])["changed"] == ["depends_on"]


@pytest.mark.parametrize(
    "fields, needle",
    [
        ({"title": " "}, "the title is blank"),
        ({"description": "no sections"}, "the description"),
        ({"priority": "urgent"}, "priority is 'urgent'"),
    ],
)
def test_edit_validates_its_fields(tmp_path: Path, fields, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.edit_task(hive, "T-001", **fields)


# -- add_comment ---------------------------------------------------------------


def _comments(hive: str, task_id: str) -> list[dict]:
    return [json.loads(line) for line in Path(tasks.comments_path(hive, task_id)).read_text().splitlines()]


def test_comments_append_and_count(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="merged")  # any status: comments are kept for good

    first = tasks.add_comment(hive, "T-001", "operator", "Looks right.")
    second = tasks.add_comment(
        hive, "T-001", "arquitecto", "This line.", file="src/a.py", line=12, commit=_COMMIT[:7],
    )

    assert (first["comment_count"], second["comment_count"]) == (1, 2)
    assert first["comment"]["author"] == "operator"
    stored = _comments(hive, "T-001")
    assert stored == [first["comment"], second["comment"]]
    assert {k: stored[1][k] for k in ("file", "line", "commit")} == {
        "file": "src/a.py", "line": 12, "commit": _COMMIT[:7],
    }
    assert "file" not in stored[0]
    assert tasks.comments_path(hive, "T-001") == str(tmp_path / "hive" / "board" / "T-001" / "comments.jsonl")


def test_the_count_skips_lines_that_are_not_comments(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    path = Path(tasks.comments_path(hive, "T-001"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"at": "2026-10-18T10:00:00Z", "auth\n["a list"]\n\n')

    result = tasks.add_comment(hive, "T-001", "operator", "Looks right.")

    assert result["comment_count"] == 1


@pytest.mark.parametrize(
    "author, text, anchor, needle",
    [
        ("operator", " ", {}, "the text is blank"),
        ("operator", "x" * (tasks.MAX_COMMENT_CHARS + 1), {}, "longer than"),
        ("the operator", "hi", {}, "not a role"),
        ("", "hi", {}, "not a role"),
        ("operator", "hi", {"file": "a.py"}, "all three"),
        ("operator", "hi", {"file": "a.py", "line": 1}, "all three"),
        ("operator", "hi", {"file": "/etc/passwd", "line": 1, "commit": _COMMIT}, "not a path inside"),
        ("operator", "hi", {"file": "../x.py", "line": 1, "commit": _COMMIT}, "not a path inside"),
        ("operator", "hi", {"file": "a.py", "line": 0, "commit": _COMMIT}, "not a line number"),
        ("operator", "hi", {"file": "a.py", "line": True, "commit": _COMMIT}, "not a line number"),
        ("operator", "hi", {"file": "a.py", "line": 1, "commit": "main"}, "not a commit hash"),
    ],
)
def test_a_bad_comment_is_refused_and_nothing_is_written(tmp_path: Path, author, text, anchor, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.add_comment(hive, "T-001", author, text, **anchor)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


def test_a_comment_on_no_task_is_no_task_and_makes_no_directory(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    with pytest.raises(tasks.NoTaskError):
        tasks.add_comment(hive, "T-404", "operator", "hi")
    assert not (tmp_path / "hive" / "board" / "T-404").exists()


def test_a_comment_waits_for_the_task_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    _blocks_while_locked(hive, "T-001", lambda: tasks.add_comment(hive, "T-001", "operator", "hi"))
    assert len(_comments(hive, "T-001")) == 1


def test_concurrent_comments_never_interleave(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")
    text = "y" * 5000
    threads = [
        threading.Thread(target=tasks.add_comment, args=(hive, "T-001", "operator", f"{i}{text}"))
        for i in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(c["text"][0] for c in _comments(hive, "T-001")) == [str(i) for i in range(10)]


# -- record_verdict (review.md step 3) -----------------------------------------


@pytest.mark.parametrize("status", ["awaiting_review", "done", "conflict"])
def test_a_comment_verdict_is_a_comment_line_with_its_kind_and_commit(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    tasks.add_comment(hive, "T-001", "operator", "First.")

    result = tasks.record_verdict(hive, "T-001", "operator", "comment", "Read it all.", _COMMIT)

    assert result["comment_count"] == 2
    assert result["comment"]["verdict"] == {"kind": "comment", "commit": _COMMIT}
    assert (result["comment"]["author"], result["comment"]["text"]) == ("operator", "Read it all.")
    assert "file" not in result["comment"]
    assert _comments(hive, "T-001")[1] == result["comment"]
    assert _read(hive, "T-001").status == status  # a comment verdict moves nothing


@pytest.mark.parametrize(
    "status, merged, needle",
    [
        ("pending", None, "it is pending, not under review"),
        ("in_progress", None, "it is in_progress, not under review"),
        ("merged", None, "it is merged, not under review"),
        ("awaiting_review", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}, "it is merged, not under review"),
        ("done", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}, "it is merged, not under review"),
    ],
)
def test_a_verdict_on_a_task_not_under_review_is_refused(tmp_path: Path, status, merged, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status, merged=merged)
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.record_verdict(hive, "T-001", "operator", "comment", "x", _COMMIT)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


@pytest.mark.parametrize(
    "kind, text, commit, needle",
    [
        ("praise", "x", _COMMIT, "not one of comment, approve, request_changes"),
        ("comment", "x", "main", "not a commit hash"),
        ("comment", "x", None, "not a commit hash"),
        ("comment", " ", _COMMIT, "the text is blank"),
    ],
)
def test_a_bad_verdict_is_refused_and_nothing_is_written(tmp_path: Path, kind, text, commit, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="done")
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.record_verdict(hive, "T-001", "operator", kind, text, commit)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


@pytest.mark.parametrize("status", ["awaiting_review", "done"])
def test_an_approve_verdict_is_taken_on_a_finished_task_not_merged(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    result = tasks.record_verdict(hive, "T-001", "operator", "approve", "Ship it.", _COMMIT)
    assert result["comment"]["verdict"] == {"kind": "approve", "commit": _COMMIT}
    assert _comments(hive, "T-001") == [result["comment"]]
    assert _read(hive, "T-001").status == status


def test_an_approve_on_a_conflict_waits_on_resolve_conflict(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="conflict")
    with pytest.raises(tasks.BoardWriteError, match="waits on resolve conflict"):
        tasks.record_verdict(hive, "T-001", "operator", "approve", "x", _COMMIT)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


@pytest.mark.parametrize("status", ["awaiting_review", "done"])
def test_an_approve_on_a_merged_task_is_refused(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status, merged={"into": "main", "at": "2026-10-11T16:00:36+00:00"})
    with pytest.raises(tasks.BoardWriteError, match="it is merged"):
        tasks.record_verdict(hive, "T-001", "operator", "approve", "x", _COMMIT)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


@pytest.mark.parametrize("status", ["awaiting_review", "conflict"])
def test_a_request_for_changes_is_taken_on_a_task_under_review(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    result = tasks.record_verdict(hive, "T-001", "operator", "request_changes", "Rename it.", _COMMIT)
    assert result["comment"]["verdict"] == {"kind": "request_changes", "commit": _COMMIT}
    assert _comments(hive, "T-001") == [result["comment"]]
    assert _read(hive, "T-001").status == status  # the action moves it, not the line


@pytest.mark.parametrize(
    "status, merged, needle",
    [
        ("done", None, "it is done, not awaiting_review or conflict"),
        ("conflict", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}, "it is merged"),
        ("awaiting_review", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}, "it is merged"),
        ("done", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}, "it is merged"),
        ("merged", None, "it is merged"),
        ("pending", None, "it is pending, not awaiting_review"),
        ("in_progress", None, "it is in_progress, not awaiting_review"),
    ],
)
def test_a_request_for_changes_not_awaiting_review_is_refused(tmp_path: Path, status, merged, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status, **({"merged": merged} if merged else {}))
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.record_verdict(hive, "T-001", "operator", "request_changes", "x", _COMMIT)
    assert not os.path.exists(tasks.comments_path(hive, "T-001"))


# -- reopen_for_changes and review_comments (ADR 272) -------------------------


@pytest.mark.parametrize("status", ["awaiting_review", "conflict"])
def test_reopen_for_changes_moves_a_task_under_review_to_pending(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    assert tasks.reopen_for_changes(hive, "T-001") is True
    assert _read(hive, "T-001").status == "pending"


@pytest.mark.parametrize(
    "status, merged",
    [("done", None), ("in_progress", None), ("pending", None),
     ("awaiting_review", {"into": "main", "at": "2026-10-11T16:00:36+00:00"}),
     ("conflict", {"into": "main", "at": "2026-10-11T16:00:36+00:00"})],
)
def test_reopen_for_changes_leaves_any_other_task_alone(tmp_path: Path, status, merged) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status, **({"merged": merged} if merged else {}))
    assert tasks.reopen_for_changes(hive, "T-001") is False
    assert _read(hive, "T-001").status == status


def test_review_comments_are_the_authors_since_the_last_closing_verdict(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="awaiting_review")
    old = "f" * 40
    tasks.add_comment(hive, "T-001", "operator", "Before the first round.")
    tasks.record_verdict(hive, "T-001", "operator", "request_changes", "First round.", old)
    tasks.add_comment(hive, "T-001", "operator", "This name.", file="src/a.py", line=3, commit=_COMMIT)
    tasks.add_comment(hive, "T-001", "arquitecto", "Not the person's.")
    tasks.add_comment(hive, "T-001", "operator", "And a test.")
    tasks.record_verdict(hive, "T-001", "operator", "request_changes", "Second round.", _COMMIT)

    got = tasks.review_comments(hive, "T-001", _COMMIT)
    assert [c["text"] for c in got] == ["This name.", "And a test.", "Second round."]
    assert (got[0]["file"], got[0]["line"]) == ("src/a.py", 3)
    first = tasks.review_comments(hive, "T-001", old)
    assert [c["text"] for c in first] == ["Before the first round.", "First round."]


def test_review_comments_without_the_verdict_are_empty(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="awaiting_review")
    assert tasks.review_comments(hive, "T-001", _COMMIT) == []  # no comments file
    tasks.add_comment(hive, "T-001", "operator", "Just a note.")
    tasks.record_verdict(hive, "T-001", "operator", "comment", "A comment verdict.", _COMMIT)
    assert tasks.review_comments(hive, "T-001", _COMMIT) == []


def test_a_verdict_on_no_task_is_no_task(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    with pytest.raises(tasks.NoTaskError):
        tasks.record_verdict(hive, "T-404", "operator", "comment", "x", _COMMIT)
    assert not (tmp_path / "hive" / "board" / "T-404").exists()


def test_a_verdict_waits_for_the_task_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="done")
    _blocks_while_locked(
        hive, "T-001", lambda: tasks.record_verdict(hive, "T-001", "operator", "comment", "x", _COMMIT)
    )
    assert len(_comments(hive, "T-001")) == 1


# -- accept --------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, to", [("proposed", "pending"), ("deferred", "pending"), ("proposed", "deferred"),
                  ("proposed", "dropped"), ("deferred", "dropped")],
)
def test_accept_moves_a_backlog_item(tmp_path: Path, start, to) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=start, priority="low")

    result = tasks.accept(hive, "T-001", to=to, priority="high", **(DROP if to == "dropped" else {}))

    assert result == {"id": "T-001", "status": to, "changed": True, "children": {}}
    assert (_read(hive, "T-001").status, _read(hive, "T-001").priority) == (to, "high")


def test_accept_to_the_status_it_has_is_a_no_op(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="pending", priority="low")
    before = Path(task_file_path(hive, "T-001")).read_bytes()

    assert tasks.accept(hive, "T-001", priority="low")["changed"] is False
    with pytest.raises(tasks.BoardWriteError, match="change its priority with task_edit"):
        tasks.accept(hive, "T-001", priority="high")
    assert Path(task_file_path(hive, "T-001")).read_bytes() == before


@pytest.mark.parametrize("status", ["in_progress", "review", "blocked", "merged", "done", "epic"])
def test_accept_never_moves_a_task_on_the_board(tmp_path: Path, status) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status=status)
    for to in ("pending", "deferred", "dropped"):
        if to == status:
            continue
        with pytest.raises(tasks.BoardWriteError, match="on the board"):
            tasks.accept(hive, "T-001", to=to, **(DROP if to == "dropped" else {}))
    assert _read(hive, "T-001").status == status


def test_pending_cannot_go_back_to_the_backlog(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="pending")
    with pytest.raises(tasks.BoardWriteError, match="on the board"):
        tasks.accept(hive, "T-001", to="deferred")


def test_dropped_is_final(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="dropped")
    with pytest.raises(tasks.BoardWriteError, match="dropped, which is final"):
        tasks.accept(hive, "T-001")
    assert tasks.accept(hive, "T-001", to="dropped", **DROP)["changed"] is False


@pytest.mark.parametrize("fields, needle", [({"to": "merged"}, "to is 'merged'"),
                                            ({"priority": "urgent"}, "priority is 'urgent'")])
def test_accept_validates_its_arguments(tmp_path: Path, fields, needle) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    with pytest.raises(tasks.BoardWriteError, match=needle):
        tasks.accept(hive, "T-001", **fields)


def test_accept_a_missing_task_is_no_task(tmp_path: Path) -> None:
    with pytest.raises(tasks.NoTaskError):
        tasks.accept(_hive(tmp_path), "T-404")


def test_accept_an_epic_moves_its_proposed_tasks_and_keeps_a_deferred_one(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive, 3)
    s1, s2, s3 = epic["tasks"]
    tasks.accept(hive, s3, to="deferred")

    result = tasks.accept(hive, epic["id"], priority="high")

    assert result == {
        "id": epic["id"], "status": "epic", "changed": True,
        "children": {s1: "pending", s2: "pending", s3: "deferred"},
    }
    assert _read(hive, epic["id"]).status == "epic"
    assert [_read(hive, c).priority for c in (s1, s2, s3)] == ["high", "high", None]
    # Again: a no-op, the epic counting as pending.
    assert tasks.accept(hive, epic["id"])["changed"] is False
    # Its deferred task is accepted on its own now that its epic is.
    assert tasks.accept(hive, s3)["status"] == "pending"


def test_a_task_of_an_epic_waits_for_its_epic(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    with pytest.raises(tasks.BoardWriteError, match="accept the epic"):
        tasks.accept(hive, epic["tasks"][0])
    # Deferring or dropping it alone is fine.
    assert tasks.accept(hive, epic["tasks"][0], to="dropped", **DROP)["status"] == "dropped"


def test_dropping_the_last_open_task_of_an_epic_closes_it(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive, 3)
    s1, s2, s3 = epic["tasks"]
    tasks.accept(hive, s3, to="deferred")
    tasks.accept(hive, epic["id"])
    tasks.mark_merged(hive, s1, "main")

    # A deferred task holds its epic open, and so does a pending one.
    assert tasks.close_epic(hive, s1) is None
    assert tasks.accept(hive, s3, to="dropped", **DROP) == {
        "id": s3, "status": "dropped", "changed": True, "children": {},
    }
    assert _read(hive, epic["id"]).status == "epic"

    tasks.mark_merged(hive, s2, "main")
    assert tasks.close_epic(hive, s2) == epic["id"]
    assert _read(hive, epic["id"]).status == "done"


def test_dropping_every_task_of_an_accepted_epic_drops_it(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    s1, s2 = epic["tasks"]
    for child in (s1, s2):
        tasks.accept(hive, child, to="deferred")
    tasks.accept(hive, epic["id"])
    tasks.accept(hive, s1, to="dropped", **DROP)

    result = tasks.accept(hive, s2, to="dropped", **DROP)

    assert result == {
        "id": s2, "status": "dropped", "changed": True, "children": {}, "epic_closed": epic["id"],
    }
    assert _read(hive, epic["id"]).status == "dropped"


def test_a_retried_drop_closes_the_epic_a_crash_left_open(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    s1, s2 = epic["tasks"]
    tasks.accept(hive, s2, to="deferred")
    tasks.accept(hive, epic["id"])
    tasks.mark_merged(hive, s1, "main")
    # The drop was written and the close never ran.
    path = task_file_path(hive, s2)
    task = read_task_file(path)
    task.status = "dropped"
    write_task_file(path, task)

    result = tasks.accept(hive, s2, to="dropped", **DROP)

    assert result["changed"] is False and result["epic_closed"] == epic["id"]
    assert _read(hive, epic["id"]).status == "done"
    assert "epic_closed" not in tasks.accept(hive, s2, to="dropped", **DROP)


def test_a_dropped_task_closes_its_epic_holding_no_childs_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    s1, s2 = epic["tasks"]
    tasks.accept(hive, s2, to="deferred")
    tasks.accept(hive, epic["id"])
    tasks.mark_merged(hive, s1, "main")
    close_epic = tasks.close_epic
    free: list[bool] = []

    def close_checking_locks(hive_dir: str, task_id: str):
        # Another thread can take the child's lock and the create lock: the
        # close holds neither, so it takes the parent's in the order ADR 129 sets.
        def take() -> None:
            with tasks.task_lock(hive_dir, tasks.CREATE_LOCK), tasks.task_lock(hive_dir, task_id):
                free.append(True)

        thread = threading.Thread(target=take)
        thread.start()
        thread.join(timeout=5)
        return close_epic(hive_dir, task_id)

    monkeypatch.setattr(tasks, "close_epic", close_checking_locks)

    assert tasks.accept(hive, s2, to="dropped", priority="low", **DROP)["epic_closed"] == epic["id"]
    assert free == [True]


def test_dropping_an_epic_drops_its_backlog_tasks(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive, 3)
    s1, s2, s3 = epic["tasks"]
    tasks.accept(hive, s2, to="deferred")

    result = tasks.accept(hive, epic["id"], to="dropped", **DROP)

    assert result["children"] == {s1: "dropped", s2: "dropped", s3: "dropped"}
    with pytest.raises(tasks.BoardWriteError, match="final"):
        tasks.accept(hive, epic["id"])


@pytest.mark.parametrize("reason", [None, "", "   \n"])
def test_a_drop_without_a_reason_is_refused(tmp_path: Path, reason) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    before = Path(task_file_path(hive, "T-001")).read_bytes()
    with pytest.raises(tasks.BoardWriteError, match="a drop needs a reason"):
        tasks.accept(hive, "T-001", to="dropped", reason=reason, by="operator")
    assert Path(task_file_path(hive, "T-001")).read_bytes() == before


@pytest.mark.parametrize("to", ["pending", "deferred"])
def test_a_reason_with_another_move_is_refused(tmp_path: Path, to) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    with pytest.raises(tasks.BoardWriteError, match="a reason is only for a drop"):
        tasks.accept(hive, "T-001", to=to, reason="why")
    assert _read(hive, "T-001").status == "proposed"


def test_a_drop_records_who_and_why(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    tasks.accept(hive, "T-001", to="dropped", **DROP)
    task = _read(hive, "T-001")
    assert (task.status, task.dropped_reason, task.dropped_by) == (
        "dropped", "no longer needed", "operator",
    )
    assert tasks.field_problems(task) == []


def test_an_epic_drop_records_it_on_each_child(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive, 3)
    s1, s2, s3 = epic["tasks"]
    tasks.accept(hive, s2, to="deferred")
    tasks.accept(hive, epic["id"], to="dropped", **DROP)
    for task_id in (epic["id"], s1, s2, s3):
        task = _read(hive, task_id)
        assert (task.status, task.dropped_reason, task.dropped_by) == (
            "dropped", "no longer needed", "operator",
        )


def test_a_retried_drop_keeps_the_first_reason(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed")
    tasks.accept(hive, "T-001", to="dropped", **DROP)
    result = tasks.accept(hive, "T-001", to="dropped", reason="other", by="reviewer")
    assert result["changed"] is False
    assert _read(hive, "T-001").dropped_reason == "no longer needed"
    assert _read(hive, "T-001").dropped_by == "operator"


def test_mark_dropped_refuses_a_board_status(tmp_path: Path) -> None:
    task = TaskFile(task_id="T-001", status="in_progress", owner=None, depends_on=[],
                    heartbeat=None, body="")
    with pytest.raises(tasks.BoardWriteError, match="in_progress"):
        tasks.mark_dropped(task, "why", "operator")
    assert task.dropped_reason is None


# --- close_resolved_debt: ADR 369 ---------------------------------------------

def _resolver(hive: str, ids=("T-050-D1",)) -> None:
    _task(hive, "T-100", status="done", resolved_debt=list(ids))


def test_close_resolved_debt_drops_proposed_and_deferred_cards(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _resolver(hive)
    _task(hive, "T-001", status="proposed", debt_id="T-050-D1")
    _task(hive, "T-002", status="deferred", debt_id="T-050-D1")
    _task(hive, "T-003", status="proposed", debt_id="T-050-D2")

    closed, warnings = tasks.close_resolved_debt(hive, "T-100")

    assert warnings == []
    assert closed == [
        "debt T-050-D1: card T-001 dropped, resolved by T-100",
        "debt T-050-D1: card T-002 dropped, resolved by T-100",
    ]
    for card in ("T-001", "T-002"):
        task = _read(hive, card)
        assert (task.status, task.dropped_reason, task.dropped_by) == (
            "dropped", "resolved by T-100", "dispatcher",
        )
    assert _read(hive, "T-003").status == "proposed"


def test_close_resolved_debt_skips_a_dropped_card_and_keeps_its_reason(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _resolver(hive)
    _task(hive, "T-001", status="proposed", debt_id="T-050-D1")
    tasks.accept(hive, "T-001", to="dropped", **DROP)

    assert tasks.close_resolved_debt(hive, "T-100") == ([], [])
    assert _read(hive, "T-001").dropped_reason == "no longer needed"


def test_close_resolved_debt_leaves_a_card_on_the_board_and_warns(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _resolver(hive)
    _task(hive, "T-001", status="pending", debt_id="T-050-D1")

    closed, warnings = tasks.close_resolved_debt(hive, "T-100")

    assert closed == []
    assert warnings == [
        "debt T-050-D1: T-100 resolved it, but its card T-001 is pending; left as it is"
    ]
    assert _read(hive, "T-001").status == "pending"


def test_close_resolved_debt_skips_the_merging_tasks_own_card(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-100", status="done", debt_id="T-050-D1", resolved_debt=["T-050-D1"])

    assert tasks.close_resolved_debt(hive, "T-100") == ([], [])
    assert _read(hive, "T-100").status == "done"


def test_close_resolved_debt_with_nothing_resolved_or_no_file_does_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", status="proposed", debt_id="T-050-D1")
    _task(hive, "T-100", status="done")

    assert tasks.close_resolved_debt(hive, "T-100") == ([], [])
    assert tasks.close_resolved_debt(hive, "T-999") == ([], [])
    assert _read(hive, "T-001").status == "proposed"


def test_close_resolved_debt_closes_the_epic_its_card_was_holding_open(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    s1, s2 = epic["tasks"]
    tasks.accept(hive, epic["id"])
    done = _read(hive, s1)
    done.status = "done"
    done.merged = {"into": "main", "at": "2026-10-23T00:00:00+00:00"}
    write_task_file(task_file_path(hive, s1), done)
    held = _read(hive, s2)
    held.status, held.debt_id = "deferred", "T-050-D1"
    write_task_file(task_file_path(hive, s2), held)
    _resolver(hive)

    closed, warnings = tasks.close_resolved_debt(hive, "T-100")

    assert warnings == []
    assert closed == [
        f"debt T-050-D1: card {s2} dropped, resolved by T-100",
        f"epic {epic['id']}: every sub-task is merged or dropped, so it is done",
    ]
    assert _read(hive, epic["id"]).status == "done"


def test_close_resolved_debt_reports_an_unreadable_card_and_goes_on(tmp_path: Path, monkeypatch) -> None:
    hive = _hive(tmp_path)
    _resolver(hive)
    _task(hive, "T-001", status="proposed", debt_id="T-050-D1")
    _task(hive, "T-002", status="proposed", debt_id="T-050-D1")
    real = tasks.mark_dropped

    def flaky(task, reason, by):
        if task.task_id == "T-001":
            raise RuntimeError("boom")
        real(task, reason, by)

    monkeypatch.setattr(tasks, "mark_dropped", flaky)

    closed, warnings = tasks.close_resolved_debt(hive, "T-100")

    assert warnings == ["debt T-050-D1: card T-001 could not be closed: boom"]
    assert closed == ["debt T-050-D1: card T-002 dropped, resolved by T-100"]
    assert _read(hive, "T-001").status == "proposed"


def test_close_resolved_debt_never_raises_when_the_cards_cannot_be_read(tmp_path: Path, monkeypatch) -> None:
    hive = _hive(tmp_path)
    _resolver(hive)

    def broken(_hive):
        raise OSError("disk gone")

    monkeypatch.setattr(tasks, "read_all_tasks", broken)

    closed, warnings = tasks.close_resolved_debt(hive, "T-100")

    assert closed == []
    assert len(warnings) == 1 and "disk gone" in warnings[0]


def test_field_problems_names_a_bad_dropped_value() -> None:
    task = TaskFile(task_id="T-001", status="dropped", owner=None, depends_on=[],
                    heartbeat=None, body="", dropped_reason=" ", dropped_by=3)
    problems = tasks.field_problems(task)
    assert any("dropped_reason" in p for p in problems)
    assert any("dropped_by" in p for p in problems)


def test_deferring_an_epic_leaves_its_tasks(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    result = tasks.accept(hive, epic["id"], to="deferred")
    assert result["status"] == "deferred"
    assert set(result["children"].values()) == {"proposed"}


def test_accept_an_epic_with_a_missing_task_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    epic = _epic(hive)
    os.remove(task_file_path(hive, epic["tasks"][1]))
    before = Path(task_file_path(hive, epic["tasks"][0])).read_bytes()

    with pytest.raises(tasks.BoardWriteError, match="propose the epic again"):
        tasks.accept(hive, epic["id"])

    assert _read(hive, epic["id"]).status == "proposed"
    assert Path(task_file_path(hive, epic["tasks"][0])).read_bytes() == before


def test_accept_waits_for_the_task_lock_and_an_epic_for_each_childs(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-100", status="proposed")
    _blocks_while_locked(hive, "T-100", lambda: tasks.accept(hive, "T-100"))
    epic = _epic(hive)
    _blocks_while_locked(hive, epic["tasks"][1], lambda: tasks.accept(hive, epic["id"]))
    assert _read(hive, epic["id"]).status == "epic"


# -- unmigrated files ----------------------------------------------------------


_UNMIGRATED = {
    "edit_task": lambda hive: tasks.edit_task(hive, "task-1", priority="low"),
    "add_comment": lambda hive: tasks.add_comment(hive, "task-1", "operator", "hi"),
    "accept": lambda hive: tasks.accept(hive, "task-1"),
    "record_verdict": lambda hive: tasks.record_verdict(hive, "task-1", "operator", "comment", "hi", _COMMIT),
}


@pytest.mark.parametrize("writer", sorted(_UNMIGRATED))
def test_the_board_writers_refuse_an_unmigrated_file(tmp_path: Path, writer: str) -> None:
    hive = _hive(tmp_path)
    legacy = Path(task_file_path(hive, "task-1"))
    legacy.write_text(
        "---\ntask_id: task-1\nstatus: proposed\nowner: null\ndepends_on: []\nheartbeat: null\n"
        "description: Old style.\n---\n\n"
    )
    before = legacy.read_bytes()

    with pytest.raises(tasks.UnmigratedTaskError):
        _UNMIGRATED[writer](hive)

    assert legacy.read_bytes() == before
    assert not (tmp_path / "hive" / "board").exists()
