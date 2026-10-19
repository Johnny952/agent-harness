import json
from datetime import datetime, timedelta, timezone

import pytest

from dispatcher import actions
from dispatcher.actions import ActionRefused, create

START = dict(project="proj", task_id="T-1")
VERDICT = dict(project="proj", task_id="T-1", params={"n": 1, "verdict": "run"})
TURN = dict(params={"thread": "th", "text": "hi"})


@pytest.fixture
def clock(monkeypatch):
    """A clock that moves one second per read."""
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]

    def tick():
        now[0] += timedelta(seconds=1)
        return now[0]

    monkeypatch.setattr(actions, "_utc_now", tick)


def names(tmp_path, sub):
    return sorted(p.name for p in (tmp_path / "actions" / sub).iterdir())


def hand_write(tmp_path, state, record):
    actions.ensure_dirs(tmp_path)
    path = tmp_path / "actions" / state / f"{record['id']}.json"
    path.write_text(json.dumps(record))
    return path


def record_of(kind="start", aid="a-20260101T000000000000Z-abcd", **over):
    base = {k: None for k in actions.FIELDS}
    base.update(
        version=1, id=aid, kind=kind, project="proj", task_id="T-1",
        params={"limit": None}, actor="operator", status="pending",
    )
    base.update(over)
    return base


def read(tmp_path, state, aid):
    return json.loads((tmp_path / "actions" / state / f"{aid}.json").read_text())


def test_create_lands_in_pending_with_every_key(tmp_path):
    rec = create(tmp_path, "start", **START)
    assert names(tmp_path, "pending") == [f"{rec['id']}.json"]
    assert names(tmp_path, "tmp") == []
    assert tuple(read(tmp_path, "pending", rec["id"])) == actions.FIELDS
    assert rec["status"] == "pending" and rec["params"] == {"limit": None}


def test_create_is_atomic(tmp_path, monkeypatch):
    def boom(*a):
        raise OSError("disk")

    monkeypatch.setattr(actions.os, "rename", boom)
    with pytest.raises(OSError):
        create(tmp_path, "start", **START)
    assert names(tmp_path, "pending") == []
    assert names(tmp_path, "tmp") == []


def test_ids_sort_in_creation_order(tmp_path, clock):
    ids = [create(tmp_path, "turn", **TURN)["id"] for _ in range(3)]
    assert sorted(ids) == ids
    assert all(actions.ID_RE.fullmatch(i) for i in ids)


def test_a_claim_succeeds_once(tmp_path):
    aid = create(tmp_path, "start", **START)["id"]
    rec = actions.claim(tmp_path, aid)
    assert rec["status"] == "running" and rec["claimed_at"]
    assert actions.claim(tmp_path, aid) is None
    assert names(tmp_path, "pending") == []
    assert read(tmp_path, "claimed", aid)["status"] == "running"


def test_unknown_kind_is_refused_400(tmp_path):
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, "explode")
    assert err.value.status == 400 and "explode" in err.value.reason


@pytest.mark.parametrize(
    "kind, kwargs",
    [
        ("start", dict(project="proj")),
        ("start", dict(project="../x", task_id="T-1")),
        ("start", dict(project=".hid", task_id="T-1")),
        ("start", dict(**START, params={"limit": 3})),
        ("start", dict(**START, params={"other": 1})),
        ("start", dict(**START, note="x" * 20_001)),
        ("start", dict(**START, actor="two words")),
        ("cancel", dict(params={"target": "nope"})),
        ("cancel", dict(task_id="T-1", params={"target": "a-20260101T000000000000Z-abcd"})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": 0, "verdict": "run"}})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": True, "verdict": "run"}})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": 1, "verdict": "maybe"}})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": 1, "verdict": "run", "prefix": 2}})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": 1, "verdict": "allow", "prefix": 0}})),
        ("command_verdict", dict(**{**VERDICT, "params": {"n": 1, "verdict": "run", "comment": "x" * 20_001}})),
        ("command_verdict", dict(**VERDICT, note="no")),
        ("turn", dict(params={"thread": "", "text": "hi"})),
        ("turn", dict(params={"thread": "th"})),
        ("turn", dict(task_id="T-1", **TURN)),
        ("turn", dict(params=None)),
    ],
)
def test_a_bad_create_is_refused_400(tmp_path, kind, kwargs):
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, kind, **kwargs)
    assert err.value.status == 400
    assert actions.read_all(tmp_path) == ([], [])


