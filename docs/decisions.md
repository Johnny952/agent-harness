# Decisions

One ADR per decision a later task could undo without knowing it was a
decision. Append; never rewrite an entry that is already here — to replace
one, strike its heading through and point at the number that supersedes it.

Everything here was decided by an agent doing the work, which is what makes it
supersedable by a later one. A ruling a human made is not an ADR and does not
belong here: it goes in [`charter.md`](charter.md), where no role may edit it.

## ADR 1 — A local board is a directory of JSON cards, and stores dispatcher vocabulary

**Status:** accepted (T-008, 2026-09-25).

**Context.** `dispatcher/vibe_kanban_client.py` is a four-method seam
(`list_issues`, `get_issue`, `create_issue`, `set_status`) built for a board
that no longer exists: `docs/ROADMAP.md` records the 2026-09-25 re-run where
`create_issue` fails with `project_id is required` against a retired service.
Every run to date has used `NullKanbanClient`, so two prioritized items in the
README — debt cards and epic decomposition — wait on a `create_issue` that
works. `docs/plans/board.md` "Phase 0 — `LocalBoardClient`" is the spec this
implements.

**Decision.** `LocalBoardClient` stores one JSON document per issue under
`local_board.dir`, named `<issue_id>.json`, written with a temp file in that
directory and `os.replace` the way `dispatcher/state_machine.py` writes account
state. Within that:

- Ids are `uuid4`, minted by the client. `dispatcher/cli.py:_seed_kanban_issue_id`
  rejects a `--kanban-issue-id` that is not a uuid, and short ids would mean
  relaxing that check. `simple_id` stays `None`: a display handle is the UI's
  problem, and it can number by `created_at`.
- A card holds exactly the fields in `CARD_FIELDS` — `issue_id`, `title`,
  `description`, `status`, `created_at`.
- `status` is the dispatcher's own string, stored verbatim: `set_status` writes
  `in_progress:implementador` as it comes, with no `status_map`. There is no
  column here to rename, so the role — the one dimension a generic board
  flattens — survives on the card.
- `list_issues(**filters)` matches `CARD_FIELDS` by equality and raises
  `ValueError` on any other key. A filter whose value is `None` is dropped, as
  `VibeKanbanClient` drops it.
- `set_status` on an id with no card raises `LookupError`, where the remote
  client warns and carries on. A remote board could legitimately be out of
  sync; local storage that has forgotten a card the task file still points at
  is a bug. Every call site in `dispatcher/dispatcher.py`
  (`_update_task_status`, `_open_kanban_issue`) already catches `Exception` and
  logs, so this surfaces without threatening a run.
- `get_issue` is a lookup and returns `None` for anything it cannot answer. A
  missing file is silent: it is the ordinary "no such issue". The other three
  each log a warning, because each means someone wrote something this client
  did not — a document that will not parse, a document that parses but carries
  no string `issue_id`, and an id that is not a bare filename.
- A missing directory is a board with no issues. The first write creates it.

**Consequences.** Phase 1's read API and Phase 2's UI read these documents, so
a status they render is `in_progress:<role>` and not a column name; sorting a
board by age means `created_at`, the only order the random ids allow. Adding a
stored field widens what `list_issues` accepts and has to be added to
`CARD_FIELDS`; removing one breaks a caller that filters on it. The client
caches nothing, so two clients over one directory see each other's writes —
which is what lets `dispatch run-task` and a later `dispatch learnings` agree,
and what a Phase 1 reader relies on. Nothing here is dispatch state:
`.hive/tasks/*.md` remains the source of truth, per `docs/plans/board.md`
"Architecture decisions".

## ADR 2 — Two boards configured is a startup error, not a precedence rule

**Status:** accepted (T-008, 2026-09-25).

**Context.** `config.yaml` now has two optional board blocks, `vibe_kanban` and
`local_board`, and a run talks to one board. `dispatcher/cli.py:_kanban()`
picks in that order, so a silent rule was available.

**Decision.** `dispatcher/config.py:_load_boards` raises when both blocks are
present, with a message naming which one to drop. Both blocks remain opt-in:
with neither, a run gets `NullKanbanClient` and says nothing about a board, so
an upgraded harness does not start writing cards it was never asked for.
`--kanban-issue-id` is accepted with either block and refused with neither —
the check is "no board", not "no MCP".

