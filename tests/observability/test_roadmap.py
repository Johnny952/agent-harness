"""`GET /api/roadmap`, against a temporary projects root and hive.

The mounts and the client are `test_api.py`'s `_harness`; plans are written
as files under the checkout's `docs/plans/`, the way T-071 wrote them, and
tasks through the dispatcher's own writer where the `TaskFile` carries the
field, and as raw frontmatter where it does not (`plan`, `project`).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dispatcher import context_transfer
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness, _task


def _plan(
    harness,
    name: str,
    status: str = "idea",
    approved: str | None = None,
    built: str | None = None,
    supersedes: tuple[str, ...] = (),
    project: str = "ia-harness",
    title: str | None = None,
) -> None:
    plans = Path(harness.projects_root) / project / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    block = (
        f"---\nstatus: {status}\napproved: {approved or 'null'}\n"
        f"built: {built or 'null'}\nsupersedes: [{', '.join(supersedes)}]\n---\n\n"
    )
    (plans / name).write_text(block + f"# {title or name}\n\nBody.\n")


def _raw_plan(harness, name: str, content: str | bytes, project: str = "ia-harness") -> Path:
    plans = Path(harness.projects_root) / project / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    path = plans / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


def _raw_task(tasks_dir, task_id: str, **front) -> None:
    """A task file with keys `TaskFile` has no field for."""
    front = {"task_id": task_id, "status": "in_progress", **front}
    path = context_transfer.task_file_path(str(tasks_dir), task_id)
    Path(path).write_text(f"---\n{yaml.safe_dump(front)}---\n\n")


def _merged(at: str | None) -> dict:
    return {"into": "main", "at": at} if at is not None else {"into": "main"}


def _data(harness, path: str = "/api/roadmap"):
    resp = _get(harness, path)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    return body["data"], body["warnings"]


def _files(rows: list[dict]) -> list[str]:
    return [row["file"] for row in rows]


def test_plans_land_in_their_band(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    for name, status in [
        ("idea.md", "idea"),
        ("design.md", "design"),
        ("approved.md", "approved"),
        ("building.md", "building"),
        ("superseded.md", "superseded"),
        ("dropped.md", "dropped"),
    ]:
        _plan(harness, name, status=status)
    _plan(harness, "built.md", status="built", built="2026-10-12")

    data, warnings = _data(harness)

    assert warnings == []
    assert data["project"] == "ia-harness"
    bands = data["bands"]
    assert sorted(_files(bands["future"])) == ["approved.md", "design.md", "idea.md"]
    assert _files(bands["now"]) == ["building.md"]
    assert sorted(_files(bands["past"])) == ["built.md", "dropped.md", "superseded.md"]
    assert data["no_plan"] == {"now": [], "past": []}


def test_a_plan_row_carries_its_frontmatter_and_title(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "old.md", status="superseded")
    _plan(
        harness,
        "new.md",
        status="built",
        approved="2026-10-11",
        built="2026-10-12",
        supersedes=("old.md",),
        title="New — the replacement",
    )

    data, _ = _data(harness)

    assert data["bands"]["past"][0] == {
        "file": "new.md",
        "title": "New — the replacement",
        "status": "built",
        "approved": "2026-10-11",
        "built": "2026-10-12",
        "supersedes": ["old.md"],
        "superseded_by": [],
        "tasks": [],
    }


def test_superseded_by_is_computed_from_the_other_plans(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "board.md", status="superseded")
    _plan(harness, "board-service.md", status="building", supersedes=("board.md",))
    _plan(harness, "other.md", status="design", supersedes=("board.md",))

    data, _ = _data(harness)

    board = next(row for row in data["bands"]["past"] if row["file"] == "board.md")
    assert board["superseded_by"] == ["board-service.md", "other.md"]
    assert data["bands"]["now"][0]["superseded_by"] == []


def test_a_band_is_newest_first_by_built_else_approved_undated_last_then_by_name(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "b-undated.md", status="design")
    _plan(harness, "a-undated.md", status="idea")
    _plan(harness, "old.md", status="approved", approved="2026-09-01")
    _plan(harness, "new.md", status="design", approved="2026-10-11")
    _plan(harness, "same-b.md", status="idea", approved="2026-10-01")
    _plan(harness, "same-a.md", status="idea", approved="2026-10-01")
    # `built` wins over an older or newer `approved`.
    _plan(harness, "built-late.md", status="built", approved="2026-01-01", built="2026-10-15")
    _plan(harness, "built-early.md", status="built", approved="2026-10-16", built="2026-10-02")

    data, _ = _data(harness)

    assert _files(data["bands"]["future"]) == [
        "new.md",
        "same-a.md",
        "same-b.md",
        "old.md",
        "a-undated.md",
        "b-undated.md",
    ]
    assert _files(data["bands"]["past"]) == ["built-late.md", "built-early.md"]


def test_a_plan_with_no_frontmatter_is_listed_in_future_with_no_status(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    path = _raw_plan(harness, "bare.md", "# Bare plan\n\n**Status:** design.\n")

    data, warnings = _data(harness)

    assert data["bands"]["future"] == [
        {
            "file": "bare.md",
            "title": "Bare plan",
            "status": None,
            "approved": None,
            "built": None,
            "supersedes": [],
            "superseded_by": [],
            "tasks": [],
        }
    ]
    assert len(warnings) == 1
    assert str(path) in warnings[0]
    assert "does not start with a '---' frontmatter block" in warnings[0]


def test_a_plan_with_an_invalid_value_is_listed_with_no_status(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    path = _raw_plan(
        harness,
        "bad.md",
        "---\nstatus: done\napproved: 2026-10-11\nbuilt: null\nsupersedes: []\n---\n\n# Bad\n",
    )

    data, warnings = _data(harness)

    row = data["bands"]["future"][0]
    assert (row["file"], row["title"], row["status"], row["approved"]) == (
        "bad.md",
        "Bad",
        None,
        None,
    )
    assert len(warnings) == 1
    assert str(path) in warnings[0]
    assert "status 'done' is not one of" in warnings[0]


def test_a_plan_that_is_not_text_is_a_warning_not_a_500(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "good.md", status="building")
    path = _raw_plan(harness, "binary.md", b"\xff\xfe\x00not utf-8")

    data, warnings = _data(harness)

    assert _files(data["bands"]["now"]) == ["good.md"]
    assert _files(data["bands"]["future"]) == ["binary.md"]
    assert data["bands"]["future"][0]["status"] is None
    assert len(warnings) == 1 and str(path) in warnings[0]


def test_a_checkout_with_no_plans_dir_is_an_empty_roadmap_and_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    data, warnings = _data(harness)

    assert data["bands"] == {"future": [], "now": [], "past": []}
    assert len(warnings) == 1
    assert "no readable plans directory" in warnings[0]
    assert str(Path(harness.projects_root) / "ia-harness" / "docs" / "plans") in warnings[0]


def test_tasks_with_no_plan_are_grouped_by_merge_month_newest_first(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md")
    _task(harness.tasks_dir, "T-001", merged=_merged("2026-09-30T10:00:00+00:00"))
    _task(harness.tasks_dir, "T-002", merged=_merged("2026-10-02T10:00:00+00:00"))
    _task(harness.tasks_dir, "T-003", merged=_merged("2026-10-17T09:00:00+00:00"))
    _task(harness.tasks_dir, "T-004", status="in_progress")
    _task(harness.tasks_dir, "T-005", status="blocked")

    data, warnings = _data(harness)

    assert warnings == []
    no_plan = data["no_plan"]
    assert no_plan["now"] == [
        {"id": "T-004", "title": None, "state": "in_progress", "merged_at": None},
        {"id": "T-005", "title": None, "state": "blocked", "merged_at": None},
    ]
    assert [group["month"] for group in no_plan["past"]] == ["2026-10", "2026-09"]
    assert [task["id"] for task in no_plan["past"][0]["tasks"]] == ["T-002", "T-003"]
    assert no_plan["past"][1]["tasks"] == [
        {
            "id": "T-001",
            "title": None,
            "state": "in_progress",
            "merged_at": "2026-09-30T10:00:00+00:00",
        }
    ]


def test_a_merged_task_with_no_date_is_in_a_null_month_last(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-001", merged=_merged(None))
    _task(harness.tasks_dir, "T-002", merged=_merged("not a date"))
    _task(harness.tasks_dir, "T-003", merged=_merged("2026-10-17T09:00:00+00:00"))

    data, _ = _data(harness)

    past = data["no_plan"]["past"]
    assert [group["month"] for group in past] == ["2026-10", None]
    assert [task["id"] for task in past[1]["tasks"]] == ["T-001", "T-002"]
    assert past[1]["tasks"][0]["merged_at"] is None
    assert past[1]["tasks"][1]["merged_at"] == "not a date"
    assert data["no_plan"]["now"] == []


def test_a_task_with_a_known_plan_is_under_it(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "roadmap-view.md", status="building")
    _raw_task(
        harness.tasks_dir,
        "T-072",
        plan="roadmap-view.md",
        merged=_merged("2026-10-17T09:00:00+00:00"),
    )

    data, warnings = _data(harness)

    assert warnings == []
    assert data["bands"]["now"][0]["tasks"] == [
        {
            "id": "T-072",
            "title": None,
            "state": "in_progress",
            "merged_at": "2026-10-17T09:00:00+00:00",
        }
    ]
    assert data["no_plan"] == {"now": [], "past": []}


def test_a_task_with_an_unknown_plan_is_under_no_plan_with_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "roadmap-view.md", status="building")
    _raw_task(harness.tasks_dir, "T-090", plan="nowhere.md")
    _raw_task(harness.tasks_dir, "T-091", plan=["roadmap-view.md"])

    data, warnings = _data(harness)

    assert data["bands"]["now"][0]["tasks"] == []
    assert [task["id"] for task in data["no_plan"]["now"]] == ["T-090", "T-091"]
    assert len(warnings) == 2
    assert "T-090" in warnings[0] and "'nowhere.md'" in warnings[0]
    assert "T-091" in warnings[1]


def test_a_task_naming_another_project_is_left_out(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _raw_task(harness.tasks_dir, "T-001", project="other")
    _raw_task(harness.tasks_dir, "T-002", project="ia-harness")
    _task(harness.tasks_dir, "T-003")

    data, _ = _data(harness)

    assert [task["id"] for task in data["no_plan"]["now"]] == ["T-002", "T-003"]


def test_an_unreadable_task_file_is_a_warning_and_the_rest_still_come_back(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-001")
    broken = context_transfer.task_file_path(str(harness.tasks_dir), "T-002")
    Path(broken).write_text("---\ntask_id: [unclosed\n---\n")
    no_block = context_transfer.task_file_path(str(harness.tasks_dir), "T-003")
    Path(no_block).write_text("no frontmatter at all\n")
    not_a_mapping = context_transfer.task_file_path(str(harness.tasks_dir), "T-004")
    Path(not_a_mapping).write_text("---\nTODO write this up\n---\n")

    data, warnings = _data(harness)

    assert [task["id"] for task in data["no_plan"]["now"]] == ["T-001"]
    assert [w.split(":")[0] for w in warnings if "no readable plans" not in w] == [
        broken,
        no_block,
        not_a_mapping,
    ]
    assert all("unreadable task file" in w for w in warnings if "no readable plans" not in w)


def test_the_plan_cap_truncates_by_file_name_and_says_how_many(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_app, "MAX_ROADMAP_PLANS", 2)
    harness = _harness(tmp_path)
    for name in ("a.md", "b.md", "c.md", "d.md"):
        _plan(harness, name)
    # Past the cap, but still a plan file a `supersedes` may name.
    _plan(harness, "e.md", supersedes=("d.md",))

    data, warnings = _data(harness)

    assert _files(data["bands"]["future"]) == ["a.md", "b.md"]
    assert len(warnings) == 1
    assert "5 plans" in warnings[0] and "3 are left out" in warnings[0]


def test_the_task_cap_truncates_by_task_id_and_says_how_many(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_app, "MAX_ROADMAP_TASKS", 2)
    harness = _harness(tmp_path)
    _plan(harness, "a.md")
    for task_id in ("T-001", "T-002", "T-003"):
        _task(harness.tasks_dir, task_id)

    data, warnings = _data(harness)

    assert [task["id"] for task in data["no_plan"]["now"]] == ["T-001", "T-002"]
    assert len(warnings) == 1
    assert "3 task files" in warnings[0] and "1 are left out" in warnings[0]


def test_an_unknown_parameter_is_a_400(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, "/api/roadmap?projet=ia-harness")

    assert resp.status_code == 400
    assert "projet" in resp.get_json()["error"]


def test_project_may_be_left_out_with_one_checkout(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md", status="building")

    data, _ = _data(harness)

    assert data["project"] == "ia-harness"
    assert _files(data["bands"]["now"]) == ["a.md"]


def test_project_is_required_with_two_checkouts_and_chooses_the_plans(tmp_path: Path) -> None:
    harness = _harness(tmp_path, projects=("alpha", "beta"))
    _plan(harness, "alpha-plan.md", project="alpha")
    _plan(harness, "beta-plan.md", project="beta")

    resp = _get(harness, "/api/roadmap")
    assert resp.status_code == 400
    assert "project is required" in resp.get_json()["error"]

    data, _ = _data(harness, "/api/roadmap?project=beta")
    assert data["project"] == "beta"
    assert _files(data["bands"]["future"]) == ["beta-plan.md"]

    assert _get(harness, "/api/roadmap?project=gamma").status_code == 404


def test_auth_is_required(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md")

    assert harness.client.get("/api/roadmap").status_code == 401


# --- epics (ADR 447) ------------------------------------------------------


def _epic(harness, epic_id: str, subs: list[str], **front) -> None:
    _raw_task(harness.tasks_dir, epic_id, split_into=subs, title=f"Epic {epic_id}", **front)
    for sub in subs:
        _raw_task(harness.tasks_dir, sub, epic=epic_id)


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def test_an_epic_is_a_row_under_its_plan_with_sub_tasks_nested(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md", status="building")
    _raw_task(harness.tasks_dir, "T-1-S2", epic="T-1", merged=_merged("2026-10-12T10:00:00+00:00"))
    _raw_task(harness.tasks_dir, "T-1-S1", epic="T-1")
    _raw_task(harness.tasks_dir, "T-1", split_into=["T-1-S2", "T-1-S1"], plan="a.md", title="One")

    data, warnings = _data(harness)

    assert warnings == []
    (epic,) = data["bands"]["now"][0]["tasks"]
    assert epic == {
        "id": "T-1",
        "title": "One",
        "state": "in_progress",
        "merged_at": None,
        "kind": "epic",
        "place": "board",
        "merged_tasks": 1,
        "tasks": [
            {
                "id": "T-1-S2",
                "title": None,
                "state": "in_progress",
                "merged_at": "2026-10-12T10:00:00+00:00",
            },
            {"id": "T-1-S1", "title": None, "state": "in_progress", "merged_at": None},
        ],
    }
    assert data["no_plan"] == {"now": [], "past": []}


def test_a_sub_task_takes_its_epics_plan_and_a_differing_plan_is_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md", status="building")
    _plan(harness, "b.md", status="building")
    _raw_task(harness.tasks_dir, "T-1-S1", epic="T-1", plan="b.md")
    _raw_task(harness.tasks_dir, "T-1-S2", epic="T-1")
    _raw_task(harness.tasks_dir, "T-1", split_into=["T-1-S1", "T-1-S2"], plan="a.md")

    data, warnings = _data(harness)

    rows = {row["file"]: row for row in data["bands"]["now"]}
    assert _ids(rows["a.md"]["tasks"]) == ["T-1"]
    assert _ids(rows["a.md"]["tasks"][0]["tasks"]) == ["T-1-S1", "T-1-S2"]
    assert rows["b.md"]["tasks"] == []
    assert len(warnings) == 1
    assert "T-1-S1" in warnings[0] and "b.md" in warnings[0]


def test_an_epic_with_no_plan_is_now_until_every_sub_task_is_merged(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "a.md")
    _raw_task(harness.tasks_dir, "T-1-S1", epic="T-1", merged=_merged("2026-09-01T00:00:00+00:00"))
    _raw_task(harness.tasks_dir, "T-1-S2", epic="T-1")
    _raw_task(harness.tasks_dir, "T-1", split_into=["T-1-S1", "T-1-S2"])
    _raw_task(harness.tasks_dir, "T-2-S1", epic="T-2", merged=_merged("2026-09-01T00:00:00+00:00"))
    _raw_task(harness.tasks_dir, "T-2-S2", epic="T-2", merged=_merged("2026-10-03T00:00:00+00:00"))
    _raw_task(harness.tasks_dir, "T-2", split_into=["T-2-S1", "T-2-S2"])

    data, warnings = _data(harness)

    assert warnings == []
    (now,) = data["no_plan"]["now"]
    assert now["id"] == "T-1" and _ids(now["tasks"]) == ["T-1-S1", "T-1-S2"]
    (past,) = data["no_plan"]["past"]
    assert past["month"] == "2026-10"
    assert past["tasks"][0]["id"] == "T-2"
    assert past["tasks"][0]["merged_at"] == "2026-10-03T00:00:00+00:00"
    assert _ids(past["tasks"][0]["tasks"]) == ["T-2-S1", "T-2-S2"]


def test_a_backlog_epic_is_in_the_future_band(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "idea.md", status="idea")
    _plan(harness, "build.md", status="building")
    _raw_task(harness.tasks_dir, "T-0", plan="build.md")
    _epic(harness, "T-1", ["T-1-S1"], status="proposed", plan="idea.md")
    _epic(harness, "T-2", ["T-2-S1"], status="proposed", plan="build.md")
    _epic(harness, "T-3", ["T-3-S1"], status="proposed", priority="normal")
    _epic(harness, "T-4", ["T-4-S1"], status="proposed", priority="high")
    _epic(harness, "T-5", ["T-5-S1"], status="dropped", plan="idea.md")
    _raw_task(harness.tasks_dir, "T-9")

    data, warnings = _data(harness)

    assert warnings == []
    future = data["bands"]["future"]
    (echo,) = [row for row in future if row["file"] == "build.md"]
    assert _ids(echo["tasks"]) == ["T-2"]
    assert echo["tasks"][0]["place"] == "backlog"
    assert echo["tasks"][0]["kind"] == "epic"
    (now_plan,) = data["bands"]["now"]
    assert _ids(now_plan["tasks"]) == ["T-0"]
    (idea,) = [row for row in future if row["file"] == "idea.md"]
    # a dropped epic is placed as a board epic is, ahead of the proposed ones
    assert _ids(idea["tasks"]) == ["T-5", "T-1"]
    assert _ids(data["no_plan"]["now"]) == ["T-9", "T-4", "T-3"]


def test_an_epic_with_no_sub_tasks_a_missing_epic_and_a_bad_epic_file_warn(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _raw_task(harness.tasks_dir, "T-1", split_into=[])
    _raw_task(harness.tasks_dir, "T-2-S1", epic="T-2")
    _raw_task(harness.tasks_dir, "T-3-S1", epic="T-3")
    Path(context_transfer.task_file_path(str(harness.tasks_dir), "T-3")).write_text(
        "---\ntask_id: [unclosed\n---\n"
    )

    data, warnings = _data(harness)

    assert _ids(data["no_plan"]["now"]) == ["T-1", "T-2-S1", "T-3-S1"]
    text = "\n".join(warnings)
    assert "epic T-1: no sub-tasks" in text
    assert "task T-2-S1: names epic T-2" in text
    assert "unreadable task file" in text
    assert "task T-3-S1: names epic T-3" in text


def test_a_plan_path_matches_the_plan_file_name(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _plan(harness, "x.md", status="building")
    _raw_task(harness.tasks_dir, "T-1", plan="docs/plans/x.md")

    data, warnings = _data(harness)

    assert warnings == []
    assert _ids(data["bands"]["now"][0]["tasks"]) == ["T-1"]
