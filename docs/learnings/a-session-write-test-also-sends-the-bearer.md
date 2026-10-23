# A session write test also sends the bearer, and one task per approve

**When it applies:** you write a test that posts to a board-service write
route (`/api/tasks/<id>/approve`, `request-changes`, `verdict`, `comments`)
as a signed-in person.

**Status:** unconfirmed — proposed by T-150's arquitecto and implementador
(one source), checked against the tree by T-150's auditor.

## What happened

A session cookie does not replace the `BOARD_ACTIONS_TOKEN` bearer:
`board_service/app.py` `_bearer_guards` refuses a request without
`Authorization: Bearer …` (401) whatever cookie it carries, and
`board_service/signin.py` `check_session` then wants the `X-CSRF-Token`
header for a write. And `dispatcher/actions.py` `_ONE_REVIEW_PER_TASK` refuses
a second queued approve or request-changes for the same task with 409, so a
test that approves twice on one task fails on the second post.

## Rule

Send the cookie, `X-CSRF-Token` and the bearer together, and give each approve
or request-changes its own task (`tests/board_service/test_verdict_author.py`
`post` shows both).