**Consequences.** An operator who writes both gets a failed startup rather than
a board nothing moves. Phase 2 is where the local board's default is meant to
flip on (`docs/plans/board.md` "Configuration"); doing that means deciding what
an existing `vibe_kanban` block then means, and this ADR is why that cannot be
"the local one wins quietly".

## ADR 3 — The read API reads cards from a local board only, and never dials a remote one

**Status:** accepted (T-009, 2026-09-26).

**Context.** `docs/plans/board.md` "Phase 1 — a read API in Python" makes a
task's `card` the board document its `kanban_issue_id` points at, and the
harness has two board implementations. `VibeKanbanClient` reaches its board by
spawning `vibe_kanban.command` as an MCP subprocess per call, over stdio, with
no timeout — and `observability/api/Dockerfile` is `python:3.11-slim`, which has
no node and no `npx`.

**Decision.** `observability/api/app.py:_read_cards` builds
`LocalBoardClient(cfg.local_board)` when `local_board` is configured and reads
no board otherwise. A harness configured with `vibe_kanban` gets `card: null`
on every task plus one warning in the envelope saying so, per request that
would have read a card. It is not an error, and it is not silence either.

**Consequences.** A read endpoint that promises never to 500 also never blocks
on a subprocess it cannot kill, and the image stays a Python image. The cost is
that a `vibe_kanban` harness sees no cards through this API; the way to change
that is a board client that speaks HTTP to something, not a socket or a
toolchain in this image. Phase 2 reads `warnings`, so the reason arrives with
the nulls. This is also why the API builds its own client instead of taking
`dispatcher/cli.py:_kanban()`: that factory is for a dispatcher that must not
know which board it got, and this service is the one caller that has to.

## ADR 4 — `unreadable()` is the one name the board seam does not carry, and `card` is a `KanbanIssue`

**Status:** accepted (T-009, 2026-09-26).

**Context.** `docs/debt/T-008-D2.md`: a card that will not parse is skipped by
`list_issues` with a log line, so a reader gets a board that is quietly short.
Phase 1's envelope is where that shortfall was meant to surface, and surfacing
it needs a method only the local board can have — every other client's skips
happen on a server. `docs/learnings/the-kanban-seam-is-a-closed-surface.md`
says the seam is closed at five public names.

**Decision.** `LocalBoardClient.unreadable() -> list[str]` returns the paths
`list_issues` skipped, over the same single directory scan (`_scan`).
`dispatcher/dispatcher.py` must never call it — it does not know which
implementation it holds — and
`tests/dispatcher/test_vibe_kanban_client.py:LOCAL_BOARD_ONLY` names it as the
one exception, so the parity test still fails on the next local-only addition.
That test was also rewritten: it compared `LocalBoardClient`'s public names
against the union of both clients', which contains them by construction, so it
had never failed a superset at all.

A task's `card` is `dataclasses.asdict` of the `KanbanIssue` the seam answers
with — `issue_id`, `title`, `status`, `simple_id` — and not the stored JSON
document. The document's other two fields, `description` and `created_at`, are
reachable only through `_read_card`, and a read API is not the place to reach
past a seam this project keeps deliberately narrow.

**Consequences.** Phase 2 can render a card's title and its
`in_progress:<role>` status, and cannot render its description or order cards
by `created_at` — the two things ADR 1 says a UI would want `created_at` for.
Getting them means widening the seam for every client in one change, with its
own ADR, which is exactly the cost the learning above says it should carry.
`unreadable()` costs one extra directory scan per request that reads cards,
because `list_issues` and `unreadable` are two calls; the scan is shared code,
not a second parser.

## ADR 5 — The envelope's other half: what a read API answers when the read fails

**Status:** accepted (T-009, 2026-09-26).

**Context.** `docs/plans/board.md` pins every 200 as
`{"data": …, "warnings": […]}` and says one unreadable file must never empty
`data` or 500. It does not say what a non-200 body looks like, nor what a
single-object endpoint answers when the one file it was asked for is the
unreadable one — and Phase 2 is written against whatever this phase did.

**Decision.** In `observability/api/app.py`:

