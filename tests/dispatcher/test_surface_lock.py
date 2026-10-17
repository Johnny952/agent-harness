"""The surface lock (docs/plans/autonomy.md B-3, docs/decisions.md ADR 83)."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from dispatcher import surface_lock

TTL = 120


def _take(state, task_id, *entries):
    return surface_lock.SurfaceLock.take(
        str(state), "proj", task_id, frozenset(entries),
        ttl_seconds=TTL, heartbeat_interval_seconds=60,
    )


def test_a_free_surface_is_taken_and_released(tmp_path):
    held = _take(tmp_path, "T-1", "dispatcher/queue.py")
    assert isinstance(held, surface_lock.SurfaceLock)
    assert surface_lock.holders(str(tmp_path), "proj", TTL) == {
        "T-1": frozenset({"dispatcher/queue.py"}),
    }
    held.release()
    held.release()  # idempotent
    assert surface_lock.holders(str(tmp_path), "proj", TTL) == {}


def test_an_overlapping_surface_is_refused_with_who_is_in_the_way(tmp_path):
    held = _take(tmp_path, "T-1", "dispatcher/")
    try:
        assert _take(tmp_path, "T-2", "dispatcher/queue.py") == ["T-1"]
        # The whole repo collides with everything.
        assert _take(tmp_path, "T-3") == ["T-1"]
    finally:
        held.release()
    second = _take(tmp_path, "T-2", "dispatcher/queue.py")
    assert isinstance(second, surface_lock.SurfaceLock)
    second.release()


def test_disjoint_surfaces_and_the_append_only_logs_coexist(tmp_path):
    one = _take(tmp_path, "T-1", "dispatcher/queue.py", "docs/decisions.md")
    two = _take(tmp_path, "T-2", "front/app.ts", "docs/decisions.md")
    try:
        assert isinstance(one, surface_lock.SurfaceLock)
        assert isinstance(two, surface_lock.SurfaceLock)
        assert set(surface_lock.holders(str(tmp_path), "proj", TTL)) == {"T-1", "T-2"}
    finally:
        one.release()
        two.release()


def test_a_dead_holders_record_is_broken(tmp_path):
    directory = surface_lock.surface_lock_dir(str(tmp_path), "proj")
    os.makedirs(directory)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    with open(os.path.join(directory, "T-9.json"), "w", encoding="utf-8") as f:
        json.dump({
            "owner": "gone", "host": "elsewhere", "pid": 1, "task_id": "T-9",
            "surface": ["dispatcher/"], "acquired": old, "heartbeat": old,
        }, f)
    held = _take(tmp_path, "T-1", "dispatcher/queue.py")
    assert isinstance(held, surface_lock.SurfaceLock)
    assert not os.path.exists(os.path.join(directory, "T-9.json"))
    held.release()


def test_only_the_owner_releases_or_refreshes(tmp_path):
    held = _take(tmp_path, "T-1", "a.py")
    try:
        assert not surface_lock.release(str(tmp_path), "proj", "T-1", "someone-else")
        assert not surface_lock.refresh(str(tmp_path), "proj", "T-1", "someone-else")
        assert surface_lock.refresh(str(tmp_path), "proj", "T-1", held.owner)
        assert "T-1" in surface_lock.holders(str(tmp_path), "proj", TTL)
        # A second process running the same task is in the way of itself.
        assert _take(tmp_path, "T-1", "a.py") == ["T-1"]
    finally:
        held.release()


def test_a_slug_or_task_id_that_is_not_one_path_component_is_refused(tmp_path):
    with pytest.raises(ValueError):
        surface_lock.surface_lock_dir(str(tmp_path), "../x")
    with pytest.raises(ValueError):
        _take(tmp_path, "../T-1", "a.py")