def test_valid_params_are_taken(tmp_path):
    create(tmp_path, "start", **START, params={}, note="why")
    allow = {"n": 2, "verdict": "allow", "prefix": 3, "comment": "ok"}
    create(tmp_path, "command_verdict", project="proj", task_id="T-2", params=allow)
    target = "a-20260101T000000000000Z-abcd"
    create(tmp_path, "cancel", params={"target": target})
    assert len(names(tmp_path, "pending")) == 3


def test_a_duplicate_start_is_refused_409(tmp_path):
    aid = create(tmp_path, "start", **START)["id"]
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, "start", **START)
    assert err.value.status == 409
    actions.claim(tmp_path, aid)
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, "start", **START)
    assert err.value.status == 409
    create(tmp_path, "start", project="proj", task_id="T-2")


def test_a_duplicate_command_verdict_is_refused_409(tmp_path):
    create(tmp_path, "command_verdict", **VERDICT)
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, "command_verdict", **VERDICT)
    assert err.value.status == 409


def test_a_start_does_not_refuse_a_verdict_on_the_same_task(tmp_path):
    create(tmp_path, "start", **START)
    create(tmp_path, "command_verdict", **VERDICT)
    assert len(names(tmp_path, "pending")) == 2


def test_a_finished_start_does_not_refuse_a_new_one(tmp_path):
    aid = create(tmp_path, "start", **START)["id"]
    actions.claim(tmp_path, aid)
    actions.finish(tmp_path, aid, "done")
    create(tmp_path, "start", **START)


def test_a_task_not_ready_is_refused_409_and_nothing_is_written(tmp_path):
    with pytest.raises(ActionRefused) as err:
        create(tmp_path, "start", **START, ready=lambda p, t: False)
    assert err.value.status == 409 and "T-1" in err.value.reason
    assert names(tmp_path, "pending") == [] and names(tmp_path, "tmp") == []


def test_ready_is_asked_only_for_start(tmp_path):
    seen = []

    def ready(project, task_id):
        seen.append((project, task_id))
        return True

    create(tmp_path, "command_verdict", **VERDICT, ready=ready)
    create(tmp_path, "turn", **TURN, ready=ready)
    assert seen == []
    create(tmp_path, "start", **START, ready=ready)
    assert seen == [("proj", "T-1")]


def test_the_same_task_id_in_two_projects_is_not_a_duplicate(tmp_path):
    create(tmp_path, "start", project="a", task_id="T-1")
    create(tmp_path, "start", project="b", task_id="T-1")
    create(tmp_path, "command_verdict", **{**VERDICT, "project": "a"})
    create(tmp_path, "command_verdict", **{**VERDICT, "project": "b"})
    assert len(names(tmp_path, "pending")) == 4


def test_kinds_carry_serial_key_and_quota(tmp_path):
    start = create(tmp_path, "start", **START)
    turn = create(tmp_path, "turn", **TURN)
    cancel = create(tmp_path, "cancel", params={"target": start["id"]})
    assert actions.serial_key(start) == "project:proj"
    assert actions.serial_key(turn) == "thread:th"
    assert actions.serial_key(cancel) is None
    assert actions.KINDS["start"].spends_quota
    assert not actions.KINDS["cancel"].spends_quota
    assert actions.KINDS["start"].handler is None


def claimed(tmp_path, kind, **over):
    path = hand_write(tmp_path, "claimed", record_of(kind, status="running", **over))
    return path.stem


def test_recover_start_not_started_goes_back_to_pending(tmp_path):
    aid = claimed(tmp_path, "start", claimed_at="t")
    assert actions.recover(tmp_path) == [(aid, "back to pending")]
    rec = read(tmp_path, "pending", aid)
    assert rec["status"] == "pending" and rec["claimed_at"] is None
    assert names(tmp_path, "claimed") == []


def test_recover_started_start_is_interrupted(tmp_path):
    aid = claimed(tmp_path, "start", started_at="t")
    actions.recover(tmp_path)
    rec = read(tmp_path, "done", aid)
    assert rec["status"] == "interrupted" and rec["finished_at"] and rec["error"]


def test_recover_command_verdict_is_interrupted_naming_command_resume(tmp_path):
    aid = claimed(
        tmp_path, "command_verdict", params={"n": 1, "verdict": "run"}
    )
    actions.recover(tmp_path)
    rec = read(tmp_path, "done", aid)
    assert rec["status"] == "interrupted" and "command-resume" in rec["error"]


def test_recover_turn_is_interrupted(tmp_path):
    aid = claimed(tmp_path, "turn", project=None, task_id=None, params=TURN["params"])
    actions.recover(tmp_path)
    assert read(tmp_path, "done", aid)["status"] == "interrupted"