- Every non-200 *this module returns*, but the 401, is
  `{"error": "<one sentence naming what was wrong>"}`. There is no `warnings`
  key on an error: nothing was read. The two Flask raises for itself are not
  covered and are not JSON: a path no route matches answers 404 and a write
  verb on a route answers 405, both `text/html` with a Werkzeug page. Measured,
  not assumed. A Phase 2 client that calls `.json()` on any non-200 therefore
  has to guard the content type, which is the cheaper half of the trade — an
  `errorhandler` per status would make the promise true at the cost of hiding
  a typo'd path behind the same envelope shape a real endpoint answers with.
- 400 for an unknown query parameter, for a non-integer `limit`/`since`, for a
  non-positive `limit`, for a `limit`/`since` outside the signed 64-bit range
  SQLite can bind, and for an absent `project` where `projects_root` holds
  more than one checkout. An empty value (`?limit=&since=`) reads as absent, so
  the URL the plan writes out is a legal request. The range check is a 400 and
  not a caught exception because it is a fact about the request: `int()` parsing
  the text does not mean `LIMIT ?` can bind the result, and `OverflowError` there
  is neither a `sqlite3.Error` nor a `ValueError`. It is not the cap on how much
  a caller may ask for; that is debt this phase declared and did not take.
- 404 for a task id with no file, a task id that is not a bare filename, a
  project slug with no checkout, and a `projects_root` with no checkouts at all.
- A file that exists and will not parse is never a 404: `/api/tasks/<id>`
  answers 200 with `data: null` and a warning naming the file, because a 404
  there would deny a file the operator is looking at.
- An account whose state file will not parse stays in `data` with `state`,
  `current_task` and `rate_limited_at` null, plus a warning naming the file: the
  account is configured whatever its state file says, and dropping it would
  hide an account from the page that watches the pool.
- A missing or unopenable events database, and a missing debt index, are `[]`
  plus a warning. Neither is an error: a harness that has never run has no
  events, and a project that has filed no debt has no index.
- A `local_board.dir` that cannot be listed at all — it is a regular file, the
  mount is gone, or its cards will not sort against each other — is every
  `card` null plus one warning naming the directory, not a 500.
  `LocalBoardClient._scan` raises for anything but a missing directory, and
  correctly so: for the dispatcher a board that reads as empty would let
  `create_issue` mint cards nobody can list, and a card's fields are text some
  phase wrote of which only `issue_id` is checked on the way in. The widening
  belongs on this side of the seam, where never-500 is the contract.
- **Never-500 is bounded, and the bound is a guard and not a promise about
  Flask.** Each task row is read, shaped *and* serialised inside one `try`:
  a frontmatter value of the wrong type raises where it is used — as a dict
  key in `_task`, or inside `jsonify` — and both points are covered, so such a
  task costs a warning naming its file. Two things stay outside it, both
  measured. The 404 and 405 Flask raises for itself are HTML, as the first
  bullet says. And the guard is per task row: a value that reaches `_envelope`
  from `/api/accounts`, `/api/events` or `/api/debt` is not serialisation-
  checked, because those three shape their own rows out of `str` and `int` and
  have no frontmatter to carry a `set`. A later endpoint that returns anything
  a parser built inherits the obligation, not the guarantee.

**Consequences.** A Phase 2 client can treat `data === null` and `data === []`
as "nothing to show, read `warnings`" and reserve its error path for non-200s.
A later endpoint added here inherits all of it, including the rule that
`warnings` names files rather than describing states. `warnings` is unbounded in
length, and so is `limit`: neither has a cap, which is a cost this phase chose
to leave — see `docs/debt/`.

## ADR 6 — A server-rendered board has three states per region and a partial, not four

**Status:** accepted (T-010, 2026-09-27).

**Context.** `docs/plans/board.md` "Phase 2" fixes four states per region —
Loading, Empty, Stale, Error — and says the region and not the page is the unit.
It wrote that table before the toolchain was ruled, and `Loading` is the one row
that depends on it: it is defined as "its fetch has not resolved", a state only
a client that resolves fetches after paint can be in. [`charter.md`](charter.md)
C-7 rules the board server-rendered Flask and Jinja, and the same spec's
*Required behaviour* forbids the alternative in as many words — "every fetch is
server-side", "the page ships no `fetch` of the api". A Jinja template is
rendered after every call the request made has already returned or failed, so
there is no instant at which a region has an unresolved fetch and a reader to
see it.

