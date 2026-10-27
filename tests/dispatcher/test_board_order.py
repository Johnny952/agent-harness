"""Tags, the rank inside a priority bucket, and the Ready order (T-085, ADR 130)."""

import threading
from pathlib import Path

import pytest

from dispatcher import tasks
from dispatcher.context_transfer import TaskFile
from tests.dispatcher.test_board_writes import (
    _DESCRIPTION,
    _OTHER,
    _blocks_while_locked,
    _epic,
    _hive,
    _problems,
    _read,
    _task,
)

STEP = tasks.RANK_STEP


def _propose(hive: str, title: str, **arguments) -> dict:
    return tasks.propose(hive, title=title, description=_DESCRIPTION, source="operator", **arguments)


def _tf(task_id: str, status: str = "pending", depends_on=(), **fields) -> TaskFile:
    return TaskFile(
        task_id=task_id, status=status, owner=None, depends_on=list(depends_on),
        heartbeat=None, body="", **fields,
    )


def _graph(*files: TaskFile) -> dict:
    return {f.task_id: f for f in files}


# --- tags -------------------------------------------------------------------


def test_tags_are_trimmed_and_kept_once_in_order(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A", tags=[" debt ", "api", "debt", "API"])
    assert _read(hive, "T-001").tags == ["debt", "api", "API"]


@pytest.mark.parametrize(
    "tags, problem",
    [
        ("debt", "a list"),
        ([""], "blank"),
        (["  "], "blank"),
        (["a\nb"], "line"),
        ([3], "string"),
        (["x" * (tasks.MAX_TAG_CHARS + 1)], str(tasks.MAX_TAG_CHARS)),
        ([f"t{i}" for i in range(tasks.MAX_TAGS + 1)], str(tasks.MAX_TAGS)),
    ],
)
def test_a_bad_tag_is_refused_and_nothing_is_written(tmp_path: Path, tags, problem) -> None:
    hive = _hive(tmp_path)
    with pytest.raises(tasks.BoardWriteError) as excinfo:
        _propose(hive, "A", tags=tags)
    assert problem in _problems(excinfo)
    assert tasks.read_all_tasks(hive) == {}


def test_a_bad_tag_on_disk_is_a_field_problem(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", tags=["ok", ""])
    _task(hive, "T-002", tags="debt")
    _task(hive, "T-003", tags=["ok"])
    assert any("tag" in p for p in tasks.field_problems(_read(hive, "T-001")))
    assert any("tag" in p for p in tasks.field_problems(_read(hive, "T-002")))
    assert tasks.field_problems(_read(hive, "T-003")) == []
    assert tasks.normal_tags(["ok", "", " ok "]) == ["ok"]
    assert tasks.normal_tags("debt") == []


def test_edit_sets_and_clears_tags(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A", tags=["api"])
    assert tasks.edit_task(hive, "T-001", tags=["api", " api"])["changed"] == []
    assert tasks.edit_task(hive, "T-001", tags=["debt"])["changed"] == ["tags"]
    assert _read(hive, "T-001").tags == ["debt"]
    assert tasks.edit_task(hive, "T-001", tags=[])["changed"] == ["tags"]
    assert _read(hive, "T-001").tags is None


def test_an_epic_s_tasks_take_its_tags(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    out = _propose(hive, "E", tags=["api"], tasks=[
        {"title": "P1", "description": _DESCRIPTION},
        {"title": "P2", "description": _OTHER},
    ])
    assert [_read(hive, i).tags for i in [out["id"], *out["tasks"]]] == [["api"]] * 3


# --- rank -------------------------------------------------------------------


def test_a_proposal_goes_to_the_end_of_its_bucket(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    _propose(hive, "B", priority="high")
    _propose(hive, "C")
    _propose(hive, "D", priority="high")
    assert [_read(hive, f"T-00{n}").rank for n in range(1, 5)] == [STEP, STEP, 2 * STEP, 2 * STEP]


def test_the_end_counts_the_board_and_legacy_tasks(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001")  # filed before the field: rank 0
    _task(hive, "T-002", rank=5000)  # on the board
    _propose(hive, "A")
    assert _read(hive, "T-003").rank == 5000 + STEP


def test_an_epic_s_tasks_follow_it_in_its_bucket(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    out = _epic(hive, 3)
    ranks = [_read(hive, i).rank for i in [out["id"], *out["tasks"]]]
    assert ranks == [STEP, 2 * STEP, 3 * STEP, 4 * STEP]
    _propose(hive, "After")
    assert _read(hive, "T-002").rank == 5 * STEP


def test_edit_reorders_inside_the_bucket(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    for title in "ABC":
        _propose(hive, title)
    # C between A and B: one write, nothing else renumbered.
    assert tasks.edit_task(hive, "T-003", rank=STEP + STEP // 2)["changed"] == ["rank"]
    assert [_read(hive, f"T-00{n}").rank for n in (1, 2, 3)] == [STEP, 2 * STEP, STEP + STEP // 2]
    assert tasks.edit_task(hive, "T-003", rank=STEP + STEP // 2)["changed"] == []


def test_a_new_bucket_puts_the_task_at_its_end_unless_a_rank_is_given(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    _propose(hive, "B")
    _propose(hive, "C")
    _propose(hive, "H", priority="high")
    assert tasks.edit_task(hive, "T-003", priority="high")["changed"] == ["priority", "rank"]
    assert _read(hive, "T-003").rank == 2 * STEP
    assert tasks.edit_task(hive, "T-002", priority="high", rank=1)["changed"] == ["priority", "rank"]
    assert _read(hive, "T-002").rank == 1


def test_the_only_task_moving_buckets_is_not_its_own_end(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    tasks.edit_task(hive, "T-001", priority="low")
    assert _read(hive, "T-001").rank == STEP


@pytest.mark.parametrize("rank", [-1, tasks.MAX_RANK + 1, True, 1.5, "3"])
def test_a_bad_rank_is_refused(tmp_path: Path, rank) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.edit_task(hive, "T-001", rank=rank)
    assert "rank" in _problems(excinfo)
    assert _read(hive, "T-001").rank == STEP


def test_a_bad_rank_on_disk_is_a_field_problem_and_sorts_as_zero(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-001", rank="first")
    task = _read(hive, "T-001")
    assert any("rank" in p for p in tasks.field_problems(task))
    assert tasks.rank_of(task) == 0


def test_accept_with_a_new_bucket_moves_to_its_end(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A", priority="high")
    _propose(hive, "B")
    tasks.accept(hive, "T-002", priority="high")
    assert (_read(hive, "T-002").priority, _read(hive, "T-002").rank) == ("high", 2 * STEP)
    _propose(hive, "C", priority="high")
    tasks.accept(hive, "T-003", priority="high")  # same bucket: keeps its place
    assert _read(hive, "T-003").rank == 3 * STEP


def test_accepting_an_epic_into_a_bucket_keeps_its_tasks_in_order(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "H", priority="high")
    out = _epic(hive, 2)
    tasks.accept(hive, out["id"], priority="high")
    ids = [out["id"], *out["tasks"]]
    assert [_read(hive, i).priority for i in ids] == ["high"] * 3
    assert [_read(hive, i).rank for i in ids] == [2 * STEP, 3 * STEP, 4 * STEP]


def test_edit_with_a_rank_waits_for_the_create_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    _blocks_while_locked(hive, tasks.CREATE_LOCK, lambda: tasks.edit_task(hive, "T-001", rank=7))


def test_concurrent_proposals_take_distinct_ends(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    def run(i: int) -> None:
        _propose(hive, f"Task {i}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    ranks = sorted(t.rank for t in tasks.read_all_tasks(hive).values())
    assert ranks == [STEP * n for n in range(1, 7)]


def test_accepted_drafts_are_ranked_at_the_end_of_normal(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _propose(hive, "A")
    draft_id = tasks.new_draft_id()
    text = _DESCRIPTION + "\n## Surface\n\n`api/`\n\n## Out of scope\n\nThe rest.\n"
    for name in ("1", "2"):
        tasks.write_draft(hive, draft_id, tasks.Draft(name=name, description=text, depends_on=[]))
    accepted = tasks.accept_drafts(hive, draft_id)
    assert sorted(_read(hive, i).rank for i in accepted.values()) == [2 * STEP, 3 * STEP]


# --- the Ready order ----------------------------------------------------------


def test_a_dependency_takes_the_bucket_of_what_waits_on_it_through_a_chain() -> None:
    graph = _graph(
        _tf("T-1", priority="low"),
        _tf("T-2", depends_on=["T-1"], priority="low"),
        _tf("T-3", depends_on=["T-2"], priority="high"),
        _tf("T-4", priority="normal"),
    )
    assert tasks.effective_buckets(graph) == {
        "T-1": "high", "T-2": "high", "T-3": "high", "T-4": "normal",
    }


def test_a_diamond_lifts_the_shared_root_once_to_the_highest() -> None:
    graph = _graph(
        _tf("T-1", priority="low"),
        _tf("T-2", depends_on=["T-1"], priority="low"),
        _tf("T-3", depends_on=["T-1"], priority="normal"),
        _tf("T-4", depends_on=["T-2", "T-3"], priority="high"),
        _tf("T-5", depends_on=["T-1"], priority="low"),
    )
    buckets = tasks.effective_buckets(graph)
    assert buckets == {"T-1": "high", "T-2": "high", "T-3": "high", "T-4": "high", "T-5": "low"}


def test_an_epic_s_tasks_take_its_bucket_and_lift_their_dependencies() -> None:
    graph = _graph(
        _tf("T-1", priority="low"),
        _tf("T-9", status="epic", priority="high", split_into=["T-9-S1", "T-9-S2"]),
        _tf("T-9-S1", epic="T-9", priority="low", depends_on=["T-1"]),
        _tf("T-9-S2", epic="T-9", priority="high"),
        _tf("T-8", status="epic", priority="low"),
        _tf("T-8-S1", epic="T-8", priority="high"),
    )
    buckets = tasks.effective_buckets(graph)
    assert buckets["T-9-S1"] == buckets["T-1"] == "high"
    assert buckets["T-8-S1"] == "high"  # raised only, never lowered
    assert buckets["T-8"] == "low"


def test_a_dependency_cycle_ends_and_takes_its_highest_bucket() -> None:
    graph = _graph(
        _tf("T-1", depends_on=["T-3"], priority="low"),
        _tf("T-2", depends_on=["T-1"], priority="low"),
        _tf("T-3", depends_on=["T-2"], priority="high"),
        _tf("T-4", depends_on=["T-4"], priority="low"),
    )
    assert tasks.effective_buckets(graph) == {
        "T-1": "high", "T-2": "high", "T-3": "high", "T-4": "low",
    }
    assert tasks.ready_order(graph) == []


def test_unknown_ids_and_bad_fields_are_ignored() -> None:
    graph = _graph(
        _tf("T-1", depends_on=["T-404"], priority="high"),
        _tf("T-2", priority="urgent", epic="T-404"),
    )
    assert tasks.effective_buckets(graph) == {"T-1": "high", "T-2": "normal"}
    assert tasks.ready_order(graph) == ["T-2"]  # T-1 waits on a task nobody has


def test_ready_is_pending_with_every_dependency_merged() -> None:
    merged = {"target": "main"}
    graph = _graph(
        _tf("T-1", status="done", merged=merged),
        _tf("T-2", depends_on=["T-1"]),
        _tf("T-3", status="done"),
        _tf("T-4", depends_on=["T-3"]),
        _tf("T-5", status="proposed"),
        _tf("T-6", status="in_progress"),
        _tf("T-7", status="pending", merged=merged),
        _tf("T-8", status="epic"),
    )
    assert tasks.ready_order(graph) == ["T-2"]


def test_a_split_parent_is_met_once_every_sub_task_is_merged() -> None:
    merged = {"target": "main"}

    def graph(first: dict, second_status: str, second: dict, parent_status: str) -> dict:
        return _graph(
            _tf("T-9", status=parent_status, split_into=["T-9-S1", "T-9-S2"]),
            _tf("T-9-S1", status="done", epic="T-9", **first),
            _tf("T-9-S2", status=second_status, epic="T-9", **second),
            _tf("T-5", depends_on=["T-9"]),
        )

    assert "T-5" not in tasks.ready_order(graph({"merged": merged}, "pending", {}, "epic"))
    assert "T-5" in tasks.ready_order(
        graph({"merged": merged}, "done", {"merged": merged}, "done")
    )
    assert "T-5" not in tasks.ready_order(
        graph({"merged": merged}, "dropped", {}, "epic")
    )


def test_ready_is_ordered_by_effective_bucket_then_rank_then_id() -> None:
    graph = _graph(
        _tf("T-1", priority="low", rank=1),
        _tf("T-2", priority="normal", rank=3 * STEP),
        _tf("T-3", priority="normal", rank=STEP),
        _tf("T-10", priority="normal", rank=STEP),
        _tf("T-4", priority="normal"),  # legacy: rank 0
        _tf("T-5", priority="high", status="in_progress", depends_on=["T-1"]),
    )
    # T-1 is lifted to high by T-5, which waits on it.
    assert tasks.ready_order(graph) == ["T-1", "T-4", "T-3", "T-10", "T-2"]


@pytest.mark.parametrize("status", tasks.BACKLOG_STATUSES)
def test_a_backlog_item_lifts_nothing(status) -> None:
    graph = _graph(
        _tf("T-1", priority="low"),
        _tf("T-2", status=status, priority="high", depends_on=["T-1"]),
        _tf("T-9", status=status, priority="high"),
        _tf("T-9-S1", epic="T-9", priority="low"),
    )
    buckets = tasks.effective_buckets(graph)
    assert (buckets["T-1"], buckets["T-9-S1"]) == ("low", "low")
