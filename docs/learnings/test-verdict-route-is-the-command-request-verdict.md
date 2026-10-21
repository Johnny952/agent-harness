# `test_verdict_route.py` is the command-request verdict, not the review's

**When it applies:** you change the review verdict (`review_verdict`, the
`/verdict` route, `tasks.record_verdict`, or the approve route) and are
looking for its tests by file name.

**Status:** unconfirmed — proposed by T-125-S2's arquitecto and implementador;
checked against the test files by its auditor.

## Why

Two different things are called a verdict.
`tests/board_service/test_verdict_route.py` tests
`POST /api/tasks/<id>/command-requests/<n>/verdict`, the `command_verdict`
action of ADR 151. The review's verdict (`docs/plans/review.md` step 3,
ADR 222) is tested elsewhere.

## Rule

For the review verdict, read `tests/board_service/test_writes.py`
(`review_verdict` and its route), `tests/dispatcher/test_board_writes.py`
(`record_verdict`) and, since T-125-S2,
`tests/board_service/test_approve_route.py`.

## Evidence

`tests/board_service/test_verdict_route.py`'s helper posts to
`/command-requests/<n>/verdict` and names no `record_verdict` or
`review_verdict`.