def test_recover_cancel_runs_again(tmp_path):
    aid = claimed(tmp_path, "cancel", project=None, task_id=None,
                  params={"target": "a-20260101T000000000000Z-abcd"})
    actions.recover(tmp_path)
    assert read(tmp_path, "pending", aid)["status"] == "pending"


def test_recover_moves_a_final_record_left_in_claimed(tmp_path):
    aid = claimed(tmp_path, "start")
    path = tmp_path / "actions" / "claimed" / f"{aid}.json"
    path.write_text(json.dumps(record_of("start", status="done")))
    actions.recover(tmp_path)
    assert read(tmp_path, "done", aid)["status"] == "done"
    assert names(tmp_path, "claimed") == []


def test_recover_leaves_pending_and_unparseable_alone(tmp_path):
    hand_write(tmp_path, "pending", record_of("start"))
    bad = tmp_path / "actions" / "claimed" / "a-x.json"
    bad.write_text("{")
    assert actions.recover(tmp_path) == []
    assert bad.exists() and len(names(tmp_path, "pending")) == 1


def test_an_unknown_kind_ends_in_done_failed(tmp_path):
    p = hand_write(tmp_path, "pending", record_of("bogus", aid="a-20260101T000000000000Z-0001"))
    c = hand_write(tmp_path, "claimed", record_of("bogus", aid="a-20260101T000000000001Z-0002"))
    actions.recover(tmp_path)
    for path in (p, c):
        rec = read(tmp_path, "done", path.stem)
        assert rec["status"] == "failed" and rec["error"] == "unknown kind"
    assert names(tmp_path, "pending") == [] and names(tmp_path, "claimed") == []


def test_claim_of_an_unknown_kind_fails_it(tmp_path):
    path = hand_write(tmp_path, "pending", record_of("bogus"))
    assert actions.claim(tmp_path, path.stem) is None
    rec = read(tmp_path, "done", path.stem)
    assert rec["status"] == "failed" and rec["error"] == "unknown kind"


def test_claim_of_an_invalid_record_fails_it(tmp_path):
    path = hand_write(tmp_path, "pending", record_of("start", project=None))
    assert actions.claim(tmp_path, path.stem) is None
    assert read(tmp_path, "done", path.stem)["status"] == "failed"


def test_read_all_skips_a_bad_file(tmp_path):
    a = create(tmp_path, "start", **START)["id"]
    b = create(tmp_path, "turn", **TURN)["id"]
    root = tmp_path / "actions"
    (root / "pending" / "garbage.json").write_text("not json")
    (root / "claimed" / "a-half.json").write_text('{"version": 1, "id": "a-ha')
    (root / "done" / "a-wrong.json").write_text(json.dumps(record_of(aid="a-other")))
    (root / "pending" / "note.txt").write_text("ignored")
    (root / "tmp" / "a-tmp.json").write_text(json.dumps(record_of(aid="a-tmp")))
    entries, warnings = actions.read_all(tmp_path)
    assert sorted(e.record["id"] for e in entries) == sorted([a, b])
    assert {e.state for e in entries} == {"pending"}
    assert len(warnings) == 3


def test_read_all_on_an_empty_state_dir(tmp_path):
    assert actions.read_all(tmp_path / "fresh") == ([], [])


def test_rewrite_replaces_in_place(tmp_path):
    rec = create(tmp_path, "start", **START)
    rec["waiting"] = "project"
    actions.rewrite(tmp_path, rec)
    assert read(tmp_path, "pending", rec["id"])["waiting"] == "project"
    assert names(tmp_path, "tmp") == []
    with pytest.raises(FileNotFoundError):
        actions.rewrite(tmp_path, {**rec, "id": "a-missing"})


def test_finish_rejects_a_non_final_status(tmp_path):
    aid = create(tmp_path, "start", **START)["id"]
    with pytest.raises(ValueError):
        actions.finish(tmp_path, aid, "running")
    assert names(tmp_path, "pending") == [f"{aid}.json"]


def test_finish_moves_to_done_with_result(tmp_path):
    aid = create(tmp_path, "start", **START)["id"]
    actions.claim(tmp_path, aid)
    actions.finish(tmp_path, aid, "done", result={"ok": 1})
    rec = read(tmp_path, "done", aid)
    assert rec["status"] == "done" and rec["result"] == {"ok": 1}
    assert rec["finished_at"] and names(tmp_path, "claimed") == []