**Decision.** The board renders **Empty**, **Stale** and **Error** per region,
plus the **partial** case (rows and a non-empty `warnings`). `Loading` is not
rendered, no skeleton markup exists, and no template carries a placeholder row.
A slow endpoint is paid for in time-to-first-byte, not in a state: the `as of`
stamp and the per-region Error state still hold, so a page that took nine
seconds because `/api/events` was slow says when it was true and names the call
that failed if one did. The spec's own *Done when* confirms the reading — the
four cases it asks the tests to cover are a 200 with rows, a 200 with `[]`, a
200 with rows and a warning, and a non-200. Loading is not among them.

**Consequences.** The state table in `docs/plans/board.md` is one row wider than
this implementation, deliberately, and the plan is not edited to match: a ruling
is reversible and C-7 says so. If C-7 is reversed at Phase 5, the Loading row
comes back with the client that can be in it, and the skeleton is written then
against the same table. Until then, "the board has no Loading state" is a
decision and not an omission, and a reviewer reading the plan against the code
should find this ADR before filing it as a gap. The independence the table asks
for survives: each region is one call, one envelope check and one outcome, so
one endpoint answering 503 costs its own region and nothing else on the page.

## ADR 7 — How the board reaches the api: one bearer token, one timeout, one boundary

**Status:** accepted (T-010, 2026-09-27).

**Context.** The board is an HTTP client of the api and nothing else. Three
things the spec asks for have no implementation shape yet: how the token is
presented, what happens when the api does not answer at all, and where the
envelope is checked. `observability/auth.py` also has to grow the second
credential without breaking the first, and without breaking a host whose
`docker/compose/.env` was written before `API_TOKEN` existed.

**Decision.**

- **The token is a bearer token, parsed off the raw header.** The board sends
  `Authorization: Bearer <API_TOKEN>`. `observability/auth.py` reads
  `request.headers.get("Authorization")` and splits the scheme itself for this
  path, rather than reading `request.authorization.token`: the existing
  `auth.type != "basic"` guard stays exactly as it is, and the bearer branch
  does not depend on which Werkzeug version populates `.token`. Comparison is
  `secrets.compare_digest` over `.encode()`d bytes on both sides, the same shape
  `check_auth` uses and for the same reason — a non-ASCII token answers 401, not
  500.
- **No token configured means the bearer path is closed**, not open. A falsy
  `token` argument rejects every bearer header without comparing. The board's
  own `requires_auth` passes no token at all, so a human's Basic credential is
  the only thing the board accepts, and the api's token is not a second way into
  the board.
- **`API_TOKEN` is optional on the api** and read with `os.environ.get`, not
  `os.environ[...]`. An operator upgrading a running host has a `.env` with
  `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD_HASH` and no `API_TOKEN`; the api
  must still start there, with Basic auth working and the token path shut.
  `scripts/configure.sh` writes the variable for a fresh install, and both
  compose files pass it to the `api` service as well as to `board` — the spec's
  Phase 1 configuration block predates the token and is not the authority on it.
- **One boundary function per call.** Every api call goes through one helper
  that returns a region result — rows, warnings, and either nothing or one error
  string — and never raises at a template. It sets `timeout=5` on
  `requests.get` (the timeout `hooks/emit_event.py` already uses for the
  collector; an unbounded read is how a board with no push becomes a page that
  never arrives), catches `requests.RequestException`, requires a 200, requires
  `application/json` before `.json()` (ADR 5 and
  `docs/learnings/flask-answers-404-and-405-in-html.md`: Flask's own 404 and 405
  are HTML), and validates the envelope shallowly — `data` present, `warnings` a
  list of strings, each row a mapping. Anything else is that region's Error state
  naming the endpoint and the status, which is what keeps a bad shape from
  becoming a `jinja2.UndefinedError` halfway down a half-rendered page.

**Consequences.** The api has two accepted credentials and one realm; a rotation
is one line in `docker/compose/.env` and a restart of two services. A test that
wants the board's Error state monkeypatches `requests.get` in the board module —
the pattern `tests/hooks/test_emit_event.py` already uses — so no test needs a
live api and the recorded fixtures the spec asks for are plain dicts. The
5-second timeout is a number, not a law: a board that starts showing timeouts
against a healthy api should raise it here with a superseding ADR rather than
removing the argument, because `timeout=None` is the failure mode this bullet
exists to prevent.

