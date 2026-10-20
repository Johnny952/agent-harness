# A queued turn is in the action queue, not in the thread's log

**When it applies:** you draw, count or look up a thread's turns in `front/`
or a test and expect a message just sent (or sent mid-turn) to be in
`conversations/<thread>.jsonl`.

**Status:** unconfirmed — proposed by T-114's architect and implementador;
checked against the worker by its auditor.

## Symptom

`POST /api/threads/<id>/turns` answers 201 and `GET /api/threads/<id>/events`
still has no line for the message: no `turn_start`, no user text, until the
worker claims the `turn` action and starts it.

## Why

`board_service/app.py` `http_turn` only queues a `turn` action. The log's
first line for a turn is the `turn_start` that `dispatcher/worker.py` `_turn`
writes after the claim, carrying `action` = the turn's action id (ADR 173).

## Rule

Read a not-yet-started turn from `GET /api/actions` (`pending`/`claimed`,
`kind: "turn"`, `params.thread`), and drop it once a `turn_start` carries its
id — as `queuedTurns` in `front/src/lib/thread.ts` does (ADR 190, 3).

## Evidence

`dispatcher/worker.py` `_turn` (the `turn_start` line); `queuedTurns` and its
tests in `front/src/lib/thread.test.ts`.
