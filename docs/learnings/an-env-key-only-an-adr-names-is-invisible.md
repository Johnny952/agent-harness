# An environment variable documented only in an ADR is invisible to the operator who has to set it

**When it applies:** you added a key that `docker/compose/*.yml` reads from
`docker/compose/.env` or from the operator's shell, and you documented it in
`docs/decisions.md` or in a compose comment.

**Status:** unconfirmed — reported once, by T-010's revisor, as blocking finding
B1 on `BOARD_PROJECT`.

## Symptom

No error at bring-up, and a broken screen afterwards. `BOARD_PROJECT` defaults
to empty in both compose files, so the stack starts; `/debt` then shows the
api's `400 project is required` on any host whose `projects_root` holds more
than one checkout — which this one does. The only explanation anywhere was
`docs/decisions.md` ADR 9, and an operator following `README.md` to bring the
stack up has no reason to open the decision log.

## Why

The three places a variable can be written serve three readers. A compose
`environment:` block is read by whoever edits the service. An ADR is read by
whoever is about to change the behaviour. Only `README.md` is read by the person
typing `docker compose up`, and that is the only one of the three who has to
act. `scripts/configure.sh` does not close the gap either — it writes the keys
it prompts for, and an optional key it deliberately does not prompt for never
reaches `.env`.

## What to do

Name it in `README.md` "### 4. Bring up the stack", beside the keys already
there, and say three things: which service reads it, what happens when it is
unset, and where the argument lives. That section is also where the
`DASHBOARD_*` pair is explained, so it is already the list an operator reads.
Do not add it to `docs/README.md`: that file names no environment variable at
all, and one entry there is the odd one out rather than a second home.

## Evidence

T-010's revisor round 1, finding B1, answered in `d46c614`. `README.md` "### 4.
Bring up the stack" now carries `BOARD_PROJECT` and the `400 project is
required` behaviour; `docs/decisions.md` ADR 9 still holds the argument.