## ADR 8 — The board's only clock is its own render time

**Status:** accepted (T-010, 2026-09-27).

**Context.** Two requirements in the same spec look like they contradict each
other. *Two kinds of stale* says the board must not derive whether a lock is
stale — the api gains `lock_expired` and the board renders it — while *Required
behaviour* says "timestamps are relative in the cell, absolute in the `title`",
and `4m ago` is `now - heartbeat`. Read carelessly, the second forbids the
first, and an implementation that obeys one can be reviewed against the other.

**Decision.** The board computes an **age** and never a **judgement**. It
subtracts an ISO timestamp the api gave it from its own render time to render
`4m ago`, with the full ISO string in the cell's `title`, for every timestamp on
every screen — `heartbeat`, a card's `created_at`, an event's time. It never
compares any of those against `heartbeat_ttl_seconds`, never loads the
dispatcher's config, and never imports `dispatcher.context_transfer`: whether a
lock is expired arrives as `lock_expired` on the task row and is rendered as the
three values it has — expired, live, and null for a task with no heartbeat,
which is a task nobody is holding rather than a task whose holder is fine. A
timestamp the board cannot parse renders verbatim with no relative part, because
an unparseable heartbeat is the api's news to report and not the board's to
hide.

**Consequences.** `docker exec`-free answers to "is this run dead" keep coming
from one place, and the page stamp (`as of <HH:MM:SS>`, every page, next to a
control that re-navigates) is what makes a relative age honest — a
five-minute-old page saying `1m ago` is legible only because the page says when
it was rendered. The rule for a reviewer is mechanical: a `timedelta` against a
timestamp is allowed in the board, a comparison against a TTL is not, and
`grep -rni "ttl" observability/board/` returning nothing is the check. If a
later phase adds a second derived fact — a queue position, a projected cooldown
— it goes in the api beside `lock_expired`, for the reason this ADR exists
rather than as a matter of taste.

## ADR 9 — `/debt` reads one project, named in the board's environment

**Status:** accepted (T-010, 2026-09-27).

