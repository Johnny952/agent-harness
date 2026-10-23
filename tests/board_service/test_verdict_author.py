"""A verdict or comment written by a signed-in person names that person (ADR 384, T-150).

Fixtures and sign-in helpers are `test_signin.py`'s, under tmp_path.
"""
import json

from dispatcher import tasks as task_store
from tests.board_service.test_app import task
from tests.board_service.test_signin import (  # noqa: F401  (fixtures)
    auth_dir, clock, cfg, client, fast_scrypt, hive, secrets_, session,
)

COMMIT = "a" * 40
BEARER = {"Authorization": "Bearer s3cret"}


def lines(hive, task_id="T-002"):
    path = hive.parent / "board" / task_id / "comments.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def post(client, hive, route, body, headers=None, task_id="T-002"):
    task(hive, task_id, status="awaiting_review")
    return client.post(f"/api/tasks/{task_id}/{route}", json=body, headers={**BEARER, **(headers or {})})


def test_a_signed_in_owner_is_the_author_of_each_write(client, hive, secrets_, clock):
    _, csrf, _ = session(client, secrets_, clock, "ana")
    review = {"project": "demo", "commit": COMMIT, "text": "Fine."}
    writes = {
        "approve": review,
        "request-changes": review,
        "verdict": {"kind": "comment", "commit": COMMIT, "text": "Hm."},
        "comments": {"text": "Note."},
    }
    for number, (route, body) in enumerate(writes.items(), start=11):
        assert post(client, hive, route, body, csrf, task_id=f"T-0{number}").status_code == 201
        [line] = lines(hive, f"T-0{number}")
        assert line["author"] == "ana"


def test_review_comments_pair_with_the_signed_in_verdict(client, hive, secrets_, clock):
    _, csrf, _ = session(client, secrets_, clock, "ana")
    assert post(client, hive, "comments", {"text": "Rename it."}, csrf).status_code == 201
    review = {"project": "demo", "commit": COMMIT, "text": "Changes please."}
    assert post(client, hive, "request-changes", review, csrf).status_code == 201
    texts = [c["text"] for c in task_store.review_comments(str(hive), "T-002", COMMIT)]
    assert texts == ["Rename it.", "Changes please."]


def test_with_no_cookie_the_author_is_still_the_operator(client, hive):
    review = {"project": "demo", "commit": COMMIT, "text": "Fine."}
    assert post(client, hive, "approve", review).status_code == 201
    assert post(client, hive, "comments", {"text": "Note."}).status_code == 201
    assert {line["author"] for line in lines(hive)} == {"operator"}


def test_a_reviewer_comment_names_the_reviewer_and_an_approve_is_refused(client, hive, secrets_, clock):
    _, csrf, _ = session(client, secrets_, clock, "rev")
    assert post(client, hive, "comments", {"text": "Note."}, csrf).status_code == 201
    review = {"project": "demo", "commit": COMMIT, "text": "Fine."}
    assert post(client, hive, "approve", review, csrf).status_code == 403
    assert [line["author"] for line in lines(hive)] == ["rev"]
