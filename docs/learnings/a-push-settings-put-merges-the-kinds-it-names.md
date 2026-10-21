# A push settings PUT merges the kinds it names; quiet hours hold the cursor

**When it applies:** you build the console's push settings screen (step 5 of
`docs/plans/notifications.md`), or you change `Notifier._tick` or
`Notifier._new_records` in `dispatcher/push.py`.

**Status:** unconfirmed — read from T-119's code and ADR 237, not yet relied
on by a second task.

## What is true

- `PUT /api/push/settings` (`push.update_settings`) sets only the kinds its
  body names; the others stay as stored. `quiet_hours`, when present,
  replaces the window and `null` turns it off. There is no `POST` alias. A
  screen that sends one toggle at a time is fine; one that wants to reset
  every kind must name every kind. The answer is the effective settings.
- The file holds only what the person set; `read_settings` overlays it on
  `notifications.KINDS`' table, so a new kind takes its default.
- In quiet hours `_tick` returns before `_new_records`: the cursor does not
  move and `notifications.jsonl` past it is the queue. Anything that reads
  records (or moves the cursor) before the quiet-hours check breaks the hold.
- A missing `push-cursor.json` makes the first read start at the end of the
  file; if that read is the one after a quiet window, the night's records are
  not pushed (they stay in the inbox).

## Rule

Send the whole set of kinds from a reset, and keep the quiet-hours return the
first thing `_tick` does after reading the settings (ADR 237).

## Evidence

`git diff main HEAD -- dispatcher/push.py` on `agent/task/T-119`;
`tests/dispatcher/test_push.py`
`test_quiet_hours_hold_the_pushes_and_send_them_when_they_end`.