**Context.** The spec's screen list says `/debt` shows "the debt index for the
configured project", and its configuration block for the board names four
environment variables, none of which is a project. `/api/debt` requires
`project` wherever `projects_root` holds more than one checkout, and the host
this runs on holds two (`ia-harness` and `scratch`, enumerated by the api's own
400 in `docs/ROADMAP.md`'s T-009 verification row). So the screen as written
answers 400 on the only harness there is, and the board has no `config.yaml` to
read a default from — by design, since it mounts nothing.

**Decision.** `BOARD_PROJECT` is an optional fifth environment variable on the
board service in both compose files, and the board sends it as `?project=` when
it is set and omits the parameter when it is not. A board on a harness with one
checkout therefore needs no configuration and the api picks; a board on a
harness with several is told which one, in the one place the board's
configuration lives. The board route also accepts `/debt?project=<slug>`, which
overrides the variable for one navigation — the api's 400 and 404 both enumerate
the available slugs, so the Error state renders a usable list and an operator
can reach the other project without editing compose. This adds a query
parameter and not a route: the spec's "no other route exists" holds.

**Consequences.** The board's environment is five variables, one more than the
spec's block writes out, and `README.md` and `scripts/configure.sh` do not
prompt for it — an unset `BOARD_PROJECT` is the right default for a
single-project harness and a visible, self-describing error for the other kind.
A later phase that gives the board more than one project's worth of screens
should read the project list from a new `/api/projects` rather than widening
this variable, because a board that shows two projects is a navigation problem
and not a configuration one.

## ADR 10 — A heartbeat the api cannot parse is `lock_expired: null`, not a warning

**Status:** accepted (T-010, 2026-09-27).

**Context.** `lock_expired` is specified as `true`, `false`, or `null` when there
is no heartbeat to judge, and `dispatcher/context_transfer.py:is_lock_expired`
computes it by `datetime.fromisoformat(task.heartbeat)`. A third case exists on
disk that neither the spec nor the task names: a heartbeat that is a string and
not a timestamp. `fromisoformat` raises `ValueError` on `yesterday afternoon`,
and a timestamp with no UTC offset parses into a naive datetime that raises
`TypeError` when subtracted from an aware `now`. Both exception families are in
`observability/api/app.py:_UNREADABLE`, so the default behaviour — deriving the
field inside the guard that wraps `_task` — is that the whole task row vanishes
from `/api/tasks` and is replaced by `<path>: unreadable task file`. Every
writer in `dispatcher/` writes UTC with an offset, so this only happens to a
hand-edited file, which is precisely the file an operator is looking at the board
to understand.

**Decision.** `_lock_expired` catches `TypeError` and `ValueError` around the
call and answers `null`, the value the field already has for "there is nothing to
judge". The row survives with its `status`, its `owner` and the unparseable
`heartbeat` string verbatim, and no warning is emitted: nothing about the *file*
is unreadable. `is_lock_expired` is unchanged — the dispatcher's callers want the
boolean it returns, and a dispatcher that swallowed a malformed heartbeat would
silently never release a lock.

**Consequences.** Three values mean three things on the board and one of them is
now two things: `null` is either a task nobody holds or a task whose heartbeat is
not a timestamp, and the board tells them apart the only way it can — by
rendering the `heartbeat` field beside the label, which ADR 8 already has it do
verbatim when the string will not parse. That is the cost, and it is paid to keep
a legible row instead of a warning about a file that is fine. If a later phase
wants the two distinguished in the data rather than on the screen, the place for
it is a separate field on the task row beside `lock_expired`, for the reason
ADR 8 gives: derived facts are the api's.

## ADR 11 — `limit` above the maximum is clamped and reported, not rejected

**Status:** accepted (out of cycle, 2026-09-28).

**Context.** ADR 5 shipped `/api/events` with an explicit "no cap" and said so in
its own Consequences: "`warnings` is unbounded in length, and so is `limit`:
neither has a cap". T-009 round 3 then added a range check that rejects an
integer SQLite cannot bind, which is a different thing — it rejects a *malformed*
parameter, and `9223372036854775806` is not malformed. `docs/debt/T-009-D2.md`
was opened for the rest, and asked for the number to be decided "together with
Phase 3's tail window in `docs/plans/board.md`". Phase 3 is not written, so this
takes the second half of that instruction seriously and not the first.

**Decision.** `MAX_EVENT_LIMIT = 1000` clamps `limit`, and the clamp appends a
warning that names the number asked for, the number answered, and `?since=` as
the way to walk the rest. It is a 200: the request is legal, it just asked for
more than one answer carries. The range check stays in front of it and keeps
answering 400, so the rule is two-sided and says which side a caller is on — out
of range is a malformed parameter, in range but large is a legal request that was
trimmed. The clamp is also carried into the missing-database return, because a
database that is not there does not make it untrue that the caller asked for more
than it could have had. `MAX_WARNINGS = 100` caps the list the same envelope
carries, spending its last slot on a tally of what was left out, so nothing is
dropped silently. Both live in `_envelope`'s funnel rather than in the four views
that build warning lists.

1000 comes from the board, which is the only client: `EVENTS_LIMIT = 200` in
`observability/board/app.py` is the largest window it opens, and five times it
leaves room for a client that has not been written without leaving the number
meaningless. Nothing in this harness is clamped today, which is the point — a cap
that bites on first contact was chosen wrong. Phase 3's tail is not constrained
by it either: that tail is bounded by `since` (`id > last`), so its `limit` is a
burst ceiling and not a page size, and the number Phase 3 eventually picks for its
window is a different number from this one.

**Consequences.** This widens ADR 5's rule that `warnings` names files rather
than describing states: a clamp warning names a *request parameter*, which is
neither a file nor a state, and it is the first warning in this service that is
about the caller rather than about the harness. The rule that survives is the one
underneath — a warning says what the caller did not get and where to look — and
that is what the widening is for. The cost ADR 5 left is now paid, and its
Consequences paragraph is false as written; it stays, because this file is
append-only and a superseded sentence with a number pointing past it is worth
more than a rewritten one. Two numbers now need revisiting when Phase 3 lands: if
its tail wants more than 1000 rows in one burst it must raise this constant or
page with `since`, and the second is the honest answer.
