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

## ADR 12 — The live tail terminates on the board, and its frames carry rendered HTML

**Status:** accepted (Phase 3, 2026-09-28).

**Context.** `docs/plans/board.md` "Phase 3" asks for a `/events` page that
updates itself instead of waiting to be reloaded. Two things about this harness
decide most of the shape before any code is written. `charter.md` C-7 says the
board is Flask and Jinja, not Next.js, so there is no bundler, no `static/` and
no component to hydrate. ADR 7 says the api's bearer token lives in the board's
environment and reaches no template, and
`test_the_token_travels_to_the_api_and_never_to_the_page` enforces it.

**Decision.** The stream terminates on the board — `GET /events/stream`, same
Basic realm as every other route — and not on the api. Three reasons, and the
first alone decides it:

- `EventSource` cannot set an `Authorization` header. A browser dialling the api
  directly would have to be handed the api's bearer token, which is the one
  thing ADR 7's boundary exists to prevent. Same-origin, the browser replays the
  board's own credentials and holds nothing new.
- The api's contract is one bounded read per call (ADR 7). A long-lived response
  is a different contract, and adopting it for one endpoint would make "every
  api call is bounded" a sentence with an exception in it.
- `GET /api/events?since=&limit=&source_app=` already *is* the query the loop
  needs. Nothing had to be added to the api, which is the checkable form of this
  decision: **Phase 3 changes no file under `observability/api/`.**

What a frame carries follows from ADR 8's "render, do not re-derive": the `data:`
is the `<tr>` produced by the same Jinja macro `/events` renders its table with,
one `data:` line per line of it, because a bare newline inside a `data:` value
ends the frame. A row that arrived on the stream and a row that arrived in the
page are therefore the same markup by construction rather than by agreement, and
the escaping is the template's, not a second implementation of it in JavaScript.
The only markup the script writes is `insertAdjacentHTML("afterbegin", e.data)`
with the table trimmed back to `EVENTS_LIMIT`; everything else it puts on the
page goes in through `textContent`.

`id:` is the event's own id — the same integer `?since=` takes — so the browser
returns it as `Last-Event-ID` and a reconnect resumes exactly where it stopped,
with no clock on either side. `retry:` is sent once at the top and `EventSource`
owns the reconnect loop; the page reimplements neither. Configuration is
nothing: five module constants, per ADR 9's reasoning that this service mounts no
`config.yaml`.

One cost is paid explicitly. Werkzeug's dev server is threaded, so one open tail
is one held thread for as long as a tab is open. `MAX_TAIL_CLIENTS = 4` with a
503 and a sentence over the cap, because a cap that fails open is a thread leak;
a refused connection holds no slot, and a finished one gives its slot back in a
`finally`.

**Consequences.** This is the board's first `<script>`, inline in `events.html`.
The line that holds is that it is *one* element and nothing else: delete it and
Phase 2's page is still there, table and filter and stamp, which is a test and
not a promise. The moment a second script wants to exist, the question of a
`static/` directory is open again, and that is the point to answer it rather
than growing the inline block.

`test_the_token_travels_to_the_api_and_never_to_the_page` asserts `"<script"
not in html` on route `/` only, and `/events` now has one. The narrower
assertions — one `EventSource`, no `fetch(`, no `XMLHttpRequest` — are what
carry the same guarantee on the page that gained a script, and they are the ones
to keep honest if the script ever grows.

The stream states the page shows — `connecting`, `live`, `retrying`, `not live`,
the last two carrying the server's stamp — sit *over* an already-resolved region
and do not reopen ADR 6's "three states per region, no Loading": the table is
rendered before the connection is attempted, and the stream's state is about the
tail, not about the data. The mark is built by the script rather than served in
the HTML, so a browser that never runs it shows no stream state at all, which is
the truth.

That distinction is what the keepalive is for — a dead tail and a quiet harness
are the same picture, and a comment frame every fifteen seconds is what makes
`live` a fact. It fires no event in the browser; its real job is the socket
write, which is what fails when the client is gone.

## ADR 13 — What the tail does with a full poll and with a failed one

**Status:** accepted (Phase 3, 2026-09-28).

**Context.** `docs/plans/board.md` "Phase 3" sketches the loop, and the
implementation departs from that sketch twice. Both departures are about the
same thing — what the stream does when the poll's answer is not simply "here are
the new rows" — and both are the kind a later task would undo without knowing it
was a decision.

**Decision, first departure.** A poll that comes back exactly at `limit` emits an
`event: gap` frame, and that frame carries **no `id:`** and is emitted **before**
the batch it describes.

`ORDER BY id DESC LIMIT n` drops the *oldest* of the window, so a full answer may
have skipped rows between `since` and what it returned. Announced rather than
swallowed: the alternative is being asked in a month why an event that certainly
happened is not in the tail. No `id:` because an id on a frame that is not a row
would commit `Last-Event-ID` past rows this connection has not sent yet — a drop
immediately after it would lose them. Before the batch because the gap is older
than everything in the batch, and the page prepends, so emitting it after would
draw it above rows that predate it.

**Decision, second departure.** A poll that fails ends the stream with one
`event: stalled` frame carrying `Region.error` verbatim, rather than retrying
inside the loop.

The board already has words for a failed api call — `_fetch` writes them, ADR 5
pins their shape — and a stream that retried internally would need a second
vocabulary for the same failure, plus its own backoff next to the one
`EventSource` already implements. Ending it hands the retry to the browser,
which is where `retry:` put it (ADR 12). A failed poll is therefore **not** a
warning: warnings are the api's, forwarded once per connection by exact text,
and a failure is a state change the page renders as `retrying`.

**Consequences.** `stalled` is the one frame that means the connection is over,
and the page renders it in the same notice list as a `gap` or a `warning` — the
state change is not its job, because the close that follows fires `onerror` and
the mark goes to `retrying` or `not live` on its own. That is deliberate: the
three frames all say "something to read", and only the connection says whether
the tail is alive. The browser then reconnects anyway, which is correct — if the
api came back, the next connection resumes from `Last-Event-ID` with no repeats;
if it did not, the page sits in `retrying`, which is true.

The honest hole is that neither departure covers a server that is *hung* rather
than failing. The socket stays open, the keepalive keeps being written into a
connection nobody is answering on the far end, and the page keeps saying `live`.
Nothing here detects that, and nothing should until it bites: the fix, if it
does, is a named `tick` frame the page can time out on, which costs a state
machine in the browser that this phase deliberately does not have.

`GAP_SENTENCE` counts the rows that arrived, not the ones that did not: it says
a poll came back full, never how many events were missed, because what `LIMIT`
dropped is not knowable from the answer. That is a real limit of the
announcement, and the reason it ends in "Reload for the full window." rather
than in a number — a reload re-renders the table from the api, which is the one
thing that certainly holds the rows the tail skipped.

## ADR 14 — ADR 6's Loading row comes back in `front/`, not in the Jinja board

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** ADR 6 left a forward conditional: "If C-7 is reversed at Phase 5,
the Loading row comes back with the client that can be in it, and the skeleton
is written then against the same table." That conditional has now fired, earlier
than Phase 5 and by a different route than the one it imagined. C-8 did not
reverse C-7 because a screen finally needed a client; it reversed it because a
client already existed — `front/`, a TanStack Start app generated outside this
repo. The conditional does not say *where* the skeleton gets written, and the
reading nearest to hand is the wrong one, because ADR 6 is about the Jinja
board and a reader arrives at the sentence from there.

**Decision.** The Loading row comes back in `front/` and nowhere else.
`observability/board/` gets no skeleton markup, no placeholder row and no
client-side fetch of the api, for the rest of its life. C-8 retires that package
once the front serves the same screens against the real api, so a skeleton added
to it now is work paid for twice — once to write, once to delete. The row is
already rendered on the other side: `front/src/routes/pool.tsx` answers
`accounts.isLoading` with "Reading pool…", which is exactly the state ADR 6
said only a client that resolves fetches after paint can occupy.

**Consequences.** ADR 6 is not edited, and neither are 7 or 12 where they cite
C-7. They were accurate on the day they were written and their reasoning is what
made the reversal cheap; this entry is the second half of that record, not a
correction to it. The state table in `docs/plans/board.md` stays one row wider
than `observability/board/` and becomes exactly as wide as `front/`, which is
the surface it was written for — so a reviewer reading the plan against the
Jinja templates still finds a row with no markup behind it, and the answer is no
longer "a ruling is reversible" but "the ruling was reversed and the row moved".

Two edges stay open and belong to the front plan rather than here. The table
makes the *region* the unit and the front's Loading is currently per page —
`pool.tsx` returns one line of text where the table asks for the grid with each
card in its own state — so parity on this row is not the same as the row
existing. And until parity, C-8 keeps the Jinja board as the tie-breaking
reference precisely because it has run against the real api; a reference that
grows states the front does not share stops being able to break ties, which is
the second reason not to retrofit it.

## ADR 15 — The console authenticates as a service, from its server half

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** `front/AGENTS.md` carries a project rule: "No authentication of any
kind: the console runs on a private network behind one trusted operator." The
api it is about to read disagrees five times — `/api/tasks`,
`/api/tasks/<task_id>`, `/api/accounts`, `/api/events` and `/api/debt` are each
`@requires_auth`. `front/src/lib/api/client.ts` sends no header on any of its
twelve functions and does not need one yet, because every one of them resolves
from `src/lib/api/mock/fixtures.ts`.

Two repairs suggest themselves and both are wrong. Dropping `@requires_auth` to
make the rule true deletes the only thing standing between the pool's state and
anything else that can reach port 8789. Putting the operator's Basic
credentials in the client ships a human's password to every tab, and
`observability/auth.py` says why that is worse than it looks: the api checks a
*hash*, so a client authenticating with Basic needs the plaintext sitting beside
the hash in `.env` and makes the hash decorative.

ADR 7 already settled the shape for the Jinja board. The api accepts two
credentials on one realm: a human sends Basic, a service sends
`Authorization: Bearer <API_TOKEN>`, and the bearer path exists precisely so a
service can call the api without holding a human's plaintext. The console is a
service by that definition, and C-8 bought it somewhere to keep a secret — it is
server-rendered, and `front/src/server.ts` exports `fetch(request, env, ctx)` on
the Node side, where no browser can read a variable.

**Decision.** The console presents the bearer token from its server half and
never from the browser. Browser code calls the console's own origin; the
console's server forwards to `observability/api/` with the header ADR 7 defines.
`@requires_auth` stays on all five routes, and Basic stays the human's path —
curl, and the Jinja board until it retires — untouched.

Three things follow that are each easy to undo by accident. Vite inlines every
`import.meta.env.VITE_*` into the client bundle, so the token is read without
that prefix, on the server, or it is published. The forward makes every browser
fetch same-origin, so `observability/api/` needs no CORS and goes on not knowing
a browser exists. And a console started with no token configured fails at
startup instead of falling through to unauthenticated requests: the api already
closes the bearer path on a falsy token rather than opening it, and the console
owes the same refusal on its own side.

**Consequences.** `front/AGENTS.md`'s third project rule stops being true the
moment `client.ts` stops reading fixtures, and the task that wires it rewrites
the rule — that file describes the console's own code and is not a charter, so
no amendment is involved. The forward is new code: the only files under
`front/src/lib/api/` are `client.ts`, `types.ts`, `queries.ts` and
`mock/fixtures.ts`, there is no `src/routes/api/`, and `src/server.ts` wraps
TanStack's SSR entry without proxying anything. A console with no server half —
a static build behind nginx — cannot satisfy this ADR at all, which is a second
reason C-8 accepted the Node runtime it accepted. Rotation stays what ADR 7 made
it, one line in `docker/compose/.env` and a restart, with one more service
reading it.

## ADR 16 — `client.ts` unwraps `data` and carries `warnings` out with it

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** Every 200 the read api answers is `{"data": …, "warnings": […]}`.
`observability/api/app.py` states the reason in one line — "Nothing is dropped
silently, which is the entire reason the envelope is not a bare array" — and
ADR 5 spends a page bounding it: an unreadable task file is `data: null` plus a
warning naming the file, an account whose state will not parse stays in the list
with its unreadable half nulled plus a warning, a missing events database is
`[]` plus a warning, and none of them is a 500.

`client.ts` is typed `Promise<Task[]>`, `Promise<Account[]>`, and so on, one
function per endpoint. The cheapest way to satisfy those signatures against the
real api is `return (await res.json()).data`. It type-checks, every screen
renders, and it converts the api's central design property into nothing: a
console that quietly drops `warnings` turns "nothing is dropped silently" into a
page that looks complete and is not, which is the failure mode ADR 5 exists to
prevent and the hardest one for an operator to catch.

**Decision.** Every function in `client.ts` returns the rows *and* the warnings
the envelope carried, and nothing between the fetch and the render discards
them. `data` is unwrapped — screens keep taking arrays and objects, not
envelopes — but the warnings ride alongside, through `queries.ts` and into the
component, and every screen that lists rows has somewhere to show them.

A warning is not an error and does not take a region to its error state. ADR 6
already named this case: three states per region plus a **partial**, which is
rows together with a non-empty `warnings`. The console inherits that vocabulary
rather than inventing a fourth state, and inherits ADR 5's reading of the two
empties with it — `data === null` and `data === []` both mean "nothing to show,
read the warnings", and the error path belongs to non-200s alone.

The guards ADR 7 put in the board's one boundary function are the console's too:
`application/json` before `.json()`, because Flask answers its own 404 and 405
in HTML; then a shallow check that `data` is present and `warnings` is a list of
strings. A non-200 is `{"error": "…"}` with no `warnings` key at all, so the
error path reads a different shape and must not look for one.

**Consequences.** `ApiError` stays for non-200s and grows no warning-carrying
sibling, because a warning is a successful read. The keys and intervals in
`queries.ts` do not change; what a `queryFn` resolves to does. Every fixture in
`mock/fixtures.ts` gains the envelope's second half or the mock stops being a
rehearsal for the api. And a screen with no place to render a warning is a
screen that is not finished — a review criterion this entry hands the revisor,
not a style note.

## ADR 17 — Renames belong to the console; absent fields are not the api's to invent

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** `front/src/lib/api/types.ts` and the api disagree in three places,
and the disagreements are not the same kind of thing.

`/api/accounts` serves `name`, `container`, `state`, `current_task` and
`rate_limited_at`. `Account` asks for `current_task_id`, and for `usage_pct`,
`is_primary`, `rank` and `heartbeat`. Exactly one field already agrees, and it
agrees exactly: the four names `state_machine.AccountState` writes are the four
the union in `types.ts` lists.

`/api/debt` serves the index's five columns — `id`, `what`, `where`, `fix`,
`card` — plus `resolved`, a boolean. `DebtEntry` asks for `state`, one of
declared, accepted, rejected or blocking, and for `task_id`.

`/api/tasks` serves `TaskFile`'s fields plus `lock_expired`. `Task` asks for
`depends_on`, `body` and `debt[]`, and has nowhere to put `kanban_issue_id`,
`resolved_debt`, `card` or `lock_expired`.

The api is not free to close these by growing whatever a fixture imagined.
`_task`'s docstring says the fields are `TaskFile`'s and it invents none but
`lock_expired`, and the module says a parser this service needs and does not
have is one to make reachable in `dispatcher/`, not one to rewrite here.

**Decision.** Sort each disagreement by whether the value exists in the harness
at all, and the answer follows from the sort.

**Served under another name: the console adapts, in `client.ts`.**
`current_task` becomes `current_task_id` in one line. No route is renamed to
match a fixture, because the route's name is the harness's word for the thing
and the fixture's is a guess about it.

**Present in the harness, absent from the route: the api grows it, its own
way.** `is_primary` is `AccountConfig.is_primary` and `/api/accounts` already
iterates `cfg.accounts` to build every row, so serving it is a line and no new
source. `depends_on` and the task body are `TaskFile`'s, admitted under the same
rule that admits every other field `_task` serves.

**Present nowhere: the console drops it.** `rank` is that. Nothing in
`dispatcher/config.py` ranks accounts, and the comment on `is_primary` says why
there is nothing to rank — it is a sort key, not a kind of account; the pool is
ordered, not partitioned, and the primary is simply the one the picker reaches
last. `pool.tsx` sorts on `a.rank - b.rank` and then titles the result "pool
priority — workers first, primary last", which is `is_primary` as a sort key and
needs no second field to express.

**The same word for a different object: neither side moves until the object is
named.** The front's `heartbeat` sits on an account and is labelled "lock
heartbeat"; in the harness a heartbeat is `TaskFile`'s, a property of the lock
on a task, which `/api/tasks` already serves and `_lock_expired` already
interprets. An account's heartbeat is therefore the heartbeat of the task that
account currently owns — a join across two routes the console already calls, not
a field for `/api/accounts` to grow. `DebtEntry.state` is the same trap.
`/api/debt`'s `resolved` is best-effort by construction: `_RESOLVED_RE` matches
a `**resolved` prefix in the **what** cell, and `dispatcher/debt.py` says the
flag it produces is offered and never used to hide a row. The four-state
lifecycle the console draws — declared, accepted, rejected, blocking — is a
debt's life inside one task's handoffs, where the implementador declares, the
revisor rules and the auditor files; it is not a column of the index, and asking
`/api/debt` for it asks the index to be a state machine it is not.

**Derived, not served.** Debt ids are written `T-010-D1`: the task is the
prefix, so `task_id` is a split in `client.ts`, and `Task.debt[]` is the same
split read the other way, a filter over `/api/debt` rather than a field on the
task.

**Served and unused stays served.** `kanban_issue_id`, `resolved_debt`, `card`
and `lock_expired` have no home in `types.ts` today and lose nothing by waiting
there; a client ignoring a field costs the api nothing. `lock_expired` in
particular is the field the Tasks screen should be reading rather than deciding
staleness for itself, for the reason ADR 18 gives.

**Consequences.** Of the ten fields the console asks for and does not get, one
is a rename, three are api work, three are a join or a split the console does
for itself, two are deletions from the front, and one — `usage_pct` — is
ADR 18's. A later task that finds a field missing reaches this list before
reaching for the api: the api not serving something is not by itself a reason
for it to start.

## ADR 18 — The console's thresholds are served, not compiled in

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** `front/src/lib/api/types.ts` ends with five exported constants and
four of them are this harness's configuration, copied as literals:
`USAGE_THRESHOLD = 90` is `quota_threshold_pct`, `COOLDOWN_S = 1800` is
`quota_cooldown_seconds`, `PRIMARY_RESERVE = 60` is `reserve_pct`, and
`HEARTBEAT_STALE_S = 120` is `heartbeat_ttl_seconds`. All four agree with the
defaults in `dispatcher/config.py` today, which is exactly what makes them
dangerous: an operator who raises `quota_threshold_pct` changes what the
dispatcher does and nothing about what the console draws, and the console goes
on labelling a marker "local threshold 90%" over a pool that parks at 95.
`config.py` also enforces a relation between two of them — `reserve_pct` may not
exceed `quota_threshold_pct`, because a higher reserve would make the primary
*more* available than a worker — and nothing in `types.ts` knows that relation
exists to preserve it.

`HEARTBEAT_STALE_S` is the worst of the four and the easiest to fix, because the
console does not need the number at all. `/api/tasks` already applies
`cfg.heartbeat_ttl_seconds` server-side and serves the answer as `lock_expired`.
A console that re-derives staleness from its own 120 is computing, on stale
input, a value it was handed.

`usage_pct` is the same problem seen from the other end. `Account.usage_pct`
drives a gauge, a parked banner and a tone, and no route serves it: the harness
learns an account's usage from a `/usage` probe run inside a container at
dispatch time, and `state_machine` persists the *outcome* — a state, a current
task, a `rate_limited_at` stamp — never the number.

**Decision.** The three thresholds that are genuinely numbers the console must
show are served, on `/api/accounts`, beside the pool they describe: a console
that has read the pool has already paid for them, and they are facts about the
configured pool rather than about any one account. `types.ts` keeps
`LEARNING_TABLE_CAP`, which is the console's own layout decision and nobody
else's. `HEARTBEAT_STALE_S` is deleted rather than served: the console reads
`lock_expired`.

`usage_pct` is not served until something persists it. The probe result is a
measurement with a time on it, and a number with no stamp, on a screen that
refreshes every 2.5 seconds, is worse than no number — it will be read as
current and will not be. Until `state_machine` records the probe and when it
ran, the pool screen renders the state it does have: the four-state enum it
already agrees with, `rate_limited_at`, and the cooldown countdown derived from
it. The gauge is not part of the parity set C-8 measures the Jinja board's
retirement against. Persisting the probe is a task of its own and this entry
does not design it.

**Consequences.** Raising a threshold becomes one edit in `config.yaml` and a
restart, and the console follows without a rebuild. The relation `config.py`
enforces stays enforced in the one place that can enforce it. The cooldown
countdown is the constant with a consumer today, and it is live the moment
`/api/accounts` carries it. The pool screen loses its most prominent widget
until usage is persisted, which is a visible regression against the Lovable mock
and a deliberate one: that gauge was reading a fixture, and the same gauge
reading nothing is the same picture with none of the meaning.

## ADR 19 — No console screen ships against a fixture

**Status:** accepted (C-8 documentation sweep, 2026-09-29).

**Context.** `front/src/lib/api/queries.ts` declares nine queries and
`client.ts` twelve functions. Five have no route behind them — `listPhases`,
`listLearnings`, `listActions`, `listThreads` and `enqueueAction` — plus
`pingActionBackend` and `setActionBackendDown`, which exist to let the mock
pretend a backend went away. Every one resolves from a fixture, and a fixture is
indistinguishable from a working screen right up until the day it is wired.

Two of the five are not "the api has not got round to it yet". `enqueueAction`
is a write, and `observability/api/app.py` forecloses it in three sentences:
every route is a `GET`, it writes nothing anywhere, and there is no docker
socket on this service — `dispatcher/docker_exec.py` arrives as an import and is
never called. C-8 already ruled that actions ride a queue and a worker and never
a socket. `listThreads` is the chat dock, which C-8 explicitly did not decide
because it collides with C-1: the conversation is the only interactive one.

**Decision.** A screen joins the parity set only when every query it renders is
backed by a route, and an unbacked screen says so on itself rather than showing
a fixture. Per endpoint:

`/api/phases` is a route this api may grow. The dispatcher already persists a
structured handoff per task and role under the hive's `handoffs` directory, and
`dispatcher/context_transfer.py` already reads one back — a `GET` over files on
a `:ro` mount, with the parser where the module docstring says a parser belongs.

`/api/learnings` is the same shape over the learnings tree, which
`dispatcher/learnings.py` already parses into entries with frontmatter and a
status.

`/api/actions` and `enqueueAction` never land here. They belong to a write
surface that does not exist yet, with its own credential and its own audit
trail, and until it does the Queue screen's action half is outside the parity
set. `setActionBackendDown` does not survive the wiring at all: a control that
fakes a failure is a fixture wearing a button.

`/api/threads` stays undecided, as C-8 left it. It is outside the parity set
until a charter ruling puts it in, and this entry does not pre-empt that ruling
by building half of it first.

A screen whose route is not ready renders an empty state that names what is
missing. The distinction is the whole point of the rule: an operator must never
be unable to tell a quiet harness from an unwired console.

**Consequences.** The parity C-8 retires `observability/board/` against is
nameable today, which C-8 needs it to be. The Jinja board serves an index, a
task page, a debt page and an events tail, and every route behind them —
`/api/tasks`, `/api/tasks/<task_id>`, `/api/debt`, `/api/events`,
`/api/accounts` — already exists, so parity waits on neither new route. Phases
and Learnings join the console when their routes land; Queue's action half and
the chat dock are out, and say so on screen until a decision brings them in.

## ADR 20 — The pool's thresholds are columns on every account row, not a sibling of `data`

**Status:** accepted (T-011, 2026-10-01). Narrows ADR 18, which decided *that* they are served and left the shape open; nothing in ADR 18 is reversed.

**Context.** ADR 18 says the three thresholds are served "on `/api/accounts`,
beside the pool they describe", and *beside* reads two ways: a fourth key next to
`data` and `warnings`, or three more columns inside every row. They are facts
about the configured pool and not about any one account, so the sibling is the
shape the sentence suggests, and it is the one that cannot be built.

Two things close it, and both are properties of code that is already running.

The envelope has exactly two keys and two tests say so for every route —
`tests/observability/test_api.py:test_every_route_answers_the_same_envelope` and
its bearer twin `test_every_route_accepts_the_configured_bearer_token`, both
asserting `set(resp.get_json()) == {"data", "warnings"}`. That is ADR 5 as
widened by ADR 11, and it is an invariant of the whole service rather than a convention of one
route: a third top-level key is not a slot this envelope has.

Nor can `data` become an object with the list inside it.
`observability/board/app.py:index` and `:task` both call
`fetch("/api/accounts", keys=ACCOUNT_KEYS)` with `many=True`, and
`_envelope_problem` checks `data` is a list before checking each row; an object
there blanks the pool table on two of the four screens the board serves.
`docs/charter.md` C-8 keeps that board the tie-breaking reference until the
console reaches parity, so breaking it to improve a shape is not available to a
task in this tier. The same function checks rows with
`missing = [key for key in keys if key not in row]` — a subset check, so rows may
grow keys freely.

**Decision.** `quota_threshold_pct`, `reserve_pct` and `quota_cooldown_seconds`
are keys on every account row, beside `is_primary`, and `data` stays a list of
rows. They are read off the `Config` `create_app` already loaded, never by a
second `load_config` inside the view, for the reason `_task`'s docstring gives
about the expiry window. They are built before the `try` that reads the state
file, with `name`, `container` and `is_primary`: a state file that will not parse
nulls what the state file says and nothing the config says.

The repetition is forced by the envelope, not chosen for the console's
convenience, and that is the honest reading of why this entry exists. What it
buys is real, though: `dispatcher/dispatcher.py:_threshold_for` holds the primary
to `reserve_pct` and a worker to `quota_threshold_pct`, so the ceiling that
actually governs an account is already per-account, and a row carrying both
numbers next to its own `is_primary` lets a reader work out which one applies
without knowing a relation ADR 18 says nothing on the console side knows.

**Consequences.** Three numbers travel once per account in a response the pool
screen polls; with the two accounts this harness runs, and the five the console's
fixtures imagine, that is not a size worth a shape nobody can serve. A pool-wide
fact that is genuinely *not* derivable per row — a count, a queue depth — has no
home in this envelope and would need an ADR to put one there; this entry
deliberately does not open that door for a value that is per-row already.
`docs/plans/board.md`'s *The shapes* list no longer enumerates every key of an
Account row, and stays as written: it is Phase 1's specification, and this entry
is the record of what grew after it.

## ADR 21 — The task body is served on the detail route only

**Status:** accepted (T-011, 2026-10-01). Narrows ADR 17, which admitted the body with `depends_on` and named `/api/tasks` for both; the admission stands and the route for one of them does not.

**Context.** `context_transfer.TaskFile.body` is not the task as the operator
asked for it — that is `description`, and the two are separate fields for the
reason the comment in `dispatcher/context_transfer.py` gives. `body` is the
running record: `handoff()` appends every phase's summary to it, so it only
grows, for the life of a task id. In this repository on 2026-10-01 `.hive/tasks/`
is 211 KB over ten cards and the largest single card is 55 KB.

`/api/tasks` is the list the console's Board screen and the Jinja board's index
both poll. Serving the body from `_task`, which both task routes share, would put
every card's whole history in every one of those responses to feed a field only
the detail screen reads.

**Decision.** `_task` grows `depends_on` and not `body`. The
`/api/tasks/<task_id>` view adds `body` to the row after it calls `_task`, inside
the same `try` that wraps the shaping and the `app.json.dumps` round. The two
task routes therefore answer different key sets, which is new for this service:
`/api/tasks` answers ten keys and `/api/tasks/<task_id>` those ten plus `body`.

This is not an invitation to sort every future field by size. The body is a
distinct case on two counts — it is unbounded and it grows monotonically, and no
screen that lists rows renders it. A field that is merely large has a cap or a
parameter available to it; this one has a route.

**Consequences.** A client cannot assume the two task routes are
interchangeable. The Jinja board already models exactly that distinction with
`TASK_KEYS` and `TASK_DETAIL_KEYS`, and the console does it in `client.ts` under ADR 16, so neither consumer pays
for it. The asymmetry is pinned from both sides in
`tests/observability/test_api.py`:
`test_tasks_reports_the_fields_the_task_file_carries` asserts a list row whole
over a fixture that has a body, and
`test_one_task_by_id_carries_the_running_body_and_the_list_route_does_not`
asserts the difference is exactly `body`. Moving the field into `_task` fails
both rather than quietly doubling the size of a polled response.

One thing this entry cannot prove, named so a later reader does not look for the
test: the `try` the assignment sits inside cannot fire on it. `read_task_file`
builds `body` by splitting the file's text, so it is always a `str` and always
serialises. The placement is the module's rule — the guard wraps the use of
parsed values, `docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md` —
held to even where this one field cannot break it, because the next field added
there may.

## ADR 22 — The console consumes the collector's events; it does not re-terminate the stream

**Status:** accepted (T-012, 2026-10-01). Answers the one tier-1 decision
`docs/plans/front.md` *Decisions this tier's tasks make* binds to the Live
tail's task, and the only one in that tier with design in it.

**Context.** ADR 12 terminates the live tail on the Jinja board: the board holds
the SSE connection and its frames carry *rendered HTML*, which is a Jinja answer
to a Jinja problem. A console that renders React cannot consume rendered HTML, so
the choice does not inherit. Two shapes were available. The console could become
a second terminator — its server half holding a stream, re-reading the collector
or standing in front of it — or it could consume what the collector already has
through `/api/events`, which is a polled `GET` by `id > since` and is already up.

**Decision.** The console consumes `/api/events`. There is no second
terminator: no SSE or WebSocket endpoint on the console's server half, no second
reader of `events.db`, and nothing re-pointed about `hooks/emit_event.py`.
`front/src/routes/tail.tsx` keeps the polling loop it has — one request every
2500ms carrying `since=<the last id it holds>` — and `listEvents` in `client.ts`
is what makes that loop correct.

**Consequences**, and the ones outside the console are why this is an ADR:

- `observability/collector/` gains no consumer and keeps exactly one writer and
  one reader of its table. The console is an HTTP client of the api and nothing
  else, which is C-8's property, and a streaming terminator in the console would
  have been the first thing to bend it.
- ADR 12's SSE stays the board's alone and retires with the board. Nothing in
  this harness grows a second streaming contract, and the HTML frames are not
  resurrected in a second consumer that cannot use them.
- `observability/collector/db.py:list_events` ends in `ORDER BY id DESC LIMIT ?`,
  and the tail's cursor is `events.at(-1)?.id` over an array it appends to. Those
  two wired together unchanged make `at(-1)` the *oldest* id of the batch: the
  cursor walks backwards and the tail re-fetches the same window forever while
  looking like it works. `listEvents` reverses each batch into ascending order
  inside `client.ts`, and `tail.tsx` is not touched for it. That makes the `DESC`
  a contract the console depends on — a later change to that order changes
  `client.ts` in the same commit, or the tail stops moving.
- `since` and `limit` compose as the route documents: when more events arrive
  between polls than `limit` allows, the newest `limit` above the cursor are
  answered and the older ones in that window are skipped. The console sends no
  `limit` at all and takes `DEFAULT_EVENT_LIMIT`, and it does not paper the skip
  over — a tail that hides a gap is worse than one that has it.
- The cost is a poll interval of latency and one request per 2.5s per open tab
  against a service that answers from SQLite. If a push surface is ever wanted,
  it is a new decision over `/api/events` — a different route, a different
  contract — and not a revival of ADR 12's frames.

## ADR 23 — The task body is bounded at its newest end, and says so

**Status:** accepted (T-012, 2026-10-01). Narrows ADR 21, which decided which
route serves the body and left its size open; closes
[`docs/debt/T-011-D1.md`](debt/T-011-D1.md).

**Context.** `/api/tasks/<task_id>` served `TaskFile.body` whole, and
`dispatcher/handoff.py`'s `handoff()` only ever appends to it: the field is the
whole history of a task id and grows for as long as that id lives — 211 KB over
ten cards in this repository on 2026-10-01, with a largest card of 55 KB. Every
other thing this service answers has a bound (`MAX_EVENT_LIMIT`, `MAX_WARNINGS`,
the size of the configured pool); this one had none. `T-011-D1` named two fixes
and left the choice to the task that renders the detail screen, because that is
the only thing that knows how much of the record the screen shows.

**Decision.** `MAX_BODY_BYTES = 128 * 1024`, applied in `_bounded_body` on the
detail route only, and the **newest** end survives. The truncation names itself
in `warnings` with the file, the real size and the cap, on `MAX_EVENT_LIMIT`'s
model under ADR 11: the request is legal and the answer is smaller than the
record, which is the envelope's job to say.

The newest end, not the earliest, because `handoff()` appends: the last phase to
run is at the end of the file and it is what a detail screen is open for. Nothing
is lost that a reader cannot reach — the operator's ask is `description`, a
separate field served whole, and the start of the running record is in the task
file. The cut is on bytes because what is bounded is a response, so it can land
inside a multibyte character: the tail is decoded with `errors="ignore"` rather
than `"replace"`, because a U+FFFD at the top of the record costs a human a
minute deciding whether the card is corrupt. The first surviving line goes with
it for the same reason, unless dropping it would leave nothing.

Not the `?body=` parameter the debt entry also offered. A parameter is an escape
hatch and not a bound: a caller that asks for everything still gets an unbounded
answer, and the entry's cost — "the largest thing this service answers, with no
warning and no cap" — survives it. It is also a contract change on
`_reject_unknown_parameters`, which passes `frozenset()` on that route, bought
for nothing. A later task that wants the earliest end, or a page through the
record, adds the parameter *on top of* this cap and keeps the default bounded.

**Consequences.** 128 KB is over twice today's largest card, so nothing in this
harness is truncated yet; the first card that is will be one of the long-lived
task ids, and the warning is how an operator finds out rather than a surprise on
a screen. Four tests pin it in `tests/observability/test_api.py` — a body under
the cap is whole and silent, a body over it keeps its newest end and warns, the
truncation starts at a whole line, and it carries no replacement character — plus
one that the list route reports nothing, because the field is still the detail
route's alone and ADR 21 is not loosened here.

The secondary cost `T-011-D1` named is bounded rather than removed: `row["body"]`
is assigned before the guard's `app.json.dumps(row)`, so the body is serialised
twice per request, and what is serialised twice is now at most `MAX_BODY_BYTES`.
The placement stays where ADR 21 put it — the guard wraps the use of a parsed
value — and moving the assignment after the round to save the second pass would
trade that rule for a sub-millisecond saving on a request a screen makes once per
view.

## ADR 24 — The bearer forward is an interception in the console's own server entry

**Status:** accepted (T-012, 2026-10-01). Narrows ADR 15, which decided *that*
the console presents the token from its server half and left the shape open;
nothing in ADR 15 is reversed.

**Context.** ADR 15 requires that browser code call the console's own origin and
that the console's server forward to `observability/api/` with ADR 7's header,
and notes the forward is new code with no file. Three shapes could carry it: a
file-based server route group under `front/src/routes/api/`, server functions
(`createServerFn`), or the Node entry the console already owns.

Two facts pick between them. `front/src/routeTree.gen.ts` is written by the
router plugin and must not be hand-edited; `front/node_modules` does not exist in
this repository and installing it is not authorised, so a task here cannot run
the generator and a shape needing an entry in that file cannot be shipped
complete. And `front/src/server.ts` already exists for exactly this layer: it
wraps TanStack's SSR entry because h3 swallows in-handler throws into a JSON 500,
which is observed behaviour and therefore evidence that this is the handler every
request passes through.

**Decision.** The forward is a closed, GET-only interception in
`front/src/server.ts`, in front of the SSR handler, over a whitelist of five
console paths that map one-to-one onto the five api routes. It is not a proxy:
anything else under `/api/` is a JSON 404 from the console, a non-GET on one of
the five is a JSON 405, and query parameters are copied by name per route — the
same posture `_reject_unknown_parameters` takes on the other side — so a
parameter the console did not mean to send cannot reach the api. `?project=` is
added to the debt call from the console's own configuration and never from the
browser. The browser's own headers are not forwarded and the api's are not
returned: what crosses is a status and a JSON body.

The console reads three names from its environment, none of them prefixed
`VITE_`, because Vite inlines every `import.meta.env.VITE_*` into the client
bundle:

- `API_BASE_URL`, defaulting to `http://api:8789` — the same name and the same
  default the Jinja board uses, so one compose network name means one thing.
- `API_TOKEN`, with no default. **Missing or empty is a startup failure**, not a
  fall-through to unauthenticated requests: the module that reads it throws at
  import, and `server.ts` imports it, so a console with no token refuses to boot
  instead of answering 401s from a screen. The asymmetry with the api — where
  ADR 7 makes the same variable optional — is deliberate: the api must still
  start on a host whose `.env` predates the token, with the bearer path shut,
  and a console with the path shut has nothing to serve.
- `CONSOLE_PROJECT`, optional, sent as `?project=` on the debt call when set and
  omitted when unset, which is ADR 9's shape. Its own name rather than the
  board's `BOARD_PROJECT`: that variable belongs to a service C-8 retires, and
  two services may legitimately be pointed at two checkouts.

Timeout is 5 seconds, ADR 7's number for ADR 7's reason — an unbounded read is
how a console with no push becomes a page that never arrives — and a forward that
cannot reach the api is a JSON 502 naming it, so `client.ts`'s content-type guard
meets JSON on every path it can take.

**Consequences.** `observability/api/` needs no CORS and goes on not knowing a
browser exists, because every browser fetch is same-origin. The CSRF middleware
in `front/src/start.ts` is untouched and its comment stays true: it filters
`handlerType === "serverFn"`, the forward is not a server function, and all five
forwarded calls are reads against a service that writes nothing. A write that
ever goes this way re-opens that sentence rather than inheriting it — which is
the one thing this entry asks a tier-3 task to notice.

`client.ts` builds same-origin relative URLs, so it must only ever be called from
the browser: a relative `fetch` on the server has no base. That holds today
because every one of the five reads is behind a `useQuery` in a component or a
`useEffect`, and React Query does not fetch during SSR without a prefetch. A
later task that moves a read into a route loader has to answer the base-URL
question there, and that is the case that would force the server-function shape
instead. It is named here so the answer is not improvised.

Rotation stays what ADR 7 made it — one line in `docker/compose/.env` and a
restart — with one more reader. The three names are documented in
`front/AGENTS.md` and `front/README.md` *Running it*, which is the list an
operator starting this service actually reads;
`docs/learnings/an-env-key-only-an-adr-names-is-invisible.md` is why they are not
left in this entry alone.

## ADR 25 — `ApiResult<T>` is what a wired read resolves to, and only a wired read carries it

**Status:** accepted (T-012, 2026-10-01). Narrows ADR 16, which decided that
`client.ts` carries `warnings` out with `data` and left the type open.

**Context.** ADR 16 says every function in `client.ts` returns the rows *and* the
warnings, and that every fixture in `mock/fixtures.ts` gains the envelope's
second half "or the mock stops being a rehearsal for the api". It was written
when all twelve functions resolved from fixtures. Eleven of the twenty-five
exports now have no route behind them, and under ADR 19 several of them never
will: `/api/actions` and `enqueueAction` are a write surface that does not exist,
`/api/threads` is undecided, and `listPhases` and `listLearnings` wait for
tier 2. A function with no route has no envelope to carry.

**Decision.** One generic type in `types.ts`:

```ts
export interface ApiResult<T> { data: T; warnings: string[] }
```

The five functions with a route behind them — `listTasks`, `getTask`,
`listAccounts`, `listEvents`, `listDebt` — resolve to it, and `data` is unwrapped
exactly as ADR 16 says: a screen takes an array or an object, never an envelope,
and the warnings ride beside it into the component. `queries.ts` does not change,
which ADR 16 already required: the keys and the intervals stay, and what a
`queryFn` resolves to moves.

The other reads keep their bare signatures and their fixtures keep no `warnings`.
That is the half of ADR 16's fixture sentence this entry takes: the five fixtures
that backed the five wired reads stop being a rehearsal for the api because they
back nothing, and a fixture for a route that does not exist cannot rehearse an
envelope that has no producer. They are kept rather than deleted — they still
type-check against the served shapes, and the deletion is a bigger edit in a tree
C-9 keeps syncing to Lovable than the dead code is worth — with a comment at the
top of `mock/fixtures.ts` saying which five reads no longer come from there.

**Consequences.** When `/api/phases` or `/api/learnings` lands, that task changes
one signature to `ApiResult<T>` and the screens that read it, and finds the
vocabulary already here. `ApiError` still belongs to non-200s alone and grows no
warning-carrying sibling, because a warning is a successful read: `data === null`
and `data === []` are both "nothing to show, read the warnings", and the shallow
guard — `application/json` before `.json()`, `data` present, `warnings` a list of
strings — is what separates the two shapes, since a non-200 is `{"error": …}`
with no `warnings` key at all.

A screen with no place to render a warning is unfinished, which ADR 16 handed the
revisor as a criterion; `docs/ui.md` *A degraded backend is a banner, not a blank
screen* now says what that place looks like, so the criterion is checkable
against a file rather than against a reading.

## ADR 26 — A task file's status carries no role, so the board's five lanes wait on `/api/phases`

**Status:** accepted (T-012, 2026-10-01). Narrows ADR 17 by sorting two fields
it did not reach; nothing in ADR 17 is reversed.

**Context.** `front/src/lib/api/types.ts` types a task's status as
`"queued" | "blocked" | "done" | in_progress:${Role}`, and
`front/src/routes/index.tsx` is built on the second half of that: it splits In
Progress into five role lanes and its own subtitle says "that split is the
point". Neither half is what the harness writes. `dispatcher/context_transfer.py`
writes `pending`, `in_progress`, `blocked` and `done` into a task file — there is
no `queued` — and the role appears in exactly one place,
`dispatcher/dispatcher.py`'s `_update_task_status(…, f"in_progress:{role}")`,
which is Kanban-only: it reaches a board card's status and never the file.
`/api/tasks` serves the file. So the role of a running phase is not a field that
route has, and the five lanes cannot be filled from it.

**Decision.** Both are ADR 17's sort applied again, and they land in different
places.

`queued` is a **rename the console does not get to make**: the served word is
`pending`, the route's name is the harness's word for the thing, and `TaskStatus`
becomes `"pending" | "in_progress" | "blocked" | "done"`. The Board's first
column is Pending.

The role of an in-progress task is **present nowhere this tier can read**, so the
five lanes collapse into one In progress column and the screen says what the
split is waiting for: `/api/phases`, tier 2 of `docs/plans/front.md`, which is
where the per-role handoff record already on disk becomes a route. That is
ADR 19's rule rather than a new one — an operator must never be unable to tell a
quiet harness from an unwired console — and it is why the lanes are not left
standing and empty, which is the same screen with none of the meaning.

`/api/phases` is **not** the only way this could be answered, and the alternative
is recorded so a later task weighs it rather than rediscovering it: every task row
already carries `card`, and on a harness with a board configured that card's
`status` is the `in_progress:<role>` string the dispatcher wrote. This harness has
no board — every `card` is `null`, which `docs/debt/README.md` says in its own
closing paragraph — so a lane fed from `card.status` would be surface that works
only on somebody else's harness and is exercised nowhere here. It is also the
wrong source on the merits: a card is a mirror the dispatcher writes to, and
`docs/plans/front.md` puts the phase record behind `/api/phases`.

**Consequences.** Nobody should close this by growing a role field on
`/api/tasks`. The file does not have it, `_task` invents nothing but
`lock_expired`, and the dispatcher would have to start writing a second status
into the card it already writes one into. The Phases task of tier 2 inherits the
lanes with the route, and `docs/plans/front.md` *What a phase row is* is already
bound to it.

Two smaller things follow and are pinned here because a later reader will
otherwise take them for defects. A status outside the four is possible — the api
serves what the file says, and `read_task_file` validates none of it — so the
Board renders a warning naming any task whose status it does not know rather than
dropping it from every column, which is what filtering on four literals does
quietly. And `splitStatus` in `front/src/lib/format.ts` keeps parsing
`in_progress:<role>`: it costs nothing, and it is the shape a card's status has
if the alternative above is ever taken.

## ADR 27 — A phase row is the handoff file a phase left, and `Phase`'s twenty-two fields sort into six served keys

**Status:** accepted (T-013, 2026-10-03). Answers *What a phase row is*, which
`docs/plans/front.md` *Decisions this tier's tasks make* binds to the Phases
task, by applying ADR 17's sort field by field. Nothing in ADR 17 is reversed;
its closing rule is what decides three of the fields below.

**Context.** `/api/phases` has one source and it is the only one:
`<hive_tasks_dir>/<task_id>/handoffs/<role>.json`, written by
`dispatcher/context_transfer.py:save_handoff` and reachable through
`handoff_path` in the same module. The envelope is exactly four keys — `role`,
`round`, `saved_at`, `handoff` — one file per role, overwritten each round, so
the file a role left is the round that was approved and there is **no history of
earlier rounds** to serve. `front/src/lib/api/types.ts` declares `Phase` with
seventeen fields and `HandoffEnvelope` with five. The gap between twenty-two and
four is this entry.

Three properties of that source decide most of the sort.

A handoff is written when a phase **ends**. Every row this route answers is a
phase that finished; `saved_at` is an end stamp and no start is recorded
anywhere. ADR 28 takes what that costs the board.

The payload is the role's own structured return, and its key set is its schema
in `dispatcher/handoff.py` — `schema_for` over `_PROPERTIES` and `_ROLE_EXTRAS`.
Observed in `.hive/tasks/T-011/` and `.hive/tasks/T-012/`: arquitecto and
auditor carry `changed debt learnings paths pending risks status subagents
verified`, the implementador adds `resolved_debt`, the revisor adds
`debt_rulings` and `verdict`. That is a per-role key set the api must not
duplicate: a second copy of a schema maintained by hand on this side is exactly
what this service's docstring refuses.

And the harness runs the phase but records almost nothing *about* the run. The
account, the model, the elapsed time, the shrink retry and the over-budget
warning are logged by the dispatcher and persisted nowhere, which is
`usage_pct`'s situation in ADR 18 — a number the harness knows at the moment it
acts on it and does not keep.

**Decision.** One row per handoff file, six keys:

```
{"id": "<task_id>:<role>", "task_id": str, "role": str,
 "round": int | null, "saved_at": ISO-8601 UTC, "handoff": {…} | null}
```

`handoff` is the payload **whole and unpromoted**, as the role returned it and
the dispatcher saved it. Nothing is lifted out of it onto the row, nothing is
projected, and a key the console does not know is not an error: the payload's
shape belongs to `dispatcher/handoff.py` and this route is a reader of it.
`handoff: null` is a real answer and not a missing file — `save_handoff` writes
the envelope even when the return would not parse, precisely so a re-run cannot
leave the previous round's payload standing as if it were this round's, and a
phase that left no parseable return is a fact the timeline shows.

The twenty-two fields, each in exactly one of ADR 17's buckets:

| Field | Bucket | What happens |
|---|---|---|
| `id` | api grows it | `<task_id>:<role>`, which *is* the file: one per role per task. The route owns the shape, so a later task that ever serves rounds adds to the id and the console does not change. |
| `task_id` | api grows it | The directory the handoffs live under. |
| `role` | api grows it | The envelope's `role`, and the file's own name. |
| `revision_round` | served under another name | The envelope's `round`. The served word wins, as `pending` did over `queued` in ADR 26: the console's field becomes `round`. |
| `started_at` | the same word for a different object | `saved_at` is when the phase **ended**. Serving one as the other would be the only lie on this route. The route serves `saved_at` under the harness's own name; the console drops `started_at` and labels the time *ended*. |
| `duration_s` | present nowhere | No start is recorded, so there is no duration. Differencing two `saved_at`s is not one either: the gap between two phases' ends holds the gates, the commit, a failover and whatever the dispatcher waited for. |
| `account` | present nowhere | Nothing in `.hive/` records which account ran a phase. `TaskFile.owner` is this bucket's trap — the account holding the task *now*, not the one that ran this phase — and `dispatcher/dispatcher.py:_commit_message` puts `Account:` in prose in a commit, which is the next row's problem. |
| `model` | present nowhere | The dispatcher passes `--model` and keeps no record. `config.yaml`'s `default_model` is the *configured* model, which is a different object from the model that ran, and `docs/plans/front.md` puts configuration reads behind the Role models task. |
| `bytes_used` | the same word for a different object | `dispatcher/handoff.py:measure` prices the model's *result* at the moment the budget is checked; the file on disk is the payload re-serialised with `indent=2`, and `subagents.backfill` and `_shrink_over_budget` sit between the two. Two numbers, one name, and the smaller consequence of confusing them is a budget bar that reads over when the phase was not. Nothing is served under this name. |
| `bytes_budget` | api grows it, and does not | `budget_for(role)` is a pure function of the role and one line away. It is not served, under ADR 17's closing rule: its only consumer was the bar whose numerator is the row above, and a denominator with no numerator is a figure nobody can read. A later task that persists a measured size serves both together. |
| `shrink_retry` | present nowhere | Logged by `_shrink_over_budget`, persisted nowhere. |
| `write_rejected` | present nowhere | And it describes something the harness *prevents* rather than records: the revisor is not in `docker_exec.WRITER_ROLES`, so its tools refuse the write and no phase-level flag is written down. |
| `gate_findings` | served under another name, on another route | `dispatcher/gates.py` renders its output as prose into the task file body under `**Dispatcher gates**`, and ADR 21 serves `body` on `/api/tasks/<task_id>` — the same screen, one region above. The console drops the chips and does not parse prose in `client.ts`; a structured gate record would need the dispatcher to keep one. |
| `commit_sha` | present nowhere | The phase commit exists on `agent/task/<task-id>` with the role, the round and the account in its message, and `.data/projects` is mounted `:ro` — but a message is not a record, this image carries no git and this service spawns nothing, and a merged-and-cleaned branch is gone. Arguing for a git read on this route is a later task's to make, with the image it costs. |
| `worktree_path` | the same word for a different object | The console's is a phase's own path. The harness's is one checkout per task that every writer role shares (`docker_exec`, `WRITER_ROLES`) and `cleanup-task` removes. |
| `learning_ids` | served under another name | `handoff.learnings` is a line per entry, naming an inbox filename inside prose rather than an id. The join to learning records needs `/api/learnings`, which is not this route's and is not built here: the detail screen's learnings region goes on naming it, per ADR 19. |
| `envelope` | the same word for a different object | `HandoffEnvelope` is a letter — from, to, a summary. The harness's handoff is a structured report. The type is replaced rather than filled, and the four rows below are what it sorted into. |
| `envelope.from_role` | served under another name | The row's `role`. |
| `envelope.to_role` | present nowhere | The record names no recipient; the cycle picks the next role and writes that in no handoff. |
| `envelope.summary` | served under another name, on another route | `dispatcher/handoff.py:body` renders each phase's prose into the task file under its own `## <role>` heading, which ADR 21 serves as `body` and the detail screen already renders. The api synthesises no prose of its own. |
| `envelope.artifacts` | served under another name | `changed` and `paths` in the payload, which carry what changed and where the detail is. |
| `envelope.open_questions` | served under another name | `pending` and `risks` in the payload. For a blocked phase `pending` is literally the missing definitions, which is the field this bucket was asking for. |

**The route.** `GET /api/phases`, one optional parameter `task_id`, on
`/api/debt`'s model. Four things about its edges:

- A `task_id` that is not a bare id is a 400, not a path that reaches
  `handoff_path`: `_is_bare_task_id` is already the lock the detail route uses.
  A bare id with no task file is `[]` plus a warning naming it, never a 404 —
  the console reaches this route with an id it read off `/api/tasks`, and a 404
  would take a region to *Broken* on a screen whose own read succeeded.
- A missing or unparseable handoff is **not** a 404 either. A 404 says the phase
  does not exist, which is a lie about a file that does. One file that will not
  parse costs a warning naming it and the rest of the rows still come back,
  which is this service's one rule.
- Rows are ordered newest `saved_at` first, and `MAX_PHASES = 100` caps the
  answer with a warning on `MAX_EVENT_LIMIT`'s model. The cap cannot bite the
  call a screen makes — one task has at most one file per role — and it is there
  so the unfiltered call does not become the second thing this service answers
  without a bound (`docs/debt/T-011-D1.md` was the first). A byte cap is the
  fix if a screen ever polls the unfiltered form; nothing does.
- The sort key is built inside the per-row guard, as a string, and the sort runs
  over strings outside it. The rule is
  `docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md` — the guard
  wraps the *use* of a parsed value, and a `saved_at` of the wrong type is a
  use — and a sort over the whole list cannot be inside a per-row `try` without
  one bad file costing every row.

**The reader.** `read_handoff` swallows `(OSError, ValueError)` and returns
`None`, which is right for its callers — they are repair paths asking what a
phase declared — and useless to a route that has to tell an operator *why* a
file did not parse. So `dispatcher/context_transfer.py` grows two functions and
`read_handoff` keeps its contract by being written in terms of one of them:
`list_handoff_roles(hive_dir, task_id)`, the roles with a file, on
`list_task_ids`' model; and `read_handoff_envelope(hive_dir, task_id, role)`,
the envelope whole, raising `OSError` and `ValueError` for the api to catch and
name. The parser stays in `dispatcher/`, where this module's docstring says a
parser belongs, and there is one definition of where a handoff lives.

**Consequences.** Of the twenty-two fields, three are served, seven are served
under another name (two of them on `/api/tasks/<task_id>`, which the same
screen already reads), four are a word for a different object, seven are
present nowhere, and one is a line the api could write and does not.
`PhaseRow` in `front/src/routes/tasks.$taskId.tsx` loses the byte bar, the
gate chips, the account, the model, the duration, the commit and the worktree,
and gains the payload: a status, a verdict where there is one, and the role's
own lists. That is a thinner row than the fixture drew and a true one.

Five of those fields have one fix between them and it is not on this route:
the dispatcher persisting what it already knows while it runs a phase — the
account, the model, a start stamp, the shrink retry. That reaches four of the
seven *present nowhere* rows, `duration_s`, `account`, `model` and
`shrink_retry`, and `started_at` out of the bucket above them, which is a real
start and not `saved_at` renamed. It reaches neither `commit_sha`, which needs
a git read on a branch that may already be gone, nor `envelope.to_role`, which
names a recipient the harness never writes down; and `write_rejected` has
nothing to persist, because the harness prevents that write rather than
recording it. That is ADR 18's `usage_pct` paragraph again, and like it, this
entry does not design it. A later task that finds a field missing reaches this
table before reaching for the api, which is what ADR 17's closing rule asks of
it: the api not serving something is not by itself a reason for it to start.

Two things this route deliberately does not do. It does not pretend to a history
of rounds — one file per role is the record, and a timeline that showed round 1
beside round 3 would be inventing the one. And it does not join to learnings:
`handoff.learnings` travels as the role wrote it, and the region that wants
records on the other end of those filenames still says it is waiting for
`/api/learnings`.

## ADR 28 — The board's In-progress lanes are not coming from `/api/phases`, because no file says which phase is running

**Status:** accepted (T-013, 2026-10-03). Narrows ADR 26, which collapsed the
five lanes into one column and named `/api/phases` as what they wait for.
Nothing in ADR 26 is reversed: its reasoning is what makes this entry short, and
its two refusals still hold.

**Context.** ADR 26 found that a task file's `status` carries no role, that
`card.status` is `null` on this harness and the wrong source on the merits, and
that `/api/tasks` must not grow a role field. It passed the lanes to this task
with the route, on the reading that a per-phase record on disk is a per-phase
record of what is *happening*.

It is not. `save_handoff` runs after a phase returns, so the newest file for a
task names the phase that **finished**. A task whose implementador is running
right now has an arquitecto file and nothing else, and five lanes fed from that
put a running task under the role that already handed it on. That is a screen
that looks live and is one phase behind, which is the one outcome worse than an
empty lane: an operator cannot tell it from a correct one.

Three candidates for the fact itself were weighed and all three fail.
`dispatcher/dispatcher.py:_update_task_status` writes `in_progress:<role>` to a
board card and never to the file, and ADR 26 already refused the card. The
dispatcher's heartbeat loop knows the role while it runs and writes only a
timestamp into the task file. And `/api/phases` answers ends, not starts, by
ADR 27's first paragraph.

**Decision.** In progress stays one column, and the Board stops naming
`/api/phases` as what the split waits for, because the route has landed and does
not answer the question. The banner names the missing *record* instead: which
role is running is not written down anywhere, the dispatcher knows it while it
runs and persists nothing, and a live lane needs that to change.

What the Board already shows in its place is named with it, so the banner is not
only a refusal: the card carries `owner` — the account holding the task — and
the api's own `lock_expired` judgement over its heartbeat, which together say a
phase is running and who is paying for it. Which phase it was last is on the
task detail, where the timeline this route feeds names the last role that ended.

`front/src/lib/format.ts:splitStatus` stays callerless and unchanged for the
reason ADR 25 and ADR 26 both give. `/api/phases` keeps its optional `task_id`
rather than requiring one, and the console calls it with an id: nothing polls
the unfiltered form, which is why ADR 27 caps it rather than tuning it.

**Consequences.** `docs/ui.md` *A region with no route says which route, and
when* grows the case this entry creates — a region waiting on a fact nobody
records, which names the record and who would have to write it rather than a
route — because naming a route that exists and does not answer sends an operator
to the wrong place, and `front/src/components/console/chat-panel.tsx` holds the
same sentence about the same fact.

The fix, if a later task wants lanes, is one line of dispatcher state and not a
route: the phase loop already holds the role, the account and the start time,
and writing them where `state_machine` or the task file can be read would serve
the lanes and five of ADR 27's rows at once — `duration_s`, `account`, `model`
and `shrink_retry` out of *present nowhere*, and `started_at` out of the bucket
above them. It does not reach `commit_sha` or `envelope.to_role`, which need
something a state line is not. That makes the lanes a dispatcher task,
ADR 18's `usage_pct` for the fifth time, and it is not this one.

## ADR 29 — A served value of the wrong *shape* is named where it would have been drawn, not rendered, dropped or called absent

**Status:** accepted (T-013, by hand after the merge, 2026-10-04). Refines
`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`, which
prescribes "the `—` sentinel or an `Absent` rather than a throw" and is the
reason this console guards at the render at all. Nothing in the learning is
reversed except that one word.

**Context.** T-013's auditor filed one instance of the failure as debt:
`PhaseList` tests `lines.length === 0` and then calls `lines.map`, so a
non-empty **string** passes the test and throws at the map. Closing it by hand
found nine more of the same shape — `handoff.paths` mapped at the *call site*,
before the component that would guard it is entered; `paths` items that are not
`{path, holds}` rendering `undefined — undefined`; array items that are not
strings throwing "Objects are not valid as a React child", once from inside an
`<svg>`; both `PhasePill` call sites handing `handoff.status` and
`handoff.verdict` straight to `{value}`; and a board card counting `.length`
over a string, which is the only one of the ten that does not throw and the
worse for it.

None of this needs a bad actor. `dispatcher/context_transfer.py` reads
`depends_on=fm.get("depends_on", [])` straight off frontmatter with no
coercion and `observability/api/app.py` serves it verbatim, so one missing dash
in a task file is a string in the board's dep chip and in both of the task
detail's dependency renders. `string[]` in `front/src/lib/api/types.ts` is an
annotation over that file, not a check of it.

The remedy the learning names does not survive contact with `docs/ui.md`
*Absent, empty and broken are three different things*, which is binding and
says broken "never degrades into an empty state". An `Absent` here says the
harness recorded nothing when the harness recorded something unreadable, which
is that rule one level down: not a region gone quiet because a read failed, but
a slot gone quiet because a value cannot be read. That entry has no fourth word
for this — it has one for a warning, and says so.

**Decision.** A new primitive, `Malformed`, names the key, the type that
arrived and the type the key means — `paths — a string, not a list` — at
`Absent`'s size, in `Absent`'s slot, in danger tone. It names the **type and
never the value**: what arrived is unvalidated file content of unbounded
length, and the key plus the type already says which file to open.

One malformed *item* does not cost its list. A value that is a list renders
every good item and a `Malformed` in the bad one's place; only a value that is
not a list at all replaces the region. Silent filtering is refused for the same
reason `Absent` is.

**Consequences.** This diverges on purpose from
`dispatcher/handoff.py:_lines` and `_pairs`, which drop an item of the wrong
type and answer `[]`. The divergence is the point rather than an
inconsistency: those two build a prompt for a model, where a malformed line is
noise worth dropping, and this one answers an operator asking what the role
returned, where it is the answer.

The guard stays at the render, which the learning already rules and which ADR
10 and ADR 27 both support — a value this harness cannot judge is news to
report, not something a reader hides. `types.ts` is **not** weakened to
`unknown`: the annotation is still what a well-formed file holds and still
catches the console's own mistakes, so these guards are conditions TypeScript
considers redundant. Nothing flags them today, because
`front/eslint.config.js` loads `tseslint.configs.recommended` rather than
`recommendedTypeChecked` and so has no `no-unnecessary-condition`. A later task
that turns type-aware linting on inherits this entry as the reason the guards
stay.

What this does **not** do is validate the harness's own output anywhere. The
fix for `depends_on` arriving as a string is a dispatcher that coerces it or a
gate that refuses the file, and neither is this entry; until one lands, the
console says what it got.

`docs/ui.md` gains *A value of the wrong shape is named, not rendered and not
dropped*, beside the entry it refines.

## ADR 30 — A component moves out of a route file when a test cannot otherwise reach it, not only when a second screen draws it

**Status:** accepted (T-013, by hand after the merge, 2026-10-04). The rule it
adds to is the one `front/README.md` states for `components/console/`: a thing
lives there once a second screen needs it, and until then it stays in the
screen that draws it, where it is read beside its only caller.

**Context.** `docs/debt/T-013-D1.md` *Fix* step 2 asks for "one render per
api-served value with a junk value in it", and after ADR 29 the renders worth
that test are `PhaseList` and `PhasePill` — both defined inside
`front/src/routes/tasks.$taskId.tsx`, both drawn by one screen. A test file
beside them cannot exist: `@tanstack/router-plugin` scans every file under
`src/routes/` and turns it into a route, and the knob that would excuse a
`.test.tsx` — `routeFileIgnorePattern`, which
`node_modules/@tanstack/router-generator/dist/esm/config.js:24` declares with
**no default** — is set in `vite.config.ts`, which is Lovable-generated and
which `docs/charter.md` C-9 keeps off-limits to a hand edit. Importing the
route module from a test elsewhere does not help either: it would pull the
whole screen, its queries and its router imports in to reach two functions.

**Decision.** Being untestable where it sits is sufficient reason to promote a
component into `front/src/components/console/`, with the same standing as a
second screen needing it. `PhasePill`, `PhaseList` and `asPathLine` move to
`front/src/components/console/payload.tsx`; `phaseTone` and `asLine` move with
them and stay module-local, because nothing outside the file calls them. The
route keeps every other renderer it has.

**Consequences.** The directory stops being a reliable answer to "how many
screens use this": a reader who finds a component there may be looking at one
promoted for a test, and the file's own header docblock is where that is said
— `payload.tsx` names this ADR and the generator as its reason. The cost is
paid once per component and it buys the only executable check this directory
has ever had.

The alternative was a `front/tests/` tree outside `src/`, which keeps the
components where they are and costs an import path that climbs out of the
screen's directory. It was rejected for the reason the promotion is cheap: the
two functions are a self-contained renderer over one api value, which is what
`components/console/` is for, and the test then sits beside what it tests the
way every other test in this repo does.

This does **not** say a test justifies any extraction. The thing promoted is
still a component with its own meaning — the renderer of one served value. A
helper that only makes sense inside its screen is tested through the screen,
once the board's `TaskCard` has a router harness to be rendered in.

## ADR 31 — `tests-in-diff` pairs a changed file with a test in the same language, and only the ungrouped suffixes take any test

**Status:** accepted (T-013, by hand after the merge, 2026-10-04). It replaces
the rule `dispatcher/gates.py` shipped with, under which one test anywhere in
a diff cleared the whole diff. The blast radius is two files: nothing outside
`gates.py` reads an `ASK` finding.

**Context.** `docs/debt/T-013-D1.md` *Fix* step 3 asks that
`dispatcher/gates.py` "learn to see `front/`", on the premise that
`tests-in-diff` "reasons about Python test files only". Running the gate's own
helpers disproves the premise: `.ts` and `.tsx` were already in
`_CODE_SUFFIXES`, and `payload.test.tsx` has the stem `payload.test`, which
`is_test` matches on its `endswith("test")` branch. The gate has seen `front/`
since the day it was written.

What it could not do was keep looking after it found one test. The body was
`if any(is_test(p) for p in changed): return []`, so a diff holding a Python
change, its Python test, and a `front/src/**` edit with no console test at all
produced no finding at all. That is not a hypothetical shape — it is the shape
T-012 and T-013 both shipped, and T-012's implementador wrote it down at the
time: "the `tests-in-diff` gate falls silent on the Python changes alone, and
that silence is not TypeScript coverage" (`docs/implementations/T-012.md`).

**Decision.** A test covers a language, not a diff. `_SUFFIX_GROUPS` maps each
code suffix to the language a test for it would have to be written in —
Python, JavaScript/TypeScript, Go, Rust, Ruby, JVM, Swift, C#, PHP, C/C++ —
and `group_of` answers which group a path falls in. The gate collects the
groups of the tests in the diff and goes on asking about every changed file
whose group is not among them.

`.sh` and `.sql` are deliberately *ungrouped*: they stay code, and any test in
the diff clears them. The census behind that is this repo's own — seven shell
files, every one an entrypoint or an operator script, and one `.sql`, a schema
— so pairing them per language would ask the same question every round and get
the same one-line answer, and a gate that is always answered the same way is a
gate a role learns to skip. A project that does test its shell names the test
`test_*`/`*_test` like everything else, which lands the test in no group
either, so the pairing stays symmetric.

**Consequences.** The finding stays one finding, not one per unpaired
language. `needs_answer` is an `any(...)` over the report, so N findings cost
exactly one `--resume` either way, and `render()` repeats the gate name on
every bullet, which would read as noise. Its text changed with the rule: "code
changed and no test in the same language did" is true in both cases, because a
diff with no test at all certainly has none in the right language, and it
names what would satisfy the gate.

A language the dict does not list behaves exactly as it did before — no group,
cleared by any test. Adding one is a line in `_SUFFIX_GROUPS`, and the
question to ask when adding it is not what the suffix is but whether a test in
that language is a thing this harness can run.

This says nothing about *running* the console's suite. The test gate executes
the single `test:` command in `docs/README.md`'s frontmatter, the agent images
have no `bun`, and `front/node_modules` is ignored and absent from a fresh
worktree — so that half of `T-013-D1` step 3 stays a human's row under
`docs/ROADMAP.md` *Deferred gates*. What a gate should do with each of the
three `front/` scripts once it can run them is ruled in `docs/charter.md`
C-10, not here.

## ADR 32 — the Flask board is retired, and C-8's parity is the four screens it had

**Status:** accepted (by hand on `main`, out of cycle — the deletion on
2026-10-04, this entry and the prose realignment on 2026-10-05). It spends the
retirement clause of [`docs/charter.md`](charter.md) C-8. Nothing else stops
running: no dispatcher module, no api route and no file under `front/` changes
behaviour, and the only test deleted is the one that tested the deleted code.

**Context.** C-8 set the condition and named the four screens: the Flask board
"is retired in the same task that closes the last of them" — tasks, a task's
detail, debt and the events tail. All four read the real api now, and V0.6c and
V0.6d walked them against the running stack on 2026-10-04, both PASS.

What stood in the way of reading the condition as met was the console's own
prose. `front/README.md` and `docs/plans/front.md` both said parity was "not
reached, and out of reach by construction", on one ground: the detail screen's
learnings region wants `/api/learnings`, a tier-2 route that does not exist, and
it names that route on screen rather than showing a fixture. Taken literally
that made the retirement wait on tier 2, which C-8 does not say and ADR 19's
enumeration of parity does not contain.

**Decision.** Two things.

First, **parity is measured against the board's four screens, not against every
region the console draws.** The board never had a learnings region. A screen the
console added cannot keep alive the service the console replaced, so
`/api/learnings` is tier 2 arriving late, not tier 1 left open. ADR 19's list is
the whole test — the four screens rendered against the real routes, warnings on
every screen that lists rows, no fixture behind any query those four make, the
bearer forward in place — and it is met.

Second, **the board is gone.** Deleted: `observability/board/` entire — its
717-line `app.py`, its `__init__.py`, its Dockerfile and its six templates —
`tests/observability/test_board.py`, and the `board:` service from both
`docker/compose/docker-compose.yml` and `docker-compose.coolify.yml`.
`tests/integration/test_compose_invariants.py` grows the inverse of the
assertion it used to make, `test_the_flask_board_service_is_gone_from_both_files`,
so a re-added service fails a test instead of a build. The `markupsafe>=2.0` pin
leaves `pyproject.toml` and `docker/agent/Dockerfile` with it: the board was the
one service that imported `escape` directly, nothing left in this project
renders a template, and Flask still brings MarkupSafe in through Jinja. The
`observability.board` package-data entry goes too.

**Consequences.** The tie-breaking reference is `git log`. C-8 made the Flask
board right wherever the two implementations disagreed about what a screen
should say, "because it has run"; from here the console is the only
implementation, and a disagreement is a bug rather than a tie.

[`docs/charter.md`](charter.md) is **not** edited. C-8's sentence stays true as
history, and the charter is the human's file — no role may edit it, and neither
may this one. A C-11 recording the retirement is a ruling a human writes or
declines; this entry is not it and does not stand in for it.

ROADMAP V0.6b cannot be re-run. It passed on 2026-09-27 against the board, and
nothing answers on 8790 now, so its block is left as written: the passing row is
what it attests to, and V0.6c and V0.6d cover those screens from here.

The `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD_HASH` pair outlives a second
service that gave it a prefix — `observability/api/` reads it for the human
realm of ADR 7, and the names stay for the reason `observability/api/app.py`
gives at the call site: renaming them means editing a `.env` that exists on a
running host to buy a spelling. `BOARD_PROJECT` in
a host's `docker/compose/.env` is inert: nothing reads it any more, the console
carries its own `CONSOLE_PROJECT` (ADR 24), and an operator may leave the line
where it is.

Phases 2 and 3 of [`docs/plans/board.md`](plans/board.md) have no
implementation. Both sections keep their specs, under a marker saying the Flask
half is retired, because those specs are what `front/` was built against and
what the four screens still owe. Phase 1, the api, is untouched; Phases 4–6 were
already `front/`'s to build.

Earlier entries that speak of the board in the present tense — ADR 6 to ADR 13
on its screens and its tail, ADR 14 keeping the Loading row out of it, ADR 24
saying of `BOARD_PROJECT` that it "belongs to a service C-8 retires" — are
not rewritten. They are read through this one, which is how this file has always
worked: an entry is narrowed by a later entry, never edited.

## ADR 33 — a dropped learning entry is moved, not unlinked

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-05). It takes
the first of the two halves of the fix in
[`docs/debt/T-012-D1.md`](debt/T-012-D1.md) and leaves the second open. Two
files change behaviour, `dispatcher/learnings.py` and the `merge-task` arm of
`dispatcher/cli.py`. Nothing else does: `delete()` behind
`dispatch learnings --drop` still unlinks, because that verb means "this entry
was wrong"; the automatic call site in `dispatcher/dispatcher.py` is untouched;
and no reader learns a new directory.

**Context.** `drop_promoted` deleted every unreviewed project-scope entry in
`.hive/learnings/inbox/` belonging to a task whose branch had just merged. Its
premise — the entry is in the project's `docs/learnings/` now, committed, so
the inbox copy only charges every later phase for a row the repo already holds
— holds at the automatic call site, which runs after
`run_phase(ctx, "auditor", final=True, …)` returned and after the task moved to
`done`. It does not hold at `merge-task`, the verb for landing a branch by
hand, which by definition lands a branch the cycle did not land. T-012's
auditor never ran; the three entries its implementador and revisor had filed
were unlinked with nothing to recover them from, `.gitignore:6` ignoring
`.hive/` and the inbox living outside every worktree. Each deletion was logged
by the entry's ref — a filename — and the "other copy" that ref pointed at was
a file nobody ever wrote.

**Decision.** The entry **moves** to `dropped/`, a third directory beside
`inbox/` and `harness/` under the hive's `learnings/` root, by `os.replace`. A
rename and not a reserialisation: unlike `promote`, nothing about the entry
changes, so what a phase wrote is byte-for-byte what a human recovers.

Three things about it are deliberate.

`dropped/` is **not enumerated** by `read_inbox`, `read_harness` or `read_all`,
and nothing is to teach them. That is what makes the move free: the drop exists
for prompt cost, and a directory no reader walks is in no prompt. A later
reader that walks it puts the cost back and undoes this entry.

It is created in `drop_promoted` and not in `ensure_dirs`. `ensure_dirs` opens
the directories a phase is told to write into, so that a phase sent somewhere
finds the somewhere already there; no phase writes here, and an empty directory
beside the two a phase *is* told to use is one more thing for a phase to wonder
about.

A name collision does not overwrite. Two tasks are free to file the same
filename, and `_free_path` parks the second beside the first as `-2`, `-3`, …,
because an overwrite there is exactly the permanent loss this directory exists
to stop.

`drop_promoted` keeps its name and its return value — the refs stay
inbox-relative — so both call sites' output keeps its shape and the debt row,
the two call sites and the cli all go on saying "drop". What `merge-task`
prints changes: it names the destination rather than only the count, because
that is the call site where the premise can be wrong, and the operator reading
that line is the one who may have to go looking.

**Consequences.** Nothing reclaims `dropped/`. It grows by one file per entry
per hand-landed merge, in a root-owned directory outside every worktree, and a
human empties it. No role is told it exists.

The second half of `T-012-D1` stays open and the row stays open with it:
`drop_promoted` still drops whether or not the promoting role ran, and the gate
the row proposes — no `## auditor` section in the task file, no drop — is
unimplemented. This entry makes that half cheaper to decline rather than
unnecessary. The loss is recoverable now; it is still silent, and an operator
has to know to look.

`tests/dispatcher/test_learnings.py` carries both halves of the claim: the move
test asserts the files left `inbox/`, arrived in `dropped/` with their text
intact and are invisible to `read_all`, and a second test files the same
filename from two tasks and asserts the first one's copy survives.

The two passages in `README.md` that said the dispatcher deletes the entries
once the branch merges are realigned in the same pass.
[`docs/debt/T-012-D1.md`](debt/T-012-D1.md) is **not** rewritten: its
blockquote of the old docstring is the record of what the premise said at
filing time, and an implementation note is a historical record here, not a
status page.

## ADR 34 — the drop waits for the promoting role's section in the task card, and every unknown keeps the entries

**Status:** accepted (T-014, 2026-10-05). Narrows ADR 33, whose closing
paragraphs left the second half of [`docs/debt/T-012-D1.md`](debt/T-012-D1.md)
open and said only that this entry's half had become cheaper to decline.
Nothing in ADR 33 is reversed: the entry still *moves* to `dropped/` when it
moves at all, `drop_promoted` keeps the name and the `list[str]` of
inbox-relative refs that entry froze, and no reader is taught that directory's
name. Three symbols are added — `context_transfer.has_phase_section`,
`learnings.droppable` (the filter that was inline in `drop_promoted`) and
`learnings.PROMOTING_ROLE` — and no config key, flag or verb is.

**Context.** `drop_promoted` takes every unreviewed project-scope inbox entry
belonging to a task whose branch just merged out of `inbox/`, on the premise
that the entry is in that project's `docs/learnings/` now. ADR 33 established
where the premise holds — the automatic call site, which runs after
`run_phase(ctx, "auditor", final=True, …)` returned — and where it does not:
`merge-task`, the verb for landing a branch the cycle did not land. What ADR 33
did *not* do is read whether the auditor ran. T-012's three entries were gone
before that, unlinked, which is what filed the row in the first place; what ADR
33 bought is that the next ones are recoverable. What it left is the silence,
and the silence is what this row has stayed open for.

A gate needs evidence that a *promotion* happened, and the harness records that
nowhere directly. The entries' destination is `docs/learnings/` on the merged
branch, inside a project checkout the dispatcher reaches only through
`docker exec`; `drop_promoted` runs in the dispatcher process with two string
arguments and touches nothing but `.hive/`. So the question is which local
record stands in for "the promoting role ran", and that is a choice about
proxies, not an implementation detail — which is why it is here rather than in
a comment.

**Decision.** The drop is gated on the task card carrying a section for the
promoting role, and every unknown resolves towards keeping the entries. Six
things make that up, and they are separate arguments.

*The evidence is the card's body.* `context_transfer.task_file_path(hive_dir,
task_id)` — `{hive_dir}/{task_id}.md` — is already in hand at both call sites,
which pass `cfg.hive_tasks_dir`: the check costs one `open` and no new
argument. The card is also the one record written by the **dispatcher** rather
than by the role it describes. `context_transfer.handoff` appends the block
`dispatcher/handoff.py:body` renders under the label the phase loop builds, and
`run_phase` returns `None` before building that label when a fatal phase did not
finish — so a section is there because a phase returned, and a role can neither
forge one nor forget to write one. It outlives everything else, too: the card
survives the worktree, the container and the process, and `cleanup-task` does
not touch it. That is as close to "the auditor finished" as any local file gets.

The heading is matched anchored at the start of a line, with exactly two
hashes, in the *parsed body* and not in the file's text, and
`## auditor (round 2)` counts as well as `## auditor`, because
`run-phase --round` labels a hand-resumed phase that way. A substring search for
the role's name is not the test: a role writes prose into its `**Detail**`, and
that prose lands inside a section. Reading the parsed body rather than the text
is the same guard one level up — a card's frontmatter carries the task
description, and a description is free to quote the heading it is asking for.
T-014's own card does exactly that.

*`list_handoff_roles` is not the evidence, although it looks like it.* Its own
docstring names the gap: a task that predates `save_handoff`, or one no phase
has finished, has no `handoffs/` directory at all. Those cards still carry their
`## <role>` sections, so a gate reading the directory would answer "no auditor"
forever for the oldest tasks in the hive and keep their entries in the inbox for
good — prompt cost with no end, which is the cost the drop exists to pay off.
That is a different bug in the same place, not a safer default. (Handoffs
surviving `cleanup-task` is not the objection; `cleanup-task` leaves the scratch
directory alone. The tasks that predate the writer are.)

*The gate lives inside `drop_promoted`, not at the `merge-task` call site.*
There are two callers today, `dispatcher/cli.py`'s `merge-task` arm and
`dispatcher/dispatcher.py`'s automatic merge, and "do not move an entry unless
the role that files it ran" is the function's promise about the files it moves
rather than one caller's good manners. A third caller — Phase 4's action queue
in [`docs/plans/board.md`](plans/board.md) is the one with a name — would
otherwise have to remember a rule it cannot see. It costs the automatic path
nothing: there the gate is a no-op by construction, because the only line that
reaches the drop is after the auditor returned, and that returning phase is
what appended the section. That is a claim about control flow, so
`tests/dispatcher/test_dispatcher.py` drives the automatic path end to end and
asserts the entries still move — a later refactor that reorders those lines
fails a test instead of surviving a comment.

*There is no way to force the drop past the gate.* A flag for that is a switch
for losing the entries again, which is the whole content of this row, and
nothing needs it: when the gate fires, the entries simply stay in the inbox,
where the next task sees them and where the table a phase is handed is already
capped at `MAX_ROWS`. The operator who really has to clear one already has a
narrower instrument than a flag — `dispatch learnings --drop <ref>` acts on one
ref at a time, with the entry in front of them, and means "this entry was
wrong".

*The drop and the message read one selector.* ADR 33 froze the return value, so
a gated call returns `[]` and `[]` cannot say what stayed. The filter that was
inline in `drop_promoted` — unreviewed, `scope == SCOPE_PROJECT`, and `task_id`
in `(entry.task, entry.carried_by)` — becomes `learnings.droppable(hive_dir,
task_id)`, which both the drop and `merge-task`'s message call. One selector
means the count printed can never disagree with the count that would have
moved, and a later task that changes which entries the drop claims gets the
message moved with it for free. `merge-task` prints how many entries stayed,
that they stayed in `inbox/`, and that no section for the promoting role was
found in the card it names — the same shape as ADR 33's line, which prints the
destination and not only the count — and it exits zero. A hand-landed
`merge-task` on a cycle that never reached an auditor is the normal recovery
path, not an operator error: it refuses nothing and loses nothing.

*Every unknown keeps the entries.* A card that is missing, or that will not
parse, is not evidence that a phase ran, so `has_phase_section` answers `False`
for both and the entries stay. It swallows all five of
`(OSError, TypeError, ValueError, KeyError, yaml.YAMLError)` — the tuple
[`a-never-500-read-wraps-the-use-not-the-parse`](learnings/a-never-500-read-wraps-the-use-not-the-parse.md)
names, because `read_task_file` raises every one of them — rather than widening
at the caller, which is where that learning puts a warning. `TypeError` is the
one worth naming: frontmatter that *scans* but is not a mapping subscripts a
`str` or a `list` at `fm["task_id"]`, and a hand-edited card is the normal state
of the cycles `merge-task` runs on, so dropping it from the tuple would crash
the merge on exactly the cards this gate exists for. This is the case it
excepts: the predicate's `False` already *means* "no evidence", the one caller's
`True` branch moves files, and a reader that raises here would turn a damaged
card into a crashed `merge-task`. It stays silent because
`dispatcher/context_transfer.py` has no logger, and the keep is logged by
`drop_promoted`, which has one.

**Consequences.** **The residual is accepted here, and not filed as a row.** The
proxy answers the whole-cycle case, which is the one that has fired. It does not
answer an auditor that ran, left its section, and ran out of turns before filing
every entry: that cycle still drops, and the entries still go to `dropped/`
rather than away. [`docs/debt/T-012-D1.md`](debt/T-012-D1.md) names the exact
test — check each ref against the merged tree — and that is a second
`docker exec` into the project checkout per merge, on every merge, to catch a
case no run has yet produced. The cost is refused on the merits rather than
deferred for capacity, which is why this paragraph is its home and a row in
[`docs/debt/README.md`](debt/README.md) is not: a row is a condition a later
task checks against its own work, and a row nobody intends to take reads as
pending work for ever. What a later task needs here is the argument, and an ADR
is where an argument lives. If the case does fire, this paragraph is the thing
to supersede, and the ref list the merge printed is the evidence that it did.

The `T-012-D1` row closes with this entry, and the entry file's `**Status:**`
line is the one line of it that changes — from open to resolved by this task,
naming ADR 33 and this ADR. The rest of
[`docs/debt/T-012-D1.md`](debt/T-012-D1.md) is left exactly as it stands, for
the reason ADR 33 gives: its blockquote of the old docstring is the record of
what the premise said at filing time, and an implementation note is a
historical record, not a status page. Its *Fix* section goes on describing two
halves and a proxy, which is what it was.

The two passages in `README.md` that ADR 33 realigned — the learnings bullet in
the components list, and *The dispatcher moves the entries with no model in the
loop* — are realigned again, to say the drop waits for the evidence. Neither
names `dropped/` as somewhere anyone looks, and nothing in any role's prompt
does either: ADR 33's move is free only because no reader walks that directory,
and a reader there would put the prompt cost back.

`dispatcher/learnings.py` names the promoting role once, as
`PROMOTING_ROLE = "auditor"`, and the gate and the printed message both read it.
The `role == "auditor"` branch in `duties` is left alone: it answers which role
gets the filing fragment, selecting on the role name the dispatcher passed in,
and a module constant about what counts as evidence is not what it is asking.

## ADR 35 — the pointers gate reads the docs that claim the present, plus the lines a task added to a record

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-07).

**Context.** Gate 4 landed by hand in `bc07f4e` (2026-09-23) with one rule:
every backticked path and every link under `docs/`, resolved from the repo root
and from beside the file citing it. The scope was the whole tree on purpose,
because the break it was built for is a pointer in a file nobody opened — what
moved was the thing the sentence pointed at, not the sentence.

Measured against this repo before the change: 690 unique (doc, token) citations,
and 41 of them broken over 24 distinct tokens. Three things are wrong with that
number, and they compound.

It stops at `_POINTER_LIMIT = 400` and says nothing, so 290 of the 690 were
never checked and *which* 290 depended on the order `grep -r` happened to walk
the tree. A gate whose coverage is decided by directory order is not a gate.

It reads a record as if it were a claim about the present. `docs/decisions.md`
is append-only, an implementation note describes a branch that already landed, a
learning names the file it was learned in — and this project's own ruling for a
record that disagrees with the tree is to leave the sentence alone and write the
disagreement somewhere newer
(`docs/learnings/a-plans-present-tense-claim-is-a-citation.md`, *What to do*).
So the gate was asking every task to answer for sentences no task is allowed to
edit. 15 of the 41 are exactly that.

It has no notion of a subproject root, or of a path this project deliberately
does not keep. `docs/charter.md` C-8 puts the console in `front/`, and a doc
about the console cites its files the way its own source imports them — from
`front/`, not from the repo root. Both of the 41 that landed in a doc about the
present are `.lovable/project.json`, tracked here as
`front/.lovable/project.json`.

And 24 of the 41 are in docs the layout never named at all — `docs/ROADMAP.md`,
`docs/plans/`, `docs/superpowers/` — where a gate between the implementador and
the revisor is spending the project's turns on files no role was told to write.

**Decision.**

*The layout decides the scope, and the layout is `dispatcher/project_docs.py`.*
Two tuples, each with the reason for the split in its own comment:
`PRESENT_DOCS` — the index, the charter, the architecture, the business, the
learnings index, the debt directory — is read whole, every task, whether the
task opened it or not. `RECORD_DOCS` — `docs/decisions.md`, the implementation
notes, the learnings themselves — is read only for the lines the task added.
`is_record(path)` answers which, and `PRESENT_DOCS` wins the overlap, because
`docs/learnings/README.md` sits inside the learnings directory and is the one
file in there about the present. A path under `docs/` that neither group names
is out of scope: not a claim that its citations are fine, a statement that no
role was told to write it and nobody owes an answer for it either way.

*A record's new lines come out of the diff, not off the disk.*
`_broken_pointers` takes the base it already had for the other gates and runs
`git diff --unified=0 --no-color <base> -- docs`, tracking the file off
`+++ b/<path>` and keeping every `+` line. The same `_POINTER_PATTERN` the grep
uses is compiled host-side as `_POINTER_RE` and run over those lines, so what
counts as a citation cannot depend on which of the two saw it. A diff that
fails to run logs a warning and yields nothing, which costs the record half of
the gate and leaves the present half intact.

*A record this task changed that the diff never saw is one this task created.*
A phase cannot commit, so a brand new implementation note is untracked and no
diff against the base mentions it — and every line in it is this task's own
claim. Those files join the grep's file list and are read whole. That is the
case the gate has actually caught in this repo: the V0.4 walk, an implementation
note citing a file its branch never created.

*Two excuses, consulted only about a token that already looks broken.* After
`ls` has answered, and only for the tokens it said were missing,
`_in_a_subproject` asks `git ls-files --cached` for a tracked path ending in
`/<token>`, and `_ignored` asks `git check-ignore` in batches of 100. A docs
tree with nothing wrong in it pays for neither. A tracked path that was deleted
is still a finding, because `ls` had already answered before either excuse was
asked — which is the whole point of the ordering.

*Grep is handed a file list, and its exit code is not an answer.* The scope is
now a list of paths rather than a directory walk, so an optional doc that a
project never wrote makes `grep` exit 2 with perfectly good matches on stdout.
The output is parsed regardless of the return code; a missing `docs/business.md`
is not a broken pointer.

*The limit stays at 400 and now says when it bites.* The new scope watches 151
unique citations on this tree, so the truncation is not reached, and raising the
number would only move the silence further out. A tree that does reach it gets
a log line naming the count and saying the rest went unchecked.

**Consequences.**

On this tree the gate now reports nothing, in 8 container round trips, over
exactly the six entries `PRESENT_DOCS` names. Before the change it reported 41
findings, none of which the task in front of it could act on.

**What stops being watched is a row, not a silence.** 11 of the 41 are excused
by the two new rules; the other 30, across 12 docs, are real stale citations in
files now out of scope, and they are filed as
[`T-014-D2`](debt/T-014-D2.md) with the four classes they fall into. The row
exists because the alternative — widening the scope back to the whole tree — is
the state this ADR is leaving, and because one of those classes is a gap in the
new rules themselves: the two excuses do not compose. `front/.gitignore` ignores
`.output`, so `git ls-files` cannot list the file for the suffix rule and
`check-ignore` is asked about a root-relative path it does not recognise, and a
build output cited from inside `front/` is excused by neither.

The gate stays a **note** and does not block, on `bc07f4e`'s own reasoning: a
citation can be absent on purpose. `docs/ROADMAP.md` cites
`/root/.claude/settings.json`, and the skills trash directory beside it, as
paths inside the agent image: not in this filesystem at any depth, and never
will be. The absolute form is already rejected as a token; the relative one is
in the row below, named there rather than cited, because a row lives in
`docs/debt/` and the gate reads that whole, every task.

Eight tests in `tests/dispatcher/test_gates.py`. The fake answers
`git diff --unified=0`, `git ls-files --cached` and `git check-ignore` as three
separate commands, and a `_scope()` helper asserts on the grep invocation
itself, because which docs are in scope *is* the ruling and a fake that answers
the same whatever it is asked would prove nothing about it. The four pointer
fixtures that predate this change are untouched and still pass.

## ADR 36 — a task fast-forwards its project's checkout before it starts, with a credential mounted from the host

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-07).

**Context.** The dispatcher clones a project once, into `projects_root`, and
then never looks at its remote again. Measured before this change: no
`git fetch`, no `git pull` and no `git push` anywhere in `dispatcher/*.py`. So
the base a task starts from is whatever the clone was last left at, and nothing
in a run says how old that is.

That base is load-bearing. A task's branch is cut by `_add_writer_worktree`
with `git worktree add -b`, whose `-b` form takes no commit-ish: the branch comes
from the clone's HEAD, wherever that is. A week of commits pushed from somewhere
else, and the arquitecto plans against a tree nobody else has, the implementador
writes on top of it, and the divergence surfaces at `merge-task` or in whoever
pulls next — the furthest possible point from the decision that caused it.

It was also, until now, not merely unused but unreachable. `docker/agent/Dockerfile`
installs `git`, `ca-certificates`, `python3`, `python3-requests` and
`python3-venv` — no `ssh`, no `ssh-keygen`, no `/root/.ssh` — and this repo's own
remote is `git@github.com:…`. A `git fetch` inside an agent answers
`error: cannot run ssh: No such file or directory`. The dispatcher container
does not mount `.data/projects` at all; every project git call it makes is a
`docker exec` into an agent, which is where this one runs too.

**Decision.**

*Once per task, before the first worktree exists.* Not once per phase and not
once per role. The four roles share `agent/task/<id>` and one writer worktree,
and a fast-forward in the middle of a cycle moves the ground under a revisor
reading what the implementador wrote — the failure `create_worktree`'s docstring
already records in this codebase ("a reused checkout is exactly how the revisor
ended up reviewing a tree with none of the implementador's work in it"). Before
the first phase is the only moment where the base can still move without
anything having been cut from it, and it is enough, because that is where the
branch comes from.

*It only ever fast-forwards, and it classifies rather than reconciles.*
`update_project_branch` fetches, compares `HEAD` against `FETCH_HEAD` with
`git rev-list --left-right --count`, and answers one of four things:

| clone vs remote | status | what the cycle does |
|---|---|---|
| level | `unchanged` | starts |
| behind | `updated` — `git merge --ff-only` | starts, on a fresh base |
| ahead | `unchanged` | starts, and the local commits are left alone |
| ahead *and* behind | `diverged` | does not start, and says why |
| could not be attempted | `unavailable` | warns, and starts anyway |

Ahead is not an anomaly here, it is the ordinary state: `merge_task_branch`
merges `--no-ff` into this very checkout's `main` and nothing in this project
pushes, so every `merge-task` leaves the clone ahead until a human pushes it. A
blanket `git pull --ff-only` would therefore fail as routine rather than as a
signal, which is how a check gets ignored.

Diverged stops the task. Reconciling two histories is a merge, and a merge is a
decision this task was never given — charter C-1, one step earlier than the
phase it usually applies to. Nothing has been written yet, so stopping costs a
dispatch and saves a branch cut from a base that was about to be rewritten.

Unavailable — an `ssh://` remote, or a fetch that failed, a private remote with
no credential mounted for it among the ways a fetch can fail — is a warning and
not a stop, because the base is then exactly as stale as it was before any of
this existed. That is the behaviour every run had until today, and refusing to
run on it would turn an optional credential into a dead harness.

*The credential is a file on the host, mounted read-only into every agent, and
never an environment variable.* `docker-compose.agents.yml` bind-mounts
`.data/credentials/git-credentials` at `/run/secrets/git-credentials:ro` on both
agents, and git is pointed at it with
`git -c credential.helper=store --file=…`. Three reasons, in the order they
decide the shape:

- **Not an env var**, because `run_docker_exec` builds its command as
  `docker exec -w <dir> -e KEY=value <container> …`: anything passed as env ends
  up on an argv that any `ps` on the host can read. A mounted file is readable
  by whoever can already read the host path, and no wider.
- **On the host and not in the image**, because these containers are built to be
  thrown away. A file on the host outlives them, and one file is shared by every
  account — no per-container login, and adding a third account adds a mount, not
  a credential.
- **Read-only scope is enough**, because nothing in `dispatcher/` pushes. A
  fine-grained token with `contents: read` is the whole requirement, which is
  also what makes mounting it into an agent that runs model-written code an
  acceptable trade.

The file is optional, and a missing one is not a reason to skip the fetch. It is
probed, and `credential.helper` is named only when it is there: a public remote
over `https://` needs no credential at all, and this runs against whatever
project the operator put under `projects_root`. A private remote with nothing
mounted for it is then refused by the remote rather than refused in advance, and
reported in git's own words with the path that was looked for appended. The
difference is not academic — the first version of this code returned
`unavailable` without fetching whenever the file was absent, and the project it
was written for, `Johnny952/agent-harness`, is public: it would have skipped a
fetch that works, on every task, for a credential it never needed.

The fetch carries `GIT_TERMINAL_PROMPT=0`. Without it a missing or wrong
credential does not fail, it asks — on a terminal nobody is watching, until
`phase_timeout_seconds`. With it, the refusal above costs a second.

*Two small things that are easy to get wrong.* The credential is probed with
`test -f` and not with `path_exists`'s `test -e`, because a bind mount whose host
source does not exist gets a **directory** created for it by Docker: the
operator who brings the agents up before writing the file has an empty directory
at that path, and a directory is not something git's `store` helper can read, so
`test -e` would pass on it and name a helper pointing at it — turning a fetch
that would have worked into one that cannot. And the comparison is against
`FETCH_HEAD`, not `refs/remotes/origin/<branch>`, so what is measured is what
was just fetched rather than whatever refspec the clone happens to have
configured.

*Off by default.* `update_project_before_task: false` is the shape every config
had before this existed and the behaviour they all got. Turning it on is the
same kind of step as `merge_on_done` for a neighbouring reason: it reaches the
network and it needs something the operator has to put on the host first.
`git_credentials_path` is the second key, and both are explained in
`config.example.yaml`, which `docs/README.md` names as the place a new key gets
explained.

**Consequences.** A project whose remote is `ssh://` or `git@…` is reported as
`unavailable`, not rewritten. Rewriting a remote is a change to the operator's
own checkout that no task asked for, and the agent image has no ssh to make the
rewrite unnecessary; flipping the remote to `https://` is a one-line step the
operator takes once, next to creating the token. Until both are done, a run with
the flag on warns on every task and behaves exactly as it did before — which is
the honest reading of its state, and is why `unavailable` is a warning that
names the path it looked at.

The credential file is gitignored (`.gitignore` ignores `.data/`), the same class
as `.data/verify/dashboard-credentials`: it cannot be committed, and nothing in
the repo contains it. Creating it is the operator's, by hand, before the agents
come up — and only for a remote that asks for one: a public project needs
neither the token nor the mount.

Thirty-one tests, in `tests/dispatcher/test_docker_exec.py` and
`tests/dispatcher/test_dispatcher.py`. Four of them assert the design rather
than the behaviour, because the design is what a later edit would quietly undo:
that the probe is `test -f`, that the fetch carries `GIT_TERMINAL_PROMPT=0`,
that the credential travels as a `store` file and never as env, and that the
whole path never issues a `push` — which is the claim the read-only token rests
on.

## ADR 37 — a phase is told not to move its base, in the prompt and in the skill

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-08).

**Context.** ADR 36 settles a task's base once, before the first worktree exists,
and that is the whole of what it settles. It says nothing to the role, and there
is nothing in `dispatcher/` that could: the role is the one with a shell in the
worktree, every git call the dispatcher makes is a `docker exec` of its own, and
a `git pull` run inside a phase is invisible from this side until the diff turns
up in a review.

It is also a reasonable-looking thing for a role to do. The four phases share
`agent/task/<id>` and one writer worktree, so a tree can genuinely look older
than the handoff that describes it — that is the ordinary symptom of a base
settled before the first phase rather than at the start of this one. And the
skill all four roles are given, `systematic-debugging`, opens Phase 1 with a
step literally called "Check Recent Changes", which is the moment where reaching
for the remote looks most like following instructions.

**Decision.** Say it twice, and split the halves on the criterion this repo
already states twice: a skill is method and travels between projects, while
anything about the harness in front of the role rides in the dispatcher-composed
prompt.

*The harness half is a prompt constant.* `_REMOTE_IS_NOT_YOURS` in
`dispatcher/dispatcher.py`, appended by `_role_prompt` for every role, next to
the docs duties: the base was settled before this task's first phase and does
not move while the task runs — no `git fetch`, no `git pull`, no `git rebase`,
no merge from a remote branch, and no push — and a rebase additionally rewrites
commits the phases before it already handed on. It also spells out the half a
prohibition alone would leave the role to invent: a tree that looks older than
the work it was handed, or something missing from it, is a finding for the
handoff and not its to repair, because the dispatcher owns the remote and a base
that really did diverge ends the task.

Every role, the mapper included. The mapper reads the whole tree before anything
has been cut from it, which is where a pull looks most harmless and is the one
place where it would move the branch every later phase inherits.

*The portable half goes in the skill.* `systematic-debugging`, inside Phase 1's
"check recent changes", where it answers the step rather than sitting beside it:
`git log`, `git diff` and `git show` are reads and tell you what changed, while
fetching moves the one variable whose movement moves every other one at once, so
the reproduction before it and the one after are not the same bug. With a red
flag ("Let me pull the latest and see if it still happens") and a row in Common
Rationalizations, because those two lists are what a role actually
pattern-matches itself against, and recorded in `skills/NOTICE.md` like every
other change to a vendored skill.

*`do not push` is only in the prompt half.* Not pushing is this harness's policy
— charter C-5 — and not a method for debugging anything. `skills/README.md`
forbids role-specific content in a `SKILL.md`, and the skill travels to projects
where pushing is the job.

**Consequences.** The halves arrive at different times. The prompt is composed
per call, so that one lands on the next dispatch; the skills are baked into the
agent image at `/opt/ia-harness/skills`, so that one reaches a container only on
the next build. For whatever window that is, the roles have the half that names
this harness, which is the better half to arrive first.

Nothing enforces it. This is instruction and not a guard: a phase that fetches
anyway is not stopped, and it surfaces as ADR 36's `diverged` on a later task,
or as a review reading a diff that is partly someone else's. The detectable form
would be the worktree's `HEAD` recorded before a phase and compared after it,
which nothing does today; this is the place to start from if it ever turns out
to be needed.

The duplication is the cost. One rule in two wordings drifts apart unless
something says which half owns what, which is what the criterion above is for: a
change about how this harness runs a task edits the constant, a change about how
to investigate a bug edits the skill.

`claude plugin details` now measures `systematic-debugging` at about 4.1k tokens
on invoke, over the 3.6k ceiling `skills/README.md` documented, so that range
moved with the edit. The always-on cost is unchanged at about 53 tokens: the
frontmatter was not touched.

One test per role — `test_every_role_is_told_the_remote_is_not_its_business`,
parametrized over the four cycle roles and the mapper — asserting that the
prompt each one gets names `git fetch`, `git pull`, `git rebase`, `do not push`
and the handoff.

## ADR 38 — a plan is a record: its new lines are checked, its old ones are not

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-08).

**Context.** ADR 35 left `docs/plans/` out of the pointers gate because most of
a plan is a prediction, and filed the question as the fourth fix of
[`T-014-D2`](debt/T-014-D2.md): whether the Status paragraph each plan opens
with is enough to put the file in `PRESENT_DOCS`. The row framed it as a binary,
and predicted that the four stale pairs it had counted in `docs/plans/` would
come back into scope with a yes.

The gate offers three positions, not two, because `_broken_pointers` reads the
lines a task added only for a doc `is_record` names, and skips every other one.
A doc in neither group is not checked at all — not whole, and not the line a
task writes into it today. That is where `docs/plans/` has been since ADR 35.
A doc in `PRESENT_DOCS` is grepped whole on every task. A doc in `RECORD_DOCS`
is read for the lines the task added, and read whole only when the task created
it.

Measured with the gate's own parser: the three plans yield 4 pairs that survive
both excuses, three in the board plan and one in the console plan, and every one
names the board or the dashboard ADR 32 retired. `docs/superpowers/plans/` adds
three more of the same kind, all in the founding plan. All three files in
`docs/plans/` open with a Status paragraph; of the two under `docs/superpowers/`,
the founding plan does and the stage-0 plan does not.

**Decision.** *`docs/plans/` is a record.* `PLANS_DIR` joins `RECORD_DOCS` in
`dispatcher/project_docs.py`, beside the implementation notes, and nothing else
in the gate changes.

`PRESENT_DOCS` was the reading the row assumed, and it is the one this project
has already ruled out. A plan in there would turn each of the four pairs into a
finding on every task for ever, and the only way to clear one is to rewrite the
plan to match the tree — which
`docs/learnings/a-plans-present-tense-claim-is-a-citation.md` forbids by name in
*What to do*, because it deletes the record of what was intended. A gate that
asks every task to do what the project says nobody may do is the state ADR 35
was written to leave.

As a record, a plan is held to what the learning asks of its reader, made
mechanical: a line written today is a claim about today, and the task that wrote
it is the one that can still fix it, while a prediction that went stale is
never asked again. A plan a task creates from scratch is untracked, so the diff
never sees it and it is read whole, which is right for the same reason it is
right for a new implementation note — every line in it is that task's own.

*`docs/superpowers/` stays out.* No role is told to write there, the founding
plan's own Status calls it a record rather than a worklist, and ADR 35's reason
for leaving an unnamed doc unread holds unchanged: nobody owes an answer for it
either way.

**Consequences.** The row's prediction does not hold, and this is where that is
said: none of the four pairs comes back into scope. They stay in the plans as
written, and `T-014-D2` keeps the only account of them.

What changes is the line a task adds. A phase that updates a plan's Status
paragraph, or appends a phase to one, now has every citation in those lines
checked, where until now nothing in the directory was read at all. On this tree
the gate still reports nothing: the present half is the same six entries, and a
diff that does not touch a plan reads none of it.

Two tests in `tests/dispatcher/test_gates.py`, beside ADR 35's pair for the
implementation notes: a citation added to an existing plan is reported while the
plan itself stays out of grep's file list, and a plan written from scratch is in
that list and reported. Both fail with `PLANS_DIR` taken back out.

## ADR 39 — the test gate runs `front/`: install, then test, then lint

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-08).

**Context.** T-013 gave `front/` a `typecheck`, a `test` and a `lint`, and ADR 31
made `tests-in-diff` ask about a `front/src/**` change, but no gate ran any of the
three scripts: the frontmatter of `docs/README.md` held one string, `python3 -m
pytest`, and that suite imports nothing under `front/`. The open half of
[`T-013-D1`](debt/T-013-D1.md) and *Deferred gates* D8 of `docs/ROADMAP.md` were
blocked on the image, which had no `bun`. What a red result costs was already
ruled by `docs/charter.md` C-10: a red type check blocks, a red lint is a note.

Two facts shaped the rest. A fresh worktree has no `node_modules`, because it is
git-ignored, so something has to install before the scripts can run at all. And
`front/`'s vitest pulls in jsdom 30, which refuses any Node below 22.22.2: under
the image's `node:20-slim` every test file failed to load with `webidl.util.
markAsUncloneable is not a function`, which the gate would have read as a red
suite on every task. `bun --bun vitest run` failed too, on `EventTarget`.

**Decision.** *The index's frontmatter names the commands, and the gate runs
them in a fixed order with a fixed severity per key.*

- `dispatcher/project_docs.py:COMMAND_KEYS` grows from `build`, `test` to
  `build`, `install`, `test`, `lint`, and each key takes one command or a list.
  A list with one unusable entry makes the whole key unusable, so a suite that
  never ran cannot read as green. `MAPPED_KEYS` keeps a mapper asked for `build`
  and `test` only; `install` and `lint` are added by whoever decides a project
  needs them.
- `gates._run_tests` runs install, then every test entry, then every lint
  entry, each with the whole timeout. A failed install is a note and stops the
  install list, and the tests still run. A red test entry blocks. A red lint is a
  note under a new gate name, `lint`. Every command that failed gets its own
  `$ command` section in the one log, each cut to its own tail.
- Once an install has succeeded, only exit 127 still means the suite could not
  run. The could-not-run markers stop applying, because `tsc` reports an import a
  change broke as "Cannot find module", and that is the failure to block on.
- `docker/agent/Dockerfile` copies `bun` 1.3.12 from the official image and sets
  `BUN_INSTALL_CACHE_DIR=/data/projects/.cache/bun`. The cache sits on the same
  mount as the worktrees, so bun hardlinks out of it instead of copying, and it
  outlives a recreate.
- The base image moves from `node:20-slim` to `node:24-bookworm-slim`. Debian is
  spelled out so the comments that rely on bookworm do not move with the tag.
- `docs/README.md` records `install: cd front && bun install --frozen-lockfile`,
  three `test:` entries (`python3 -m pytest`, `cd front && bun run typecheck`,
  `cd front && bun run test`) and `lint: cd front && bun run lint`.

The install is per worktree, not shared. One `node_modules` symlinked across
worktrees would be one lockfile for every branch, and a task that bumps a
dependency would test against the old one.

**Consequences.** Measured inside a recreated `agent-cuenta1` on a throwaway
worktree, on Node 24.21.0 and bun 1.3.12:

| Step | Result | Time |
|---|---|---|
| `bun install --frozen-lockfile`, cold cache | 486 packages | 5.1 s |
| the same, warm cache | — | 0.6 s |
| `bun run typecheck` | exit 0 | 4.4 s |
| `bun run test` | exit 0, 2 files, 23 tests | 1.7 s |
| `bun run lint` | exit 1, 134 problems (124 errors, 10 warnings) | 3.2 s |
| `python3 -m pytest` | exit 0, 1125 passed, 10 skipped | 8.0 s |

`node_modules` and the cache are 441M each, but every one of the 37356 files is
a hardlink, so the two together take 454M. Each further worktree costs about
13M, not 441M. A round now spends roughly 18 s on the gate with a warm cache.

The lint was red on `main` when this was measured: 124 `prettier/prettier`
errors and 10 warnings, nine `react-refresh/only-export-components` and one
`react-hooks/exhaustive-deps`. Later the same day the 20 files carrying the
prettier errors were formatted, with nothing but layout changing, and `eslint .`
now exits zero with the ten warnings, which do not fail it. A red lint stays a
note under C-10 either way; on today's tree no task starts with one.

The gate was not run end to end on a real task here. The clone the agents work
on predates this frontmatter, so `_run_tests` there still reads one `test:`
string. Each command was run by hand in the container, and the order and
severities are pinned by seven tests in `tests/dispatcher/test_gates.py` and one
in `tests/dispatcher/test_project_docs.py`: the commands run in order, a broken
import after a clean install blocks, a missing runner is still a note after a
clean install, a failed install is a note and the suite still runs, a red lint
is a note, a lint-only index still runs, and the log holds every command that
failed.

## ADR 40 — the board's `rows` is memoised, and the `useMemo` that reads as redundant is the point

**Status:** accepted (T-015, 2026-10-08). Narrows ADR 39's tally — which counted
ten lint warnings on `main` and named one of them
`react-hooks/exhaustive-deps` — and leaves that entry as written.

**Context.** `front/src/routes/index.tsx:BoardPage` read its task rows as
`const rows = tasks.data?.data ?? []`. The `?? []` is a fresh array identity on
every render whenever the read has not answered, and `rows` is the first
dependency of the `filtered` `useMemo` below it, so
`react-hooks/exhaustive-deps` warned that the logical expression could make that
memo's dependencies change on every render and asked for the initialization to
be wrapped in a `useMemo` of its own. It was the one warning on this lint that
is not `react-refresh/only-export-components`.

The cost is not hypothetical. `useNow()` ticks at 2000 ms, so this screen
re-renders every two seconds whether anything was read or not, and the filter
ran on each tick rather than when the query, the search box or the account
filter changed.

**Decision.** *`rows` is wrapped in its own `useMemo`, keyed on `tasks.data`.*

Not inlined into `filtered`. `rows` has three other readers on the screen — the
`n of m` count, the empty state, and `unplaced`, whose rows become the per-task
warnings — and inlining would give each of them its own copy of the default.

The dependency is `tasks.data`, not `tasks.data?.data`, matching `debtByTask`
immediately above it: that memo reads `debt.data?.data ?? []` against
`[debt.data]` and draws no warning today, which is this file's own proof that
the parent path satisfies the rule. React Query holds `data` identical between
renders, so the memo recomputes when the read answers and not otherwise.

A later reader will see `useMemo(() => tasks.data?.data ?? [], [tasks.data])`
and take it for ceremony around a default — the one plausible way this gets
undone without anyone meaning to undo a decision. It is not ceremony: the memo
exists for the array's *identity*, not for the cost of `??`, and deleting it
both restores the warning and un-memoises `filtered`. The reason is on the line
in the file as well as here.

**Consequences.** `bun run lint` reports nine warnings where it reported ten,
every one of them `react-refresh/only-export-components`, and still exits zero.
`docs/README.md` *Stack* carries the new count. The screen renders exactly what
it rendered before: no state, no branch and no markup moved.

`docs/charter.md` C-10 is not triggered by this. Its condition for promoting the
lint from a note to a blocking gate is a clean tree, and nine warnings is not
clean. The nine were left alone deliberately: each is a module exporting a
component beside a non-component, which is a question about that route's shape
rather than a lint fix, and the task that takes them is the one that would meet
C-10's condition.

Nothing asserts the memo. A test of referential stability across renders tests
React rather than this screen, and the render is unchanged either way —
`front/src/routes/-index.test.tsx` mounts `TaskCard` and never mounts
`BoardPage`. The test gate's `tests-in-diff` check (ADR 31) will ask about a
`front/src/**` change arriving with no `front/` test, and this paragraph is the
answer to it.

## ADR 41 — `/api/learnings` serves the hive's entries, project-filtered, in the phase table's own order

**Status:** accepted (T-016, 2026-10-08). Builds the second of the two routes
ADR 19 sorted into "routes this api may grow" and `docs/plans/front.md` tier 2
names; `/api/phases` was the first (ADR 27). The console's half is ADR 42.

**Context.** The traps a run discovers are files under
`<hive>/learnings/{inbox,harness}/*.md`, one per entry, written by the phases
themselves and parsed by `dispatcher/learnings.py` into `Entry` — frontmatter as
the dict it was read as, plus the body's `## Symptom`, `## Why`, `## Rule` and
`## Evidence` sections. `duties()` renders a table of them into every phase's
prompt, capped at `learnings.MAX_ROWS`. Nothing outside the dispatcher could
read any of it: the console's `listLearnings` returned `mockLearnings()`, whose
`L-01` ids exist nowhere in this harness, and the console's own forward refused
`/api/learnings` as not one of the six routes it carried.

The api already mounts the whole hive: the repository's `.hive` directory,
read-only, at the path the dispatcher uses, in both compose files. So this route
needs no mount, no environment key and no change to either compose file.

**Decision.** *`GET /api/learnings` answers one row per entry in the hive's
`inbox/` and `harness/`, filtered to the project whose phases would be shown
them, in the order those phases see, with the api's judgement on which rows
reach a phase at all.*

Eight parts, each with its own reason.

**1. The parser is the dispatcher's, and three selectors are lifted, not
copied.** The route reads through `learnings.read_dir`, which is already public
and takes the root and one directory. Three functions are added to
`dispatcher/learnings.py`, each of them code that already existed inside
`table()` and `duties()`, so neither side can drift from the other:

- `eligible(entries, project)` — `applicable()` minus the refuted ones, which is
  the cut `duties` makes before `table` orders anything.
- `ordered(entries, harness)` — the sort `table()` does: confirmed first and
  refuted last, stale after fresh inside each band, `ref` as the tie-break.
- `handed(entries, harness)` — `ordered(...)` cut to `MAX_ROWS`, which is
  exactly the rows a phase's prompt carries.

`table()` is rewritten to call the last two and renders the same bytes it
rendered before; `duties()` calls `eligible`. A fourth function, `unreadable`,
answers the `.md` files in one directory that `read_dir` skipped — the
`LocalBoardClient.unreadable()` shape (ADR 3, ADR 4, `docs/debt/T-008-D2.md`),
for the same reason: a reader that silently drops what it cannot parse leaves
the one caller whose contract is "report the damage" nothing to report. It
re-reads the directory rather than widening `read_dir`'s return, because
`read_dir` has four callers inside the dispatcher that want entries and nothing
else.

**2. `?project=` is the only parameter, on `/api/debt`'s rule.** Optional where
`projects_root` holds one checkout, required where it holds several, 404 for a
slug it does not hold — `_project` unchanged. The filter is `learnings.applicable`,
which is what a phase of that project is shown: everything a human has promoted
into `harness/`, plus everything that project discovered itself. Another
project's unreviewed entry is not served, because it is not something this
project's phases can be handed, and this screen's whole subject is what they
are handed. The slug is the right key because `dispatcher/dispatcher.py` passes
the same `slug` to `duties()`, and the console's forward already sends
`CONSOLE_PROJECT` on `/api/debt`.

An entry whose `project:` frontmatter names a project with no checkout under
`projects_root` is therefore invisible to this route. That is deliberate: a
misspelled filter answering everything is the failure `_reject_unknown_parameters`
exists against, and `_project`'s 404 names the checkouts that do exist.

**3. `docs/learnings/` is not in this read.** The filed index in a project's
checkout is a different object from the hive inbox: it is permanent, committed,
per-project, written by the auditor, and read by a phase out of the repo. The
hive inbox is live, shared by every project, drained when a branch merges, and
read into the prompt by `duties()`. Serving them as one list would merge two
lifecycles into one table and make the 40-row cap meaningless.

It is also the only option that needs a second parser. `/api/debt` can read
`docs/debt/README.md` because `dispatcher/debt.py:index_rows` exists; nothing
anywhere parses `docs/learnings/README.md`, and writing that parser in
`observability/` is what this module's docstring refuses — "a parser this
service needs and does not have is one to make reachable in `dispatcher/`". A
later task that wants the filed index serves it under its own route, with its
parser in `dispatcher/`.

**4. `dropped/` and `archive/` stay invisible.** The route reads `inbox/` and
`harness/` and nothing else, because that is what `read_all` enumerates. ADR 33
made the invisibility of `dropped/` the property that keeps a wrong drop
recoverable for free, and `docs/debt/T-012-D1.md` names teaching any reader to
enumerate it as the one thing that landed half depends on. This route does not.

**5. What a row carries: ten keys.** Every one of them is rendered by the
console (ADR 42); the sort is field by field, ADR 17's rule.

| Key | Source | Why |
|---|---|---|
| `ref` | `Entry.ref` | `inbox/<slug>.md` — the pointer every prompt, every `learnings` CLI verb and every `refutes:` line already uses. It is the identity, not a surrogate, and it carries which side of the human review the entry is on |
| `task` | `Entry.task` | Which task found the trap. The provenance a reader asks for first, and the key the task detail's region joins on (ADR 42) |
| `carried_by` | `Entry.carried_by` | The task whose auditor is due to file it. With `task` it is the last of `learnings.droppable`'s four conditions; the console applies that one alone, so the rows it shows for a task are a superset of what `merge-task` drops (ADR 42) |
| `scope` | `Entry.scope` | `project` or `harness` — one trap in one codebase, or one in what every project shares |
| `status` | `Entry.status` | `confirmed`, `unconfirmed`, `refuted`. The confirmation rule is the dispatcher's and this route reports it |
| `when` | `Entry.when` | The trigger line: when this applies, as a condition the next agent can check. The console's fixture called it `trigger` |
| `rule` | `Entry.rule` | The one line the harness's own table shows — the first non-blank line of `## Rule`, falling back to `when`. Not the body: see below |
| `stale` | `Entry.stale(harness)` | Written under a permission surface this harness no longer has, so it is shown and not counted as evidence. The api's judgement, against the fingerprint of the config it holds |
| `in_phase_table` | `handed(eligible(…))` | Whether this row reaches a running phase right now. The screen's central claim, and not something a client can derive |
| `phase_table_cap` | `learnings.MAX_ROWS` | The cap the row above is measured against, repeated per row because the envelope has no slot beside `data` for a collection-wide fact (ADR 20's shape) |

Served and left out, with the reason:

- **`body`**, the whole entry. Nothing in the console renders it, `rule` is the
  line the dispatcher's own table shows, and a per-row field of unbounded length
  multiplied by every row is the shape `docs/debt/T-011-D1.md` was. An operator
  who wants the Symptom opens the file the `ref` names.
- **`path`**, the absolute path inside the api container. It publishes a mount
  layout, and `ref` is the pointer everything else in this harness cites.
- **`project`**. Every served row is already this project's or the shared
  store's, by part 2, so the column would read one value or blank. A later task
  that renders provenance for a promoted entry adds it.
- **`reviewed`**, which is `ref`'s own directory prefix.
- **`harness`**, the fingerprint itself — twelve hex characters that answer
  nothing a human can read. `stale` is the judgement over it, on ADR 10's rule
  that this service derives a fact rather than publishing the input.
- **`refutes` and `refuted_by`**. Real and unrendered. They are the next thing
  this route grows if a screen ever explains *why* a row is refuted.
- **`fingerprint`** and **`orphaned_from`**, which are the reconcile pass's
  bookkeeping and mean nothing to a reader.

**6. Damage is a warning, never a 500, and it is per directory.** Three
warnings, on the shape the other reads use:

- A `.md` file `read_dir` skipped — unreadable, or with no usable frontmatter —
  is named, on `_read_cards`' wording: it is in no row and the rest of the list
  still comes back.
- A directory that exists and will not list is named, and the *other* directory
  is still read. `read_dir` guards with `os.path.isdir` and then calls
  `os.listdir`, which still raises for a directory it cannot read, and this
  route lists two —
  `docs/learnings/a-per-item-listing-in-a-never-500-read-needs-its-own-guard.md`
  is the same shape one level up. The guard is at this caller and `read_dir`
  keeps raising, because its dispatcher callers need the raise.
- A missing learnings root is `[]` plus one warning naming it, on
  `_no_events_warning`'s model. `ensure_dirs` makes the tree on every
  `run-task`, so its absence means no run has happened here — and an operator
  reading an empty Learnings screen is owed the difference between "no entry has
  been written" and "the api is not looking where the dispatcher writes". A root
  that exists with nothing in it is empty and silent.

There is no per-row `try` and no `app.json.dumps` round, unlike `/api/tasks` and
`/api/phases`, and that is a property of the row rather than a relaxation of the
rule: every value above comes through `Entry`'s own accessors, which coerce with
`str()`, or is a `bool` or `MAX_ROWS`. No file content reaches the envelope
un-coerced, so there is nothing a YAML document could put in a row that JSON
cannot serialise. **A later field read straight off `meta` reopens this**, and
takes the guard and the serialisation round with it.

**7. The order served is the phase table's, and the answer is capped.** Rows
come back in `ordered()`'s order, so the first row is the first row a phase
sees and the last is the first to fall off the end of the cap. `MAX_LEARNINGS =
500` bounds the answer on `MAX_PHASES`' model, with the clamp named in
`warnings`: the inbox is drained at merge and holds seventeen entries today, so
nothing in this harness is clamped, and the cap exists so this is not the next
thing this service answers with no bound at all. It bounds a count and not
bytes, which is enough while `rule` and `when` are one line each.

**8. The fingerprint is computed once, in `create_app`.** `harness_fingerprint`
takes `cfg.permission_mode`, `cfg.allowed_tools` and
`docker_exec.WRITER_ROLES`, the same three the dispatcher passes at
`dispatcher/dispatcher.py` — so the api's answer to *stale* is the answer a
phase dispatched now would get. It comes off the one `Config` `create_app`
holds, never a second `load_config` in a view
(`docs/learnings/a-new-field-on-an-api-row-has-two-questions.md`).
`dispatcher/docker_exec.py` becomes an explicit import of this module for one
frozenset; it is still never called, which is the sentence this module's
docstring already carries.

**Consequences.** Seven routes, not six. `observability/api/app.py`'s module
docstring, `docs/README.md`'s `observability/api/` row and the console's forward
all carry a count that moves, and the console's own rejection message —
pinned in `front/src/lib/api/forward.test.ts` — moves with them.

`in_phase_table` and `stale` are recomputed on every request against the
directory as it stands, so a new, refuted or promoted entry moves this screen at
the next poll. The harness fingerprint they are compared against does not move:
`create_app` loads one `Config` at boot and computes `harness` from it once
(part 8, and `docs/learnings/a-new-field-on-an-api-row-has-two-questions.md`),
so an operator who changes `permission_mode` or `allowed_tools` in
`config.yaml` sees `stale` change only after `compose-api-1` restarts. The
dispatcher reads `config.yaml` per run, so until that restart the screen can
disagree with what the next phase is handed.

A refuted row is served with `in_phase_table: false`, because `eligible` drops
it before the cap is applied. The console shows it and says so; the point of
retiring an entry is to stop it costing turns, not to hide that it was written.

The route is read-only like every other: no `ensure_dirs`, no `reconcile`, no
`stamp`, and nothing that would write into a `:ro` mount. The suite pins that
the hive is byte-identical after a request, the way it pins it for the task
endpoints.

## ADR 42 — the console reads the learnings tree, and the detail's region shows what the task filed

**Status:** accepted (T-016, 2026-10-08). The console half of ADR 41. Narrows
ADR 18 on `LEARNING_TABLE_CAP` and ADR 27 on the learnings join, and leaves both
entries as written.

**Context.** `listLearnings` returned `mockLearnings()`; the Learnings screen
drew that fixture and the task detail's learnings region drew an `EmptyState`
naming `/api/learnings` as the route it was waiting for, per ADR 19 and
`docs/ui.md` *A region with no route says which route, and when*. ADR 27 said
the join that region wanted "travels as `handoff.learnings`" and waited only on
this route.

It does not. `dispatcher/handoff.py`'s schema makes `learnings` a list of
strings described as "proposed learnings: something true of this project that
the next task would want to know", and the handoff files on disk are prose: *"A
handoff is written when a phase ends, so the newest file names the role that
finished"*. Some lines happen to quote a ref inside the sentence and most do
not. There is no key to join on, and there never was one to wait for.

**Decision.** *The Learnings screen renders the served rows in the order they
were served; the detail's region shows the entries this task filed or carries;
and the console stops holding the harness's numbers.*

**The type.** `LearningEntry` becomes ADR 41's ten keys under the api's own
names — `ref`, `task`, `carried_by`, `scope`, `status`, `when`, `rule`, `stale`,
`in_phase_table`, `phase_table_cap`. The fixture's `id`, `trigger`, `body` and
`retired` are gone: `id` was a surrogate where the harness has a pointer,
`trigger` and `body` were its words for `when` and `rule`, and `retired` was a
second axis beside `status` that nothing on disk records. ADR 17's closing rule
holds — the route's name is the harness's word for the thing, and the console
renames nothing here.

`mockLearnings` is **kept and conformed** to the served shape rather than
deleted, which is ADR 25 and
`docs/learnings/narrowing-a-served-type-is-also-a-fixture-edit.md`: a fixture
typed against the served interface is what makes a typecheck catch a type that
drifts from the route. Its paragraph in `front/src/lib/api/mock/fixtures.ts`
moves from the fixtures that still back a screen to the ones that back nothing.

**`LEARNING_TABLE_CAP` is deleted in favour of `phase_table_cap`.** ADR 18 kept
it as "the console's own layout decision and nobody else's", which was true of a
console with no route: a number of rows to draw. It is not a layout number. It
is `learnings.MAX_ROWS`, the dispatcher's cap on the table a phase's prompt
carries, and this screen's subject is which rows get there — so a literal in
`types.ts` is the console asserting a dispatcher constant it cannot see change.
This is `HEARTBEAT_STALE_S`'s fate in ADR 18 and the same reasoning, applied to
the one constant that entry kept.

**The screen sorts nothing.** The api answers in the order a phase sees, so the
screen's `statusRank` comparator and its `retired`-first tie-break go, and the
search filter is all that stands between the rows and the table. `listPhases`
already took this position for the same reason — the boundary does not reorder
what the route decided — and the task detail's `byCycle` remains the one
screen-side sort in the console, with its reason written on it.

Three renders follow from the served fields:

- The *in table* column is `in_phase_table`, not an index into the screen's own
  sort. A refuted row reads *refuted — not handed to phases*, a row with
  `in_phase_table` reads *in table*, and the rest read *over the
  `phase_table_cap` cap*.
- `stale` is a qualifier beside the status pill, the way `learnings.table`
  renders `confirmed (stale)`. It is served, never computed, which is
  `docs/ui.md` *Staleness is served, never computed* applied to its second
  instance.
- `when` and `rule` are empty strings for an entry whose frontmatter or body
  does not carry them, and an empty string renders through `Absent` — the
  harness recorded nothing there, which is a fact and not a blank cell.
  `scope` and `status` are `_str`-derived on the api side and so may hold any
  string: an unknown value renders verbatim rather than keying a tone table,
  the way `PhaseRow` renders an unknown role
  (`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`).

**The detail's learnings region changes subject.** It shows the rows whose
`task` or `carried_by` is this task id — what this task filed, and what its
auditor is due to file — every served row naming this task, harness-scoped and
promoted entries included. That is the last of `learnings.droppable`'s four
conditions and not its whole: `droppable` also requires an inbox entry, not yet
reviewed, with `scope: project`. So `merge-task` drops a subset of this region,
and on this hive, where most inbox entries are `scope: harness`, a small one. The
region keeps the wider set on purpose — a harness-scoped entry a task filed is
still something that task found — and it is the one task-scoped question the
tree can answer.

It is not "learnings handed to these phases", and that is the half that cannot
be built: the table a phase was handed is computed at dispatch from the
directory as it stood, the harness fingerprint of the day and the cap, and
nothing writes it down. By `docs/ui.md` *A region with no route says which
route, and when*, that makes it the third sentence — *nothing records that* —
and a region in that state names the record and not a route, and names what it
shows instead. The region does both: it shows what the task filed, and its copy
says the handed set is recomputed per dispatch and recorded nowhere, with each
phase's own proposed-learnings prose in the timeline above.

The region is a secondary read on a discrete block, so a failed read takes the
region and not the screen — an `ErrorState` inside it, like the debt region
beside it — and its warnings join the one banner at the top of the screen in the
order the api sent them.

**Consequences.** `/api/learnings` becomes the forward's seventh route, carrying
no browser parameter and taking `?project=` from `CONSOLE_PROJECT` the way
`/api/debt` does. The forward's "not one of the six routes" message becomes
seven, and the `forward.test.ts` case that used `/api/learnings` as its example
of a path the console refuses is retargeted at a route ADR 19 says is never
coming.

`listLearnings` returns `ApiResult<LearningEntry[]>` like the other six wired
reads, so `learnings.data` is the envelope and its two callers — the Learnings
screen and the task detail — unwrap `.data` and carry `warnings` to a banner.
That is ADR 16's shape, unchanged.

The Learnings screen's empty state stays one state, the way `debt.tsx`'s does:
a filter that matches nothing still reads as an empty inbox. That is a
cross-screen wording gap rather than this screen's bug — two wired screens have
it — and it is declared rather than fixed here, because an entry in
`docs/ui.md` would bind every filtered screen in the console and this task
touches one of them.

What the console still cannot show about an entry is its Symptom, its Why and
its Evidence: ADR 41 serves `rule` and not `body`, so the screen is an index and
the file the `ref` names is the document. A screen that wants the body asks that
route to grow a detail form, which is the same shape as `/api/tasks` and
`/api/tasks/<id>`.

## ADR 43 — agent containers carry a headless Chromium, and QA reads the screenshots its own run made

**Status:** accepted (operator, 2026-10-09). Not built: `docs/plans/browser.md`
is the plan, and its first two steps are the next infrastructure task. Narrows
the sentence in `docs/ROADMAP.md`'s V0.6e block that no phase can open a
browser, and leaves that block as written.

**Context.** `docker/agent/Dockerfile` ships no browser, so every console check
has been a human's or the operator's row. V0.6e was walked on 2026-10-09 by the
operator, with a Playwright script from the host, and found the detail screen's
error state flickering with "Reading task…" — a defect no phase could have
seen. The work this harness exists for happens inside the agent containers,
and the front a task builds talks to services there, so a browser that only
the host has cannot see what the task built where it runs. Some checks are
also visual: a truncated label, an overlap, a banner that never appears. Those
need a screenshot, and the model reads images directly; one screenshot at
1280×800 is on the order of 1.4k tokens, paid once.

**Decision.** *Every agent container can drive a headless Chromium. The browser
build lives in `.data/ms-playwright/`, filled by a one-shot install service and
mounted read-only at `/ms-playwright`; the image carries only a pinned Node
Playwright client and Chromium's system libraries, with one version declared
once for both.*

*A phase that touches a screen walks it with a script it writes (option A in
`docs/plans/browser.md`).* The implementador writes the script; it asserts the
states the task promises and, where the task promises something visual, takes
screenshots at a fixed viewport, of the element or region the task touched
rather than the whole page. The revisor and the auditor rerun the script and
read the screenshots their own run produced, never the implementador's, which
may be stale. A task that promises nothing visual needs assertions and no
screenshots.

*A phase walks against the dev server with `/api/**` answered by route
fixtures by default*, because fixtures reach states real data does not (empty,
error, over the cap, a filter matching nothing). It may walk against a live
service running in its own container when that service needs no bearer. What
needs the harness api's bearer or docker stays the operator's, and so does a
judgement of taste about how a screen looks; a visual defect a screenshot
shows is QA's.

*`playwright-cli` and its skills (option B) wait until a phase needs to explore
a screen before scripting it, and are adopted only after reading their
`SKILL.md` and pinning a version. Playwright MCP (option C) is not used in
phases*: every action returns a page snapshot into context, which is the cost
`docs/plans/token-economy.md` shows compounding. Anthropic's `webapp-testing`
skill (option D) is not adopted, because it needs Python Playwright in the
image.

**Consequences.** Until the browser is built, the operator walks console rows
from the host, as on 2026-10-09; that is the interim, not the model. A Results
log row walked by a phase says so — "walked by T-0xx's implementador, headless,
against route fixtures" — and its script and screenshots live under
`/data/.hive/tasks/T-0xx/verify/`, not in the repo. A console check can split
into a phase's row against fixtures and the operator's row against the live
api. The phases' allowlist gains the one command the walk runs, as the
operator's edit. The agent image grows by Chromium's libraries; the browser's
version moves with one build arg and one run of the install service. Memory
under the 4 GB `mem_limit` with Chromium and the dev server both running is
measured once, as D8 was, before it is trusted.

## ADR 44 — a filter that matched nothing is Empty's other half, and the collection is tested first

**Status:** accepted (T-017, 2026-10-09). Answers the closing paragraph of
**ADR 42**, which declared this gap rather than fixing it, and leaves that entry
as written — `docs/decisions.md` is append-only, so the sentence there stays
true of the day it was written and this number is where it was answered. Closes
`docs/debt/T-016-D1.md`.

**Context.** `docs/ui.md` *Absent, empty and broken are three different things*
had one empty state, defined as "a query that succeeded and matched nothing".
Two wired screens with a filter box tested the **filtered** list against it:
`front/src/routes/learnings.tsx` told an operator who mistyped a slug that no
phase has ever written a learning entry, and `front/src/routes/debt.tsx` that
the project has filed no debt. Both sentences are about the harness; the true
statement was about the characters the operator had just typed. The Board had
already split the branch — `rows.length === 0` then `filtered.length === 0`,
with *No task matches these filters* — so the console held two answers to one
question, and the one with no entry behind it was the one two screens used.

**Decision.** *The vocabulary gains the second state, the collection is tested
before the filter, and every filtered screen over a wired read transcribes it.*

`docs/ui.md` gains **No match** as a bullet beside *Absent*, *Empty* and
*Broken*, worded as Empty's other half rather than as a fourth state: the
heading's count is unchanged because a filter that matched nothing is a
successful query over a collection that is not empty, which is Empty's own
sentence with a different subject. What it may say is the query and the control
that clears it; what it may not say is anything about the harness.

Four things follow that a later task could otherwise undo without knowing they
were decided:

- **The order is the collection, then the filter**, and the collection is read
  off what the api served rather than off the filtered list. A collection that
  really is empty keeps Empty's sentence even with a query in the box: the
  filter is not why there is nothing there, and a screen that answers the
  filter first would tell an operator their query is at fault for an empty
  harness. This is the Board's shipped order, generalised.
- **Naming the control is words, not a button.** No filtered screen in the
  console ships a clear control, and the entry asks for the sentence that names
  the box — *clear the filter box* — rather than an affordance. A clear button
  is a change to every filtered screen at once and to *A region with no route
  says which route, and when*'s rule about controls, not something a task
  holding one screen adds to it.
- **There is no new primitive.** `EmptyState` is the shared one already and it
  takes two strings; the subject noun differs per screen — a learning entry, a
  debt row, an event — so a component would be parameterised down to the
  wording it was meant to hold. What binds the wording is the `docs/ui.md`
  entry, which is the point of filing it there. Each route instead exports the
  small component holding its own two branches, which is what makes the branch
  testable with a plain `render` and no router or query client — the shape
  `front/src/routes/-index.test.tsx` already uses for `TaskCard`.
- **The query is console state and reaches no route.** Nothing about a filter
  is served, so this decision has no api half, and an entry that could only be
  honoured by changing a route would have been filed in the wrong place
  (`docs/plans/front.md` *What `docs/ui.md` may decide, and what it may not*).

**Which screens.** Three screens have a filter box over a wired read.
`learnings.tsx` and `debt.tsx` are the two defects and get the split.
`front/src/routes/tail.tsx` is the third: its empty state tests the unfiltered
`events`, so it never claims the collection is empty when it is not, but a
filter that matches nothing leaves a silent region with no sentence at all, and
the entry asks for one. It is in this task because a known divergence on the
day an entry lands is how the console ends up with two answers again — the
condition this ADR exists to end — and its filters are three, so it names its
controls the way the Board does rather than quoting one query. `index.tsx`
already conforms and is not touched. `front/src/routes/sessions.tsx` has the
conflation in one sentence — *Either no session is running or your filters hide
everything* — and is **out of scope**: `listSessions` returns a fixture, a
fixture is never empty, and the state is unreachable until the screen is wired.
The entry says so, so the task that wires it inherits the split rather than
discovering it.

**Consequences.** `docs/debt/T-016-D1.md` is resolved and its row in
`docs/debt/README.md` is marked resolved in place, which is two edits
(`docs/learnings/correcting-an-index-entry-is-two-edits.md`); the row stays,
because a fix can be reverted. Each of the three screens grows a second
`EmptyState` and an exported component beside its route, and the `front/` suite
grows tests for both branches on both of the two defective screens — the count
the suite prints is quoted from `bun run test` and never derived by counting
`it(` blocks
(`docs/learnings/counting-it-blocks-undercounts-the-front-suite.md`).

What this task cannot verify is the only thing the change is about: a sentence
an operator reads. `docs/ROADMAP.md` **V0.6f** is the walk — a nonsense query
in each of the three filter boxes, and `/tail`'s picker for the case with no
query to quote — written beside the change and `NOT RUN`, for V0.6e's reason:
a phase can run every `front/` script its task grants and cannot open a
browser.

The next filtered screen is bound by the entry and not by these three
transcriptions, which is the difference between this and fixing `learnings.tsx`
alone. A screen that genuinely needs one state for both cases — a region where
the collection and the filter cannot be told apart — changes the `docs/ui.md`
entry and owes an ADR, because by then three shipped screens rely on it.

## ADR 45 — a test sits beside its route as `-name.test.tsx`, and `routeFileIgnorePrefix` is why

**Status:** accepted (T-018, 2026-10-10). **Narrows ADR 30 and does not
supersede it**: ADR 30's decision stands, only its *Context* names the wrong
knob, so that entry is left exactly as written and its heading is not struck
through — `docs/learnings/a-stale-count-in-a-dated-paragraph-is-not-a-finding.md`
*an ADR on `main` narrowed by a new entry*. It also reconciles ADR 30 with
**ADR 44**'s third bullet, which decided the opposite shape without saying that
it had.

**Context.** Two entries in this file disagree about whether a test may live
under `front/src/routes/`. ADR 30 says one cannot: the generator "scans every
file under `src/routes/` and turns it into a route", and the knob that would
excuse a `.test.tsx` — `routeFileIgnorePattern`, correctly described there as
having no default — "is set in `vite.config.ts`", which `docs/charter.md` **C-9**
keeps off-limits to a hand edit. ADR 44's third bullet has each route export the
component holding its own branches, "tested with a plain `render` and no router
or query client", and T-017 shipped three such tests *under* `src/routes/`:
`-learnings.test.tsx`, `-debt.test.tsx` and `-tail.test.tsx`, beside the
`-index.test.tsx` T-012 added in `bc93b1d`. Four files exist where ADR 30 says
none can.

The generator, read with `front/node_modules` filled in this worktree by
`cd front && bun install --frozen-lockfile`:

- `@tanstack/router-generator`'s `baseConfigSchema`, in
  `dist/esm/config.js`, declares **`routeFileIgnorePrefix` with a zod
  `.default("-")`** (line 23) and `routeFileIgnorePattern` as `.optional()` with
  no default (line 24). Three copies are installed — 1.167.21 hoisted, 1.167.40
  nested under `@tanstack/router-plugin` 1.168.42 and under
  `@tanstack/start-plugin-core` — and all three agree, at the same two lines.
- `getRouteNodes` in `dist/esm/filesystem/physical/getRouteNodes.js` filters the
  `readdir` listing before anything parses a file: a dirent whose name
  `startsWith(routeFileIgnorePrefix)` is dropped, directories included, and
  `routeFileIgnorePattern` is consulted only in the branches where it is set.
- Nothing in this repo sets either knob. `front/vite.config.ts` passes
  `tanstackStart: { server: { entry: "server" } }` and nothing else, and a grep
  for `routeFileIgnore` across `front/node_modules/@lovable.dev/` comes back
  empty. The default is what the four files rely on, and C-9's off-limits file
  is not in the way at all.
- A file that *is* read as a route and exports no `Route` produces a **warning**
  — *"This file will not be included in the route tree"*, with the `-` prefix as
  its first suggestion — and the generator returns `null` for it: the
  `no-route-export` branch of `dist/esm/generator.js`. It does not error.

ADR 30 is accurate on one clause of three: `routeFileIgnorePattern` really has no
default. It is wrong that the knob is set in `vite.config.ts` — nothing sets it —
wrong that a file the generator cannot route is an error, and wrong in the
conclusion both were there to support, that a test file beside a route cannot
exist. It also named the one knob of the two that is not the one in use.

So what this entry retires is ADR 30's **premise**: that reaching a one-screen
renderer from a test costs either a `vite.config.ts` edit or a move. What stands
is its **decision**, which was never about the generator — being unreachable from
a test is a reason to promote a component, with the same standing as a second
screen needing it. The rule survives; the condition it fires on goes from
"always, under `src/routes/`" to almost never.

**Decision.** *A renderer that belongs to one screen stays in its route module,
exported, and is tested from `-<name>.test.tsx` beside it.
`front/src/components/console/` keeps the reasons for a move that were never
about testability, and gains none from this entry.*

- **The default is the `-` prefix, and it needs no configuration.** A test for a
  branch or a renderer inside a route goes in `-<route-file>.test.tsx` in the
  same directory, exporting what it renders from the route module. This is ADR
  44's third bullet, generalised from three screens to the rule.
- **A second screen drawing it still moves it.** Unchanged — the standing rule
  `front/README.md` states for that directory, and the reason ADR 30 added to
  rather than replaced.
- **A non-component export still moves.** `react-refresh/only-export-components`
  reads a route module's `Route` as a component (a PascalCase `const` from a call
  expression) and `front/eslint.config.js` configures the rule with
  `allowConstantExport: true`, which excuses a constant and not a function. So a
  helper, a formatter or a tone map exported beside `Route` for a test is a tenth
  warning against the nine `docs/debt/T-015-D1.md` holds as the bar, and it moves
  instead. `asPathLine` is exactly this shape and is why
  `front/src/components/console/payload.tsx` is one of the nine. This bullet is
  read off the rule's configuration and that warning's location; no phase of
  T-018 added such an export to measure it.
- **Untestability where it sits is no longer a reason on its own.** It survives
  only where the route module cannot be imported into a test at all — a module in
  its import graph that throws or needs a server at import. No route does today:
  nothing under `src/routes/` reaches `front/src/lib/api/server-env.ts`, the one
  module in this tree that throws at import, and
  `front/src/lib/api/forward.test.ts` shows the cheaper answer (`vi.mock`) for
  when something does. A phase that believes it has found this case names it in
  the handoff rather than moving a file on a hunch.
- **Nothing moves back.** `front/src/components/console/payload.tsx` and
  `front/src/components/console/learnings.tsx` stay where ADR 30 put them: the
  first is right under this entry's third bullet anyway, the second was promoted
  under a trigger this entry retires, and moving a tested component to re-derive
  the same tests is churn. ADR 30's own *Consequences* already warns that the
  directory is not a reliable answer to "how many screens use this"; that stays
  true, and each file's docblock says which reason put it there.

**Consequences.** Five documentation surfaces repeat the retired mechanism and
are corrected in this task, none of them by editing an ADR: `front/README.md`
*Writing a test*, whose bullet is rewritten to name `routeFileIgnorePrefix`, its
`-` default and this number rather than restating the reasoning; the docblocks of
`payload.tsx` and `console/learnings.tsx`, which cite ADR 30 and the wrong knob;
`front/src/routes/README.md`, whose *Routes* opening says every `.tsx` file in
the directory defines a route, which the four test files falsify; and
`docs/learnings/a-route-can-export-the-component-its-branch-lives-in.md`, whose
*What to do* tells a reader to prefer it over `front/README.md` — true until this
task and wrong after it — which is the file plus its row in
`docs/learnings/README.md`
(`docs/learnings/correcting-an-index-entry-is-two-edits.md`), with `status`
unchanged. No source, test or configuration file changes: `front/vite.config.ts`
is untouched, and adding `routeFileIgnorePattern` to it is both unnecessary and
C-9's to forbid.

A sixth edit is not one of the five. `docs/implementations/T-017.md`'s paragraph
on the disagreement is a dated record of what that task found and chose to leave,
and it stays as written; its closing sentence is a present-tense claim inside a
record, so it takes one clause naming this task and this number and nothing else
(`docs/learnings/a-stale-count-in-a-dated-paragraph-is-not-a-finding.md`). Two
more pre-existing claims are **not** in scope and are not findings: ADR 30's own
*Context*, which is append-only, and ADR 24's parenthetical that
`front/node_modules` "does not exist in this repository and installing it is not
authorised" — false of a phase whose task grants `bun install`, which is how this
entry's *Context* was read, and a narrowing another task owes.

What protects this entry from a dependency bump is the same thing that hid the
error: `routeFileIgnorePrefix`'s default is load-bearing for four filenames and
for `front/src/routeTree.gen.ts`, which no phase here can regenerate. A bump
that changed the default would put four test files into the route tree and break
the build, which is a failure a reader cannot miss — unlike a plausible sentence
in a README, which sat there from T-013 to T-018. That is why the bullet points
at this number instead of carrying the mechanism: the mechanism belongs to a
version, and the decision does not.

## ADR 46 — `front/node_modules` is installable by any phase whose card grants it, and no granted script regenerates the route tree

**Status:** accepted (T-019, 2026-10-10). **Narrows ADR 24 and does not
supersede it**: one parenthetical clause of ADR 24's *Context* is false, and
both its decision and the conclusion that clause was there to support are
untouched — so that entry keeps its number, its `**Status:**` line, its
parenthetical and an unstruck heading
(`docs/learnings/an-adrs-context-can-be-false-while-its-decision-stands.md`,
and ADR 45's own shape, which narrowed ADR 30 the same way a day earlier).

**Context.** The paragraph in ADR 24 that establishes the two facts the bearer
forward rests on parenthesises that `front/node_modules` "does not exist in
this repository and installing it is not authorised". That clause is the row
[`T-018-D1`](debt/T-018-D1.md), and the same claim had reached three more
documentation surfaces in other words: the closing paragraph on `T-015-D1` in
`docs/debt/README.md` ("no phase of this project can run `bun` and a worktree
has no `node_modules`"), the `T-013-D1` row in that index, which says "no gate
*runs* any of the three scripts" in a cell that opens by saying the gate runs
them, and the paragraph above *Writing a test* in `front/README.md`
("**Nothing under `front/` runs in the loop**", "blocked on an agent image with
`bun` in it"). One stale fact in four places, each citing another rather than
the tree.

The install clause has been false since 2026-10-08, and every part of that is
readable in this repository:

- **The agent image ships `bun`.** `docker/agent/Dockerfile` copies the binary
  out of the official `bun` image at 1.3.12, in its `bun` build stage, symlinks
  `bunx` beside it, and sets `BUN_INSTALL_CACHE_DIR=/data/projects/.cache/bun`
  — the same mount the worktrees sit on, so an install hardlinks out of the
  cache rather than fetching, and the cache outlives a container recreate
  (ADR 39).
- **The index declares the commands.** `docs/README.md`'s frontmatter carries
  `install: cd front && bun install --frozen-lockfile`, two `test:` entries
  spelled `cd front && bun run typecheck` and `cd front && bun run test`, and
  `lint: cd front && bun run lint`. The dispatcher's test gate has run them in
  the order install, test, lint since that date (ADR 39), so the three scripts
  are not merely runnable by a phase — they already run on every round, between
  implementador and revisor.
- **Phases run it.** All four phases of T-018 and every phase of T-019 ran
  `cd front && bun install --frozen-lockfile`: `486 packages installed`, under
  a second with a warm cache. The install is per worktree and not shared (ADR
  39), and which phases share one is `dispatcher/docker_exec.py:create_worktree`
  — the writer roles check the branch out at a persistent
  `worktrees/<task-id>/work`, so the implementador inherits the arquitecto's
  `node_modules`, while a reviewing role's path is rebuilt before every add and
  never does
  (`/data/.hive/learnings/inbox/T-019-only-the-revisor-gets-its-own-worktree.md`,
  narrowing T-018's entry). Running it unconditionally is the cheap default:
  13 ms when it is already filled.
- **The inbox entry the clause agreed with is retired.**
  `/data/.hive/learnings/inbox/T-015-bun-is-refused-in-a-phase.md` carries
  `status: refuted` and `refuted_by: T-016`.

What makes the install reproducible rather than lucky is `front/bun.lock` plus
`--frozen-lockfile`, which installs what the lockfile pins and fails instead of
resolving anything new — so `front/bunfig.toml`'s `minimumReleaseAge` guard
never has a resolution to apply to. The cache on the projects mount is what
makes it fast; the lockfile is what makes it the same tree in every worktree.

What is **not** false is the conclusion ADR 24 drew from the clause: a task
here cannot run the route generator, so a shape needing a new entry in
`front/src/routeTree.gen.ts` cannot ship complete from a phase. The commands a
card grants are the install plus `front/package.json`'s `typecheck`
(`tsc --noEmit`), `test` (`vitest run`) and `lint` (`eslint .`). None of them
loads the generator: it arrives with the `tanstackStart` plugin that
`front/vite.config.ts` pulls in through `@lovable.dev/vite-tanstack-config`,
which only `dev`, `build`, `build:dev` and `preview` run — none of them
granted — and `front/vitest.config.ts` exists so that a test run does not load
that plugin, listing `plugins: [react()]` and nothing else, which its own
docblock states as the reason. Measured rather than argued:
`git status --porcelain` came back empty in this worktree after the install and
all three scripts had run, with `routeTree.gen.ts` tracked.

**Decision.** *`front/node_modules` is installable by any phase whose card
grants it, `cd front && bun install --frozen-lockfile` is the form, and no
command a card grants regenerates `front/src/routeTree.gen.ts`.*

- **The card is the authority, and this file is not.** A phase reads its own
  command grant and runs what it names. ADR 24's clause is not a standing
  prohibition and has not been one since ADR 39; a card that does not grant the
  install is a card whose task does not need it, which is not the same as a
  rule that a phase may not install.
- **The form is `cd front && bun install --frozen-lockfile`**, run from the
  worktree root, once per phase, in that phase's own worktree.
- **The lockfile is what makes it reproducible**, and the cache on the projects
  mount is what makes it cheap. A task that needs a dependency *changed* moves
  `front/bun.lock` and is a different decision; this entry is no licence for
  that.
- **ADR 24's conclusion stands.** No granted command runs the route generator,
  so a change needing a new entry in `front/src/routeTree.gen.ts` still cannot
  ship complete from a phase, and ADR 45's protection — a default whose change
  breaks the build loudly — is unaffected.
- **A doc saying a phase cannot run `bun` is wrong rather than dated.** Where
  it is live prose, it is corrected and pointed at this number instead of
  carrying its own copy of the mechanism
  (`docs/learnings/a-docs-claim-about-a-dependency-is-a-claim-about-a-version.md`).

**Consequences.** `docs/debt/T-018-D1.md` is resolved by this entry together
with the surfaces T-019 corrects against it, none of them an ADR: the
`T-015-D1` closing paragraph and the `T-013-D1` row in `docs/debt/README.md`;
the paragraph above *Writing a test* in `front/README.md`; step 2's "Nothing of
that is written yet" in `docs/debt/T-013-D1.md`, which is stale for the other
reason — `front/src/routes/-index.test.tsx` has imported `TaskCard` and built a
memory history since T-012; the lead-in to that entry's *Fix*, which said one
half of step 3 "is still open", stale for a reason other than the install
clause too — step 3(b)'s gate landed on 2026-10-08; and two entries whose
*What to do* rests on the false clause,
`docs/learnings/the-pytest-suite-is-the-whole-gate.md` and
`docs/learnings/a-docs-claim-about-a-dependency-is-a-claim-about-a-version.md`,
each with its row in `docs/learnings/README.md`
(`docs/learnings/correcting-an-index-entry-is-two-edits.md`), `status`
unchanged on both.

Four surfaces are deliberately left, so that a later round does not re-raise
them:

- **ADR 24 itself**, append-only, unstruck, parenthetical intact. This entry is
  the only correction it gets.
- **The declaration-era sentences in `docs/debt/T-013-D1.md`** — *What*'s "A
  phase may not run `bun install`", *Why it stays*, and the dated `Done …by
  hand` blocks in *Fix* steps 1 and 3(b). They are that declaration as it was
  made and measurements with their dates on them, so the entry takes one added
  paragraph in its own idiom — the one it already uses for a wrong clause of
  the declaration — rather than a rewrite.
- **The implementation notes** that describe the retired condition:
  `docs/implementations/T-012.md`, `T-013.md`, `T-016.md` and `T-018.md`. They
  are records (`dispatcher/project_docs.py:RECORD_DOCS`), and this entry is the
  newer record their disagreement is recorded in.
- **`docs/ROADMAP.md` V0.6c and V0.6d**, whose "no phase can run it" bullets
  give the install clause as half their reason. The conclusion stands on
  grounds this task does not re-decide — those checks want a browser and the
  compose stack — so the clause is declared as `found` debt instead of being
  edited here, because rewriting the reason without ruling on the browser would
  put a claim about what a phase can run into a doc whose whole job is saying
  what has been verified.

No source, test or configuration file changes. `front/vite.config.ts` is
untouched (`docs/charter.md` C-9), and nothing about the gate moves: ADR 39
already runs the three scripts, and this entry only writes down what a phase
may do with them itself.

## ADR 47 — every phase is told one operation per Bash call, in the prompt and not in the learnings

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-10). **Extends
ADR 37 and does not supersede it**: a second constant rides beside
`_REMOTE_IS_NOT_YOURS`, appended at the same place for the same five roles,
and ADR 37 itself is left as written.

**Context.** T-017, T-018 and T-019 each lost turns to a Bash call the CLI's
permission check refused whole, in a different shape every time — a trailing
`; echo "EXIT=$?"`, an expansion, two `cd`s, a `cd` before `git` — although
every piece of each call was allowed on its own. The evidence is
`.hive/learnings/harness/T-019-a-trailing-echo-exit-refuses-the-whole-call.md`,
which names the siblings it retired. A learnings table reaches a phase as a
list of traps, which it reads only once it has already walked into one.

**Decision.** `_role_prompt` appends `_ONE_OPERATION_PER_CALL` to every role's
prompt, the mapper included: one operation per Bash call — no `;`, `&&`, `||`
or pipe to chain a status echo, no `$?`, `$(...)`, `${...}` or loops — the exit
read from the command's own output, the card's own
`cd <dir> && <granted command>` as the one exception, and the Read tool with an
absolute path for a file outside the worktree. Same criterion as ADR 37: it is
about the permission check this harness runs its phases under, not a method, so
it belongs in the prompt and not in a vendored skill.

**Consequences.** Every phase pays a few sentences of prompt for it. Its test is
`test_every_role_is_told_one_operation_per_bash_call`, parametrized over the
four cycle roles and the mapper. No config changes: `allowed_tools` grants
exactly what it did.

## ADR 48 — the primary's week is paced against its own reset; `reserve_pct` keeps the session

**Status:** accepted (T-020, 2026-10-10), implementing
`docs/plans/token-economy.md` **P8**. **Narrows ADR 20 and does not supersede
it**: ADR 20's shape — `quota_threshold_pct`, `reserve_pct` and
`quota_cooldown_seconds` as columns on every `/api/accounts` row — stands, and
so does its *Decision*. What this entry narrows is the sentence ADR 20 reasons
from, that "`dispatcher/dispatcher.py:_threshold_for` holds the primary to
`reserve_pct`, so the ceiling that actually governs an account is already
per-account" and a reader with both numbers and `is_primary` can work out which
one applies. After this entry the primary is governed by two ceilings, and only
one of them is a configured number. ADR 18's premise that three served
thresholds are the numbers a console must show is narrowed the same way.

**Context.** `reserve_pct` (60) was one number applied to two windows:
`quota.exceeds_threshold` compares it with `session_pct` and `week_pct` alike.
Against a weekly counter that is wrong in both directions, and both were
observed. It is too loose just after a reset, where the harness may spend the
operator's whole 60% on day one of seven; and too strict at the end, where
cuenta1 was parked at 84% of its week on 2026-10-09 with the week resetting the
next day, so about 40% of that week expired unspent. `docs/charter.md` **C-2**
says the reserve is "a ceiling, not a partition", which is the property a fixed
number cannot keep over a window that refills on a date.

The date is readable. `/usage` prints it on the week line — `Current week (all
models): 86% used · resets Oct 10, 4:59pm (UTC)` — and `UsageInfo.week_reset`
has captured that clause since the parser was written, with nothing reading it.

**Decision.** With *d* days from the probe to the reset `/usage` reported, the
primary's week is held to `PACE_START_PCT` (10) for *d* > `PACE_RAMP_DAYS` (6),
to `10 + PACE_RAMP_PCT × (6 − d) / 6` for 1 < *d* ≤ 6, and to `PACE_FINAL_PCT`
(95) for *d* ≤ 1 — then lowered by `PACE_PROBE_MARGIN_PCT` (5). The five
constants live beside the parser in `dispatcher/quota.py`; none of them is
configuration, for the reason the next paragraph gives about the reset itself,
and because a ramp whose shape an operator can edit is five more numbers to
reconcile with `reserve_pct`.

Four things that decision deliberately fixes:

- **The primary only.** A worker exists to be spent and keeps
  `quota_threshold_pct` on both windows; pacing one moves work in time without
  adding any. This is `docs/plans/token-economy.md` P8 *Scope*.
- **The session keeps `reserve_pct`.** The week is what resets on a date; the
  five-hour session has its own counter, and C-2's reason for the reserve — a
  fallback phase must not spend the console to the wall, because the operator
  then loses the thread that would notice — is about the window a phase runs
  in. So `_threshold_for` stays the primary's *session* ceiling and the
  admission line `pick_idle_account` reports, and `_load_reserve_pct`'s
  invariant (`reserve_pct` ≤ `quota_threshold_pct`) keeps governing something
  real. No ADR argued for pacing the session and this entry does not.
- **The margin is for the phase that has not run yet.** The probe runs between
  phases, so a phase may overshoot the ceiling by its own whole cost. Five
  points is a guess, and the one P8 *Failure mode* asks for until P5 records a
  measured phase cost; it is a constant rather than a key so that replacing it
  with a measurement is one edit and not a config migration.
- **The reset is read at every probe, never configured and never predicted.** A
  week starts at a moment set by the account's own history: the reset is not a
  fixed weekday, and the one observation of a roll (cuenta2 between 13:08 and
  17:37 UTC on 2026-10-09, next reset `Oct 16, 1:59pm (UTC)`) fits a seven-day
  cadence from the previous reset but is a single sample. Recomputing the
  ceiling on each probe is also what keeps it from stepping at midnight, and it
  needs no scheduler: `_recheck_cooling_accounts` already probes every parked
  account while none is IDLE, so a primary parked by the ceiling is released by
  the ceiling rising past its `week_pct`.

**The fallback.** The clause is free text with no year. `quota.parse_reset`
accepts `%b %d, %I:%M%p` and `%b %d, %I%p` followed by a zone in parentheses,
takes the year that puts the reset within the next seven days of the probe's
`now`, and answers `None` on anything else — a missing clause (the CLI omits it
from a 0% line), a shape it cannot read, a zone it cannot resolve. On `None` the
primary's week falls back to `reserve_pct`, which is today's behaviour exactly,
and the dispatcher logs that it fell back and which of those it was. The
fallback is never more permissive than the code it replaces, so a parse failure
can delay work but cannot wave an account through a line it would have parked
on.

**One decision, two readers.** `_quota_decision` is the only thing that
compares a probe with a ceiling, and both `check_quota_ok` and
`_recheck_cooling_accounts` call it. `_threshold_for`'s docstring already
explains why they must agree — a recheck on a looser number waves an account
straight back to IDLE the moment the gate parks it, and the two loop — and with
a ceiling that moves, two expressions of the same rule is a flap waiting for
the day they disagree.

**The switch.** `pace_primary_week`, on by default, turns the pacing off and
restores the fixed reserve on both windows. It exists because this changes when
the operator's own account accepts work, and an operator who disagrees with the
ramp needs one line rather than a patch. It is the only new key: the ramp's
numbers are constants.

**Consequences.** The primary refuses fallback work much earlier in its week
than it used to — 5% of the week just after a reset — and much later in the
last day, which is the trade P8 argues for and is visible the first time a
cycle falls back. The park log names the ceiling, the days left and the reset it
read, because a number that moves is not explained by the number alone.
`/api/accounts` goes on serving `reserve_pct` per row (ADR 20), and after this
entry that column is the primary's session ceiling and its weekly *fallback*,
not its weekly ceiling: the console's pool screen can label it correctly only
once something serves the paced value, which is filed as this task's debt and
belongs to whichever task next touches that route. `dispatcher/operator.py`'s
`status` listing is inside this task and does print both, so the one surface an
operator reads without a browser is not wrong in the meantime.

## ADR 49 — the last `/usage` probe is persisted in the account's state file and served on its row

**Status:** accepted (T-020-D1, server half, 2026-10-10). **Narrows ADR 18 and
does not supersede it**: ADR 18's rule that a probe number is not served until
something persists it with its time stands, and this entry is the something.
What it narrows is the consequence ADR 18 drew from the rule, that the probe's
numbers stay unserved. ADR 20's columns and ADR 48's ceilings are unchanged;
this entry serves what ADR 48 computes and did not keep.

**Context.** Since ADR 48 the primary's week parks on a ceiling that is a
property of one probe — paced against the reset that probe read, recomputed
every time — and nothing kept it. `/api/accounts` could serve `reserve_pct`,
which is the session's ceiling and the week's fallback, and nothing else, so the
console's pool screen labels the primary's week with a number that no longer
governs it (`docs/debt/T-020-D1.md`). ADR 18's `usage_pct` gap is the same
missing record seen from the gauge: `state_machine` persisted a probe's
outcome — a state, a `rate_limited_at` — and never the probe.

**Decision.** `dispatcher/dispatcher.py:_quota_decision` writes the probe it has
just decided on, through `state_machine.record_probe`, as `last_probe` in that
account's state file. It is the one place a probe meets a ceiling and both
`check_quota_ok` and `_recheck_cooling_accounts` call it (ADR 48), so the gate
and the recheck both record and neither can forget to.

- **Where: the state file, because the api already reads it.** The read api
  reads configuration and the dispatcher's own state files and nothing else, so
  a probe there is served by a reader that exists; any other store is a new
  thing for the api to open, for the deployment to mount, and for an
  unreadable-file rule to be written for. The file is per account, which is
  what a probe is about, and its writer is atomic already. `set_state` rebuilds
  the document, so it carries `last_probe` by hand as it carries
  `rate_limited_at`; `record_rate_limit` and `clear_rate_limit` rewrite the
  document they read and keep it without a change.
- **What: one record, the latest, not a series.** `probed_at` (epoch seconds,
  the unit `rate_limited_at` is stored in, taken from the same `_utc_now` the
  ceiling was computed against), `session_pct` and `week_pct`, the raw
  `session_reset` and `week_reset` clauses, `exceeds`, `session_ceiling_pct`,
  and `week_ceiling`. For a paced primary `week_ceiling` is `paced: true` with
  `quota.week_ceiling`'s `pct`, `days_left`, the parsed reset as ISO 8601 and
  `fallback_reason` (null unless it fell back to `reserve_pct`). For a worker,
  and for the primary with `pace_primary_week` off, it is `paced: false` with
  the configured threshold as `pct` and the rest null. The raw clauses go in
  beside the parse because a fallback is explained only by the text that would
  not parse. A series belongs to `docs/plans/token-economy.md` **P5**'s
  collector, and nothing here pre-empts it.
- **Served: `last_probe` on every `/api/accounts` row.** Null when the account
  was never probed. A record that is there and not one — not an object, or
  missing a key — nulls itself only, with its own warning, after the state half
  of the row has been read: the account's state is still true when its probe is
  not. A file the state half cannot read nulls `last_probe` with the rest and
  adds no second warning. `reserve_pct` and every other column stay as ADR 20
  set them.
- **A failed write never touches the decision.** `_record_probe` catches and
  logs everything. The record is for the console and the decision keeps an
  account off a wall; a write error must neither crash the gate nor reach
  `_recheck_cooling_accounts`'s fail-open `except`, which would read it as a
  failed probe and wave the account back to IDLE.

**Consequences.** ADR 18's precondition for `usage_pct` is met: a percentage
with a stamp is served, and a console can show it with its age. The pool
screen relabel is the half of T-020-D1 this entry does not do — splitting the
primary's row into the session's ceiling and the week's paced one, read from
`last_probe` — and the debt stays open until it lands. A record is only as
fresh as the last probe, and an IDLE account that nothing dispatches is not
probed, so a consumer reads `probed_at` before it reads the numbers.

---

## ADR 50 — `front/`'s lint moves under `test:`, so a red one blocks

**Status:** accepted (by hand on `main`, out of cycle, 2026-10-10). **Narrows ADR
39 and does not supersede it**: install, then test, then lint is still the
gate's order, and `lint:` still means a note. What changes is which key holds
`front/`'s lint, because `docs/charter.md` C-11 superseded C-10 that day.

**Context.** ADR 39 put `cd front && bun run lint` under `lint:` because C-10
ruled a red lint a note: `eslint .` was red on `main` before any phase touched
it. By 2026-10-10 it reports zero problems — errors cleared on 2026-10-08,
warnings with T-015-D1 — which is the condition C-10 set, and C-11 promotes it.

**Decision.** The command moves from the index's `lint:` key to the end of its
`test:` list, after `bun run test`. `dispatcher/gates.py` does not change. A red
`test:` entry already blocks, and `_could_not_run` already reads a command that could not
start (exit 127, or missing-module output when the install did not succeed) as
a note rather than a block, for the lint as much as for the suite. The alternative was a gate-level switch that made `lint:` blocking. It
was not taken: it would change what `lint:` means for every project under
`projects_root` to serve one project's ruling, and C-11 promotes `front/`'s
lint only.

**Consequences.** A red lint now reads `` `cd front && bun run lint` failed ``
under the tests gate, not the lint gate. The command says what it is, and the
finding loses only the label. This project has no `lint:` key now, so the
lint loop in `run_gates` runs nothing here. It stays, for projects that keep
one. The phases' `Bash(bun run lint*)` permission in `config.yaml` is
unchanged. A phase that runs the lint itself before handing off now runs a
check that can block its round.


---

## ADR 51 — a probe's staleness is the console's mark; `docs/ui.md`'s served-staleness rule covers the lock

**Status:** accepted (T-021, 2026-10-10). **Narrows ADR 18 for the probe and
does not supersede it**: a lock's staleness is still served as `lock_expired`
and no screen compares a heartbeat against a number of its own.

**Context.** The pool card marks a `last_probe` stale when it is older than
`PROBE_STALE_S`, thirty minutes, a constant in `front/src/routes/pool.tsx`.
`docs/ui.md` *Staleness is served, never computed* reads, on its face, as
forbidding that. ADR 18 deleted `HEARTBEAT_STALE_S` under it, because the api
already applied `heartbeat_ttl_seconds` and a console constant would drift
from the config the dispatcher acts on. Two outcomes were open: serve a
`probe_stale` judgement from the api, or rule that the entry does not reach
the probe.

**Decision.** The entry does not reach the probe, and `docs/ui.md` says so in
a paragraph of its own. A probe's age is not a lock. Nothing in the harness
expires a probe or acts on its age, so there is no server window and no
config key for the console's number to disagree with, which was the whole of
ADR 18's harm. ADR 49 hands the reading to the consumer in its own words: a
consumer reads `probed_at` before it reads the numbers. The mark only marks:
the numbers stay, beside their age. Serving `probe_stale` was not taken. It
would invent a server window that only the console reads, and it would need a
change under `observability/` for a judgement nothing on the server makes.

**Consequences.** `PROBE_STALE_S` stays where it is, and its comment and the
`docs/ui.md` paragraph cite this entry. If the dispatcher ever gives a probe an
expiry of its own (re-probing an idle account on a timer, for one), the
judgement moves to the api with it, the console reads the served field, and
the constant and this exception go. The probe's age is now read through
`front/src/lib/format.ts:agoEpochSeconds`, the same helper T-021 introduced
for `rate_limited_at`.

## ADR 52 — `run-task` says how it ended: 0 finished, 75 held, 1 blocked

**Status:** accepted (T-023, 2026-10-10). Builds `docs/plans/token-economy.md`
P7. Supersedes nothing; `run-phase`'s exit 1 for a phase that did not land is
unchanged.

**Context.** `run_task_cycle` returned None on every way out, and `cli.py`
ignored it, so a cycle exited 0 whether the auditor closed the task, the
revisor ran out of rounds, or no account was free for the next phase. On
2026-10-09 two cycles stopped for lack of an account and exited 0, which reads
as a pass to anything that only sees the code. The CLI already exits 1 for a
refused merge, a phase that did not land and an unknown learnings entry, and
argparse exits 2 for a usage error.

**Decision.** `run_task_cycle` returns `CycleOutcome`: `FINISHED`, `HELD` or
`BLOCKED`. `cli.py` maps it to 0, `EXIT_HELD` = 75 and `EXIT_BLOCKED` = 1. The
held code is 75, sysexits.h's `EX_TEMPFAIL` ("temporary failure; retry
later"). It is not 1, because a held task needs a wait and a blocked one needs
a person, and a script has to be able to tell them apart; it is not 2, which is
argparse's; and it is a code with a published meaning rather than one picked
here. Held is decided where it happens: the one no-account return in
`dispatch_phase` sets `DispatchResult.no_account`, `run_phase` copies it to
`CycleContext.held` when a phase the task depends on stops on it, and the cycle
returns `HELD` from any stop after that. An optional phase (the mapper) that
finds no account does not hold the run, because the task goes on without it.
`run-phase` uses the same split: `run_single_phase` returns the unsuccessful
result, `no_account` set, instead of None, and the CLI exits 75 on it.
An explicit field was taken over reading `account == ""`, which other failures
share. An outcome returned to the CLI was taken over an exception raised from
the dispatcher, because every stop already returns through `close_cycle`'s
`finally` and the CLI is the only place an exit code means anything.

**Consequences.** A blocked cycle now exits 1 where it exited 0: rounds
exhausted, a blocked arquitecto, a revisor block disguised as debt, a foreign
lock, a diverged project branch, a missing description and a failed phase.
That is the same mechanism and the same code `run-phase` already used, so it
was taken with the held case rather than left to contradict it. A refused merge
at the end of a finished cycle still exits 0: the task is done, and `merge-task`
is the verb whose exit code reports a merge. The card is blocked on a hold as
before, so the board does not tell held from blocked; the exit code and the log
line do.

## ADR 53 — a phase's cost is one line per `claude` call in the task's own usage log, not a key on the handoff envelope

**Status:** accepted (T-024, 2026-10-11). Builds
`docs/plans/token-economy.md` P5. Narrows ADR 27's *Context* — the clause
saying the harness records almost nothing *about* a run — and reverses none of
its decisions: the handoff envelope keeps its four keys and `/api/phases` keeps
its six.

**Context.** `dispatcher/docker_exec.py:exec_claude` parses the
`--output-format json` object the CLI prints into `ClaudeResult.raw`, and
leaves `raw` as `{}` whenever nothing parsed — a crash, a stdout that is not
JSON, a JSON list, the host-backstop timeout. Three readers touch it, each
asking one question: `is_rate_limit_error` (`is_error`, `api_error_status`),
`_exec_succeeded` (`is_error`, and emptiness) and `handoff.parse`
(`structured_output`). Nothing reads `usage`, `num_turns`, `duration_ms` or
`total_cost_usd`, and the phase ends with all four in a local that goes out of
scope.

**Which keys a real `raw` holds could not be verified from this tree.** Nothing
persists one. `<hive_tasks_dir>/<task_id>/handoffs/<role>.json` is ADR 27's
four-key envelope and carries no CLI object; ADR 49's probe record carries
parsed percentages; `.data/verify/` is outside the directories a phase may
read. The names used below are taken from `docs/ROADMAP.md` V1.1's *Record*
step, from its 2026-09-24 V5.1 Results row (CLI 2.1.273: `num_turns`,
`total_cost_usd`, `api_error_status`, `stop_reason`) and from the root
`README.md` item 2 (`usage` with input, cache creation, cache read and
output) — and T-024 was forbidden a live probe. So nothing here may depend on
a key existing.

**A phase is not one `claude` call.** `dispatch_phase` loops over accounts when
one answers 429, and each attempt is a call the service served and the account
paid for. Inside one attempt `_run_gates`, `_refuse_review_writes` and
`_shrink_over_budget` may each spend one `--resume`, and each **replaces**
`result` with the retry's — whose `usage` is that one turn, not the phase's.
`save_handoff` writes one file per role, overwritten each round, and
`run_phase` returns before reaching it whenever a phase did not finish.

**Decision.** (b), its own file: `<hive_tasks_dir>/<task_id>/usage/calls.jsonl`,
append-only, **one JSON object per line per `claude` call the dispatcher made
for a phase** — the attempt itself and each of the three retries, on every
account tried.

```
{"task_id": str, "role": str, "round": int | null, "account": str,
 "call": "phase" | "gate-retry" | "review-retry" | "shrink-retry",
 "session_id": str | null, "recorded_at": ISO-8601 UTC, "measured": bool,
 "raw_keys": [str], "is_error": bool | null, "num_turns": int | null,
 "duration_ms": int | null, "total_cost_usd": float | null,
 "input_tokens": int | null, "output_tokens": int | null,
 "cache_read_input_tokens": int | null,
 "cache_creation_input_tokens": int | null}
```

The names split in two halves, which is the rule rather than a coincidence.
The first nine are the dispatcher's own and it always knows them. The rest keep
the CLI's **own** spelling, so there is no translation table to maintain and a
key the CLI renames shows up as a column that went all-null rather than as a
number quietly meaning something else.

Never a guessed number. Every measured field is `null` when the CLI did not
give it, and nothing here is summed, scaled, defaulted to zero or carried over
from another call. `measured` is `false` exactly when `raw` is empty — the
call produced no result object at all — and then every measured field is
`null`: a phase that crashed or timed out records that it has no usage, which
is a different fact from a phase that cost nothing. `raw_keys` is the sorted
top-level key names of the object, values excluded, so the first real run says
on its own whether `usage` and `total_cost_usd` were ever there, without
another probe. `is_error` is kept because a 429 attempt and a clean phase are
the same shape otherwise, and both appear in this log.

Where it is written: `dispatch_phase` builds one recorder per attempt, once the
account is picked, and hands it to the three retry helpers; each call site
records its own result as the call that produced it. Writing it can never
cost the phase — the write is caught and logged on `_record_probe`'s model,
because this is bookkeeping on a path that may already be failing.

What is **not** in this log: the `/usage` probes of `check_quota_ok`,
`_recheck_cooling_accounts` and `operator._probe_into`, the last reached from
`operator.account_reports(cfg, probe=True)` and so only from `cli.py`'s
`status --probe` — `operator.format_status` is handed the reports and makes no
`claude` call of its own. `/usage` is a local
command (V1.3, V5.1: `num_turns` 0, `total_cost_usd` 0), ADR 49 already
persists what a probe found, and a probe is not a phase. There is no config
key either: the record costs no quota and no model call, and a switch for it
would only ever be a way to lose the series.

**Why not (a), a `usage` key on the handoff envelope.** Four reasons, and the
first alone decides it. One file per role, overwritten each round, keeps the
approved round and destroys the earlier ones — and a series of rounds is
exactly what P5 is for, since a task's cost is its rounds. A phase that failed
or timed out writes no envelope at all, so (a) would have to move
`save_handoff` onto the failure path, changing what ADR 26, ADR 27 and ADR 28
rest on: that every row of `/api/phases` is a phase that finished. One record
per role and round cannot hold the four calls an attempt can make, and keyed
off the final `result` it would report a shrink retry's two turns as the
phase's whole cost — measured, mislabelled, and worse than absent. And the
envelope is a role's return; what a call cost is the dispatcher's own
observation, which is the same separation `handoffs/` was namespaced for.

**Why not the collector**, which is P5's own wording ("sending them to the
collector"). `dispatcher/` has no HTTP client and no `requests` import; the
collector is reached only by `hooks/emit_event.py`, from inside an agent
container. A POST would add a network failure mode to a cycle that has none,
its `events` table is hook-fed and keyed on session and source app, and
`observability/api/` cannot even open that database over its `:ro` mount
without the care `docs/learnings/a-read-only-sqlite-open-still-writes.md`
describes. A file under `.hive/` is on the filesystem the dispatcher already
owns and the api already reads.

**Consequences.** `/api/phases` is unchanged and serves nothing new: the route
lists `handoffs/`, the log is a sibling directory, and nothing reads it at all.
No console screen and no card anywhere in `docs/` owns a view of what a phase
cost — `front/`'s Tokens screen and `docs/plans/front.md`'s Tokens task are a
container's *provider login*, the same word for a different object, gated on a
privileged read and a charter ruling that have nothing to do with this log. A
later task that serves this is choosing a row shape for a series, and is
inventing the screen as well as the route, which is why this entry guesses at
neither.

Root `README.md` item 2, *Record usage per phase*, is both the request this
entry answers and the prescription it declines. Its keys are the ones recorded
here, which is why it is cited for their spelling; but it asks for them to go
to the collector — argued against above — "tagged with role, model, effort,
round, and a fingerprint of the agent config (CLI version, skills, MCP
servers, compaction window)", so that rates can be compared *across config
versions*. A line carries role, round, account and call, and nothing about the
configuration the call ran under: no fingerprint, because nothing in this
harness records the CLI version, the skill set or the compaction window a phase
ran with, and one assembled from `config.yaml` at read time would describe the
configuration *now* rather than the one that ran — ADR 27's `model` row below
is the same trap. Model and effort are omitted for that reason and not for want
of a value: both are flags this dispatcher sets, so a later task may add them
as what was *requested*, which is a weaker fact than the rest of the line and
has to be labelled as one. Item 2 stays as written, outside this task's files,
and the cross-config comparison it wants waits on a record of the config that
is not this one.

ADR 27's *present nowhere* rows move only half a step. `account` and a phase's
real elapsed time are now recorded, per call, in this log, and so is
`shrink_retry`, which that table has as "Logged by `_shrink_over_budget`,
persisted nowhere": a `call: "shrink-retry"` line is exactly that record, per
call rather than per phase. `model` is still nowhere, because what the
dispatcher passes as `--model` is the configured model and not the one that
answered. The phase row keeps its six keys until a task decides what a row over
several calls means.

The write discipline is not `_write_atomic`'s, and the difference is
deliberate: this is a log, not a document, so a line is appended with `O_APPEND`
rather than the whole file being re-serialised per phase. By convention the
dispatcher holding the task lock is its only writer, and convention is all that
is: the scratch dir is mounted into the agent containers at the same path, so an
aggregator must take its integrity from the reader's half of the contract and
not from a writer count. That half: skip a line that will not parse instead of
calling the file damaged, and do not assume the last line is complete. The
file's mode is `_write_atomic`'s 0644, set on the descriptor at creation, for
that helper's own reason — a reader in another container is a reader with
another uid.

Citing the file in a doc needs the placeholder spelling used throughout this
entry. Spelled bare, it has a slash and an extension, so
`dispatcher/gates.py:pointer_token` reads it as a path, and it is a path no
worktree will ever hold — `.hive/` is gitignored and lives outside the repo.

P4 is not unblocked by this. It asks for a measured budget — "the median
ia-harness implementador costs X% of a window" — and one task's records are not
a median, nor is a token count a window percentage, whose weighting is still
unpublished. P4's surface proxy and P8's 5-point margin stand as written until
enough of these files exist to replace them.

## ADR 54 — the usage log is served verbatim, one group per attempt, and nothing in it is summed

**Status:** accepted (T-024-D1, 2026-10-10, by the operator, out of cycle).
**Narrows ADR 53 and does not supersede it**: the write, the seventeen keys and
the file are unchanged, and this entry decides only the reader ADR 53 declined
to — its row shape, its route and what it may compute. Extends ADR 3's route
list by one and leaves `/api/phases` (ADR 27) as it is.

**Context.** ADR 53 appends one line per `claude` call to
`<hive_tasks_dir>/<task_id>/usage/calls.jsonl` and nothing opens it
(`docs/debt/T-024-D1.md`). Three things make the reader's shape a decision
rather than a detail. A line is one call, an attempt is up to four calls on one
account, a phase is as many attempts as a failover tries, and a task is several
phases over several rounds
(`docs/learnings/a-phase-is-not-one-claude-call.md`). Whether a resumed call's
`usage` counts that one turn or re-counts the session is unknown
(`docs/debt/T-024-D2.md`), and was not probed here because this task was
forbidden a live container. And which keys the CLI actually prints is not
pinned — ADR 53 records `raw_keys` precisely because no stored `raw` exists to
read them off.

**Decision.** A reader and a route, and no aggregate that depends on D2.

The reader is `dispatcher/context_transfer.py:read_usage(hive_dir, task_id)`,
returning `UsageLog(lines, skipped)`. It is ADR 53's reader half, written as
that entry states it: a line that will not parse — bad UTF-8, bad JSON, `NaN`
or `Infinity`, nesting deep enough to raise `RecursionError`, or a value that
is not an object — is skipped and **counted**, and the last line is trusted
only if it ends in a newline, because an append can be cut short. Blank lines
are skipped and not counted; they are not damage. A missing log, usage
directory or scratch directory reads as empty, since a task that has made no
call has written none of the three. Every other `OSError` propagates, so a
caller can tell "no calls" from "may not look". An id that is not one path
component raises `ValueError` before anything is opened. The count replaces an
`unreadable()` sibling on the `LocalBoardClient` model
(`docs/learnings/a-silently-dropping-reader-needs-an-unreadable-sibling.md`):
the damage is per line, not per file, and a count returned by the one read is
a second read the caller does not have to make.

The route is `GET /api/tasks/<task_id>/usage`, behind the same auth as every
other route, taking no query parameters. It answers `{"data": [group, ...],
"warnings": [...]}`, one group per attempt in file order:

```
{"task_id": str, "role": <line's>, "round": <line's>, "account": <line's>,
 "calls": [line, ...], "call_count": int, "call_kinds": [str],
 "measured_calls": int, "unmeasured_calls": int,
 "phase_call": line | null}
```

A line opens a new group when its `call` is `"phase"`, the one an attempt
always writes first, or when its `(role, round, account)` differs from the open
group's — what a failover or a re-dispatch looks like if a `phase` line is
missing. Values are compared with `==` and never hashed, because a hand-edited
line may hold a list where a string belongs.

`calls` is the lines **verbatim**: nothing projected, renamed or defaulted, so
a key the CLI adds or drops reaches the reader as it is and no subkey is
assumed. The group adds only what is true under either answer to D2: how many
calls, which kinds in order of first appearance, how many were measured
(`measured is True`) and unmeasured (`is False`) — a line holding anything else
in `measured` is in neither, so the two falling short of `call_count` is the
sign the format drifted — and the attempt's `phase` line under `phase_call`,
the one per-attempt figure that is right whatever D2 says, since a session's
first call has nothing to accumulate. `phase_call` is `null` for a group with
no `phase` line, never a retry's line promoted into its place.

**Nothing is summed, averaged or totalled** — not tokens, not cost, not
`num_turns`, not `duration_ms`. If a resumed call's `usage` is cumulative, a
sum over an attempt's lines counts the phase's context once per retry, and the
reader would serve a number somebody trusts that is wrong by a factor of up to
four. So the sum is **deferred to D2's answer**, and what changes then is
already known. If usage is per call, a per-attempt total is the sum of its
lines' fields. If it is cumulative, it is the last line's value, or the
consecutive differences where a per-call figure is wanted. Either way it lands
as a **new** key on the group under a **new** ADR narrowing this one — never as
a rewrite of `calls` and never into this entry
(`docs/learnings/an-adr-is-never-where-a-deferred-answer-lands.md`) — and a
null field in any line of the attempt makes that total null, on ADR 53's never
a guessed number.

Responses:

- **404** `no task '<id>'` for an id `_is_bare_task_id` refuses, and `no task
  '<id>' in <hive_tasks_dir>` when there is no task file — even if a log
  exists for it. The detail route's two answers in its own words. A
  `ValueError` from the reader, reachable only through `.` and `..`, which
  pass the bare-filename check but leave the hive as a directory name, is the
  first 404 too.
- **200, `data: []`** for a task that has made no call.
- **200, `data: null`** with a warning when the log cannot be read: `permission
  denied reading the usage log`, citing this entry, for a `PermissionError`,
  and `unreadable usage log` for any other `OSError`, or for lines whose values
  break grouping or serialisation. `null` and not `[]`, because an empty list
  claims the task made no call.
- **A warning beside the data** naming how many lines were skipped, and, past
  `MAX_USAGE_CALLS` (1000) lines, that only the newest were served — the
  attempt a reader came for is the last one — and that the first group served
  may lack its `phase` line.

Never 500, on ADR 5; the guard wraps the use of the parsed values and not the
parse (`docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md`).

**Why a path segment and not `/api/usage?task_id=`.** The id is required and
names one task, which this api already spells `/api/tasks/<task_id>`; a query
parameter here is a filter on a list, and there is no unfiltered form of this
read worth bounding — one log is one task's.

**Why 404 and not ADR 27's 200 for an unknown task.** ADR 27 answers 200 on
`/api/phases` because that route is a list filtered by task, where an empty
list is the honest answer to a filter that matches nothing. This route is one
task's resource, read by whatever opens `/api/tasks/<task_id>` first, and that
route already answers 404 for the same id: a 200 here would contradict its own
parent.

**Why not per phase or per task.** A per-phase row is a total over attempts
and a per-task row a total over phases, so both are sums and both wait on D2.
Per call alone, a flat list, is correct under D2 but leaves the reader to
re-derive attempts from a stream whose boundaries ADR 53 already marks, and a
consumer that wants the flat list has it as the concatenation of `calls`.

**Consequences.** The write side is unchanged, and one of its properties is
recorded rather than fixed: `append_usage` creates the file 0644 on the
descriptor, but the usage directory is created by `os.makedirs` at the
dispatcher's umask, so on a host with umask 077 it is 0700 and this service's
uid cannot list it
(`docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md`).
`handoffs/` and the scratch directory share the same risk. The route names that
cause in its warning instead of serving `[]`; a fix belongs to the writer and
to all three directories at once.

No screen consumes this route, and none is built here. No console screen and
no card owns a view of what a phase cost — the Tokens screen is a container's
provider login (`docs/learnings/the-tokens-screen-is-logins-not-spend.md`) —
so the route is a contract without a consumer, and `front/` is untouched. That
work is filed as `docs/debt/T-024-D3.md`.

D2 stays open and is now the only thing between this route and a per-attempt
total. `raw_keys` travels in every served line, so the first real dispatched
phase's log answers which keys the CLI printed without another probe. P4 is
still not unblocked: a route over one task's lines is not a median either.

## ADR 55 — a directory the dispatcher creates under the hive tasks dir is 0755, whatever the umask

**Status:** accepted (operator, 2026-10-10, out of cycle). **Narrows ADR 54's
*Consequences*** — the paragraph recording that `append_usage` makes the usage
directory at the dispatcher's umask, and that `handoffs/` and the scratch
directory share the risk — and supersedes nothing: ADR 54's route, row shape
and warnings stand, and ADR 27's and ADR 53's files keep their paths and their
0644.

**Context.** `dispatcher/context_transfer.py` set every **file's** mode on
purpose and every **directory's** by accident. `_write_atomic` chmods its temp
file 0644 before the `os.replace`, so the handoff envelope ADR 27 serves is
0644 under any umask, and `append_usage` creates `calls.jsonl` `O_EXCL` and
`fchmod`s the descriptor (ADR 53). But three directory creations took the
process umask: `ensure_scratch_dir`'s `os.makedirs` (the scratch dir, and the
hive tasks dir if missing), `append_usage`'s `os.makedirs` (`usage/`, and the
scratch dir if missing), and `_write_atomic`'s `Path.mkdir(parents=True)`,
which for `save_handoff` makes `handoffs/` and possibly the scratch dir. Under
umask 077 all of them are 0700, and `observability/api/` — over a `:ro` mount,
possibly as another uid — cannot traverse to the 0644 file inside
(`docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md`).
ADR 54 recorded this and put the fix on the writer, for all three directories
at once.

**Decision.** One helper, `_makedirs_readable(hive_dir, path)`, makes every
directory below the hive tasks dir for these paths, and every directory it
**creates** ends up 0755:

- **Only below `hive_dir`.** The hive tasks dir and its parents are still made,
  if missing, by `os.makedirs` at the umask, as before, and are never chmodded:
  they belong to whoever set `.hive/` up. A `path` outside the hive raises
  `ValueError`.
- **Only what this call created.** Each component is `os.mkdir`ed alone; a
  `FileExistsError` skips it without touching its mode. That is also the
  `exist_ok` race's answer — two processes making the same directory, the
  loser's `mkdir` fails and the winner's chmod stands — and it means a
  directory an older dispatcher, a phase or the operator made keeps its mode.
- **On a descriptor opened `O_NOFOLLOW | O_DIRECTORY`**, after the `mkdir`,
  because the scratch dir is writable by the phases and a name swapped for a
  symlink between the two calls must not redirect the chmod. The `mkdir`'s
  own mode is masked by the umask, so the `fchmod` is what sets it.

`ensure_scratch_dir`, `append_usage` and `save_handoff` call it for the scratch
dir, `usage/` and `handoffs/`. `save_handoff` makes its parent before
`_write_atomic`, whose own mkdir then finds it there and stays at the umask on
purpose: for a task file its parent is the hive tasks dir itself.

A chmod that fails raises `OSError` to the caller like every other failure in
the helper, which is `append_usage`'s rule: the module raises and the call site
decides. So a failed chmod costs exactly what a failed `mkdir` there already
cost — `_UsageRecorder.record` logs it and the phase carries on, the gate's
`ensure_scratch_dir` is inside `_run_gates`' swallow, and the cycle's
`ensure_scratch_dir` and `save_handoff` let it propagate. Swallowing it in the
helper instead would leave a 0700 directory, which is exactly the silent
failure ADR 54's warning exists to name, and an `fchmod` by the uid that just
made the directory has no ordinary way to fail.

**Why 0755 and not 0750 or a group.** The reader is "another uid", not a known
group: the api's container user is not pinned against the dispatcher's, and
`_write_atomic` already chose world-readable 0644 for the files inside, so
anything tighter on the directory would only re-close what the file mode
opened. Nothing in these directories is secret beyond what their 0644 files
already show.

**Why not `os.umask`.** Process-wide, and the heartbeat thread writes
concurrently — `_write_atomic`'s own comment rejects it for that reason.

**Consequences.** Directories that exist on a host from before this change
keep their mode: the helper never chmods what it did not create, so a scratch
dir or `usage/` made 0700 earlier stays 0700 until the operator widens it by
hand (`chmod 755` on that task's scratch dir and its `handoffs/` and `usage/`).
`/api/tasks/<task_id>/usage`'s `PermissionError` warning stays, reworded to
name that cause rather than today's dispatcher. Other writers under the scratch
dir — the gates' test log, whatever a phase writes there — are not this
entry's; neither are `learnings.ensure_dirs`' directories, which sit beside the
hive tasks dir, not under it.

## ADR 56 — a project with a deploy key fetches over SSH, and the key is made on the dispatcher's side

**Status:** accepted (user, 2026-10-11, out of cycle; `docs/plans/projects.md`
step 2). **Supersedes ADR 36's *Consequences*** where they say an `ssh://` or
`git@…` remote is always `unavailable` because the agent image has no ssh. ADR
36's flag, its HTTPS store, its fast-forward-only rule and its outcomes stand.

**Context.** ADR 36 fetched with one HTTPS token, mounted from the host, and a
personal access token usually opens every repository of its owner. The user
asked first, among the projects work, for a key per project: a GitHub deploy
key opens one repository and is revoked from that one
(`docs/plans/projects.md`, *Decisions*, 2).

**Decision.**

- **Both images carry `openssh-client`.** The agent image copies
  `docker/agent/github_known_hosts` to `/etc/ssh/ssh_known_hosts`: github.com's
  three published host keys, pinned, checked against GitHub's published
  fingerprints by `tests/integration/test_agent_dockerfile.py`. Trusting the
  first answer would trust whoever answers first.
- **The key lives at `{project_keys_dir}/<slug>/id_ed25519`.**
  `project_keys_dir` defaults to `/run/secrets/projects`, from the host's
  `.data/credentials/projects`: writable in the dispatcher, read-only in the
  agents, never backed up, never in the environment.
- **`project-keygen --project <slug>`** runs `ssh-keygen -t ed25519` with no
  passphrase and the comment `ia-harness:<slug>`, under a temporary name moved
  into place, and refuses an existing key without `--replace`. It prints the
  public half, its fingerprint and where to paste it; nothing in it reads the
  private half.
- **`update_project_branch` chooses per project.** When the remote is SSH or
  `https://github.com/` and the key file exists, it fetches with
  `GIT_SSH_COMMAND="ssh -i <key> -o IdentitiesOnly=yes -o BatchMode=yes -o
  StrictHostKeyChecking=yes -o UserKnownHostsFile=/dev/null"`; an HTTPS GitHub
  remote goes through `-c url.git@github.com:.insteadOf=https://github.com/`
  for that one command, so the operator's remote is still never rewritten.
  Otherwise it uses ADR 36's store. An SSH remote with no key is `unavailable`
  and names the key path and `project-keygen`.

**Consequences.** The token keeps working for every project without a key, so
projects move one at a time. A refused fetch with a key asks whether its
public half is among the repository's deploy keys. The push of C-5 is not
built, so it has no SSH path yet; it takes the same choice when it is.
`docker-compose.coolify.yml` mounts neither credential, as before.

## ADR 57 — every phase runs at an effort the config names, per role

**Status:** accepted (user, 2026-10-11, out of cycle; `docs/plans/models-5-5.md`
step 2). Narrows the effort half of the revision loop's escalation: the
escalation stands, and what a round below it runs at changes.

**Context.** Until now a phase carried no `--effort` until its round passed
`escalate_effort_after_round`, so it ran at whatever the image's CLI defaults
to. Under Opus 5.5 a level thinks more per turn than the same level did
before, and the plan sets each role's level on purpose and measures it.

**Decision.**

- **`default_effort`**, `medium` by default, is passed as `--effort` on every
  phase, the cartografo's included.
- **`role_effort`** maps a role to its own level over the default. Its keys
  are the phase roles (`role_skills.ROLE_SKILLS` and the cartografo).
- **The escalation wins over both** once a round passes
  `escalate_effort_after_round`, as before. `Config.effort_for(role,
  round_num)` is the one place this order lives.
- **Checked at load.** `default_effort`, `escalated_effort` and each
  `role_effort` level must be one of `low`, `medium`, `high`, `xhigh`, `max`
  (`claude --help`, 2.1.296), and an unknown role is refused, so a misspelt
  role cannot run at the default without a word.

**Consequences.** Which level ran is in the config, not in the CLI version. A
config that names none of the keys runs every phase at `medium`, which is
Opus 5.5's own default, so the cost of a round below the escalation should not
move; step 6's measurement is what decides any other level. The CLI has no
`max_tokens` flag, so that open question closes with nothing to set.

## ADR 58 — a phase that ends without its handoff is resumed a bounded number of times, then failed

**Status:** accepted (user, 2026-10-11, out of cycle; `docs/plans/models-5-5.md`
step 3). Spends within the run already authorized (C-4, *Amended* 2026-10-11).

**Context.** Anthropic's guide to Opus 5.5 warns that in an unattended run the
model may end a turn with a progress report, a list of what is left, or a
question, instead of carrying on. Under `claude -p` that turn ends the phase.
Until now such a phase counted as succeeded when the CLI reported no error,
and the missing handoff surfaced only later, as a role with nothing to read.

**Decision.**

- **The paragraph.** Every phase whose role has a handoff schema
  (`handoff.schema_for`) gets `handoff.UNATTENDED_PHASE` appended to its
  system prompt, after the role's skills (`_phase_system_prompt`). It says the
  phase is unattended, ends only with the structured handoff, and names the
  ways a turn ends too early. The main thread never gets it: a person answers
  there.
- **The resume.** A phase that succeeded but whose result `handoff.parse`
  cannot read is resumed with `--resume <session>`, the same model, effort and
  schema, and one short line (`handoff.continue_prompt`), up to
  `max_handoff_resumes` times (config, 2 by default; 0 turns it off). Each
  resume is recorded as `handoff-resume`, so it counts in the run's spending.
- **Then failed.** When the resumes run out, or there is no session to
  resume, the phase's result is marked `is_error` and takes the existing
  failure path. A resume that fails, or hits a rate limit, is returned as it
  is, so the rate-limit failover carries the session on as it does for a phase.
- **Where.** In `dispatch_phase`, right after the phase is recorded and
  before the gates, so a gate never runs on a phase that handed nothing on.

**Consequences.** A stop that is only a pause costs one short resume instead of
a failed round. A phase that cannot produce its handoff now fails at once
rather than one role later. The worst case is `max_handoff_resumes` extra
calls per phase, each bounded by `phase_timeout_seconds`.

## ADR 59 — the board seam is removed: the task files are the board and nothing mirrors them

**Status:** accepted (T-028, 2026-10-11; `docs/plans/board-service.md` step
2). Supersedes ADR 1, ADR 2, ADR 3 and ADR 4. Their headings are left
unstruck because T-028's card forbids editing an earlier entry; this entry is
the pointer.

**Context.** The dispatcher talked to a board through a four-method seam with
three clients behind it. No run ever used anything but the client that does
nothing: Vibe Kanban never got past its cloud login, and the local board's
cards repeated what the task files already say. The user ruled on 2026-10-10
that the board is the harness's own: the task files are its data and the
console is its view (`docs/plans/board-service.md` section 1).

**Decision.**

- **The seam is deleted.** The client module with its three clients and its
  test file, the client factory and the issue-id seeding in `dispatcher/cli.py`,
  the `--kanban-issue-id` flag, and the client argument of `open_cycle`,
  `run_task_cycle`, `run_single_phase` and `_file_accepted_debt` all go.
- **`_update_task_status` goes with it.** It only ever moved a card: the task
  file's `status` is written by `tasks.handoff` and `tasks.acquire_lock`, and
  still is. So nothing about the task file changes, and a cycle that stops
  blocked still leaves no `blocked` in it; its record is the log line and the
  exit code (ADR 52). Writing that status is the dispatcher's later work
  (the plan's sections 4 and 6), not this removal's.
- **`kanban_issue_id` is no longer a `TaskFile` field.** A task file that
  still has the key reads without error and loses it on its next write. This
  is the silent drop
  `docs/learnings/dropping-a-key-from-read-task-file-deletes-it-on-the-next-write.md`
  warns about, taken on purpose: the value has no reader left, so there is
  nothing to move first and no refusal to add.
- **Either board block is a startup error.** `load_config` refuses
  `vibe_kanban`, `local_board` and the older `vibe_kanban_mcp_url` when the
  key is present at all, naming the key and this ADR. A block that was
  silently ignored would leave an operator watching a board nothing moves,
  which is ADR 2's argument and the part of it that survives.
- **Debt is filed without cards.** The dispatcher still assigns each accepted
  declaration its id and names it to the auditor; it opens nothing. The
  index and its five columns (`COLUMNS` in `dispatcher/debt.py`) do not
  change, because the api and the console read them: the auditor writes
  `none` under `card` until step 5 of the plan puts a backlog item's id
  there. `card_title`, `card_description`, `card_ids` and
  `close_resolved_debt` had no other caller and go; `merge-task` no longer
  closes anything. `resolved_debt` stays in the task file and on the api.
- **The api serves no card.** `/api/tasks` and `/api/tasks/<task_id>` drop
  `card` and `kanban_issue_id`, read no board and warn about none. A task row
  is eight keys: `task_id`, `status`, `owner`, `depends_on`, `heartbeat`,
  `description`, `resolved_debt`, `lock_expired`, plus `body` on the detail
  route (ADR 21).
- **The compose service is removed** from both compose files, with its
  `kanban` profile and the `vibe_kanban_data` volume declaration.

**Consequences.** One thing less to configure, and no path left by which a
second copy of a task's state can disagree with the task file. Removing a
volume's declaration deletes no volume: a host that ever ran the profile
keeps `vibe_kanban_data` until someone removes it by hand. The console's
types still declare `card` and `kanban_issue_id` on a task; no screen reads
either, so they are stale annotations until a front task removes them. The
`mcp` package is still installed by `pyproject.toml` and the agent image and
nothing imports it. `docs/plans/board.md` *The shapes* and ADR 10, ADR 21 and
ADR 23 quote the old task key set; they are records and stay as written. A
later step that wants a board tool builds it on `dispatcher/tasks.py`, not on
a client seam.

## ADR 60 — epics: the arquitecto may split a task, and a dependency waits for its merge

**Status:** accepted (T-031, 2026-10-12; task 4 of `docs/plans/task-split.md`
section 8, which the user approved on 2026-10-11 with
`docs/plans/token-economy.md` P4). Narrows ADR 52 and does not supersede it:
`run-task` has a fourth exit code. The text is that plan's *Proposed ADR*
with its cards and its `[epic]` card title removed, since ADR 59 left no
card to title or move.

**Context.** `docs/plans/token-economy.md` P4 sizes a task to one surface so
its cycle fits one account's window, and makes the arquitecto a backstop for a
task that spans more. Until now its only early exit was a block, so a task too
big for one window ran the implementador anyway. The user also asked that the
sub-tasks of a big task stay tagged with it, as an epic. The design is
`docs/plans/task-split.md`; T-025, T-029 and T-030 built its first three
tasks, inert, and this entry is the switch.

**Decision.**

- **The arquitecto's handoff has a required `split` array**
  (`_SPLIT_PROPERTY` in `_ROLE_EXTRAS["arquitecto"]`, `dispatcher/handoff.py`),
  empty unless the task spans more than one surface. No other role is offered
  it. The signal is a non-empty list, not a new status.
- **A block wins over a split, and a split stops the cycle before the
  implementador.** `run_task_cycle` reads the block first. A non-empty
  `handoff.split_of` then goes to `_file_split` in `dispatcher/dispatcher.py`,
  which checks it (`handoff.split_problems`: two to four entries, `after`
  naming earlier entries only), has `tasks.file_split` write one task per
  entry as `<parent>-S<n>` with `depends_on`, `epic` and the entry's
  description in its own `description.md`, and set the parent to
  `status: epic` with `split_into`. The cycle returns `CycleOutcome.SPLIT`
  and `run-task` exits 3 (`EXIT_SPLIT`), printing the ids in run order. Not 0,
  because the task did not ship; not 1, because nothing is wrong.
- **A split that cannot be filed blocks the task and files nothing.** That is
  a split with problems, a description `tasks.parse_description` refuses, a
  task that is itself inside an epic (one level deep), and a parent the next
  rule refuses. The implementador is not dispatched: running it on a task its
  arquitecto called too big is what this path exists to prevent. In a cycle
  the task is then marked `blocked` as any blocked cycle's is. A split that
  was filed is not a block: the task's status is `epic`.
- **Which parent may be split.** A task whose file says `pending`,
  `in_progress` or `blocked` may become an epic. One that is `done` or already
  `epic` may not, and neither may one whose lock is live, since the phase
  holding it would write its own status over `epic` at its handoff. This is a
  default T-031 chose, not a ruling: `docs/business.md` carries it as
  unconfirmed.
- **`run-phase` proposes, `file-split` files.** `run-phase --role arquitecto`
  prints a split it was handed and files nothing. `dispatch file-split
  --task-id <id> --project <slug>` reads the saved
  `<hive_tasks_dir>/<task_id>/handoffs/arquitecto.json` and calls the same
  `_file_split`, running no model, opening no cycle and marking nothing. It
  exits 0 when it filed and 1 when it refused: no saved handoff, a blocked
  one, an empty split or one that cannot be filed. It is also the recovery
  for a `run-task` that died between the handoff and the filing.
- **An epic is not dispatched.** `open_cycle` refuses a task whose file says
  `status: epic`, with one log line naming its `split_into`, before the
  supplied description is written, before any account is picked and before
  the lock is taken. So the refusal covers `run-task` and `run-phase`, the
  file is left byte for byte, and it is not written `blocked`. To split a task
  again, the operator deletes the sub-task files and sets the status back by
  hand.
- **Nothing is auto-dispatched.** The split writes files and stops. The
  operator reads them, may edit or delete any, and runs each sub-task with
  `run-task`; running it is the approval.
- **What T-029 and T-030 already built belongs to this decision.** Both merge
  paths write a `merged` block to the task file; `open_cycle` refuses a task
  whose `depends_on` names one that is missing or not merged; merging an
  epic's last sub-task closes the epic. The operator may write the same
  files by hand, and `tasks.epic_problems` checks them.

**Consequences.** A task too big for one window costs one arquitecto phase
instead of a full cycle. Sub-task ids never collide with debt ids
(`<task>-D<n>`). `depends_on` is enforced for every task, not only split ones,
and against the merge, not `done`, so a sub-task waits for a person's approval
of the one before it once `docs/plans/review.md` is built. A sub-task's
description must parse as a task description, so the arquitecto is told to
write `## Goal` and `## Done when` in each; one that does not is a block, at
the cost of that one phase. An epic shows as `status: epic` in its task file
and nowhere else until the console has an epic view. A script reading
`run-task`'s exit code has a fourth value to handle; the root `README.md`
does not list it yet (`docs/debt/T-028-D2.md`).

## ADR 61 — the pointers gate checks a task's own citations first and in full; the limit is 1000 and bounds the present docs

**Status:** accepted (T-036, 2026-10-13). Supersedes one paragraph of ADR 35,
*The limit stays at 400 and now says when it bites*, and nothing else in it:
the scope, the diff, the fresh records, the two excuses and the note level all
stand. ADR 35's heading is not struck, because the entry is still in force.

**Context.** `_broken_pointers` in `dispatcher/gates.py` built one list: the
present docs (`PRESENT_DOCS` in `dispatcher/project_docs.py`) first, then the
records the task created, then the lines the task added to a record. It
stopped at `_POINTER_LIMIT = 400` unique (doc, token) pairs, so whatever it
dropped came off the end, and the end was the task's own citations. Those are
the only citations a task owes an answer for, and the only breaks the gate has
caught (ADR 35, *A record this task changed that the diff never saw is one this
task created*).

ADR 35 kept 400 because the present docs gave 151 pairs. Measured on this tree
at T-036's base (`32d13e9`): **400 unique pairs, exactly**, over 337 candidate
paths, from 3820 backticked spans and links. The method is the gate's own, run
host-side in a throwaway test: the same `grep -r -H -o -E` with
`_POINTER_PATTERN` over `PRESENT_DOCS`, each span through `pointer_token`,
unique on (doc, token). `docs/README.md` gives 52, `docs/charter.md` 19,
`docs/business.md` 18, the learnings index 36 and the debt directory 275 over
33 files; the architecture doc `PRESENT_DOCS` names does not exist here. The
debt directory was most of the growth and gains a file per filed debt.

So the loop reached 400 on the last present-doc pair and broke there. On this
tree, before this change, the gate checked none of a task's own citations: not
a fresh implementation note, not a line added to this file. It also logged
"the rest went unchecked" when no present-doc pair had been skipped, because
the test was `>=` and the line named only the limit.

**Decision.**

*The task's own citations go first and none is dropped.* The pairs from fresh
records and from the lines a task added to a record are put in the list before
any present-doc pair, and no limit applies to them. One `grep` still reads the
present docs and the fresh records together; its pairs are sorted afterwards
by `project_docs.is_record`, which is true of a fresh record and false of
every present doc.

*The limit bounds the present docs only, and they get what the task's own left
of it.* The present docs are given `_POINTER_LIMIT` minus the number of the
task's own pairs, never less than zero. A present-doc pair past that is not
checked. The gate goes on reading to count them, since the count costs no
container call.

*The limit is 1000.* At 400 the present docs fill it today and the next debt
file would leave some unchecked, chosen by the order `grep -r` walks the debt
directory, which ADR 35 called "not a gate". The cost is `ls` round trips:
`_missing` asks in chunks of `_POINTER_CHUNK` (100) paths, and a pair has at
most two candidate paths. 400 pairs is at most 8 round trips and 1000 is at
most 20. Measured, this tree's 400 pairs are 337 paths and 4 round trips, the
same as before this change, and at that ratio 1000 pairs are about 9. The
limit is kept rather than removed because the gate runs on every project
between two phases, and a docs tree nobody measured should not buy an unbounded
number of `docker exec` calls.

*The log line names how many went unchecked.* It is written only when at least
one present-doc pair was skipped, and it carries that count, the limit and how
many of the limit the task's own pairs took.

**Consequences.** A broken pointer in a task's own note or added line is
reported however large the present docs are. The finding lists the task's own
breaks first, since the report keeps the list's order and shows the first
eight. A task that cites more than 1000 paths of its own is checked in full and
leaves the present docs unchecked for that run, with the log line saying so.
Which present-doc pairs go unchecked past the limit is still `grep`'s walk
order; the limit moves that out, it does not end it. The lasting answer is the
scope, a resolved debt file read whole for ever, and that is
`dispatcher/project_docs.py`'s to change, not this gate's. The wider check
turned up no new finding: the 5 pairs whose paths are absent
(`.lovable/project.json` twice, a `.data/` evidence file three times) all sat
inside the first 400 and are excused as before. Tests are in
`tests/dispatcher/test_gates.py`; the build is `docs/implementations/T-036.md`.

## ADR 62 — the debt index is a present doc; every debt entry file is a record

**Status:** accepted (T-038, 2026-10-13).

**Context.** ADR 35 put the whole debt directory in `PRESENT_DOCS`, so the
pointers gate read every entry file whole on every task. An entry file stays on
disk once its entry is resolved, so the present-doc pair count only grew: 275 of
400 pairs at T-036's base, 285 of 413 at T-038's, about a dozen more per filed
entry, against the 1000-pair limit of ADR 61. ADR 61 named the scope as the
lasting answer and left it to `dispatcher/project_docs.py` (debt T-036-D1). Two
shapes were on the card: a resolved entry's file becomes a record, or the index
stays present and every entry file becomes a record.

**Decision.** `PRESENT_DOCS` names `DEBT_INDEX` in place of `DEBT_DIR`, and
`RECORD_DOCS` gains `DEBT_DIR`. `is_record` is unchanged: `PRESENT_DOCS` already
wins the overlap, which is the layout ADR 35 gave the learnings index inside the
learnings directory, now applied to the debt index inside the debt directory.
An entry file is a record whether its entry is open or resolved. The file is a
filing measured at the base of the task that filed it, and the index row is the
present claim: what, where, fix, and the `Detail:` link to the file.

The other shape was weighed and rejected. Making only resolved files records
would make `is_record` depend on content: the gate would have to read a status
from the row's `**Resolved` prefix or the file's first paragraph, decide what
to do when the two disagree, and see a file change class over its life. Open
entries would still feed the count. Grepping only the files of open entries is
the same shape with the same costs.

This narrows the list in ADR 35's paragraph *The layout decides the scope, and
the layout is `dispatcher/project_docs.py`.* where it says "the debt directory",
which now reads as the debt index. It also answers the sentence in ADR 61's
Consequences that begins "The lasting answer is the scope". It strikes neither
entry; no paragraph is superseded outright.

**Consequences.** On this tree the present docs give 193 unique (doc, token)
pairs instead of 413, and the debt part is the index's 65 alone. From now on the
count grows with index rows only, not with the files behind them. The gate no
longer reads the old lines of any entry file, open or resolved. It still reads
the index rows whole, so a renamed or deleted entry file is caught through the
row's `Detail:` link. It still reads every line a task adds to an entry file,
uncapped, and an entry file a task creates before the gate runs. An entry file
the auditor files after the gate is not read, the same as a learning. A broken
pointer in an entry file's old lines is owed by no later task under the record
rule. The task that resolves the entry reads the file through its row, and the
row's own where and fix cells stay checked. The count is in
`docs/implementations/T-038.md`; the tests are in
`tests/dispatcher/test_project_docs.py` and `tests/dispatcher/test_gates.py`.

## ADR 63 — a landed piece of a README *Prioritized* item is a status line and a pointer, not a kept spec

**Status:** accepted (T-040, 2026-10-14). Taken by T-037 on 2026-10-13 and
recorded here after the fact; `docs/implementations/T-037.md` *Decisions*,
item 2, is where it was first written.

**Context.** The root `README.md` *Future work* had a convention of its own,
stated in item 1's *Documenting is a method* bullet: the spec bullets under a
*Prioritized* item "are never trimmed when a piece of it lands". Each item kept
its whole design text however much of it was built. By T-037's base
(`7cc44ca`) the section ran about 990 lines under an intro that called all of
it "not designed or implemented", over pieces that were built, planned in
`docs/plans/` or overtaken by an ADR. A reader could not tell from the README
which text was still open. T-037 classified every piece against the tree and
rewrote the section, but its card put this file out of scope, so the decision
went into that task's own note and nowhere a later task looks for decisions.

**Decision.** The convention is retired. Under each *Prioritized* item the
README holds one line per piece: the piece, its class in bold (built, planned,
superseded or open) and, unless it is open, a pointer to where it now lives —
code, a plan, an ADR or `docs/ROADMAP.md`. The text of a piece that is still
open, and written nowhere else, lives in `docs/plans/backlog.md`, moved word
for word under the item's own number and title, and the list's lead points
there for every open line. Text classified built, planned or superseded is
copied nowhere; it stays in git (`git show 7cc44ca:README.md`). Item numbers
and bold titles do not change, so a doc that cites "README item 2" still lands
on a line that points onward.

**Consequences.** A task that lands a piece of a *Prioritized* item changes
that piece's README line, its class and its pointer, instead of leaving the
spec in place beside the built thing. The README's *Future work* stays a status
list, and `docs/plans/backlog.md` is where open work no plan covers is read.
This ADR does not decide how a landed piece's text leaves the backlog. Docs
that described the old section, listed in `docs/implementations/T-037.md`
*References elsewhere to what moved*, are records or were left as they are;
none states the convention as current.

## ADR 64 — a resumed call's tokens and turns are its own; `total_cost_usd` is the session's so far

**Status:** accepted (T-041, 2026-10-14). **Narrows ADR 53 and ADR 54 and
edits neither**: the log's seventeen keys, its write and the route's group
shape stand. Answers `docs/debt/T-024-D2.md`.

**Context.** ADR 53 writes one line per `claude` call, a `--resume` retry
included, and records each field as the CLI gave it. Whether a resumed call's
figures count that call or the whole session was unknown, so ADR 54 summed
nothing and said what a per-attempt total would become under each answer. No
probe was needed to settle it: two real retries are already in the logs. These
are their attempts, read from `<hive_tasks_dir>/<task_id>/usage/calls.jsonl`
(`in` `input_tokens`, `cr` `cache_read_input_tokens`, `cc`
`cache_creation_input_tokens`, `out` `output_tokens`):

| task, role, round | call | in | cr | cc | out | num_turns | total_cost_usd |
|---|---|---|---|---|---|---|---|
| T-030 revisor r1 | phase | 24 | 892485 | 127973 | 13396 | 41 | 1.470297 |
| T-030 revisor r1 | shrink-retry | 4 | 127973 | 2639 | 1906 | 2 | 1.5551396 |
| T-031 implementador r1 | phase | 8 | 97668 | 37282 | 2827 | 11 | 0.3743616 |
| T-031 implementador r1 | gate-retry | 14 | 254190 | 11033 | 4261 | 14 | 0.5987396 |

Each pair shares one `session_id`, both lines are `measured: true`, and every
line's `raw_keys` holds `usage`, `num_turns` and `total_cost_usd`, so the
fields were the CLI's and not nulls.

**Decision.** Two answers, one per kind of field.

**Tokens and `num_turns` are per call.** A running total never goes down.
T-030's retry is below its phase on every token column and on `num_turns` (2
after 41), and on `duration_ms` too (15091 after 151653); T-031's retry has a
smaller `cache_creation_input_tokens` than its phase (11033 after 37282).
Neither fits a session total. T-030's cache read, 127973, is exactly the
phase's cache creation: the context that one resumed call read back, not a
sum, which would be at least 892485. So the four token fields and `num_turns`
of a line are that call's, and summing an attempt's lines is correct.

**`total_cost_usd` is cumulative over the session.** T-030's retry is smaller
than its phase on every token column, yet its `total_cost_usd` is larger
(1.5551396 after 1.470297). No per-call price can do that, so the figure
includes the phase's: the phase's 1.470297 plus about 0.0848 for the retry
itself. The argument needs no price table, only that cost does not fall as
tokens rise. T-031 fits the same reading (0.3743616, then 0.5987396, about
0.2244 for the retry) but does not prove it alone, since its retry read more
cache than its phase. So the cost of an attempt is its **last** line's
`total_cost_usd`, the cost of one call in it is the difference from the line
before it in the same session, and a sum of an attempt's cost lines is never
correct.

**Limits of the evidence.** Two retries, of two kinds, `shrink-retry` and
`gate-retry`, each the only retry in its attempt. No `review-retry` and no
attempt with two or more retries has been read. The CLI version is not on the
line (ADR 53 records none), so "one version" is inferred from both attempts
running on the same account on 2026-10-12, a few hours apart. `raw_keys` also
lists `modelUsage`, which the line does not record, so nothing here says how
the cost splits by model. If a later log contradicts this, with a token field
that only reads as a session total or a `total_cost_usd` that falls within one
session, this entry is what is wrong, and a new ADR narrows it.

**What it means for ADR 54's per-attempt total.** Of the two outcomes ADR 54
foresaw, each field takes the one its kind decided. Token fields and
`num_turns` may be summed over an attempt's `calls`. Cost is the last call's
`total_cost_usd`, never a sum. ADR 54's rule for nulls still holds: a null token
field in any line of the attempt makes that token total null, and a null
`total_cost_usd` on the last line makes the attempt's cost null, never taken
from an earlier line. A failover attempt on another account is a new session
and a new group, so a phase's cost is the sum of its attempts' last lines, and
a task's is the sum of its phases'.

**Consequences.** No code changes here. The route serves no total yet; adding
one is a new key on the group under its own ADR, as ADR 54 says, and that task
now knows what to compute. `docs/debt/T-024-D3.md`'s screen may print such a
total once it is served. `T-024-D2` is resolved by this entry, and its file
stays as a record. ADR 53's *Context* line that a retry's `usage` "is that one
turn, not the phase's" holds, with one call in place of one turn, since a
retry can take several turns (T-031's took 14). P4 is still not unblocked: a
correct per-attempt cost from a few tasks is not a median.

## ADR 65 — every phase runs on the model the config names, per role

**Status:** accepted (T-044, 2026-10-14). Lands refinement (1) of
`docs/plans/backlog.md` item 4; mirrors ADR 57, which did the same for the
effort, and edits neither ADR 57 nor the escalation.

**Context.** Every phase but the cartografo ran on `default_model`, so the
arquitecto's planning and the implementador's execution spent the same model.
The backlog item asked for a per-role override and said to make the split
data-driven first, from item 2's per-phase usage records. Those records exist
(ADR 53, ADR 64), but a few tasks' worth of them is not a median, and the
operator wants the split now: implementador and revisor on
`claude-sonnet-5-5`, arquitecto and auditor on `claude-opus-5-5`, set in the
operator's own `config.yaml`, not in this repo.

**Decision.**

- **`role_model`** maps a role to the model ID its phases run on. Its keys are
  the phase roles (`config.PHASE_ROLES`), the same set `role_effort` takes. It
  is optional: absent or empty, every phase runs as before.
- **The precedence** is the phase's own model, then `role_model`, then
  `default_model`. The only phase with its own model is the cartografo, whose
  `mapping_model` is passed explicitly and still wins, so a `role_model` entry
  for it is accepted and has no effect. `Config.model_for(role)` is the one
  place the last two steps live, and `run_phase` is the one place it is read.
- **One model per phase.** `dispatch_phase` passes the model it was given to
  every call the phase makes — the first one, the handoff resumes (ADR 58),
  the shrink retry, the gate and review retries — so a phase never spans two
  models.
- **The escalation is untouched.** Past `escalate_effort_after_round` a round
  runs at `escalated_effort` (ADR 57) on the same model as the round before;
  switching model on escalation is not part of this.
- **Checked at load.** An unknown role is refused, naming the valid roles, so
  a misspelt role cannot run on `default_model` without a word. A value that
  is not a non-empty string is refused. Which model IDs exist is the CLI's to
  say, not the config's.

**Consequences.** The split is the operator's judgment, made ahead of item 2's
usage records rather than derived from them; those records are what will say
whether it holds, and changing it is a config edit. Which model *ran* is still
recorded nowhere (the `model` row of the table in ADR 27 holds): `role_model`
is the configured model, and serving it, or a Role models screen, is a later
task's. Refinement (2) of the backlog item, escalating on signals other than
the round count, stays open.

## ADR 66 — `role_model` refuses `cartografo` and points at `mapping_model`

**Status:** accepted (T-045-S1, 2026-10-14). Amends ADR 65's accepted roles,
which it leaves as written; edits nothing else in ADR 65.

**Context.** ADR 65 let `role_model` take every phase role, the cartografo's
included, and said an entry for it is accepted and has no effect: the
cartografo's phase is always given `mapping_model` explicitly, and an explicit
model wins over `role_model`. A config that sets `role_model.cartografo`
therefore loads without a word and changes nothing, which is the silent
misconfiguration the load-time check was added to prevent.

**Decision.**

- **`role_model`'s keys** are the phase roles less the mapper:
  `PHASE_ROLES` minus `MAPPER_ROLE` — arquitecto, auditor, implementador,
  revisor. `PHASE_ROLES` itself is unchanged.
- **`cartografo` is refused at load** by `_load_role_model` in
  `dispatcher/config.py` with a `ValueError` that names `role_model`, says the
  cartografo's model is set by `mapping_model`, and names `mapping_model`.
- **Any other unknown key** is refused as before, and the roles that message
  lists are the four accepted ones, not `cartografo`.
- **`role_effort` is untouched**: it still accepts `cartografo`, because the
  cartografo's effort has no other setting.

**Consequences.** An operator who had `role_model.cartografo` in a config now
gets a load error where they had a no-op, and moves the value to
`mapping_model`. `config.example.yaml` and the root `README.md` still describe
ADR 65's set until the next sub-task of T-045 updates them.

## ADR 67 — the usage region prints figures in fixed formats and the served call names verbatim

**Status:** accepted (T-046, 2026-10-14). Decides the two choices
`docs/plans/front.md` *The usage task: what a task's `claude` calls cost*
binds to the Usage task. Narrows nothing: ADR 54 and ADR 64 stand, and the
console still sums nothing.

**Context.** The card leaves two things open: how a figure is formatted, and
whether `call_kinds` and a line's `call` are relabelled on screen. It says
both are one-screen choices unless a second screen shows spend, and only then
belong in `docs/ui.md`. One screen shows spend, so they are recorded here
and in `docs/implementations/T-046.md`, and `docs/ui.md` is not changed.

**Decision.**

- **Tokens and turns** print as whole numbers grouped by thousands with a
  comma, in the `en-US` format whatever the browser's locale: `1,234,567`.
  The locale is fixed so a test, a screenshot and an operator read the same
  string. Nothing is abbreviated (`1.2M`): an abbreviation is rounding, and a
  rounded token count cannot be checked against the log.
- **Dollars** print as `$` and four decimals: `$0.0421`. Two decimals would
  print a cached retry's sub-cent cost as `$0.00`, which reads as a recorded
  zero (`docs/ui.md` *Absent, empty and broken are three different things*).
- **`duration_ms`** prints as `N ms` under one second, seconds with one
  decimal under a minute (`12.3 s`), and minutes and whole seconds above
  (`4m 7s`, the shape `formatAge` already prints).
- **`recorded_at`** prints through `formatClock`, the UTC clock time the phase
  timeline uses for `saved_at`, with the served string as its `title`.
- **A null field** is `Absent`; **a value that is not a finite number** where
  a figure belongs is `Malformed` wanting a number. Neither is ever zero.
- **`call_kinds` and `call` print the served string verbatim**, in monospace:
  `phase`, `gate-retry`, `shrink-retry`, `review-retry`. The vocabulary is the
  dispatcher's (`dispatcher/dispatcher.py`, the `recorder.record` calls), a
  relabel table would be a second copy of that list in `front/` that drifts
  when a kind is added, and an unknown kind would need the verbatim fallback
  anyway. The strings are already readable.

**Consequences.** The three formatters live in `front/src/lib/format.ts`
beside `formatClock` and take a number already checked to be one; the guard
is at the render, on `docs/ui.md` *A value of the wrong shape is named, not
rendered and not dropped*. A second screen that shows spend moves these
rules into `docs/ui.md` as one entry and cites this ADR as where they came
from. A new call kind in the dispatcher needs no console change.

## ADR 68 — a usage line names the model it asked for and the models the CLI says ran

**Status:** accepted (T-047, 2026-10-14). Narrows ADR 53: its key set grows
by two, and nothing else in it changes. Takes up the opening ADR 53 left in
its *Consequences* ("a later task may add them as what was *requested*") and
ADR 65's "which model *ran* is still recorded nowhere". Edits neither ADR 54
nor ADR 64.

**Context.** Since ADR 65 each role runs on the model `Config.model_for` or
`mapping_model` names, and `dispatch_phase` passes it as `--model` to every
call of the phase. The usage log keeps no model, so nothing shows whether the
per-role split was applied or what each model cost. Real lines show
`modelUsage` in `raw_keys`: the CLI's result object carries an object keyed
by the model names that served the call.

**Decision.** Every line gains two keys, on every call kind — `phase`,
`handoff-resume`, `shrink-retry`, `gate-retry`, `review-retry` — and on every
account a failover tries:

- **`model`**: the `model` `dispatch_phase` was given and passed to
  `exec_claude`, as a string, or `null` when none was passed and the CLI chose
  its own default. It is the dispatcher's own fact, fixed for an attempt, so
  `_UsageRecorder` holds it beside the account and round, and it joins the
  dispatcher's half of the line, which is now ten keys. It is written whether
  or not the call was measured.
- **`models_used`**: the sorted key names of `raw["modelUsage"]`, names only
  and never its values, or `null` when `raw` has no `modelUsage` or it is not
  an object. An unmeasured line (`raw` empty) is therefore `null`. It keeps
  the CLI's own spelling on ADR 53's rule, and assumes nothing about the
  object's inner shape beyond "its keys are model names".

The line is nineteen keys: seventeen scalars plus `raw_keys` and
`models_used`, each a list of names or, for `models_used`, `null`.

**Why both and not one.** They answer different questions. `model` is what
was *asked for* — the weaker fact ADR 53 said such a key would be, and the one
that shows whether `role_model` was applied, which only the dispatcher knows.
`models_used` is what the CLI *reports ran*, which can differ: a CLI default
when `model` is `null`, a fallback, or a call served by more than one model
(the CLI's subagents run on their own). Either alone would let a reader take
one for the other. Neither is guessed: no default model is filled into
`model`, nothing is inferred from the role, and an absent or malformed
`modelUsage` is `null`, not `[]`.

**Consequences.** ADR 54's reader and `GET /api/tasks/<task_id>/usage` serve
the two keys unchanged, because lines travel verbatim; the api changes only a
comment that counted the keys. A line written before this has neither key, and
nothing back-fills it: a reader treats a missing key as absent, never as
`null` meaning "asked for the default". The console shows neither yet;
showing them is a separate front task. Summing or pricing per model is not
decided here and waits, like every total, for its own key under its own ADR.

## ADR 69 — the usage region prints `model` and `models_used` as served, and a missing key is its own reading

**Status:** accepted (T-049, 2026-10-14). Narrows ADR 68's *Consequences*
clause "The console shows neither yet" without editing it: the task detail's
usage region now shows both. ADR 67's rules stand, and the console still sums
and compares nothing.

**Context.** ADR 68 put two keys on every usage line written after it, and
none on the lines before. The region (ADR 67, `docs/plans/front.md` *The
usage task*) printed neither. Four readings have to stay apart: a line from
before ADR 68 (the key is missing), a call that asked for no model
(`model: null`), a call whose CLI reported no `modelUsage` (`models_used:
null`), and an empty `modelUsage` (`models_used: []`). `docs/ui.md` *Absent,
empty and broken are three different things* makes `Absent` mean "the
harness recorded nothing here"; a key missing from an old line is not that,
because the harness had no such key to record, and `model: null` is not that
either, because it is a recorded fact — nothing was asked for. The strings are
one region's choice, so they are recorded here and `docs/ui.md` is not
changed, on ADR 67's rule.

**Decision.** One slot per key, labelled `model` and `models used`, drawn on
every line behind the disclosure and in the *first call* block from
`phase_call`'s own keys, whether or not the line was measured (ADR 68 writes
`model` either way):

| Served | `model` slot | `models used` slot |
|---|---|---|
| key missing | *written before models were logged* | the same |
| `null` | *CLI default* | `Absent` (*not recorded*) |
| a string / a list of strings | the string, monospace | each name, monospace, in served order |
| `[]` | — | *empty list* |
| anything else | `Malformed` wanting *a string or null* | `Malformed` wanting *a list or null* |

A list holding a non-string keeps its other names and puts a `Malformed`
wanting *a model name* in that item's place (`docs/ui.md` *A value of the
wrong shape is named, not rendered and not dropped*). The two italic
readings are muted text and not `Absent`, so none of the four can be taken
for another. A missing key is tested as `undefined`: JSON carries no
`undefined`, so it means the key was not on the line.

The console does not re-sort `models_used`, does not flag a `model` missing
from `models_used`, does not derive a group-level model from several lines,
and does not guess a model from the role or the config.

**Consequences.** `UsageLine` in `front/src/lib/api/types.ts` gains both keys
as optional. No mock fixture holds a usage line (`listUsage` reads only the
api), so no fixture changes. A second screen that shows models moves this
table into `docs/ui.md` as one entry, as ADR 67 says of its formats.

## ADR 70 — what a commit leaves in the worktree is kept in the hive as a list and a patch, and every end of run names it

**Status:** accepted (T-050, 2026-10-14). Leaves `dispatcher/project_docs.py`
`_COMMIT_SCOPES` and `commit_excludes` as they are, and keeps the two log
warnings `dispatcher/docker_exec.py` `_warn_changes_outside` and
`_warn_excluded_changes`.

**Context.** `commit_worktree` holds the auditor's commit to `docs/` and
every commit away from the excluded paths, and names what it did not commit
only in a log warning. The worktree goes at merge and the edit with it. On
T-047 the auditor fixed a test comment, said in its handoff that it kept the
fix, and `main` never got it: the warning was the only trace. The scope is
not the bug. It exists so the phase after the review cannot change the
reviewed work unreviewed, and the excluded paths are ones no role may change
at all; committing either would undo the rule. What is missing is a record
that outlives the worktree and a sentence an operator reads without opening
the log.

**Decision.**

1. *What is recorded.* After every commit the cycle makes for a writer role
   (`_should_commit`), whatever `git status` still shows dirty in that
   worktree is what the commit left behind. That one rule covers both
   cases: after a scoped commit it is the out-of-scope edits plus the
   excluded ones, after an unscoped commit it is the excluded ones only.
   Each path is sorted into `outside` or `excluded` (an excluded path, or
   one under it, is `excluded`; the rest is `outside`).
2. *Where.* Two files per role under the task's scratch dir, in a
   `left-behind` subdirectory beside `handoffs` and `usage`:
   `<hive_tasks_dir>/<task_id>/left-behind/<role>.json` holds `role`,
   `round`, `saved_at`, `outside`, `excluded` and `patch` (the absolute path
   of the second file), and
   `<hive_tasks_dir>/<task_id>/left-behind/<role>.patch` holds a
   `git apply`-able diff of exactly those paths against the worktree's
   `HEAD`: `git diff --binary HEAD` for tracked ones and a `--no-index`
   diff from `/dev/null` for each untracked file. One pair per role,
   overwritten by its next commit, like a handoff. A commit that leaves
   nothing removes that role's pair, so a record always describes the last
   commit of that role and a clean phase records nothing.
3. *Never fails the phase.* Taking and writing the record is wrapped at the
   call site in `dispatcher/dispatcher.py`; any exception is one warning
   naming the task and role, and the phase carries on, as `_UsageRecorder`
   does for the usage log (ADR 53).
4. *Shown.* One line per record, on stderr, naming the task, the role and
   round, the `outside` and `excluded` paths, the patch file, and that the
   edit is not on the branch. `run-task` prints every record of the task
   when the cycle returns, whatever its outcome; `run-phase` prints the
   record of the role it ran; `merge-task` prints every record before it
   merges, and merges anyway with its exit code unchanged. The cycle's own
   merge (`merge_on_done`) logs the same lines as warnings before merging.
5. *Not applied.* Nothing commits, applies or deletes the kept edit. An
   operator who wants it reads the patch and applies it by hand on a branch
   of their choosing.

**Consequences.** The record is attributed to the phase whose commit left
the path, not to the phase that wrote it: the writers share one worktree, so
an implementador's excluded edit is recorded again by every later commit
until someone removes it. The record says what was dirty after which commit
and makes no claim about authorship. A stale record of a role that never
commits again stays until the task's scratch dir is gone; `round` and
`saved_at` date it. No api route or console screen reads the record yet.

## ADR 71 — an account is taken under one `flock` in `state_dir`, before the `/usage` probe, and every state-file write holds the same lock

**Status:** accepted (T-051, 2026-10-14). Closes the single-instance guard
of `docs/plans/backlog.md` item 3 by a narrower means than it names; the
orphan phase after Ctrl+C under the same item stays open.

**Context.** `dispatch_phase` in `dispatcher/dispatcher.py` picked an IDLE
account with `pick_idle_account`, probed it with `check_quota_ok` (a
`/usage` call that takes seconds), and only then wrote `BUSY` with
`state_machine.set_state`. Every writer in `dispatcher/state_machine.py`
is a read-modify-write of one JSON file finished by `_write_state`'s
tempfile and `os.replace`, with no lock. Two dispatcher processes running
two different tasks could both read one account IDLE, both probe it, both
write `BUSY` and both send a phase into the same container.
`tasks.acquire_lock` guards one task, not an account. The same unlocked
read-modify-write let one writer erase what another had just added: a
`set_state` that read the file before a concurrent `record_probe` wrote it
drops that `last_probe`, and the same holds for `rate_limited_at`.

**Decision.**

1. *Where the lock lives.* One lock file per state directory,
   `<state_dir>/.accounts.lock`, named by `state_machine.LOCK_NAME`. It is
   taken with `fcntl.flock(LOCK_EX)` on a descriptor opened for that one
   acquisition (`os.open` with `O_RDWR | O_CREAT`, mode 0644) and closed when
   the acquisition ends, which is what releases it. The file is created on
   first use and never deleted: unlinking a `flock` file lets two processes
   lock two different inodes.
2. *What it covers.* Every write to an account's state file: `set_state`,
   `record_rate_limit`, `clear_rate_limit`, `record_probe` and the claim
   below each hold it from their read to their `os.replace`. Nothing else is
   done under it — no `docker exec`, no probe, no task file write — so it is
   held for the length of one small file read and one write. Readers
   (`get_state`, `get_last_probe`, the api, `status`) never take it:
   `os.replace` already makes each read see a whole document, and the api
   reads `state_dir` over a read-only mount. The lock is not reentrant (a
   second `flock` on a new descriptor blocks even in the same process), so
   each public writer takes it once and calls unlocked internals.
3. *The claim.* `state_machine.claim(state_dir, account, task_id) -> bool`
   writes `BUSY` with that `current_task_id` and a fresh `busy_since` only if
   the account is IDLE (a missing file counts as IDLE), and returns whether
   it did. It is `set_state` with `only_from={IDLE}`; `set_state` gains that
   keyword, writes nothing and returns `False` when the account's state is
   not in it, and returns `True` otherwise. Callers that ignore the return
   keep today's behaviour.
4. *Claim before the probe.* `dispatch_phase` claims the picked account
   first and probes it second, and no longer writes `BUSY` after the probe.
   A lost claim adds the account to `tried` and moves on to the next pick,
   as a failed probe does. A probe that parks the account writes
   `PRE_COOLDOWN` or `COOLING_DOWN` over the claim, the same end state as
   when it parked an IDLE account; a probe that raises puts the account back
   to IDLE before re-raising, as the phase's own `except` does. Claiming
   first is what makes the probe's parking write safe: probing first would
   let a loser's probe park an account the winner has already started a
   phase on. The cost is that an account is `BUSY` for the seconds its
   probe takes, which `_waitable_workers` already reads as "running a
   phase", and the reaper's floor of `heartbeat_ttl_seconds` on
   `busy_since` already covers.
5. *Transitions decided across a probe are conditional.*
   `_recheck_cooling_accounts` decides to recover an account from a probe
   that ran outside the lock, so its write is
   `set_state(IDLE, only_from={PRE_COOLDOWN, COOLING_DOWN},
   forget_rate_limit=True)`: one locked write that recovers the account and
   drops `rate_limited_at` only if it is still parked, so it cannot overwrite
   a claim another process made meanwhile. `reap_stale_busy_accounts`
   writes with `only_from={BUSY}`. The writes made by the process that holds
   the account — the phase's release, its cooldown, its `except`, the
   parking in `check_quota_ok` — and `release-account`, which is an
   operator's override, stay unconditional.
6. *Not a global lock.* No lock is held for a run, a cycle or a phase, so
   two tasks on two IDLE accounts run their phases at the same time. A
   single-instance guard would serialise every run behind one process for
   the hours a phase lasts, to fix a race that needs only the moment of the
   claim. One dispatcher per host is not what this project wants:
   `docs/plans/projects.md` plans parallel work across projects.

**Consequences.** The lock holds only between processes that see the same
lock file: it relies on `state_dir` being one local filesystem shared by
every dispatcher process (one host, or one bind mount). `flock` over NFS or a
network share may not exclude, and two hosts with separate `state_dir`
copies are two pools, as they were before. A dispatcher killed while holding
the lock frees it, because the kernel drops a `flock` with its last
descriptor. The reaper's conditional write narrows but does not close one
window: a phase judged dead that ends, and an account re-claimed by another
process, both between the reaper's read and its write, would be reaped; the
reaper only fires past a stale heartbeat, so this needs a phase that
finishes in the same milliseconds it was judged dead. The orphan phase after
Ctrl+C (`docs/plans/backlog.md` item 3) is untouched: that is a dead
dispatcher's account, which this lock does not see. Anything that lists
`state_dir` must keep reading accounts by their own `<name>.json`; the lock
file is not JSON and is not an account.

*Amended after the merge.* Point 4's last clause was wrong: the probe can
last `_USAGE_PROBE_TIMEOUT_SECONDS` (120) plus the host backstop
`docker_exec.HOST_BACKSTOP_SECONDS` (60), longer than
`heartbeat_ttl_seconds` (120), so a claimed account could be reaped while
still probing. The reaper's floor is now `heartbeat_ttl_seconds +
_PROBE_WORST_CASE_SECONDS`, the sum of those two. The probe's cleanup also
catches `BaseException`, so a Ctrl+C during the probe gives the account back
instead of leaving it `BUSY`; that write is conditional on `BUSY`, like the
others in point 5.

## ADR 72 — the collector reads with the operator's token and takes writes with a token per agent, bound to its source_app

**Status:** accepted (T-052, 2026-10-14).

**Context.** The collector's `POST /events` and `GET /events` had no auth.
Anything on `ia_harness_net` could read every agent's events or forge one
under any `source_app`, and the agent containers on that network run
arbitrary code from cloned repos. The backlog item proposed one shared
token. One token does not fit, though: the agents must hold a credential to
post, and whatever an agent container holds, repo code running in it can
read. If that one token also read, every agent could read what the other
agents did. It also leaves `source_app` as a claim in the body that any
holder can set to anything.

**Decision.**

1. *Read and write are split.* `GET /events` takes `COLLECTOR_READ_TOKEN` as
   a bearer, checked with `observability/auth.py` `check_token`. That token
   is the operator's alone. In every compose file it is given to the
   collector and to no other service. A write token is refused on `GET`, and
   the read token is refused on `POST`.
2. *One write token per agent, bound to its source_app.* The collector reads
   the binding from one explicit map,
   `COLLECTOR_WRITE_TOKENS=<source_app>=<token>,...`. The map is assembled in
   compose from one `.env` key per account, `COLLECTOR_WRITE_TOKEN_<NAME>`:
   the account name uppercased, with `-` written as `_`. The stock map is
   `agent-cuenta1=${COLLECTOR_WRITE_TOKEN_CUENTA1},agent-cuenta2=${COLLECTOR_WRITE_TOKEN_CUENTA2}`.
   Each agent gets its own variable as `COLLECTOR_TOKEN`.
   - Entries split on the first `=`, so a token may contain one.
   - Whitespace around entries is stripped.
   - A trailing comma is allowed.
3. *The token decides the source_app.* The collector authenticates `POST`
   before it reads the body, and a missing or unknown token is a 401. It
   stores the source_app the token is bound to and ignores any `source_app`
   in the body. A leaked write token can only forge events as the agent it
   already belonged to. The lookup compares every entry in constant time and
   does not stop at the first match.
4. *No silent open mode.* `main()` refuses to start, exits 2, and names the
   variable in each of these cases:
   - the read token is unset, empty or whitespace;
   - the map is unset or empty;
   - an entry is malformed;
   - an entry's token is empty, which is what compose writes when `.env`
     lacks that account's key; the message names the source_app;
   - a source_app appears twice;
   - two agents share a token;
   - a write token equals the read token.
5. *The hook stays best-effort.* `hooks/emit_event.py` sends
   `Authorization: Bearer $COLLECTOR_TOKEN` when the variable is set. A
   refused post is one stderr line naming the HTTP status, and the hook never
   fails because of it.
6. *Compose spells every one of these bare, as `${VAR}`.* There is no default,
   for the same reason as the `DASHBOARD_` credentials: a default baked into
   the file would be a token every clone knows. `scripts/configure.sh`
   generates the read token and one write token per account with `gen_token`.
   `tests/integration/test_compose_invariants.py` checks each deployment, the
   split files together and the Coolify file alone. It asserts:
   - no agent gets the read token;
   - each write variable reaches its own agent and the collector, and no
     other service;
   - each agent's `COLLECTOR_TOKEN` is the variable that the map binds to
     its `SOURCE_APP`.

*Ruled out:*
- *One shared token*, for the reasons in Context.
- *Scanning the environment for `COLLECTOR_WRITE_TOKEN_*` and deriving the
  source_app from the suffix.* That mapping is implicit: a renamed or
  missing account would be undetectable, because a missing variable is
  simply absent, and the binding would live in a naming convention instead
  of beside `SOURCE_APP` in compose.
- *Mounting the tokens as files.* The other credentials already live in
  `.env`, and an agent container can read a mounted file as easily as its
  environment.

**Consequences.**
- An existing host's `.env` lacks the three keys. After this change the
  collector refuses to start until `scripts/configure.sh` is rerun or the
  keys are added by hand.
- Adding an account now means three edits beside its agent/dind pair: its
  `.env` key, its `COLLECTOR_TOKEN`, and its entry in the collector's map.
  The compose invariants fail if any one of them is missing.
- Rotating a token means editing `.env` and recreating the collector and
  that agent.
- The read api reads `events.db` directly and is unaffected.
- The dispatcher has a `collector_url` but never calls the collector, so it
  gets no token.
- Payload redaction, TLS, rate limiting and network segmentation are still
  open. A token travels in clear over `ia_harness_net` and over loopback.

## ADR 73 — a review that repeats the last one's finding escalates the next round's effort, compared on fingerprints and never on prose

**Status:** accepted (T-053, 2026-10-14). Builds part of refinement (2) of
`docs/plans/backlog.md` item 4; leaves the round-count rule of
`Config.effort_for`, `max_revision_rounds` and the usage record (ADR 53, 68)
as they are.

**Context.** The only escalation signal is the round count:
`escalate_effort_after_round` exceeded, the round runs at `escalated_effort`.
The backlog named two other signals, the revisor repeating the same
`CHANGES_REQUESTED` complaint and a result text coming back suspiciously
short, and warned against a heuristic over freeform `result_text` that never
fires as intended. The revisor's return had nothing to compare: its reasons
were spread across `pending`, `risks` and prose, and `pending` also holds
whatever else the next phase should know, so two reviews sharing a `pending`
line says nothing about the round being sent back twice for one reason.
`discarded_writes_prompt` already told a reviewing phase to put the change it
wanted "under `findings`", a field no schema had.

**Decision.**

1. *The field.* The revisor's schema gains `findings`, a list of strings:
   what sends this round back, one line each, empty when approving. Its
   description tells the revisor that a finding an earlier review raised and
   that still holds is copied verbatim. That description is the whole prompt
   change; `_role_prompt` is untouched. `render` prints it first among the
   lists, so the next round's implementador and revisor read the last
   review's findings in the task file. `handoff.findings_of` reads it.
2. *The comparison.* A review's reasons are its `findings` and the debt it
   rejected (`debt.rejected`, a finding by `run_task_cycle`'s own rule),
   each keyed by `debt.fingerprint`: lowercase, digits and hashes zeroed,
   punctuation to spaces, the normalization the debt index files by. A
   review repeats when its keys intersect those of the last review that sent
   a round back. Equality on a fingerprint is all: no similarity score, no
   stemming, no model asked. The verbatim instruction is what makes that
   enough; a reworded finding does not match, and that is a missed
   escalation the round count still catches, never a wrong one.
3. *What it does.* With `escalate_effort_on_repeated_finding: true`, a review
   that sends the round back and repeats makes every later implementador
   round of the cycle run at `escalated_effort` (`Config.effort_for` with
   `repeated_finding`, passed by `run_phase`). The revisor's own effort stays
   on the round-count rule: it is the implementador that keeps failing to
   fix the thing. The round-count rule is checked first and unchanged, so
   the signal can only escalate sooner. A round the gates blocked has no
   review and changes neither the last reasons nor the flag.
4. *Said once.* When it fires the cycle logs one info line naming the task,
   the round about to run, the effort it will run at and the repeated
   finding(s). Nothing fires on the last round, which has no next one.
5. *Off by default.* A config without the key is the round-count rule
   alone, exactly as before. With the defaults (escalate after round 2 of 3)
   the round count already escalates the only round the signal could, so it
   starts to matter once `escalate_effort_after_round` is raised.

**Not built: a suspiciously short result.** There is no threshold to compare
against. The length of a result text varies by role, by task and by how much
went to files under `paths` rather than into the return; with a schema the
`result` can be a CLI placeholder of fixed length whatever the work, and a
short structured return is what the byte budget asks for. Any cut-off would
be a number picked by hand that fires on a good terse phase and misses a bad
long one, the heuristic the backlog warned about. It stays open there.

**Consequences.** The signal is as good as the revisor's copying: a revisor
that rephrases a finding it still holds never fires it. Once fired the flag
stays for the rest of the cycle, like the round-count rule, rather than
dropping back when a later review finds something new. The usage record
(ADR 53, 68) does not carry the effort a phase ran at, so the fire line is
where the escalated level is written down; adding effort to the usage record
is its own change to that contract. Switching model on a signal is not built.

## ADR 74 — the hook redacts secrets before the POST, by fixed rules, and fails closed

**Status:** accepted (T-054, 2026-10-14). Builds the hook-payloads bullet of
`docs/plans/backlog.md` item 6; the collector, the read api and the rows
already in `events.db` are unchanged.

**Context.** `hooks/emit_event.py` posted the whole Claude Code hook event as
`payload`, and the collector stores it as it came: file contents, a `cat
.env`, a command line carrying a token, a tool's reply. ADR 72 put a token on
the collector; what an agent sends through it was still in clear. The hook
is copied alone into the agent image (`docker/agent/Dockerfile`), so whatever
redacts has to live in that one file, on the standard library.

**Decision.**

1. *Where.* `hooks/emit_event.py` redacts the event before the POST, with
   every rule, pattern and list in one block of module constants in that
   file, and a `redact(event, env)` that returns a new structure. There is
   no setting that turns it off.
2. *What is a secret name.* One pattern, used by rules 3, 6 and 7: a name
   containing `token`, `secret`, `password`, `passwd`, `passphrase`,
   `credential` or `apikey` (any case), or `key` as a word of the name —
   not touching a letter on either side (`API_KEY`, `key`, `ssh-key`) or
   camel-cased after a lower-case letter or digit (`apiKey`). `KEYBOARD` and
   `monkey` are not secret names.
3. *Environment values* (`env-secret`). The value of `COLLECTOR_TOKEN`, and
   of every variable whose name is a secret name and whose value is at least
   8 characters, is replaced wherever it appears in any string, longest
   value first. Shorter values are skipped: `SOME_KEY=1` would otherwise
   redact every `1` in every payload.
4. *Shapes*, in this order: PEM private key blocks, from `-----BEGIN …
   PRIVATE KEY-----` to its `END` line or, with no `END`, to the end of the
   string (`private-key`); `sk-ant-…` (`anthropic-key`); `ghp_`, `gho_`,
   `ghu_`, `ghs_`, `ghr_` and `github_pat_` tokens (`github-token`); AWS
   access key ids `AKIA…`/`ASIA…` (`aws-access-key-id`); the credential after
   `Bearer ` (`bearer-token`) and after `Authorization: Basic `
   (`basic-auth`), keeping the scheme word.
5. *Whole fields* (`secret-field`): a dict entry whose key is a secret name
   and whose value is a string has the whole value replaced.
6. *Pairs in text*: `"name": "value"` where `name` is a secret name
   (`secret-field`), and `NAME=value` — unquoted up to a space or shell
   separator, or a quoted string on one line, never `==` — where `NAME` is a
   secret name (`secret-assignment`). The name and the `=`/`:` stay.
7. *Secret files* (`secret-file`). When any string in `tool_input` names a
   file whose base name is `.env`, `.env.*`, `.envrc`, contains `credential`,
   ends `.pem` or `.key`, starts `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`,
   is `.netrc`, `.pgpass`, `.npmrc` or `.pypirc`, or contains `secret` and
   ends `.json`/`.yaml`/`.yml`/`.toml`/`.env`/`.txt`, then every string in
   `tool_response` except under the keys `type`, `filePath`, `file_path`,
   `path` and `cwd` is replaced whole, and so is every string in `tool_input`
   under `content`, `old_string`, `new_string` or `new_source`. Whatever tool
   it was, Read, Bash `cat`, Grep, Write or Edit.
8. *The marker* is `[REDACTED:<rule>]`, the rule names above in brackets;
   never a hash, a length or a prefix of the secret. A value already
   redacted is not redacted again.
9. *The shape is kept.* Keys, key order, lists and every non-string value
   come out as they went in; only strings change, so an event with no
   secret posts byte-identical to before.
10. *Fails closed.* If `redact` raises anything, the hook posts the event
    with `payload` `{"redaction": "[REDACTED:redaction-failed]"}` and the
    same `event_type`, prints one stderr line naming the exception's type
    and not its message, and does not fail the hook. The unredacted event is
    never posted.

**Left out, and why.** A secret in a shape no rule knows is posted as it
came: another vendor's key (OpenAI, Slack, Google, Stripe), a JWT, a
password in a URL (`https://user:pass@host`), an AWS secret access key
standing alone (40 base64 characters look like any hash), a `--token value`
flag with a space, a YAML `token: value` line in a file not named secret, a
secret split across two events or encoded. The vendor shapes are the stack's
own (Anthropic, GitHub, AWS); every extra free-text pattern costs false
positives on ordinary output and none is matched by an anchor as safe as a
prefix. No entropy heuristic: it is not deterministic in the sense this
needs, a threshold picked by hand. The rows already in `events.db` are not
rewritten.

**Consequences.** Redaction over-reaches by design: `max_tokens=4096`,
`sort_key=…` or `primary_key=True` in code an agent reads show a marker,
a file named `credentials.py` or `.env.example` loses its content in the
event, and the console shows less of what happened there. A secret that
sits in the environment under a name the pattern misses is caught only if
it has a known shape. The agent image must be rebuilt for the change to
reach a container; until then they post unredacted.

*Amended after the merge.* As merged, `_redact_text` and the walk in
`redact` returned any string that began with `[REDACTED:` unchanged, so a
tool output or prompt that opened with marker-like text carried the rest of
its secrets through (`"[REDACTED:x] ghp_…"` posted the token). Both early
returns are removed: every string goes through every rule. That is safe
because the pair and assignment rules already refuse to match a value that
is a marker, and no shape matches one, so redacting a marker again leaves it
as it is. `test_text_starting_with_a_marker_is_still_redacted` covers it.


## ADR 75 — the console edits the dispatcher's settings through an overrides file, written by a writer service of its own

**Status:** accepted (T-055, 2026-10-15). Design only; nothing is built. It
decides `docs/plans/backlog.md` item 0 and the *Role models write* bullet of
`docs/plans/front.md` tier 3. The build is `docs/plans/console-config.md`. It
leaves ADR 19's write-free api and tier 3's queue as they are.

**Context.** The operator wants to read and change the dispatcher's settings
from the console instead of editing `config.yaml` on the host. Item 0 holds
the operator's answers of 2026-10-15 and three proposals the operator agreed
to; this entry starts from them and does not reopen them:

- `config.yaml` stays as written and holds the defaults. The console writes
  only a separate overrides file, which `load_config` merges and validates.
  A reset is deleting a key.
- The writer is a small separate service with its own credential, not a
  write route on `observability/api/app.py`.
- Credentials and the login are a second task, through tier 3's queue.

Item 0 also fixes four constraints. The save is validated by the same
`load_config` the dispatcher runs. It is atomic, with the previous version
kept. It is attributed and logged. It takes effect on the next dispatcher
invocation.

Three facts of the tree shape the details:

- `dispatcher/cli.py` calls `load_config` once per verb, at the start, and a
  verb runs in a fresh `docker compose run`.
- `create_app` in `observability/api/app.py` loads the config once, at boot.
- Every mount of `config.yaml` is a single-file bind mount, `:ro`.

**One part of the proposals cannot hold as worded.** "Write access to that
file alone" cannot be a single-file bind mount. `os.replace` onto a path
that is itself a bind-mount point fails inside the container (`EBUSY`), so
the writer could not rename into it. A rename on the host would leave every
other container's single-file mount on the old inode, so the dispatcher
would go on reading the old file. The writer's one read-write mount is
therefore a **directory that holds the overrides file and nothing but its
own companions**: the previous version, the change log and the lock. It holds
no other setting, no credential, no state and no task file. That keeps what
the proposal was for: the writer can change the overrides and nothing else.

**Decision.**

1. *The overrides file.*
   - **Name and place.** `overrides.yaml`, in a directory `config.d` beside
     `config.yaml`. On the host that is `<repo>/config.d/overrides.yaml`,
     git-ignored like `/config.yaml`. In every container that mounts it, it
     is `/app/config.d/overrides.yaml`, next to `/app/config.yaml`.
     - The writer mounts the directory read-write.
     - The dispatcher and the api mount it `:ro`.
     - No agent container mounts it.
   - **How `load_config` finds it.** It derives the path from the path it was
     given: `config.d/overrides.yaml` in the same directory as the config
     file. So no new flag, environment variable or key is needed, and
     `--config /app/config.yaml` finds `/app/config.d/overrides.yaml`.
     `load_config` also takes an optional `overrides_path` keyword. The
     writer uses it to validate a candidate file before the candidate
     becomes the real one.
   - **Absent or empty.** A missing file, or one that is empty or holds only
     comments, means no overrides. The result is exactly today's.
   - **Format.** YAML, read with `yaml.safe_load`, the loader `config.yaml`
     already uses. The top level is a mapping from a top-level config key to
     its value. A database is overkill, as the operator guessed, and YAML
     lets the operator read the file on the host. The writer writes it with
     `yaml.safe_dump(sort_keys=True)` under a fixed two-line comment header
     that says which service wrote it and that deleting a key resets it.
   - **The merge rule: one rule for every key, nothing deep.** An override
     replaces the whole value of the top-level key it names. Then the
     merged mapping goes through every check `load_config` already makes.
     - *Nested maps* (`role_model`, `role_effort`) are replaced whole, not
       merged per role. To take one role out of a map `config.yaml` sets,
       the override carries the map without that role; `{}` empties the
       map. The console always reads and saves the whole map, so the rule
       is never felt as a surprise there. Its cost: a role that `config.yaml`
       later adds to an overridden map is hidden while the override stands.
       The read route marks every overridden key, which is how that stays
       visible. A deep merge was ruled out because it has no way to *remove*
       a role without a tombstone value, and a tombstone is a second rule.
     - *Lists* (`fallback_roles`) are replaced whole. Nothing appends.
   - **Removing a key.** An override cannot make a key absent from the
     merged result. It can only give a key a value. Absence keeps the
     meaning `load_config` gives it in `config.yaml`, such as no
     `primary_account` meaning a flat pool. A `null` override is refused,
     never read as "delete": reset is deleting the key from the overrides
     file.
   - **Which keys may appear.** `dispatcher/config.py` gains one table of
     every key `load_config` reads, with its class from part 5. The writer
     and `load_config` both read that table. An overrides file naming a key
     that is not *editable* is refused by `load_config` itself, whoever
     wrote the file.
   - **Strict types.** An override's value is type-checked by its key's kind:
     a boolean must be a YAML boolean, an integer must not be a boolean, a
     model ID must be a non-empty string. Then the merged mapping is
     validated as a whole. Today some keys are coerced rather than checked
     (`bool(raw.get("merge_on_done"))`, `max_revision_rounds` read unchecked), and
     those coercions stay for `config.yaml`, so no config that loads today
     starts failing. Only an override is held to the stricter check.
2. *Validation.*
   - **The writer refuses a bad save.** A save is accepted only if
     `load_config` accepts `config.yaml` merged with the candidate overrides
     file. Otherwise the save is refused with `load_config`'s message and
     nothing on disk changes.
   - **Repair is always possible.** The rule is about the result of the save,
     not the file as it stands. So when the file on disk is already bad, a
     save that repairs it is accepted, a reset of the offending key included.
   - **A bad file on disk anyway.** This can happen through a hand edit, a
     `config.yaml` change that breaks an override that was valid (an
     override naming a primary account the base no longer has), or a write
     from outside the writer. `load_config` raises a `ValueError`, as for
     any invalid config, and the verb exits before it probes, claims an
     account, takes a lock or makes a worktree. The message names
     `/app/config.d/overrides.yaml` and says to reset the key from the
     console or delete it from the file.
   - **No fallback.** The dispatcher never falls back to `config.yaml`
     alone. Running on settings the operator did not choose, such as an old
     quota threshold, is the silent change every loader in `config.py`
     refuses. The same holds for an overrides file that does not parse or
     is not a mapping.
   - **The api.** It loads the config at boot, so a bad file stops it at its
     next restart, as a bad `config.yaml` does today.
3. *The write.* All of it under an exclusive `flock` on the lock file in the
   directory, so two saves never interleave.
   - **Optimistic concurrency.** The writer reads the current file and its
     *revision*: the sha256 of its bytes, or `null` when there is no file. A
     save carries the revision it was based on. If that is not the current
     one, the save is refused with a conflict and nothing is written.
   - **The candidate.** The new mapping is written to a temp file in the same
     directory, with mode 0644 so the dispatcher and the api can read it,
     and validated with `load_config(..., overrides_path=<temp>)`.
   - **The previous version.** The current file is copied to
     `overrides.yaml.prev` by temp file and `os.replace`. That is one
     previous version on disk. The change log below holds every older value.
   - **The swap.** `os.replace` moves the temp file onto `overrides.yaml`.
     That is atomic, so a dispatcher starting at that moment reads the old
     file or the new one, never half of one. This is the
     `atomic-writes-copy-state-machine` recipe from `docs/learnings/`.
   - **The change log.** One JSON line is appended with `O_APPEND` to
     `changes.jsonl` in the same directory: `at` (UTC ISO 8601), `actor`,
     `via`, `key`, `op` (`set` or `reset`), `old` and `new` (the key's
     override before and after, `null` meaning not overridden),
     `revision_before` and `revision_after`. No value in it can be a secret,
     because no credential key is editable (part 5).
   - **If the log cannot be written,** the writer puts the previous version
     back with `os.replace` and answers 500, so no change stands without its
     log line. A crash between the swap and the append is the one window
     where a change can stand unlogged. `revision_before` on the next line
     shows the gap.
   - **Attribution.** `via` is the credential that authenticated the save,
     which is always `console` today. `actor` is the person the console says
     made the save. Until the login of `docs/plans/server-deployment.md`
     exists, the console has no signed-in person, and `actor` is the fixed
     string `operator`. After it, `actor` is the signed-in user, and only an
     `owner` may save (server-deployment *Decisions*, 2): a `reviewer` sees
     the screen without controls. Attribution is therefore as good as the
     console's own login, and the ADR says so rather than implying more.
4. *The writer service.*
   - **Language: Python, on Flask.** The constraint that decides it is that
     the save is validated by the same `load_config`, which is Python. A
     writer in any other language would need a second validator, and the
     two would drift. Flask is what the collector and the api already run.
   - **Where it lives.** A top-level package `config_writer`, with an
     `app.py`, a `Dockerfile` and its tests under `tests/config_writer`. It
     imports `dispatcher.config` and `observability/auth.py`, and nothing
     else of the harness. Not under `observability/`, because it is not
     observability, and that directory's api is the one tier 3 keeps
     write-free. `pyproject.toml`'s `include` gains `config_writer*`.
   - **Its own container, not a process in an existing one.**
     - *Not in the api's container.* The api's write-freeness is a property
       of its container: every mount `:ro`, asserted by
       `tests/integration/test_compose_invariants.py`. A second process
       there would need a read-write mount, and the api's process would get
       it too.
     - *Not in the dispatcher's container.* The dispatcher runs one verb and
       exits, so there is no long-lived process to share. It also holds the
       docker socket, and a web process beside the socket is what C-8 and
       tier 3's floor forbid.
     - So a separate container with no socket and no other writable mount is
       the only shape in which "write access to that directory alone" is
       true.
   - **The compose service.** `config-writer`, in `docker-compose.yml` and in
     `docker-compose.coolify.yml`, because the Coolify file is a whole deploy
     path (`docs/learnings/a-new-service-lands-in-two-compose-files.md`).
     - *Mounts:* `../../config.yaml:/app/config.yaml:ro` and
       `../../config.d:/app/config.d`, which is its only read-write mount.
       Nothing else: no docker socket, no `.hive`, no `dispatcher_state`, no
       projects, no credentials.
     - *The other services:* the dispatcher and the api each gain
       `../../config.d:/app/config.d:ro`.
     - *Port:* `127.0.0.1:8790:8790`, beside the api's 8789.
     - *Network:* a network of its own, `config_writer_net`, and **not**
       `ia_harness_net`. The agent containers run arbitrary code from cloned
       repos on `ia_harness_net` (ADR 72), and a service that changes quota
       policy should not be one hop from them.
   - **Its credential.** `CONFIG_WRITER_TOKEN`, a bearer checked with
     `observability/auth.py` `check_token`.
     - *Who holds it:* the writer and the console's server half, and no
       other service.
     - *How it is made:* it is spelled bare as `${CONFIG_WRITER_TOKEN}` in
       compose, and `scripts/configure.sh` generates it with `gen_token`.
     - *Missing:* the writer refuses to start if it is unset, empty or
       whitespace, and exits 2 naming it. That is ADR 72's "no silent open
       mode".
     - *No Basic auth:* no human calls this service directly.
   - **Its routes.** It speaks the api's envelope, `{"data", "warnings"}`.
     - `GET /settings`. Its `data` carries:
       - the base mapping, the overrides mapping and the revision;
       - one row per key of part 5's table: key, class, reason, effective
         value, and `source` (`config.yaml`, `override` or `default`);
       - the role names `role_model`, `role_effort` and `fallback_roles` may
         name;
       - the last 20 lines of the change log;
       - `error`: `load_config`'s message when the merged config fails,
         otherwise `null`.

       A failing merge is still a 200, so the console can show what is
       wrong and offer the reset that repairs it.
     - `PUT /settings/<key>`, with body `{"value", "revision"}`, sets an
       override.
     - `DELETE /settings/<key>`, with body `{"revision"}`, resets one.
     - Both take an `X-Actor` header, the person from part 3.
     - Refusals: 400 for an unknown or non-editable key or a malformed body,
       409 for a stale revision, 422 for a save `load_config` refuses, with
       its message.
     - The writer loads the config **per request**, unlike the api. Its job
       includes repairing a file that does not load, so it must not refuse
       to start over one.
   - **How the console reaches it.** Through the console's server half, the
     way it reaches the api (ADR 15, ADR 24). `front/src/server.ts` gets a
     second interception for `/settings` and `/settings/<key>`, forwarding
     `GET`, `PUT` and `DELETE` with `Authorization: Bearer
     <CONFIG_WRITER_TOKEN>` to `CONFIG_WRITER_URL`, which defaults to
     `http://127.0.0.1:8790`. Neither name takes a `VITE_` prefix. The api's
     GET-only `FORWARDED` whitelist does not change. Unlike `API_TOKEN`, the
     writer token is optional for the console: the console starts without it
     and the settings screen shows its error state naming the variable, so a
     host with no writer loses one screen and not the console.
5. *The editable-key table.* These are all the keys `load_config` reads. The
   three classes are *editable*, *read-only* in the console, and
   *credential*, meaning the credential belongs to the second task. Path,
   container and credential keys are never *editable*. A read-only or
   credential key is still shown on the screen, without a control.

   | Key | Class | Reason |
   |---|---|---|
   | `accounts` | read-only | Names containers. Adding an account is `docs/plans/server-deployment.md`'s, with a container template. |
   | `primary_account` | editable | C-3 amended makes the primary a setting a person changes from the console. It must name a configured account. |
   | `quota_threshold_pct` | editable | Quota policy the owner sets. `reserve_pct` is checked against it on the merged result. |
   | `reserve_pct` | editable | Quota policy. It must not exceed the threshold, and the merged check catches that. |
   | `pace_primary_week` | editable | A boolean policy switch (ADR 48). |
   | `quota_cooldown_seconds` | editable | A timeout that is already validated as a positive integer. |
   | `fallback_roles` | editable | Which phases the primary takes. It is a list, replaced whole. |
   | `default_model` | editable | Item 0 names it. The override must be a non-empty string. |
   | `role_model` | editable | Item 0 names it. It is a map, replaced whole, and validated against the roles. |
   | `mapping_model` | editable | Like `default_model`, for the cartografo (ADR 66). |
   | `default_effort` | editable | It is one of `EFFORT_LEVELS`, checked. |
   | `role_effort` | editable | It is a map, replaced whole, and validated against the roles and the levels. |
   | `escalated_effort` | editable | It is one of `EFFORT_LEVELS`, checked. |
   | `escalate_effort_after_round` | editable | A round count. The override must be a non-negative integer. |
   | `escalate_effort_on_repeated_finding` | editable | A boolean switch (ADR 73). |
   | `max_revision_rounds` | editable | It bounds a cycle's spend. The override must be a positive integer. |
   | `max_handoff_resumes` | editable | The cap C-4 amended puts in the config. It is already validated. |
   | `phase_timeout_seconds` | editable | A timeout that is already validated. |
   | `gates_test_timeout_seconds` | editable | A timeout that is already validated. |
   | `mapping_max_turns` | editable | A cap that is already validated. |
   | `merge_on_done` | editable | Item 0 names it. It is a boolean, and the override must be one. |
   | `gates_enabled` | editable | Item 0 names it. It is a boolean. |
   | `mapping_enabled` | editable | Item 0 names it. It is a boolean. |
   | `heartbeat_ttl_seconds` | read-only | Two dispatchers running at once must agree on it, or one reaps the other's live lock. `load_config` does not validate it. |
   | `heartbeat_interval_seconds` | read-only | It pairs with the TTL above, for the same reason. |
   | `projects_root` | read-only | A path inside the containers, fixed by the compose mounts. |
   | `hive_tasks_dir` | read-only | A path, the same as above. |
   | `state_dir` | read-only | A path, the same as above. |
   | `collector_url` | read-only | A service address on the compose network. |
   | `permission_mode` | read-only | It widens or narrows what every phase may do. The api's boot-time harness fingerprint (ADR 41 part 8) reads it. |
   | `allowed_tools` | read-only | A standing grant to every phase. `docs/plans/command-requests.md` is where grants are given. |
   | `update_project_before_task` | read-only | Turning it on needs a credential or deploy key on the host that the console cannot see. That is the second task's ground. |
   | `git_credentials_path` | credential | A path. Renewing the GitHub token at that path is the second task. The key itself is never edited. |
   | `project_keys_dir` | credential | A path to the deploy keys (ADR 56). It is the same as above. |
   | `vibe_kanban`, `local_board`, `vibe_kanban_mcp_url` | read-only | They are read only to be refused (ADR 59), and an override naming one is refused as well. |

   Count by row: 22 *editable*, 11 *read-only* (10 settings and 1 row for
   the 3 refused keys), and 2 *credential*. Every key item 0 lists is
   *editable* except the accounts. "The timeouts" in item 0 is read as the
   four timeouts and caps that are already validated. The two heartbeat
   keys are left out, for the reason in their rows.
6. *When a change takes effect.*
   - **The unit is one invocation.** A verb loads the config once, at its
     start, and keeps that `Config` until it exits. A change applies to the
     **next invocation** that loads it: the next `run-task` or `run-phase`,
     a `run-task` that a held run (exit 75, ADR 52) re-invokes, and
     `merge-task` and `status` too.
   - **A task that is running keeps the settings it started with** for every
     phase, revision round and resume left in that invocation. That
     includes `max_revision_rounds`, the efforts, the models and
     `merge_on_done` at its end. Nothing signals a running dispatcher, and
     nothing re-reads the config inside a cycle. A cycle whose budget,
     model or round limit could change halfway would be a run no person
     authorized in that shape (C-4).
   - **So the settings are per invocation, not per task.** A task run in two
     invocations can run its later phases on the newer settings. Each usage
     line records the model of its call (ADR 68). The effort is not recorded
     (ADR 73, *Consequences*).
   - **The api.** It reads the overrides at its **next restart**, because
     `create_app` loads once (`docs/learnings/a-cfg-derived-row-field-is-frozen-at-boot.md`).
     Until then the Pool screen's served thresholds can lag a saved
     `quota_threshold_pct` or `reserve_pct`. The settings screen says so.
     It reads its own values from the writer, which loads per request.

**Ruled out.**

- *A write route on the api.* The operator's first thought, but it ends
  ADR 19's write-free api and tier 3's floor.
- *Editing `config.yaml` in place.* It loses the comments, makes reset
  impossible to tell from a value, and puts the defaults and the operator's
  choices in one file.
- *A database.* It is overkill, as the operator said, and the dispatcher
  would need a client for it.
- *Deep merge with tombstones,* for the reason in part 1.
- *A single-file mount,* for the reason in Context.
- *Re-reading the config between phases,* for the reason in part 6.

**Boundary of the second task, the credentials and the login.** It is the
Tokens task of `docs/plans/front.md`. It goes through tier 3's queue and a
worker, and never through a docker socket in a web process:

- renewing the GitHub token at `git_credentials_path`;
- the deploy keys under `project_keys_dir`;
- the Claude login: `claude auth login` run in an account's container, its
  URL shown in the console, and the code pasted back.

It writes credential files and reaches containers. This writer does
neither, and it never holds a credential. Nothing in this entry decides
that task's queue, worker or screens. Whether `update_project_before_task`
becomes *editable* is that task's to decide, once the console can see that
a credential exists.

**Charter.** No ruling is needed and none is written.

- *C-4.* It governs starting a run, and a save starts nothing. Changing a
  quota threshold is a person's act, the same act as editing `config.yaml`
  on the host today. The operator answered item 0's question 1 yes on
  2026-10-15.
- *C-3 amended.* It already says the primary is changed from the console.
- *C-12 amended.* A save is not a verdict, so the owner-only rule in part 3
  comes from `docs/plans/server-deployment.md` *Decisions*, 2, not from
  C-12.
- *What would need a ruling.* If someone wants a reviewer to save settings,
  or wants a save to start or stop a run, that is a charter question, not
  this ADR's.

**Consequences.**

- An operator who edits `config.yaml` by hand and also overrides the same
  key from the console sees the override win. The screen shows the source of
  every value.
- A `config.yaml` edit can break an override that was valid. The dispatcher
  then refuses every verb until the override is reset. That is the price of
  having no fallback.
- `config.example.yaml`, `scripts/configure.sh` and the root `README.md`
  must each document `config.d` and `CONFIG_WRITER_TOKEN`
  (`docs/learnings/a-config-key-has-three-homes.md`). No `Config` field is
  added.
- A stack started before the build has no `config.d` directory. Docker
  creates it empty on the first `up`, owned by root, which the writer (root
  in its container) can write and the others can read.

## ADR 76 — an expired login parks the account as AUTH_EXPIRED, which only the operator's re-login and `release` clear

**Status:** accepted (operator session, 2026-10-15). The dispatcher and the
operator verbs only; the api already serves the state as it is and the
console learns the new value in a task of its own. Numbered 76 because
T-055's branch holds ADR 75.

**Context.** On T-055 the implementador phase on cuenta1 came back with
`Failed to authenticate: OAuth session expired and could not be refreshed`.
Nothing in the dispatcher knew that shape. The quota probe before it had
already returned a `/usage` report with no session or week lines, which the
unreadable-report rule waves through, so the account was picked; the phase
then failed as an ordinary error, and the account went back to IDLE to be
picked again. An expired login is not a counter: it does not reset with
time, `/usage` (local statistics) can look healthy through it, and only the
operator can renew it, interactively, in the container.

**Decision.**

1. *Recognised.* `is_auth_error(result)` is true when the result is an
   error (`raw["is_error"]`) and either `api_error_status` is 401 or the
   text carries one of a fixed list of the CLI's login phrases (`failed to
   authenticate`, `oauth session expired`, `oauth token has expired`,
   `invalid api key`, `please run /login`, `authentication_error`). 403 is
   left out: it is a permission refusal, not a login one. A result that is
   not an error never matches, whatever it says.
2. *Where.* Both places a container answers: the quota probe in
   `check_quota_ok`, and the phase in `dispatch_phase`. The rate-limit check
   runs first in each, so a refusal that is both stays a rate limit.
3. *A state of its own.* The account is set to `AUTH_EXPIRED`, with an
   ERROR log naming the account and the fix (`docker exec -it <container>
   claude`, `/login`, then `release`). No rate-limit stamp is written:
   nothing about the quota was learned.
4. *A phase refused on its login fails over.* `dispatch_phase` releases the
   task's lock and tries the next account, resuming the same session the
   way a rate-limit failover does. Nothing is committed for the refused
   attempt.
5. *Never re-probed, never waited for.* `_recheck_cooling_accounts` looks at
   PRE_COOLDOWN and COOLING_DOWN only, and `_waitable_workers` counts only
   accounts on a counter; AUTH_EXPIRED is neither, so a re-probe cannot
   bring it back on a healthy-looking `/usage`, and a worker parked on its
   login does not hold the primary's fallback back. `release` is the one
   way out, as for any manual park.
6. *Visible.* `status` notes the fix on an AUTH_EXPIRED account; `status
   --probe` reports a probe that hits an expired login (reported, not
   recorded, like every probe result there); `release` from AUTH_EXPIRED
   says the next phase sent there parks it again if the login was not
   renewed.

**Left out.** Detecting the expiry before a phase, by a probe that needs the
login (the `/usage` probe does not, reliably); renewing the login from the
dispatcher or the console, which is the credentials task of ADR 75's plan;
the console's rendering of the new state, a front task of its own
(`front/src/lib/api/types.ts`'s `AccountState` and the pool screen's colour
map).

**Consequences.** One more state the operator clears by hand, on purpose. An
expired account costs one refused attempt, not a loop. A new wording of the
CLI's login error that hits none of the phrases and carries no 401 falls
back to the old behaviour, an ordinary failed phase; the list is extended
when one is seen.


## ADR 77 — the automatic approve merges a finished task only when seven named conditions hold, per project, off by default

**Status:** accepted (A-3 of `docs/plans/autonomy.md`, 2026-10-15). The
dispatcher only; the push of the base is A-4 and the console shows nothing
new yet.

**Context.** `docs/plans/autonomy.md` *Decisions*, 1, asks for a merge
without a person, but only when what a person would look at is already
known to be fine, and with a comment saying what was checked either way.
`merge_on_done` (ADR 52's cycle) merges unconditionally; the pre-merge
check (A-2) and the surface read (A-1) are the pieces this needs. Two
shapes had to be fixed: what "protected" means, and how a project turns it
on before `projects.md`'s project record exists.

**Decision.**

1. *The key.* `auto_approve` in `config.yaml` is a map of project slug to a
   real YAML boolean (`auto_approve: {myproj: true}`); anything else, or a
   bare boolean for every project, is refused at load. A slug not listed is
   off. It moves into the project record when `projects.md` builds one.
   `protected_paths` is a map of slug to a list of non-empty path entries,
   read with the surface's semantics.
2. *Where it runs.* Where `merge_on_done` runs, after the auditor, in
   `run_task_cycle`. A project with the key on gets the check whatever
   `merge_on_done` says: the conditions win over the unconditional merge.
   A project without it runs exactly the old path.
3. *The conditions,* each evaluated, every failure named
   (`dispatcher/auto_approve.py`, a pure function over what the cycle has):
   - `gates`: the last implementador round's gate report exists and has no
     BLOCKING or ASK finding. No report (gates off, or a run that did not
     finish) fails it: a project that wants the approve runs the gates.
   - `revisor`: the verdict is APPROVED, with no `findings` and no debt
     rejected or read as a block.
   - `auditor`: the auditor's handoff is `complete`, with nothing `pending`,
     no debt declared by the auditor itself, and no debt filed for the task
     this cycle. The auditor's schema has no findings field, so this is the
     reading of "no finding"; a prose return fails it.
   - `pre-merge`: the A-2 check passed. It runs even when another condition
     already failed, so a conflicting branch still moves to `conflict`.
   - `surface`: the task's *Surface* names paths (none is the whole repo,
     never approved), the branch's changed paths could be read
     (`git diff --name-only --no-renames <target>...<branch>`, read after
     the pre-merge check), and each is inside the surface or one of the
     append-only logs.
   - `protected`: no changed path matches the protected list.
   - `authorized`: the cycle was started by a person. `run_task_cycle`
     takes `authorized`, default true: a `run-task` is typed by the
     operator. The queue (A-5) passes its own value.
   Changed paths that cannot be read fail both `surface` and `protected`,
   never pass as an empty change.
4. *The protected list.* Fixed: `docs/charter.md`, `**/.env*` (an env file
   anywhere in the tree, not only at the root), `config.yaml` (the root's,
   the harness's own config), `docker/compose/**`, `.data/**`. Then the
   configured credential paths (`git_credentials_path`,
   `project_keys_dir`), kept only when they fall inside the project's
   checkout and made repo-relative, since a path outside it cannot be
   changed by a branch. Then the project's `protected_paths`.
5. *The comment.* Under `## automatic approve` on the task: on a refusal,
   each failed condition and why, the task left `done` (or `conflict`) and
   its branch kept for `merge-task`; on a merge, each condition and what was
   checked, the target and the merge commit, and the merge recorded as any
   other; on an approve whose merge git refused, that it did not land and
   why.

**Consequences.** Turning it on is one line per project, and a refused task
says exactly what to look at. A task with no Surface paths, a project with
the gates off, or an auditor that answers in prose is never merged by
itself, on purpose. `authorized` is not something the dispatcher can verify:
a scripted `run-task` counts as typed. Nothing is pushed until A-4.

## ADR 78 — the base is pushed after an approve, fast-forward only, with the credential the fetch already uses

**Status:** accepted (A-4 of `docs/plans/autonomy.md`, 2026-10-15). The
dispatcher only; the task-branch push of C-5 (`review.md` step 5) and the
queue that stops on a refused push (A-5) are not built here.

**Context.** `docs/plans/autonomy.md` *Decisions*, 4, makes the push of the
base the dispatcher's, after a merge on an automatic approve (ADR 77) or on
`merge-task --push`, never forced, and leaves to this task where the
credential lives (`review.md` *Decisions*, 4, "an ADR written when step 4 is
built"). Two credentials already reach the remote, both for the fetch of
ADR 36: the https:// token in git's `store` format at
`/run/secrets/git-credentials` (from the host's
`.data/credentials/git-credentials`), and, preferred when present, the
project's deploy key at `<project_keys_dir>/<slug>/id_ed25519` (ADR 56). ADR
36 said read-only scope was enough "because nothing in `dispatcher/`
pushes". That stops being true here.

**Decision.**

1. *The credential.* The push uses exactly what the fetch uses, through one
   function (`docker_exec._remote_access`) that both call: the deploy key
   over SSH when the project has one, the mounted https:// token otherwise.
   A project never has one credential for reading and another for writing.
   It is never an environment variable (the `docker exec` argv is readable
   by `ps`, ADR 36), and neither the api nor the board service holds it
   (`review.md` *Decisions*, 4). A project that pushes needs write scope on
   that credential: *Allow write access* on its deploy key, or a token that
   can push; one without it comes back as a refused push in git's words.
   This supersedes ADR 36's "read-only scope is enough" for a project that
   pushes, and only for it.
2. *What is pushed, where.* `docker_exec.push_base` pushes the branch the
   project's checkout is on, `refs/heads/<b>:<branch.<b>.merge>`, to
   `branch.<b>.remote`, and nothing else: no remote is guessed, no task
   branch is pushed. Never `--force`, never `--force-with-lease`, never a
   `+` refspec, so a remote that moved refuses the push. It runs in the agent
   container `cleanup_container` picks, inside the merge's
   read_owner/restore_owner window.
3. *When.* Only after a merge landed: in `_auto_approve_and_merge` after an
   approved merge, and in `merge-task` only with `--push`, when the merge
   landed or the base already held the branch (so a re-run after a refused
   push pushes without merging twice). `merge_on_done` (`_merge_task_branch`)
   never pushes: it is not an approve.
4. *The outcomes* (`docker_exec.PushOutcome`): `pushed`; `no-upstream` (no
   remote, a branch that tracks nothing or tracks a branch of the same clone,
   a detached checkout), which is said and is not an error; `push-refused`
   (the upstream moved, the credential was refused, the remote is neither
   SSH nor https://, or the push could not run at all). On a refusal the
   local base keeps the merge and nothing is reset, rebased or rewritten.
5. *What a refusal leaves.* After an automatic approve: the task stays
   `done` with its `merged` block, and the `## automatic approve` comment
   says the push did not land and why; a push that landed is named in the
   same comment. `_auto_approve_and_merge` returns `ApproveResult(merged,
   push)`, whose `push.refused` is what the queue (A-5) stops on. After
   `merge-task --push`: the merge is recorded as any other, the reason goes
   to stderr, and the command exits 4 (`EXIT_NOT_PUSHED`), distinct from 1
   (the branch did not land). The CLI writes no comment on the task; the
   person who ran it reads the reason.

**Consequences.** A pushing project needs one setting changed on GitHub
(write access on its deploy key), and nothing new on the host. The honest
cost: both credentials are mounted read-only into every agent container,
because the fetch and now the push run there by `docker exec`, and the
phases run in those same containers. A credential with write scope is
therefore readable from a phase's container, which is weaker than the
charter's "no role ... holds the credential, which stays on the dispatcher's
side" (C-5, *Amended* 2026-10-11) reads; no role is given a command that
uses it, and no prompt names it. Moving the git calls that hold a
credential into a container no phase runs in is the fix, and it is not this
task's. The comment on the mount in `docker/compose/docker-compose.agents.yml`
("needs no write scope") is now true only of a project that does not push.
A refused push is never retried by the dispatcher; reconciling the base with
what the remote gained is a person's decision (C-1), after which
`merge-task --push` pushes it.

## ADR 79 — a queue runs its tasks once each, in dependency order, against a limit of measured tokens

**Status:** Accepted (2026-10-17).

**Context.** Charter C-4 (*Amended*) lets the owner start several tasks with
one yes: each task in the list runs for one cycle, a failed task is not
retried, and the queue stops at a limit set in measured tokens. Task A-5 of
`docs/plans/autonomy.md` builds that queue (`dispatcher/queue.py`,
`python -m dispatcher run-queue`). Several things the plan names had to be
fixed to a single meaning: what a token is, when the limit is checked, what
"out of quota" does to a task, and what happens across a kill.

**Decision.**
1. *The plan and the yes.* `run-queue --project P --task-id A B ... --limit N`
   reads every task's description, orders the tasks by `depends_on` with a
   stable topological sort (list order breaks ties), and refuses an empty
   list, a duplicate, a missing or unparseable description, a non-positive
   limit and a dependency cycle. It prints the order, the roles a cycle runs,
   the parallelism (only 1; B-3 is not built and `--parallel` other than 1 is
   refused), the limit and whether the project auto-approves, then asks for
   the yes unless `--yes` is given. That yes is the authorization every cycle
   in the queue receives (`authorized=True`).
2. *Measured tokens.* The spend of one usage line is input + output +
   cache_creation + cache_read tokens, unweighted, each field per call
   (ADR 64); a null field counts zero. A line with `measured: false`, or with
   all four fields null, adds nothing and is counted as unmeasured, and the
   summary says how many there were. `total_cost_usd` is never read. A
   task's spend is the usage-log lines written after the offset recorded
   when it started.
3. *When the limit is checked.* Before each task, and before each phase,
   through a `may_start_phase` hook `run_task_cycle` now takes. A phase that
   has started is let finish, so a queue can pass its limit by at most one
   phase. A cycle stopped by the hook is held, not blocked, and its task is
   recorded `limit_reached`; the queue stops with status `limit_reached`.
4. *Once each, and out of quota.* No task is started twice. A cycle held
   before it spent anything (no account was free) never began, so its task
   goes back to pending and the queue waits for an account, looking every
   five minutes, without spending. A cycle that spent and was then held,
   blocked or split is recorded as such and not retried. A task whose
   dependency failed is skipped with a comment; a task whose dependency is
   merely unmerged (auto-approve off, or a person's merge pending) makes the
   queue end `waiting`, and `--resume` takes it up later.
5. *The file and a kill.* The queue lives in `<state_dir>/queues/<id>.json`,
   written atomically before and after every task. `--resume <id>` reloads
   it; a task found `running` was cut off mid-cycle and is recorded
   `interrupted` with its spend counted, never restarted; the board is not
   touched, and a lock the dead cycle left is a person's to clear.
6. *A refused push.* The cycle hands its `ApproveResult` to an `on_approve`
   hook. A refused push (ADR 78) stops the queue with status `push_refused`,
   which a resume does not lift.
7. *Exit codes.* 0 every task finished; 1 a task failed, the queue is
   waiting, or the yes was declined; 4 a push was refused; 5 the limit was
   reached (`EXIT_LIMIT`, new).

**Consequences.** The owner's yes covers a bounded spend that the dispatcher
measures from its own log rather than from anything a phase reports. The
bound is soft by one phase, which the confirmation does not yet say. Without
auto-approve, a queue of dependent tasks runs only the first of each chain
and then waits for a person. Waiting for quota calls the cooling-account
recheck, which runs a `/usage` probe and spends nothing. Parallel cycles
(B-3) will need the spend read per cycle rather than from one offset.

## ADR 80 — a project's base is locked for each operation on it; a held lock is waited on, bounded, then refused

**Status:** accepted (B-1 of `docs/plans/autonomy.md`, 2026-10-17). The base
lock only; B-3's surface lock and the scheduler that runs tasks in parallel
are not built here.

**Context.** Several operations move a project's base checkout: the
dispatcher's update before a task (`update_project_branch`), the automatic
merge (`_merge_task_branch`), the automatic approve with its pre-merge check,
merge and push (`_auto_approve_and_merge`, ADR 77 and 78), the mapper's
commit, and an operator's `merge-task`. Each one brackets its git calls with
`read_owner`/`restore_owner`. With one task at a time and nobody at the CLI
they never met. Two cycles in parallel, or an operator's merge while a cycle
updates, can interleave on one checkout, and two owner windows can cross so
that one restores ownership while the other's git is still writing. The task
lock (`acquire_lock`) is per task and does not cover this.

**Decision.**

1. *The lock.* One file per project, `<state_dir>/base-locks/<slug>.lock`,
   a JSON record of `owner` (`host:pid:uuid8`, new for each acquisition),
   `host`, `pid`, `operation`, `acquired` and `heartbeat`. Each read-check-write
   on it runs under a short `fcntl.flock` on `<path>.guard`, and each write is
   atomic (`context_transfer._write_atomic`), so two contenders never both
   believe they took it. Only the owner that holds it can release it or
   refresh it.
2. *Liveness.* The holder refreshes the heartbeat from a daemon thread every
   `heartbeat_interval_seconds`. A heartbeat within `heartbeat_ttl_seconds`
   is live. Past the TTL, a holder on this host whose pid is still alive is
   still live (a hung git is not a dead holder), and the lock is not broken.
   A stale holder on another host, a dead local pid, or a corrupt record is
   dead, and the lock is broken and taken. A live holder is never broken.
3. *When the lock is held.* The caller waits, polling every 2 seconds, for
   up to `BASE_LOCK_WAIT_SECONDS` (300). That is longer than the default
   TTL, so a crashed holder is reaped within the wait. After that the
   operation is refused with `BaseLockHeldError`, which names the holder,
   its operation and since when. Every base operation is short, so a wait
   longer than this means something is wrong, and the dispatcher reports it
   rather than queueing behind it without end. What each caller does with
   a refusal:
   - the cycle's update ends the cycle BLOCKED before any phase runs;
   - `merge_on_done` does not merge, and a warning says why;
   - an automatic approve records a refused `pre-merge` condition in its
     `## automatic approve` comment, and the task stays `done`, not merged;
   - `merge-task` prints `not merged` and the reason, and exits 1.
4. *The window.* The lock is taken before `read_owner` and released after
   `restore_owner`, so owner windows never cross. It is held for that one
   operation and no longer. In an automatic approve the pre-merge check, the
   merge and the push are one locked operation: the check's verdict is about
   the base as it stands when the merge lands. `push_base` therefore takes no
   lock of its own, and it runs inside the merge's window. The lock is not
   reentrant, and no base operation calls another while holding it.
5. *What is locked.* The mapper's commit is locked, because it commits what
   becomes the project's mapping docs. The writing phases' commits go to the
   task's own branch, and they take no lock.

**Consequences.** Base operations on one project are serialized across the
dispatcher's threads and processes and the CLI on the same `state_dir`. The
cost is a wait of up to five minutes, and on a timeout a refused operation
that a person or the next cycle has to retry; nothing is retried
automatically. A holder that hangs while alive on this host is never broken,
so a stuck git holds the base until someone kills it, by design: breaking a
live holder is how two writers end up on one checkout. The lock assumes one
`state_dir` shared by everyone who moves the base; a second host with its own
`state_dir` is not covered.

## ADR 81 — the append-only logs merge by union, ADR numbers are handed out in blocks per task, and a number used twice stops the merge

**Status:** accepted (B-2 of `docs/plans/autonomy.md`, 2026-10-17). ADR
numbers 79 and 80 are held by the tasks planned beside this one,
which is the scheme below applied by hand.

**Context.** `docs/plans/autonomy.md` *Decisions*, 7. Two tasks that run at
once both append to `docs/decisions.md`, `docs/ROADMAP.md`'s Results log,
`docs/implementations/` and `docs/learnings/`. Each append lands at the end
of the file, so git sees two edits to the same place and the second merge
conflicts on what is not a conflict at all. And two arquitectos that each
read the log, see ADR 12 as the highest and write ADR 13, produce two ADR 13s
whatever git does.

**Decision.**

1. *Union merges.* `.gitattributes` marks the four logs `merge=union`: a
   merge of two appends keeps both, ours first, with no conflict. A project
   that wants the same carries the same attributes; the dispatcher does not
   write them into a project.
2. *A counter per project.* `dispatcher/adr_numbers.py` keeps
   `state_dir/adr/<slug>.json`, `{"next": N, "blocks": {task: [first,
   last]}}`, apart from the account state files. It is read and rewritten
   under an `flock` on `<slug>.lock` and written to a temporary file in the
   same directory and `os.replace`d, as `state_machine` writes the account
   state: a crash leaves the old file or the new one, and two processes or
   threads never hand out one block twice.
3. *A block per task.* When a task's arquitecto starts (the cycle and
   `run-phase` both go through `run_phase`), the dispatcher reads the
   project's `docs/decisions.md` from its checkout and reserves a block of
   3 numbers (`DEFAULT_BLOCK`) starting at `max(next, highest + 1)`, where
   `highest` is the highest `## ADR N` heading in the log, struck-through
   ones included and fenced code skipped. A re-run of the same task's
   arquitecto is given the block it already holds. The prompt carries "Your
   ADR numbers are N to N+2 ...". An unused number is a gap, and gaps are
   allowed. When the block cannot be taken (no container, no readable
   state), the phase runs without the line and step 4 still holds.
4. *A gate after the merge.* The pre-merge check (ADR 76/77's), once its
   merge of the base is clean, reads `docs/decisions.md` in the merged
   worktree and fails when an ADR number or a whole `## ` heading appears
   twice (`gates.decisions_log`). That is a broken record, not broken code,
   so it runs before the test gate and whether or not `gates_enabled` is
   set, and it sends the task to `conflict` with the duplicate named, the
   same as a merge git refused. A branch already holding the base needs no
   merge and is not checked.

**Consequences.** This repository's own `docs/decisions.md` is under the
scheme from here on: its merges are union merges and its duplicates are
caught by a test (`test_this_repositorys_own_log_has_no_duplicates`) as well
as by the gate when the harness merges itself. An attribute names a whole
file, so `docs/ROADMAP.md` merges by union outside its Results log too: two
branches that edit the same line of its other sections keep both lines
instead of conflicting, and that is for the reviewer to see. The block size
is a guess (`autonomy.md`, *Open questions*); a task that needs a fourth ADR
takes a number past its block and relies on the gate. The arquitecto's duty
text in `project_docs` still says "the next number"; the prompt line, which
comes later, is what names the numbers.

## ADR 82 — the arquitecto drafts a plan's tasks into the board's drafts directory, and only `accept-drafts` turns a draft into a task

**Status:** accepted (A-6 of `docs/plans/autonomy.md`, 2026-10-17).

**Context.** `docs/plans/autonomy.md` *Decisions*, 9. Cutting a decided plan
into tasks is work an agent can do. The owner's part is to read the cut and
approve it, and until the owner does, nothing the agent wrote may run. A queue
(ADR 79) needs tasks that carry descriptions, `depends_on` and, for B-3, a
Surface it can read as paths. A draft has to come out in that shape or be
refused, and accepting one must not need the owner to retype it.

**Decision.**

1. *One phase, a run like any other.* `dispatch draft-specs --project <slug>
   --plan <path>` runs one arquitecto phase through `dispatch_phase`, under a
   fresh draft id `d-<UTC timestamp>-<4 hex>`. That id is the run's task id,
   so the account claim, the lock, the worktree (`agent/task/<draft id>`), the
   usage records and the task file all key on it. On success the task file
   ends `drafted`, a status nothing runs. The plan path must be relative to
   the project's root and may not contain `..`. The prompt is its own
   (`_draft_prompt`), not `_role_prompt`: there is no description to embed,
   and the run must leave the repo as it found it. It still carries the git
   rule, the Bash rule and the handoff note.
2. *Where drafts live.* `<board>/drafts/<draft id>/<name>.md`, one file per
   build step (`D1.md`, `D2.md` ...), outside every worktree. Each file opens
   with YAML frontmatter whose one key is `depends_on`, a list of sibling
   draft names, followed by the task description. `accepted.yaml` beside the
   drafts records each accepted draft's name and the task id it became.
3. *The section rules.* A draft is held to the rules every description is
   held to (`## Goal` and `## Done when`), and also needs `## Surface` and
   `## Out of scope` (`tasks.DRAFT_REQUIRED`). Its Surface has to name at
   least one path as `surface.parse` reads it: an empty Surface means the
   whole repo, which no queue can run beside anything. Its `depends_on` may
   name only drafts of its own set, never itself, and never in a cycle.
   `check_drafts` judges the whole set and names every problem at once.
   `draft-specs` runs it on what the phase wrote and exits 1 on any problem.
   The set stays on disk either way, so the owner can fix a file by hand
   rather than pay for the run again.
4. *Accepting.* `dispatch accept-drafts <draft id> [--only NAME ...]` files
   each targeted draft as a `pending` task. It gives each one the next free
   `T-<n>`, counting both the task files and the board's description
   directories, in the drafts' name order. It stores the draft's description
   as written and rewrites `depends_on` from sibling names to the new ids.
   Without `--only`, every draft not yet filed is targeted, and the whole set
   is held to the rules. With `--only`, only the named drafts are targeted and
   judged, so one bad draft does not stop the rest. A targeted draft whose
   sibling dependency is neither targeted in the same call nor accepted
   before is refused: a dependency on a draft is one nothing can ever merge.
   An unknown name or an already filed draft is refused. Everything is
   checked before anything is written. The ids go into `accepted.yaml` before
   any task file, so a call that died part way is finished by the same call
   with the same ids, and no draft is ever filed twice.
5. *A draft never runs.* `tasks.write_description` refuses a draft id (and
   `drafts`), so a draft id can never carry a description. Because of that,
   `queue.plan` refuses one ("has no description") and `run-queue` exits 2
   before showing a queue. `open_cycle` refuses one before anything else and
   names `accept-drafts`, so `run-task` exits blocked. A draft runs only under
   the task id `accept-drafts` gives it.
6. *Its model.* `role_model` accepts the key `arquitecto-draft`
   (`config.DRAFT_ROLE`). When it is unset, a draft run uses the arquitecto's
   model, and failing that the default. The effort is the arquitecto's.

**Consequences.** A plan becomes tasks for the cost of one arquitecto phase
plus the owner's reading, and accepted drafts go straight into `run-queue`.
A draft run leaves behind a worktree, an `agent/task/<draft id>` branch and a
`drafted` task file on the board, and nothing reaps them yet. Editing a draft
before accepting it is editing the file; nothing records that it was edited,
and `accepted.yaml` records only what was filed. The prompt asks for no repo
changes, but nothing enforces that. The arquitecto is a writer role, so
anything it leaves in its worktree is committed to the draft branch, and that
branch is never merged: no task carries its id. A real `draft-specs` run, the check on the cut it
produces (*Done when* of A-6), is still to be made and needs the owner's
authorization.


## ADR 83 — a parallel queue serializes each write window by surface, maps once, and tells the implementador when the base moved under its plan

**Status:** accepted (B-3 of `docs/plans/autonomy.md`, 2026-10-17). It
narrows `docs/plans/projects.md` *Decisions*, 5 ("within a project,
later"): a queue started with `--parallel` above 1 runs up to that many
cycles of one project at once, under the rules below. Every other start
still runs one cycle per project. Number 82 is reserved for A-6.

**Context.** `docs/plans/autonomy.md` *Decisions*, 6. A-5's queue (ADR 79)
runs one cycle at a time, so a project with several accounts idle uses one.
Running two cycles of one project at once is safe while they read. The
mapping, the arquitecto's plan and the gates are each confined to the
task's own branch, and the base lock (ADR 80) and the union merge of the
logs (ADR 81) already cover the operations they share. What is not safe is
two implementadores writing the same files on two branches. The second
merge then conflicts, or worse, merges cleanly over a plan that no longer
holds.

**Decision.**

1. *Slots.* `run-queue --parallel N` runs up to N cycles at once. The
   default is 1, and N may not exceed the number of configured accounts,
   since there is still one phase per account. With 1 the queue runs
   exactly as ADR 79 says, through the same code. A free slot goes to the
   first task in the confirmed order that can start, once an account could
   take its phase. A task whose dependency is still running waits, like
   one whose dependency is not merged. The limit is read live: the spend
   of the finished tasks plus what the running ones have written so far.
   When it is reached, no cycle starts another phase. A refused push stops
   every running cycle at its next phase, and those cycles are recorded
   `limit_reached` with a note saying why.
2. *The write window and the surface lock.* A cycle's write window runs
   from its first implementador until its merge or the end of its cycle.
   Before that phase it takes the project's surface lock
   (`dispatcher/surface_lock.py`), which is one record per holder under
   `state_dir/surface-locks/<slug>/`. It may take the lock only when no
   live holder's surface overlaps its own (`surface.overlaps`). So the
   append-only logs never collide, and a whole-repo surface collides with
   everything. Every check-and-write runs under an `flock`. Liveness works
   like the base lock's: a fresh heartbeat, or a live pid on this host. A
   dead holder's record is broken on the next look. Among one queue's
   waiters, a task waits while an earlier task in the confirmed order
   wants an overlapping window, so the order decides a contested lock as
   well as a free slot.
3. *The free roles.* The mapping and the arquitecto take no surface lock
   and run as soon as a slot is free, whatever the surfaces. The mapping
   runs once per queue. A second task that needs it waits for the first
   and checks `_needs_mapping` again. If the index is still missing (the
   first task's map stays on its branch until it merges), that task runs
   unmapped, as a failed mapping would leave it. It does not map a second
   time.
4. *Merging the base into the branch at the window, and the stale-plan
   note.* On taking the lock, the cycle merges the base into its branch,
   under the base lock, using the pre-merge's own detached checkout
   (`premerge_base`, then `adopt_premerge`). If the branch already holds
   the base, nothing is said and no phase is spent. If the merge changed a
   path that the task's surface or the arquitecto's *Changed* lines name,
   the round-1 implementador's prompt carries a note. The note names the
   tasks whose merges came in (read from their `Merge agent/task/<id>`
   commits) and the paths. It says the plan predates them, and tells the
   implementador to end `blocked`, saying what to replan, if the plan no
   longer holds. A merge that would conflict is aborted and named in the
   same note, and the pre-merge check before the merge still stops on it.
   When the noted implementador blocks, the cycle ends blocked there.

**Consequences.** Two arquitectos may plan overlapping surfaces at once.
The second one's plan is then checked against what the first merged, by
the note and not by a replan, and replanning is the implementador's call
to block back. A clean merge of the base at the window is adopted without
its own gate run, because the implementador's gates run on that branch
next. A map made by a task that has not merged is not seen by later tasks
in the queue. They run unmapped, which costs context but not correctness.
Two cycles may look for the last idle account at once. The loser's next
phase is held, and a hold before any spend sends the task back to wait, as
in ADR 79. The surface lock is per `state_dir`, like the base lock. Fair
shares between projects, the console's slots screen, and more than one
phase per account remain out of scope.

## ADR 84 — two phases at once on one Max account: go for B-4

**Status:** accepted (T-056, the measurement part of B-4 of
`docs/plans/autonomy.md`, 2026-10-17). T-056 was not dispatched, so it holds
no reserved block. 84 is the next number after the highest one in this file,
and the counter under `state_dir/adr/` had not been started.

**Context.** `docs/plans/autonomy.md` *Decisions*, 8, makes
`max_concurrent_phases` (B-4) depend on a measurement: whether one Max
account runs two phases at once without the second throttling the first.
`docs/implementations/T-056.md` records the method and the figures.

**Decision.**

1. *Threshold.* *Go* when the median wall time of the concurrent runs is no
   more than 25% above the median of the solo runs, and no run is refused
   (no 429, no rate-limit retry, no error result). The owner set this
   threshold before round 2 ran.
2. *Result.* Go. The concurrent median was +11.4% over solo (397.4 s
   against 356.6 s). Per output token it was +2.3%. None of the nine runs
   was refused. A pair finished its two runs in about 60% of the wall time
   that two runs back to back take.
3. *What it does not buy.* Each run costs the same share of the account's
   five-hour window whether it runs alone or beside another, about 2–3
   points of a Max x5 session per six-minute run. Two phases at once reach
   the account's session ceiling (`reserve_pct`) in half the wall time.
   B-4 trades wall time for nothing else, so the ceilings and the queue's
   limit (ADR 79) keep their meaning per phase and need no change.
4. *What follows.* T-057 (an account holds a list of holders) and T-058
   (`max_concurrent_phases`, default 1) may be built. The default stays 1,
   and the owner raises it per account, only on a Max account.

**Consequences.** The measurement was read-only and short (three solo runs,
six concurrent ones, one afternoon, one account). If a real implementador
pair, running tests and builds, comes out past the threshold, that
supersedes this ADR, and the default of 1 makes going back a config edit.

## ADR 85 — an account's state file holds a list of holders, and the quota and login states are account-wide

**Status:** accepted (T-057, the state machine half of B-4 of
`docs/plans/autonomy.md`, 2026-10-17). Narrows ADR 71's point 3, the claim,
and does not supersede it: the lock, what it covers and the order of claim
and probe all stand.

**Context.** ADR 84 says *go* for more than one phase on one account. The
state file in `dispatcher/state_machine.py` had room for one: `state`,
`current_task_id` and `busy_since`. `claim` took an account only from
`IDLE`, and every write replaced the whole document, so the release a phase
makes (`set_state(..., AccountState.IDLE)`) would drop a second phase
running beside it. Nothing may claim with a capacity above 1 until T-058, so
this has to land with every existing caller answered as before.

**Decision.**

1. *The shape.* The document keeps `state`, `rate_limited_at` and
   `last_probe`, and gains `holders`: a list, in claim order, of
   `{"task_id", "busy_since"}`, each holder with its own stamp. `state` is
   always written as what `get_state` answers: the parked state if the
   account is parked, otherwise `BUSY` with any holder and `IDLE` with none.
   `current_task_id` and `busy_since` are still written, as the first
   holder's (null, and no `busy_since`, with no holder).
2. *The old shape.* A document with no `holders` key reads as one holder,
   `current_task_id` with `busy_since`, when its `state` is `BUSY`, and as
   none otherwise. Nothing rewrites a file just to convert it.
3. *The first holder lives in the old keys too.* Where the list and the two
   top-level keys disagree, the top-level keys win for the first holder.
   They are all that a reader or a writer built for the old shape can see or
   change — an image not yet rebuilt, or the test helper that backdates a
   stamp by editing the file — and the first holder is the oldest claim,
   the one `get_current_task` and `get_busy_since` answer for.
4. *Per-holder writers.* `claim(state_dir, account, task_id, capacity=1)`
   admits a phase while the account has fewer than `capacity` holders, is
   not parked and does not already list that `task_id`. `release(state_dir,
   account, task_id)` removes that one holder and leaves the others and any
   parked state; it returns whether it removed one. `park(state_dir,
   account, state)` parks the account and keeps its holders, and
   `unpark(state_dir, account, *, forget_rate_limit=False)` lifts a park, if
   there is one, and keeps them too. Each holds `_locked` (ADR 71) from its
   read to its write. `get_holders` returns the whole list.
5. *Account-wide states.* `PRE_COOLDOWN`, `COOLING_DOWN` and
   `AUTH_EXPIRED` belong to the account, not to a phase. While one is set,
   `get_state` answers it whatever the holders, and `claim` refuses. The
   holders that were running stay listed until each is released, and lifting
   the park leaves the account `BUSY` if any remain.
6. *`set_state` keeps its whole-account meaning.* `IDLE` leaves no holder
   and no park. `BUSY` leaves exactly one holder, `current_task_id` with a
   new stamp. A parked state parks the account with no holder. `only_from`
   is checked against `get_state`'s answer. A parked `set_state` drops the
   holder because every caller that parks today is the process holding the
   only holder, and it moves on without releasing it. A park that kept the
   holder would list a phase that is not running there, and
   `operator._held_task_ids` would read that task, which is by then live on
   the next account, as a live lock on the parked one, so `release-account`
   would refuse it. `park` and `unpark` are the writers that keep holders,
   and T-058 moves the callers onto them and onto `release`.

**Consequences.** Until T-058 every account holds at most one phase, and
`dispatcher/dispatcher.py`, `dispatcher/operator.py` and
`observability/api/app.py` get the same answers as before. At a capacity
above 1, each `set_state` call in those files would drop the other holders,
so T-058 has to replace all of them: the phase's releases and its two
`except` paths, the parks in `check_quota_ok`, `_park_auth_expired` and
after a 429, the recovery in `_recheck_cooling_accounts` (by `unpark`),
both writes in `reap_stale_busy_accounts`, and `release-account`. A reader
built before this ADR sees only the first holder of a file written after it.
`record_rate_limit`, `clear_rate_limit` and `record_probe` are unchanged:
they rewrite the document they read, so they carry the holders with
everything else.

## ADR 88 — `max_concurrent_phases` per account, default 1; a phase releases only itself, and a park keeps the others

**Status:** accepted (T-058, the dispatcher half of B-4 of
`docs/plans/autonomy.md`, 2026-10-17). Builds on ADR 84 and ADR 85.

**Context.** ADR 85 gave the state file a list of holders and the writers
`claim(..., capacity=)`, `release`, `park`, `unpark`. Every caller in
`dispatcher/dispatcher.py` and `dispatcher/operator.py` still used
`set_state`, which replaces every holder, so a second phase on one account
would be dropped by the first one's release, park or reap.

**Decision.**

1. *The key.* Each `accounts` entry takes `max_concurrent_phases`, an
   integer of at least 1, default 1. A bool, a non-integer or a value below 1
   is refused at load. With every account at 1 the picker, the claim and
   every write answer exactly as before. `--parallel` stays the ceiling on
   phases at once; this only adds slots.
2. *The pick.* An account is available while it is not parked and holds
   fewer phases than its `max_concurrent_phases`. The order is the primary
   last, then, within each of the two groups, an account with no holder
   before one with a free slot beside a running phase, then config order.
   So a worker's second slot is taken before an idle primary: the primary is
   the operator's console and spending it is the cost the `fallback_roles`
   and `_waitable_workers` gates exist for. Those gates are unchanged.
3. *A phase releases only itself.* A phase that ends, well or badly, and
   every error path between its claim and its return call
   `release(state_dir, account, task_id)`.
4. *A park keeps the others and then releases its own.* A quota or login
   park (`PRE_COOLDOWN`, `COOLING_DOWN`, `AUTH_EXPIRED`) is written with
   `park`, so the other phases running there keep their hold and finish.
   The phase that parked the account is moving to another one, so it then
   calls `release` for itself (ADR 85 point 6 is why: a listed holder that
   is not running there reads as a live lock). Park first, release second:
   the other order leaves a moment where the account is open and a free
   slot can be claimed on it.
5. *Leaving a park* is `unpark`, which is a no-op on an account that is not
   parked; the recheck keeps `forget_rate_limit=True`. An account leaving a
   park with holders left is `BUSY`, not `IDLE`.
6. *The reaper judges each holder,* on any account, parked or not, by that
   holder's card heartbeat and its own `busy_since` under the same floor and
   timeout as before, and releases only a dead holder. A holder with no
   stamp gets one (`state_machine.stamp_holder`) instead of a whole-account
   rewrite.
7. *`set_state` stays only where a whole-account write is meant:*
   `release-account` with no task id, which frees every holder and lifts the
   park. `release-account --task <id>` frees that holder alone and leaves the
   park.

**Consequences.** Raising `max_concurrent_phases` is the owner's config
edit, per account, and only on a Max account (ADR 84 point 4); lowering it
back to 1 is the way out, and a holder already above the new capacity is not
evicted, only no new one is admitted. The observability api and the console
still show the first holder only, through `get_current_task`; `dispatch
status` shows all. Fair share of slots between projects is not decided here.

## ADR 91 — the Coolify compose mounts the deploy keys, and not the https:// credential file

**Status:** accepted (T-059, authorized by the owner 2026-10-17).

**Context.** `docker/compose/docker-compose.coolify.yml` mounted neither git
credential (`docs/plans/projects.md`, *What exists today*, the SSH bullet), so
a Coolify deploy could not fetch or push (ADR 78) a private project. The split
files mount two: the directory `.data/credentials/projects` (ADR 56) and the
file `.data/credentials/git-credentials` (ADR 36). A bind mount whose source
is missing gets a directory created in its place, which is harmless for the
first and breaks the second unless the file exists before `up` — a step a
Coolify import has no place for.

**Decision.** The Coolify file mounts `../../.data/credentials/projects` at
`/run/secrets/projects`, writable in `dispatcher` (so `project-keygen` can
write a pair) and `:ro` in every agent, as the split files do. It does not
mount the https:// file. A private project deployed through Coolify uses its
deploy key, with *Allow write access* when the project pushes (ADR 78).
`tests/integration/test_compose_invariants.py` holds both compose paths to
the deploy-key mounts.

**Consequences.** On Coolify, a project with no deploy key reaches only a
public https:// remote; there is no token fallback. Adding the
`git-credentials` mount there later needs a way to create the file before the
first `up`, and is a decision to revisit this ADR, not an oversight to fix.

## ADR 94 — a draft run's arquitecto is a reader: a detached checkout of the base, never committed, removed when the run ends

**Status:** accepted (T-060, decided by the owner 2026-10-17). It supersedes
the part of ADR 82's *Consequences* about the worktree, the branch and the
commits, and the words "the worktree (`agent/task/<draft id>`)" in its
*Decision* 1. The rest of ADR 82 stands.

**Context.** ADR 82 runs `draft-specs` as one arquitecto phase through
`dispatch_phase`, and the arquitecto is in `docker_exec.WRITER_ROLES`. So a
draft run checked out a new `agent/task/<draft id>` branch in a writer
worktree, and `_should_commit` committed whatever the phase left there to a
branch no task carries and nothing merges. The prompt asks for no repo
changes; nothing enforced it, and nothing removed the worktree or the branch.

**Decision.**

1. *An explicit keyword, not a role.* `dispatch_phase` and
   `docker_exec.create_worktree` take `at_base: bool = False`.
   `run_draft_specs` passes `at_base=True`; nothing else does. It names what
   the phase is given, a checkout of the project's base, and the reader
   behavior follows from that: the base branch is already checked out in the
   project clone and git will not check one branch out twice, so the checkout
   is detached, and a detached checkout has no branch a commit could land on.
   A flag on the call rather than a new role name keeps `WRITER_ROLES`,
   `role_model`, the skills, the schema and the effort all keyed on
   `arquitecto` as ADR 82 has them.
2. *The checkout.* With `at_base=True`, `create_worktree` rebuilds
   `worktrees/<draft id>/<role>` (so `worktrees/<draft id>/arquitecto`) as the
   reviewing roles' checkout is rebuilt, `git worktree add --detach <path>
   HEAD`, run in the project clone: the commit the clone's `HEAD` points at.
   No branch is created or checked out, writer role or not.
3. *No commit.* `dispatch_phase` skips the commit and the left-behind record
   (`_should_commit`, `_record_left_behind`) when `at_base` is set, whatever
   the role. `_should_commit` itself is unchanged.
4. *The review-writes retry does not fire.* `_refuse_review_writes` stays
   keyed on `WRITER_ROLES`, so it returns early for the arquitecto. Its
   prompt (`handoff.discarded_writes_prompt`) asks for the change under
   `findings` and speaks of the task branch; the arquitecto's schema has no
   `findings` and a draft has no task branch, and a draft's output is the
   files in the drafts dir, which no checkout holds. Instead, the cleanup
   logs a WARNING naming the paths the phase left dirty in the checkout
   before it removes them, so a draft that broke the prompt's rule is on
   record.
5. *Removed when the run ends.* `run_draft_specs` removes
   `worktrees/<draft id>/` and prunes git's worktree bookkeeping
   (`docker_exec.remove_task_worktrees`) in a `finally`, after success, after
   a failed phase and after an exception, in `cleanup_container`'s container,
   with ownership read before and restored after as `_drop_review_worktrees`
   does. Best-effort: a failed removal is a WARNING and never the error the
   run ends with. The one exit that skips it is `LockHeldError`: the lock is
   taken before the checkout is made, so a run refused its lock made nothing,
   and a checkout at that path belongs to the run holding the lock; removing
   it would pull the tree out from under a live phase. That run removes its
   own when it ends.
6. *The task file stays.* `<hive tasks>/<draft id>.md` still ends `drafted`
   on success, and its `usage/` log stays: it is how a draft set is found and
   what the run cost. It is not part of the checkout and is not removed.

**Consequences.** A draft run leaves no branch and no worktree, and commits
nothing; its whole output is the drafts dir, the task file and the usage log.
`tasks.check_drafts` and `accept-drafts` are untouched. A normal task's
arquitecto is unchanged: the writer worktree on `agent/task/<task id>`, and
its commit. Leftovers of runs made before this ADR are not reaped; nothing
here looks for them. A failover inside one draft run rebuilds the checkout at
the same path, so the resumed session finds the same working directory.

## ADR 97 — the console is the `console` compose service, built for Node by `NITRO_PRESET` in its image

**Status:** accepted (T-061, decided by the owner 2026-10-17, who authorized
the edit of `docker/compose/**`).

**Context.** The console (`front/`, ADR 15, ADR 24, `docs/charter.md` C-8) ran
only as `bun run dev` on the host. `docs/plans/server-deployment.md` *How it
would be built*, step 3, puts a TLS proxy in front of it, and a proxy needs a
service to point at. `front/vite.config.ts` is protected and builds through
`@lovable.dev/vite-tanstack-config`, which hands nitro `defaultPreset:
"cloudflare-module"` — a fallback, not a `preset` — so an unconfigured build
targets Cloudflare. nitro resolves its preset from the config's `preset`, then
`NITRO_PRESET`, then `SERVER_PRESET`, and only then `defaultPreset`; the Lovable
config deletes `NITRO_PRESET` only inside a Lovable sandbox (`LOVABLE_SANDBOX=1`
or `DEV_SERVER__PROJECT_PATH` set). Both read from the installed packages on
2026-10-17. `API_TOKEN` was spelt `${API_TOKEN:-}` because no service in the
compose files needed it; the console needs it and refuses to boot without it
(`front/src/lib/api/server-env.ts`).

**Decision.**

1. *One service, both files.* `docker-compose.yml` and
   `docker-compose.coolify.yml` each get a `console` service, written right
   after `api`: built from `docker/console/Dockerfile` with the repo root as
   context, like every other image; `restart: unless-stopped`; `depends_on:
   [api]`; on `ia_harness_net`; published on `127.0.0.1:8790:8790` in both
   files, since the Coolify file publishes its other services on loopback as
   well. 8790 is the retired Jinja board's port (ADR 32), freed by the service
   this one replaces. No `volumes` at all and no docker socket: the console
   reads only through the api (ADR 15).
2. *The Node target is chosen by an environment variable in the image's build
   stage*: `ENV NITRO_PRESET=node-server`, and nothing else. Not `nitro: {
   preset }` in `vite.config.ts`, which is protected and would also change the
   Lovable round-trip's own Cloudflare build (C-9); not a `nitro.config.*` in
   `front/`, which the Lovable config looks for and which would retarget every
   build, the host's `bun run build` and Lovable's included. The variable
   exists only inside the image build, so every other build is untouched.
3. *The image.* Multi-stage: a build stage on the same Node base as the agent
   image with the same bun binary copied in (`docker/agent/Dockerfile`), which
   copies `front/`'s manifests first and runs `bun install
   --frozen-lockfile` (so `bunfig.toml`'s release-age floor holds), then the
   sources by explicit path, then `bun run build`; and a runtime stage on the
   same slim Node base that copies only `.output/` and runs
   `node .output/server/index.mjs` as the image's `node` user, with
   `HOST=0.0.0.0` and `PORT=8790`. The build context is filtered by
   `docker/console/Dockerfile.dockerignore` (BuildKit's per-Dockerfile ignore,
   which replaces the root `.dockerignore` for this build): an allowlist of
   `front/`, minus `node_modules`, `.lovable/`, build outputs and any `.env*`
   or `*.local`. The explicit `COPY` paths keep those out even under a builder
   that ignores that file.
4. *Spelling.* `API_TOKEN` is spelt bare, `${API_TOKEN}`, in the console **and
   the api** of both files. One variable takes one spelling per file (the
   invariant compares a set of one), and since a plain `up -d` now starts a
   service that refuses to boot without it, an unset `API_TOKEN` is no longer a
   state the file runs without: compose's warning is the feature, as for the
   `DASHBOARD_` pair. The api's own behaviour without it (ADR 7) is unchanged;
   only the warning is new. `API_BASE_URL=http://api:8789` is a literal.
   `CONSOLE_PROJECT` is optional and spelt `${CONSOLE_PROJECT:-}`.
   `tests/integration/test_compose_invariants.py` moves `API_TOKEN` from the
   optional parametrization to the required one and puts `CONSOLE_PROJECT` in
   its place.
5. *The Coolify render needs no new heredoc.* `render_coolify_compose` in
   `scripts/configure.sh` copies the Coolify file verbatim down to the first
   `agent-` line, so a `console` written above the agent pairs is rendered as
   it stands, and `tests/scripts/test_configure.py` holds it byte for byte.

**Consequences.** `docker compose -f docker/compose/docker-compose.yml up -d`
now starts the console at `http://127.0.0.1:8790`. An `.env` without
`API_TOKEN` makes compose warn and the console exit at boot under its restart
policy; the rest of the stack starts as before, and `scripts/configure.sh`
already writes the token. The host `bun run dev` path is unchanged. The proxy,
TLS and a domain are the next task (server-deployment step 3), as are sign-in
and anything beyond loopback; nothing here exposes the console. A `nitro`
upgrade that stops reading `NITRO_PRESET` ahead of `defaultPreset`, or a
Lovable config that starts deleting it outside the sandbox, silently brings
the Cloudflare target back into the image: that is the thing to recheck on
such an upgrade.

## ADR 100 — TLS in the plain compose is a `caddy` service in front of the console; in Coolify it is Coolify's proxy

**Status:** accepted (T-062, decided by the owner 2026-10-17, who authorized
the edit of `docker/compose/**`).

**Context.** `docs/plans/server-deployment.md` *Decisions* 1 ends TLS "at the
server's proxy (Coolify's, or a Caddy service in the plain compose)", and *How
it would be built* step 3 is that proxy. ADR 97 made the console a compose
service on `127.0.0.1:8790`, so there is something to point at. Every service
in the three compose files publishes on loopback only, and the api and the
collector hold credentials a public port must never reach. A domain is not
known in the repo, and a developer bringing the stack up on a laptop has none.

**Decision.**

1. *One service, plain file only.* `docker-compose.yml` gets `caddy`: the
   official image pinned to one version (never `latest`), `restart:
   unless-stopped`, `depends_on: [console]`, on `ia_harness_net`. It is the
   one service in any compose file that publishes on every address: 80 and
   443 over TCP and 443 over UDP, because Caddy serves HTTP/3 by default and
   keeping it costs one line. `docker-compose.coolify.yml` and
   `docker-compose.agents.yml` get nothing: in Coolify, its own proxy issues
   the certificate for the domain set on the `console` service in its UI, and a
   second proxy there would fight it for 80 and 443. This is a deliberate
   exception to the rule that a service lands in both files.
2. *Mounts.* Two named volumes, `caddy_data` at the image's data directory
   (the certificates and the ACME account, so a restart does not ask Let's
   Encrypt again) and `caddy_config` at its config directory, plus the
   Caddyfile from `docker/caddy/Caddyfile` mounted read-only. Nothing else: no
   docker socket, no hive, no state, no config file, no credential.
3. *The site address is one placeholder with a fallback,* spelt the same in
   two places so neither compose's nor Caddy's empty-variable rule matters.
   The Caddyfile's site address is `{$HARNESS_DOMAIN:http://localhost}`, and
   compose passes `HARNESS_DOMAIN=${HARNESS_DOMAIN:-http://localhost}`, which
   also covers a variable present and empty in the .env file. A bare host name
   (`console.example.com`) is a site Caddy serves over HTTPS with an automatic
   certificate and an automatic HTTP-to-HTTPS redirect. The fallback carries
   the `http://` scheme, which is what turns automatic HTTPS off for that site:
   plain HTTP on port 80, no certificate, no ACME attempt and no local CA. No
   `auto_https` global option is used, because it cannot differ per value.
4. *The ACME email is optional:* `email "{$ACME_EMAIL}"` in the global
   options, with compose passing `ACME_EMAIL=${ACME_EMAIL:-}`. The quotes keep
   one argument when the variable is empty, so the directive parses and an
   empty email means an ACME account without one. Not the runtime form
   (`{env.ACME_EMAIL}`), which the ACME issuer expands with an error on empty.
5. *Headers.* On every response: `X-Content-Type-Options nosniff`,
   `X-Frame-Options DENY`, `Referrer-Policy strict-origin-when-cross-origin`
   and the `Server` header removed. `Strict-Transport-Security` only behind a
   matcher that is `protocol https` and not host `localhost`, so it is never
   sent over plain HTTP and never for localhost, even if someone sets the
   domain to it. No Content-Security-Policy: what the console's build needs is
   not measured, and a wrong one breaks the console silently.
6. *What it proxies.* `reverse_proxy console:8790` and nothing else. The api
   (8789), the collector (8787) and the registry mirror (5000) are not routed,
   and every other service keeps its `127.0.0.1` publish, the console's
   included, so an SSH tunnel still works.

**Consequences.** `docker compose -f docker/compose/docker-compose.yml up -d`
now also binds 80 and 443 on every host address; a host where either is taken
fails that one service. With no `HARNESS_DOMAIN` the console answers on
`http://localhost` as well as on 8790. With one, the operator needs a DNS A
record to the host and 80/443 open before `up`, or Let's Encrypt's challenge
fails and Caddy retries. The console still has only Basic auth through the api;
exposing it publicly before sign-in (plan step 4 ordering) is the operator's
call, and this ADR does not make it safer than that.
`tests/integration/test_compose_invariants.py` holds the shape: Caddy in the
plain file only, the only non-loopback publisher, its exact mounts, and the
Caddyfile text. Changing Caddy's version is an edit of the pin and a recheck of
points 3 and 4, which rest on how that version reads env placeholders.

## ADR 103 — Caddy asks for Basic auth on everything it proxies, and refuses its config without a hash

**Status:** accepted (T-063, decided by the owner 2026-10-17, who authorized
the edit of `docker/compose/**`). Narrows ADR 100 and does not supersede it.

**Context.** ADR 100's *Consequences* say: "The console still has only Basic
auth through the api; exposing it publicly before sign-in (plan step 4
ordering) is the operator's call, and this ADR does not make it safer than
that." The first clause is false. The console's server half forwards every
`/api/*` call with its own bearer (`front/src/lib/api/forward.ts`, ADR 7 and
ADR 24) and takes no credential from the browser, so the api's Basic auth
guards the api's own port and never the console. Anyone who reaches the
console reads tasks, accounts, events and debt. Since ADR 100 that is anyone
who reaches port 80 with `Host: localhost`, and the internet once
`HARNESS_DOMAIN` is set. Sign-in (`docs/plans/server-deployment.md`, *The
order across the plans* item 4) is not built. ADR 100's decision stands: only
the premise about the console's auth is retired here.

**Decision.**

1. *Where.* The `caddy` site block holds `basic_auth` with no matcher, beside
   the one `reverse_proxy console:8790`, so every request Caddy proxies, with
   or without `HARNESS_DOMAIN`, asks for the credential. There is one site
   block and one `reverse_proxy`, so no route skips it. bcrypt, Caddy's
   default for the directive, made with `caddy hash-password`.
2. *Names.* `CONSOLE_AUTH_USER` and `CONSOLE_AUTH_HASH`, read by `caddy`
   only. `CONSOLE_` because the door is the console's; `AUTH_HASH` and not
   `PASSWORD_HASH` so nobody reads it as a sibling of
   `DASHBOARD_PASSWORD_HASH`, which is a sha256 the api checks on its own port:
   a different format for a different door, and never reused here.
3. *The user has a fallback, the hash has none.* The user is spelt with the
   same fallback in both places, as ADR 100 point 3 does for the site:
   `{$CONSOLE_AUTH_USER:console}` in the Caddyfile and
   `CONSOLE_AUTH_USER=${CONSOLE_AUTH_USER:-console}` in compose. The hash is
   bare in both: `{$CONSOLE_AUTH_HASH}` and
   `CONSOLE_AUTH_HASH=${CONSOLE_AUTH_HASH}`.
4. *Fail closed is Caddy refusing the config, not compose refusing.* With no
   hash, the account line is the user alone, and `basic_auth`'s parser refuses
   a line without a password, so Caddy exits at load, again on every restart
   its policy gives it, and nothing listens on 80 or 443. Compose's
   `${CONSOLE_AUTH_HASH:?...}` was not taken: compose interpolates the whole
   file before it starts any service, so a missing hash would stop `api` and
   `console` too, which must still start. The bare `${CONSOLE_AUTH_HASH}` keeps
   compose's "variable is not set" warning, the rule the other required
   credentials follow. The user's fallback is what makes the refusal certain:
   with both unset the block would otherwise be empty, a case this ADR does
   not want to rest on. Whatever Caddy does with a credential that is present
   but wrong, `basic_auth` stays in front of the proxy, so no case serves the
   console without one.
5. *The `$` in a bcrypt hash.* In the .env file the hash is single-quoted
   (`CONSOLE_AUTH_HASH='$2a$14$...'`): compose takes a single-quoted value
   literally, and unquoted or double-quoted it would read `$...` as variables
   and mangle the hash. `scripts/configure.sh` stays bash and coreutils, which
   cannot make bcrypt: it writes the two lines commented, quoted that way, and
   the `docker run` of the pinned Caddy image's `caddy hash-password` that
   makes the hash.

**Consequences.** `up` with no hash starts every service but `caddy`, which
logs the parse error and restarts; the console stays on `127.0.0.1:8790`,
unauthenticated, for an SSH tunnel, as ADR 100 point 6 left it. On the
`http://localhost` fallback the credential crosses the network in clear,
like everything else on that port; a host reached from outside sets
`HARNESS_DOMAIN`. Two things stay open. The console behind Coolify's proxy
(`docker/compose/docker-compose.coolify.yml`) still has no auth at all:
Caddy is not there (ADR 100 point 1), so the README says not to give it a public domain without
Coolify's own basic auth or a private network; this ADR does not change that
file. And Basic auth is a stopgap: plan item 4's sign-in replaces it, and that
task removes `basic_auth` and these two variables under its own ADR.
`tests/integration/test_compose_invariants.py` holds the shape: the
directive in the Caddyfile, the two variables on `caddy` alone, the hash bare
in both files, the user's fallback the same in both.

## ADR 106 — the key table gains `auto_approve` and `protected_paths` as read-only, and an error is the overrides' only when `config.yaml` alone loads

**Status:** accepted (T-064, 2026-10-17). Narrows ADR 75 parts 1, 2 and 5
for task 1 of `docs/plans/console-config.md`. ADR 75's text is not edited.

**Context.** ADR 75 part 5 says its table holds "all the keys `load_config`
reads". It was written on 2026-10-15, and ADR 77 added two keys to
`load_config` the same day: `auto_approve` and `protected_paths`. They are
in neither the table nor its count. Task 1's test checks the table against
`Config`'s fields plus the removed board keys, so they need a class.

ADR 75 also says every error the overrides cause names the overrides file,
including one found only on the merged mapping. It does not say how
`load_config` knows the overrides caused a merged-result error.

**Decision.**

1. *Two more rows, both read-only.* The table then has 15 *read-only*
   keys: ADR 75's ten settings, its three refused board keys, and these two.
   - `auto_approve`, read-only. It turns on merges no person looks at
     (ADR 77). It moves into the project record of `docs/plans/projects.md`,
     and making it editable from the console is a decision for that plan or
     a later ADR, not something this table settles in passing.
   - `protected_paths`, read-only. Loosening it widens what merges
     unattended (ADR 77), and it lives wherever `auto_approve` lives.

   An account's `max_concurrent_phases` (ADR 88) is a field inside
   `accounts`, not a top-level key. It stays read-only with `accounts`.
2. *Unknown keys.* An overrides key that is not in the table at all is
   refused like a non-editable one, with a message saying it is not a
   config key.
3. *Who caused a merged-result error.* When the merged mapping fails a
   check and the overrides file is not empty, `load_config` loads
   `config.yaml` alone.
   - If `config.yaml` alone loads, the overrides caused the error. The
     `ValueError` names the overrides path and the overridden keys, and
     says to reset the key from the console or delete it from the file.
   - If `config.yaml` alone fails too, its own error is raised unchanged.
     Telling the operator to reset an override would not fix a broken
     `config.yaml`.

   This costs a second load only on a failure.
4. *The file's own failures.* Only a missing file (`FileNotFoundError`)
   means no overrides. Any other `OSError`, such as a directory where the
   file should be, propagates unchanged, as it does for `config.yaml`.

**Consequences.** Task 2's `GET /settings` serves 22 *editable*, 13
*read-only* and 2 *credential* rows, counting the three board keys as one
row as ADR 75 does. A `config.yaml` that is broken by itself is reported as
`config.yaml`'s problem even when an override is also wrong. The operator
sees the override's error after fixing the base.

## ADR 109 — the console, Caddy, the api and the writer share `console_net`, which no agent joins; the writer listens on 8791

**Status:** accepted (T-065, decided by the owner 2026-10-17, who authorized
the edit of `docker/compose/**`). Narrows ADR 75 part 4 (*Port*, *Network*,
*How the console reaches it*), ADR 97 point 1 (the console's network) and
ADR 100 point 1 (Caddy's network). None of their text is edited.

**Context.** ADR 75 was written while the console ran only on the host. It
gave the writer `127.0.0.1:8790` and a network of its own,
`config_writer_net`, and had the console reach it at `http://127.0.0.1:8790`.
Since ADR 97 the console is the `console` compose service on
`127.0.0.1:8790`, on `ia_harness_net`, and since ADR 100 Caddy is there too.
The agent containers are on `ia_harness_net` and run arbitrary code from
cloned repos (ADR 72). The console has no auth of its own: its server half
forwards every `/api/*` call with its own bearer
(`front/src/lib/api/forward.ts`), and Caddy's `basic_auth` (ADR 103) guards
only what enters through Caddy. So an agent can call
`http://console:8790/api/...` and read the api without a credential, and once
task 3 of `docs/plans/console-config.md` adds the settings screen it could
change quota policy through the console. That is the hop ADR 75's separate
network was meant to remove, and the console now sits on the agents' side of
it. Port 8790 is the console's.

**Decision.**

1. *One new network, `console_net`,* declared in `docker-compose.yml` and
   `docker-compose.coolify.yml`. A plain bridge, not `internal`, because
   every service on it publishes a loopback port and Caddy needs egress for
   ACME. It has no `name:` key, so compose prefixes it with the project name
   and no other file can join it as `external` by the bare name.
   `config_writer_net` is not created.
2. *Who is on it.*
   - `console`: `console_net` only. It leaves `ia_harness_net`.
   - `caddy` (plain file only): `console_net` only. It leaves
     `ia_harness_net`.
   - `api`: `console_net` and `ia_harness_net`. It needs the collector.
   - `config-writer`: `console_net` only, never `ia_harness_net`.
   - Nothing else. No agent, sidecar, dind, collector, registry mirror or
     dispatcher service joins it, in any compose file, and
     `docker-compose.agents.yml` never declares it.
3. *The writer listens on 8791* and publishes `127.0.0.1:8791:8791`, so a
   console run on the host with `bun run dev` reaches it on loopback.
4. *The console is given the writer's address and token now,* in both
   files: `CONFIG_WRITER_URL=http://config-writer:8791` (a literal) and
   `CONFIG_WRITER_TOKEN=${CONFIG_WRITER_TOKEN}` (bare, like `API_TOKEN`). The
   compose side of task 3 is then done, and task 3 writes only the forward.
   **Task 3's host default for `CONFIG_WRITER_URL` becomes
   `http://127.0.0.1:8791`**, not ADR 75's `http://127.0.0.1:8790`, which is
   the console's own port.
5. *The token's holders* are `config-writer` and `console`, and no other
   service of either deployment.
6. *What holds it.* `tests/integration/test_compose_invariants.py` asserts,
   for both files, the exact set of services on `console_net`, that
   `console`, `caddy` and `config-writer` are not on `ia_harness_net`, that
   the api is on both, the token's two holders, the writer's one publish,
   and that no service of `docker-compose.agents.yml` names `console_net`.

**Coolify.** `docker-compose.coolify.yml` carries no proxy labels and no proxy
network: the domain is set on the `console` service in Coolify's UI (ADR 100
point 1). Coolify attaches its own per-resource network to the services at
deploy time and connects its proxy to that network, so its proxy reaches
`console` without `ia_harness_net`, and moving the console off it does not
break that. This was read from Coolify's documented behaviour, not run, since a
phase cannot deploy. The same injection is a limit of this ADR. If Coolify
attaches that network to every service of the resource, agent containers
included, then on Coolify an agent shares a network with the console whatever
`console_net` says. The separation is guaranteed for the plain compose
files only. ADR 103's advice stands: no public domain on the Coolify console
without Coolify's own auth or a private network. Run `docker network inspect`
on a Coolify deploy to settle it.

**Consequences.**

- A console on `console_net` reaches the api and the writer by service name,
  and Caddy reaches the console. An agent no longer resolves `console`,
  `caddy` or `config-writer` on the plain compose.
- `render_coolify_compose` in `scripts/configure.sh` writes the Coolify
  file's trailing `networks:` block from a heredoc, so `console_net` is
  added there too. Without that the byte-for-byte render test fails, and the
  next `configure.sh` run would drop the network.
- Two existing invariants assumed the old networks and change with this
  ADR: `test_the_console_reads_only_through_the_api` and
  `test_caddy_gets_no_socket_and_no_state`.

## ADR 110 — the writer's edges: a reset repairs any key the file holds, a file that does not parse is fixed on the host, and the writer runs as root

**Status:** accepted (T-065, 2026-10-17). Narrows ADR 75 parts 3 and 4 for
task 2 of `docs/plans/console-config.md`. ADR 75's text is not edited.

**Context.** ADR 75 fixes the writer's routes, its write and its token, and
leaves some cases open. One of them conflicts with part 2's "repair is always
possible". If a hand-edit puts a non-editable key in the overrides file,
every verb refuses to start, and ADR 75's 400 for a non-editable key would
also refuse the reset that removes it. The task also asks how `config.d`
comes to exist, and who may write it, before the first `up`.

**Decision.**

1. *A reset may name any key the file holds.* `DELETE /settings/<key>`
   answers 400 for a key that is not *editable* only when the current
   overrides file does not hold it. A key the file holds is reset whatever
   its class, unknown keys included. `PUT` keeps ADR 75's 400 for every key
   that is not *editable*.
2. *A reset of a key the file does not hold,* with the current revision,
   answers 200 with the current settings. It writes nothing and logs
   nothing.
3. *A file that does not parse,* or is not a mapping, cannot be edited key by
   key. `PUT` and `DELETE` answer 422 with `load_config`'s message, which
   already says to delete the key from the file. The operator fixes the file
   on the host. `GET` still answers 200, with `error` set, `overrides: null`
   and the revision of the bytes on disk.
4. *What a 422 names.* The candidate is validated with
   `load_config(config_path, overrides_path=<temp file>)`. Every occurrence of
   the temp file's path in the message is replaced with the real
   `overrides.yaml` path, so the message names the file the operator knows.
   An error from `config.yaml` alone (ADR 106 part 3) is also a 422. The save
   is refused because the result does not load, whoever caused it.
5. *`X-Actor` is required* on `PUT` and `DELETE`. A missing or blank header is
   a 400, so no change is logged without a person. `via` is the fixed string
   `console`, the one credential there is.
6. *Shapes.*
   - Every 200 is `{"data", "warnings"}`. A successful `PUT` or `DELETE`
     answers with the same `data` as `GET`, read after the write.
   - Every other status is `{"error": <message>}`.
   - The 401 carries a `WWW-Authenticate` Bearer challenge with the realm
     `ia-harness-config-writer` and no Basic challenge, as the collector
     does.
   - `GET`'s `data` holds `base`, `overrides`, `revision`, `rows`, `roles`,
     `recent_changes` and `error`.
     - `rows` is one row per `CONFIG_KEYS` entry, in the table's order:
       `key`, `class`, `kind`, `reason`, `value` and `source`.
     - `value` is what the dispatcher would run on: the loaded `Config`'s
       attribute when the merge loads. When it does not, `value` is the
       winning layer's raw value, or `null` for a `default` source. The three
       board keys are never fields, so their `value` is `null`.
     - `roles` maps `role_model`, `role_effort` and `fallback_roles` to the
       role names each accepts, from `dispatcher/config.py`'s constants.
     - `recent_changes` is the log's last 20 lines, newest first. A line that
       does not parse is skipped and named in `warnings`.
7. *Files in `config.d`.* `overrides.yaml`, `overrides.yaml.prev`,
   `changes.jsonl`, and the lock `overrides.lock`, all mode 0644, plus
   short-lived dot-named temp files. A first save writes no `.prev`. A reset
   that empties the file leaves `{}` under the header, not an absent file.
8. *The writer runs as root in its container,* like the api and the
   collector (no `USER` in its Dockerfile).
   - Docker creates a missing bind-mount source root-owned, and the
     Coolify deploy has no step that could create it first. A non-root uid
     would need a host `chown`, which `scripts/configure.sh` cannot do
     without `sudo`.
   - Root keeps the root-owned directory writable on every path. The
     container holds no socket and has one writable mount, so root there
     reaches `config.d` and nothing else.
9. *`scripts/configure.sh` creates `config.d`* beside the `config.yaml` it
   writes (`--config-out`'s directory), whether or not it overwrote that
   file. The directory then belongs to the operator, who can delete or
   replace a file in it without `sudo`, which is the hand repair part 3
   leaves to the host. A host that skips the script still works: docker
   creates the directory root-owned and the root writer writes it.

**Consequences.** The files the writer creates are root-owned on the host.
The operator can delete them, since the directory is theirs, but editing one
in place needs `sudo` or a copy and a rename. ADR 75's *Consequences* about a
root-owned `config.d` stays true for a host that never ran the script.
`tests/config_writer/` pins each part.

## ADR 112 — the settings forward: `/api/settings` onto the writer, its own token, methods and timeout, beside the api's whitelist

**Status:** accepted (T-066, 2026-10-17; the paths, the variables, the headers,
the methods and the timeout were decided by the owner in the task, the failure
statuses and the CSRF answer by its arquitecto). Narrows ADR 75 part 4 (*How
the console reaches it*) and the first bullet of `docs/plans/console-config.md`
task 3 *The forward*; ADR 109 part 4 already moved the host default. ADR 24 is
not reversed: its whitelist is unchanged, and the sentence of its
*Consequences* about a write is answered below, not struck.

**Context.** ADR 75 has `front/src/server.ts` intercept `/settings` and
`/settings/<key>`. Everything the browser fetches as JSON already lives under
`/api/`, `front/src/lib/api/forward.ts` answers every other `/api/*` path with
a JSON 404, and a bare `/settings` would shadow a page path in the SSR router. The writer's own
contract is `config_writer/app.py` and `tests/config_writer/test_app.py`:
`GET /settings`, `PUT /settings/<key>` with `{"value", "revision"}`,
`DELETE /settings/<key>` with `{"revision"}`, a required `X-Actor`, the
envelope on 200 and `{"error"}` on everything else (ADR 110 part 6). Flask's
`get_json` reads a body only under a JSON content type.

**Decision.**

1. *Paths.* The browser calls `/api/settings` and `/api/settings/<key>`, and
   the forward maps them onto the writer's `/settings` and `/settings/<key>`.
   Every path that is `/api/settings` or starts with `/api/settings/` belongs
   to this forward, never to the api's whitelist.
2. *Separate from `FORWARDED`.* The two paths are not entries of the api's
   list: they have their own upstream, token and timeout. `FORWARDED`, its
   count and its rejection message stay as they are.
3. *Variables,* read in `front/src/lib/api/server-env.ts` beside the api's,
   neither with a `VITE_` prefix:
   - `CONFIG_WRITER_URL`, default `http://127.0.0.1:8791` (ADR 109 part 3), a
     trailing slash trimmed as `API_BASE_URL`'s is;
   - `CONFIG_WRITER_TOKEN`, optional. Unset, empty or whitespace is *unset*,
     as the writer's own `main` reads it. Unlike `API_TOKEN` it does not throw
     at import: every other screen keeps working, and this forward answers 503
     with a JSON body naming the variable.
4. *Methods and shape.* `GET` only on `/api/settings`. `PUT` and `DELETE` only
   on `/api/settings/<key>` with exactly one segment, `TASK_DETAIL`'s shape:
   the key is decoded (a key that will not decode is a 404) and re-encoded
   into the writer's path. A wrong method on a right shape is a JSON 405;
   anything else under the prefix — a second segment, an empty one — is a
   JSON 404. Neither reaches the writer. The shape and method checks run
   before the token check, so a 405 or 404 does not depend on configuration.
5. *What crosses.* The forward sends `Authorization: Bearer
   <CONFIG_WRITER_TOKEN>`, `X-Actor: operator` (ADR 75 part 3: the console
   has no signed-in person yet) and `Accept: application/json`. On `PUT` and
   `DELETE` it also sends the browser's body bytes unchanged, under its own
   `Content-Type: application/json`, which the writer needs to read a body at
   all. Nothing else of the browser's request crosses: no header, no cookie,
   no query parameter. The writer's status, body and content type come back as
   they came, as the api's do.
6. *Timeout and failures.* 5000 ms, the api's number (ADR 7, ADR 24), as its
   own constant `CONFIG_WRITER_TIMEOUT_MS`: a save is one small file write
   under a lock, so nothing argues for a longer wait. A timeout is a JSON
   504, and a refused or failed connection a JSON 503. Both bodies name the
   service, *the config-writer service*, and never its address; the address
   goes to the server log, as the forward already does for the api. 503 and not the
   api's 502 because the settings screen treats every one of these as one
   state, *unreachable*, which `docs/plans/console-config.md` task 3 words as
   "the forward answers 503".
7. *CSRF.* ADR 24 asked a task sending a write this way to notice the CSRF
   middleware in `front/src/start.ts`, which filters server functions only.
   The answer is that the writes are `PUT` and `DELETE`, which are never a
   CORS *simple* request: a cross-site page cannot send either without a
   preflight `OPTIONS`, and the forward answers `OPTIONS` with a 405 that
   carries no `Access-Control-*` header, so the browser refuses the write
   before it is sent. A `POST`, which a form can send cross-site, is a 405
   on both paths. The middleware's filter stays as it is. A later change that
   accepts `POST` here, or answers a preflight, re-opens this part.

**Consequences.**

- A host console started without `CONFIG_WRITER_TOKEN` boots and loses one
  screen. In compose both files already give the `console` service both
  variables (ADR 109 part 4), so nothing under `docker/compose/` changes.
- `front/AGENTS.md` and `front/README.md` name five variables, not three.
- `front/src/lib/api/forward.test.ts` pins every part above; the token-unset case re-imports the
  module under a second `server-env` mock, since the value is read at import.
- Plan task 3's *Built* note says the path was narrowed here.

## ADR 113 — the settings screen ships read-only first; how it renders the writer's rows and its states

**Status:** accepted (T-066, 2026-10-17). Takes the split
`docs/plans/console-config.md` task 3 allows. Narrows nothing.

**Context.** Task 3 is the forward, a table of 39 rows, seven kinds of
control, save and reset with a revision, and their 409 and 422 states. The plan
lets its arquitecto cut it into a read-only screen first and the controls
second, and tier 3's floor allows no control that is not wired.

**Decision.**

1. *The split.* T-066 builds the forward complete (ADR 112) and the screen
   with every state except the ones a save or a reset produces. The controls,
   *Save*, *Reset* (the broken-config banner's included), the 409 banner, the
   422 message under a row, and the refetch after a write are the follow-up
   task's. The read-only screen draws no control and no disabled control.
2. *One read, no poll.* `getSettings` in `front/src/lib/api/client.ts` reads `/api/settings`
   through the same `readEnvelope` as the api's reads, so a writer error is an
   `ApiError` carrying its `{"error"}` message and a 200 carries its
   `warnings`. `settingsQuery` has no `refetchInterval`: the plan's *Stale*
   state says there is no poll.
3. *Title and nav.* The page title and the nav label are *Settings*; the route
   `/models`, its file and the `m` chord stay.
4. *States.*
   - *Loading:* one muted line for the page.
   - *Unreachable:* any failed read — the forward's 503 or 504, a 401 from the
     writer, a non-JSON answer — takes the page with an `ErrorState` that
     names the config-writer service and both variables and quotes the
     served message. Nothing else is drawn.
   - *Broken config:* `error` non-null is a danger `Banner` carrying the
     message verbatim and saying the dispatcher refuses every verb until it is
     fixed; the table is still drawn.
   - *Warnings:* one `WarningBanner`.
   - *The note* (the plan's three sentences, verbatim) is an `info` `Banner`,
     always shown when the read succeeded.
   - *No overrides* is not an empty state: every row draws with its source.
     An empty `recent_changes` is an `EmptyState` titled *No change has been
     saved from the console*.
5. *A row* shows the key, the effective value, the source, the class and the
   reason. A value is one of any JSON type, so it renders by type: `null` as
   `Absent` reading *not set*, a string, number or boolean as itself in
   `mono`, a list or mapping as compact JSON in `mono`. A credential row adds
   that renewing it belongs to the Tokens screen. `rows` or `recent_changes`
   that is not a list is named with `Malformed` in its region (ADR 29).
6. *A change line* shows its time, actor, key, operation, and old and new
   values in the row rendering above. The time is the UTC date and
   `formatClock`'s clock, because a log of twenty changes spans days and
   `formatClock` alone drops the date; the served `at` string is the cell's
   `title`.

**Consequences.** The mock the screen read (`listRoleModels`,
`listModelOptions`, `setRoleModel` and their two queries) is removed from
the client and from `front/src/lib/api/queries.ts`; the fixtures and types
behind them in `front/src/lib/api/mock/ops-fixtures.ts` and
`front/src/lib/api/ops-types.ts` stay, unused, because those files are
outside this task. `docs/ui.md` *One shell, one nav, one set of chords* names
`m` settings. Plan task 3 carries a dated note naming what the follow-up
builds.

## ADR 114 — the settings screen's controls: one write at a time with the revision, a refetch after it, and where a refusal is shown

**Status:** accepted (T-067, 2026-10-17). Completes `docs/plans/console-config.md`
task 3, the follow-up ADR 113 part 1 left. Narrows nothing.

**Context.** ADR 113 shipped the screen read-only. The writer (ADR 110) takes
`PUT /settings/<key>` with `{value, revision}` and `DELETE /settings/<key>`
with `{revision}`, refuses a stale revision with 409 and a value or a config
that does not load with 422, and serves `revision: null` when there is no
overrides file. The forward (ADR 112) already passes both writes through.

**Decision.**

1. *Two writers.* `putSetting(key, value, revision)` and
   `resetSetting(key, revision)` in `front/src/lib/api/client.ts` go through
   the same `readEnvelope` as every read, which now takes an optional method
   and JSON body. A refusal is an `ApiError` carrying the writer's message and
   status. `revision` is sent as served, `null` included, and the key is one
   encoded path segment.
2. *One write at a time, then a refetch.* The page holds one mutation. While it
   is pending, or while the read is fetching, every control on the page —
   the banner's *Reset* included — is disabled, so no write is ever sent on a
   revision the page has not yet read back. On success the page invalidates
   `settingsQuery` and refetches, rather than taking the write's own envelope,
   so the table shown is always a read's.
3. *Controls by key, then by kind.* Only an `editable` row gets a control;
   `read-only` and `credential` rows, and the unreachable state, get none.
   - `role_model`: a text field per role in `roles.role_model`, suggesting
     the model IDs the base and the overrides already name (`default_model`,
     `mapping_model`, every `role_model` value) through one `datalist`;
     empty means *(default)*.
   - `role_effort`: a select per role over *(default)* and `EFFORT_LEVELS`.
   - Both send the whole map: a role on *(default)* is taken out of it, and
     an entry for a role not in the writer's list is kept.
   - `fallback_roles`: a checkbox per role, sent in the writer's role order.
   - `primary_account`: a select over the `accounts` row's names.
   - `bool` a switch, `effort` a select, `positive_int` and
     `non_negative_int` a number field, `string` a text field (with the model
     suggestions on `default_model` and `mapping_model`).
   - Any other kind says this console has no control for it; its *Reset* is
     still offered when overridden.
   Each row has *Save*; *Reset* only when its source is `override`. The
   selects are native `<select>`, the console's idiom on the Tail, Index and
   Sessions screens.
4. *What the front checks.* A number field sends nothing when it is empty or
   not a whole number, and says so under the row. A range (positive, not
   negative) and every other value is the writer's to refuse: its 422 is the
   one source of truth, so the front does not restate `CONFIG_KEYS`.
5. *Where a refusal is shown.*
   - *409:* a warning banner above the table says the settings changed since
     the page loaded and the change was not saved; the page refetches, and
     every row's draft is dropped for the current value, because the edit was
     made against a table that no longer exists.
   - *Any other refusal* (422, 400, 503, 504, a non-JSON answer) is quoted
     verbatim on an alert line directly under its row, and the control keeps
     the value that was sent, so it can be corrected. A new write clears it.
   - *The broken-config banner* offers *Reset* for every key the overrides
     hold, editable or not, since any of them may be what breaks the load; its
     refusals are shown in the banner. When `overrides` is `null` the file
     does not parse, so no reset can succeed and none is offered: the banner
     says it is fixed on the host.
6. *Drafts.* A row's draft lives in the row and is reset when the revision
   changes or a 409 arrives (the row is keyed by both).

**Consequences.** `front/src/test/render-route.tsx` keys a served answer by
`"<METHOD> <path>"` before the bare path, takes a list answered in order (the
last repeating) for a refetch, and records every request's method, path and
body as `sent`, so a write's body is asserted as `fetch` received it. Plan
task 3 is built; its *Done when* tests are in `front/src/routes/-models.test.tsx`.

## ADR 115 — the run skill is generated in mapping, staged by the phase and moved by the dispatcher; the gates still read only the frontmatter

**Status:** accepted (T-068, 2026-10-17; the staging, the move's rules and
`mapping_allowed_tools` were decided by the owner in the task, after a probe).
Builds step 1 of `docs/plans/skills-adoption.md` and narrows that plan's
second bullet of section 1. The plan's text is not edited.

**Context.** The plan has the cartografo run Claude Code's bundled
`/run-skill-generator` and the gates read their commands from the skill "when
the project's config names none". Config never names a project's commands:
the gates read them from the YAML frontmatter of the project's
`docs/README.md` (`project_docs.read_commands`), which the cartografo already
writes (ADR 66, ADR 80). A `SKILL.md` is prose for a model, and a gate runs
with no model in the loop. A probe on 2026-10-17 (`claude -p` in
agent-cuenta1, Haiku, `acceptEdits`, the standing allowlist, a toy project)
found:
- the CLI is 2.1.296 (`docker/agent/Dockerfile`, `ARG CLAUDE_CODE_VERSION`),
  past the generator's 2.1.145;
- the generator runs under `-p` when the prompt starts with
  `/run-skill-generator`;
- a phase's Write anywhere under `.claude/` is refused, while `docs/` is not;
  told to write the skill to `docs/run-skill/run-toy/`, the generator did so
  (`SKILL.md` and a `driver.mjs`) and its text named the staging path;
- with the standing allowlist only `node --test` ran; `npm test`,
  `node <script>`, `npm --version` and compound commands were refused, and
  the skill marked what it could not verify as unverified.

**Decision.**
1. *The frontmatter stays the only thing a gate reads.* `dispatcher/gates.py`
   is unchanged and never parses a skill. The mapper runs the generator first,
   then fills `build:`, `test:` and, where a fresh checkout needs one,
   `install:` from the commands the generator actually ran and saw succeed. A
   key it did not verify, or that was refused, is omitted, never guessed.
2. *`MAPPED_KEYS` gains `install`*, because the mapper is now asked to
   establish it: the generator runs the install and sees it succeed. `lint`
   stays out of it.
3. *The skill is for the roles.* `.claude/skills/run-<name>/` is committed to
   the project, so the arquitecto and the implementador get it as a project
   skill from their worktree. The index's table of docs gains a row for it.
4. *The phase stages it, the dispatcher moves it.* The cartografo's prompt
   opens with `project_docs.mapper_lead()`: `/run-skill-generator` followed
   by the staging terms, before `Role:` and every duty. The generator is
   user-invocable only. Named inside `_MAPPER`, a second probe showed the
   model reach for the Skill tool and be refused, so it never ran; as the
   prompt's leading slash command it runs, and a third probe with the whole
   mapping prompt after it wrote the skill and then the docs. The lead line
   has the generator write to `docs/run-skill/run-<name>/`
   (`project_docs.RUN_SKILL_STAGING`). Every path inside the skill is its
   final `.claude/skills/run-<name>/...` path, and the skill never mentions
   the staging directory. After the phase returns,
   `project_docs.move_staged_run_skills` runs inside the mapping branch of
   `dispatch_phase`, under the same `_base_lock` as the mapping commit
   (ADR 80) and before it, with no model in the loop:
   - each `docs/run-skill/<dir>/` moves whole to `.claude/skills/<dir>/`;
   - an entry that is not a directory, or whose name does not match
     `run-[a-z0-9-]+`, is not moved, and is logged;
   - an existing `.claude/skills/<dir>/` is never overwritten: it is logged,
     and the staged copy is left out of the commit;
   - `docs/run-skill/` is then removed, so nothing staged is ever committed;
   - with nothing staged it is a no-op, and mapping commits as before.
   No other phase gets the move. The cartografo's commit has no scope
   (`commit_scope` returns None) and excludes only the charter; nothing in
   the dispatcher excludes `.claude/`, so the moved skill is in the commit.
   No other role's scope is widened.
5. *It is generated once*: only when the mapping phase runs, which is only
   when the project has no index (`_needs_mapping`). Nothing regenerates it on
   its own. A task that changes how the project is built or run updates it,
   as any task updates the docs it makes stale.
6. *`mapping_allowed_tools`*, a list of tool patterns validated like
   `allowed_tools` (`_load_allowed_tools`), default `[]`, read-only in
   `CONFIG_KEYS`: a permission is the operator's to grant, by hand, in
   `config.yaml`. `_phase_permission_flags(cfg, role)` appends it to
   `allowed_tools` for the cartografo's call only; every other role's flags
   are exactly what they were, and a test asserts it. `permission_mode` does
   not change for mapping: no `bypassPermissions`. With the list empty
   mapping still runs, and records only what it verified.
7. *The fingerprint stays on `allowed_tools`.* The two
   `learnings.harness_fingerprint(cfg.permission_mode, cfg.allowed_tools,
   docker_exec.WRITER_ROLES)` readers in `dispatcher.py` (and the one in
   `cli.py`) are unchanged. They describe the grants a task's cycle phases run
   under, and those are unchanged; `mapping_allowed_tools` reaches the
   cartografo alone, a one-time phase that writes the docs and the staged
   skill and whose grant is not one a later phase holds.

**Consequences.** The owner fills `mapping_allowed_tools` per the projects in
`projects_root` (for example `Bash(docker compose*)`, `Bash(npm *)`,
`Bash(make*)`) and turns `mapping_enabled` on; until then the generator can
verify little more than `node --test`, and the frontmatter is thin. Projects
already mapped, this harness included, get no skill: nothing regenerates one.
A project whose own `.gitignore` excludes `.claude/` would drop the moved
skill from the mapping commit; the dispatcher does not override it. Still
unverified: bringing a project up in the agent's dind under `-p`. A resumed
mapping phase is not told to run the generator again: the leading command
belongs to the first call of the session.

## ADR 116 — the CLI's permission denials are recorded on the usage line and in the handoff envelope, and listed on the task page

**Status:** accepted (T-069, 2026-10-17; the shape, the caps and the front's
states were decided by the owner in the task). Builds step 1 of
`docs/plans/command-requests.md` (decision 1). The plan's text is not edited.

**Context.** A phase runs under `permission_mode` and `allowed_tools`, and a
tool call outside them is refused. The `claude -p --output-format json` result
lists what was refused in `permission_denials`, and the dispatcher dropped it:
the only trace was the transcript. Before any allowlist is widened, or any
command request is designed (the plan's later steps), the refusals in real
runs have to be seen. The usage log (`<hive>/tasks/<id>/usage/calls.jsonl`,
ADR 53, ADR 54, ADR 68) already has one line per CLI call, and the handoff
envelope (ADR 27) is the phase's record.

**Decision.**
1. *The usage line gains one key, `permission_denials`*, read from the raw
   result by `_usage_record` for every call:
   - `null` when the key is absent or is not a list; `[]` when the CLI
     reported an empty list. A missing key, `null` and `[]` stay three facts;
   - each entry is reduced to `tool_name`, `tool_use_id` and `tool_input`.
     A missing `tool_input`, or a `tool_use_id` that is not a string, is
     `null`; any other key is dropped;
   - an entry that is not an object, or whose `tool_name` is not a string,
     becomes `{"malformed": <Python type name>}`, never a guess;
   - at most 50 entries are kept (`_MAX_PERMISSION_DENIALS`); when more were
     reported, `permission_denials_dropped` counts the rest. That key exists
     only then;
   - every string value inside an entry longer than 500 characters
     (`_MAX_DENIAL_STRING`) is cut to its first 500 plus `"…[cut N chars]"`,
     N being the characters removed. Keys are not cut. The raw result is not
     mutated.
2. *No other redaction.* The same tool inputs are already in the phase's
   transcript, and the log is 0644 in a 0755 directory under the root-owned
   hive: it adds no reader that the transcript does not already have.
3. *The handoff envelope gains a fifth key, `permission_denials`*: the reduced
   list (or `null`) of the phase's final result, the last retry's when the
   call was retried. The dropped count stays on the usage line.
   `context_transfer.save_handoff` writes the key only when it is given one
   (`PERMISSION_DENIALS_ABSENT` is the default), so a phase with no account,
   which made no call, writes the four keys it always did. An envelope
   written before this ADR has no key, and reads as "not recorded";
   `read_handoff_envelope` is unchanged.
4. *Recording never fails a phase.* The reduction is wrapped: any exception is
   logged as a warning and the call's `permission_denials` is `null`; the rest
   of the line and the phase's outcome are unchanged.
5. *`task_usage` gets no logic change.* `MAX_USAGE_CALLS` keeps its value; its
   comment now says `permission_denials` is the one list whose length the CLI
   decides, and that the caps above keep a line to tens of KB at worst.
6. *The task page lists them.* Each call line in the usage region has a
   *denied* row with four states: missing key, "written before denials were
   logged" (muted); `null`, Absent; `[]`, "none"; a list, one row per entry
   with the `tool_name` and a one-line, shortened `tool_input`, the whole
   value in its `title`. A malformed entry, or a value of the wrong type, is
   shown as Malformed; a dropped count as "+N more not recorded". An attempt's
   header gets an "N denied" badge, the kept entries plus the dropped counts
   of its calls, when N is above zero.

Out of scope: command requests, verdicts, the allowlist, an Approvals card,
`allowed_tools` and `permission_mode`, `/api/phases`, backfilling old lines,
docker, `config.yaml`, `front/vite.config.ts`.

**Consequences.** Lines and envelopes written before this ADR carry no key,
and the page says so rather than showing "none". The page shows refusals
without the transcript open, which is what the plan's next steps need. The
caps bound a line, not the log: a phase that is refused often still writes
one line per call.

## ADR 117 — a phase files a denied command as `command_requests`, checked by the dispatcher, and one accepted request ends the cycle blocked

**Status:** accepted (T-070, 2026-10-17; the roles, the limit and the
recording were decided by the owner in the task). Builds step 2 of
`docs/plans/command-requests.md` (decision 2). The plan gains a *Built* note
under decision 2 and a dated answer under its open question on roles; the
rest of its text is not edited.

**Context.** ADR 116 made the refusals visible. A phase that is refused a
command it needs still has nowhere to say so: it either works around the
refusal (another tool, another spelling, the job done by hand) or ends with
the need buried in prose. The plan's decision 2 gives it a handoff field and
ends the phase blocked, so a person can decide. Verdicts, running the command
and resuming are later steps; this one only has to file, check, record and
stop.

**Decision.**
1. *The field is on the implementador and the cartografo only.* Those two run
   commands for the task; the arquitecto, the revisor and the auditor read, so
   their schemas have no field and they cannot file. This answers the plan's
   open question without a check on what a reading role's request would do.
   `handoff.files_command_requests(role)` is the one test of it.
2. *The shape.* `command_requests` is an array of at most 3 entries
   (`maxItems`, `_COMMAND_REQUESTS_MAX`), each `argv` (a non-empty list of
   strings, no shell), `reason`, `target` (`kind` one of `agent`, `dind`,
   `service`, and `name`, empty unless the kind is `service`) and `cwd`
   (relative to the worktree). Like every handoff field it is required, an
   empty array when nothing is filed. No `minLength` in the schema: what the
   CLI's validator enforces is the check's job, not the schema's.
3. *A pure check, `handoff.command_requests(payload)`*, answers
   `(accepted, refused)`. Each refused entry is `{"request", "reason"}`, the
   reason naming every problem found (`command_request_problems`): not an
   object, an empty or non-list `argv`, an `argv` item that is not a
   non-empty string, a `reason` that is not a string, an unknown `kind`, a
   `service` with no name, a `cwd` that is not a string, is absolute or has a
   `..` segment. Entries past the third are refused "over the limit of 3
   requests" whatever they hold, since a schema the CLI did not enforce is
   not a reason to act on more. A missing field, `null` or a non-list files
   nothing. Resolving a target to a container and the denylist are step 3.
4. *One accepted request blocks.* `_filed_command_requests` runs after the
   implementador (right after the phase returns, before the revisor) and
   after the mapping phase (in both mapping branches, before the arquitecto).
   It ends the cycle `Blocked` with the task file's new `blocked_reason:
   command_request`. When every request is refused it does not block: the
   refusals are logged as a warning and recorded, and the cycle goes on as if
   none had been filed, because a malformed request is no reason to stop a
   phase that may have finished.
5. *No gates and no revisor after a filed request.* `_run_gates` returns
   before grading when the implementador's return carries an accepted
   request: grading a worktree the phase says it could not finish spends a
   retry on a known answer. The revisor never runs because the cycle has
   already ended.
6. *`blocked_reason` is written only when set.* `TaskFile.blocked_reason`
   defaults to `None` and `write_task_file` leaves the key out then, so every
   existing task file reads and writes as before. `tasks.mark_blocked` takes
   an optional `reason` and sets it (no reason clears a stale one);
   `tasks.acquire_lock` clears it whenever it takes the task, since a task
   taken again is running, whatever stopped it last.
7. *The verdicts are recorded in the handoff envelope*, a sixth key
   `command_requests: {"accepted": [...], "refused": [...]}`, written by
   `run_phase` for the two filing roles only (`COMMAND_REQUESTS_ABSENT` is
   `save_handoff`'s default, as in ADR 116). Not a separate file: the
   envelope is already the one record of a phase's answer, written atomically
   with it, read by `read_handoff_envelope`, and a second file per phase is a
   second thing to keep in step with the first. The later steps read the
   accepted list from there without checking it again.
8. *The budget grows by 768 bytes for each filing role*: the cartografo's
   from 2560 to 3328, the implementador's from 7168 to 7936. Three requests
   of about 250 bytes each fit without eating the summary's room; the line
   budget the prompt states follows (19 and 48 lines), and the shrinking
   margin `worth_shrinking` keeps grows with it (332 bytes for the
   cartografo).
9. *The prompt.* Both roles get one paragraph (`COMMAND_REQUESTS_PROMPT`):
   do not work around a denied command, file it (at most 3, argv with no
   shell, cwd relative) and end the phase. The rendered handoff gains a
   *Command requests* section, each entry shown as accepted or refused.

Out of scope: target resolution, `accounts[].dind` and the denylist (step 3);
verdicts, running the command and resuming (step 4); the allowlist (step 5);
the Approvals card, any front or api change, and the notification (step 6);
`--permission-prompt-tool` (decision 8).

**Consequences.** A filed request leaves the task blocked until someone takes
it again by hand: with no verdict yet, retrying is the operator's only answer,
and the request stays readable in the envelope and the task file's handoff.
Refused requests cost the phase nothing but a log line. Task files and
envelopes written before this ADR have no key and read as before.

## ADR 118 — a filed command request is resolved to the phase's own account and checked against a denylist in code

**Status:** accepted (T-073, 2026-10-17; the `dind` key, the pure resolver,
where the check runs and deferring `--permission-prompt-tool` were decided by
the owner in the task). Builds step 3 of `docs/plans/command-requests.md`
(decisions 3 and 7). The plan gains *Built* notes under decisions 3 and 7 and
a dated note under decision 8; the rest of its text is not edited.

**Context.** ADR 117 lets the implementador and the cartografo file a command
they were refused, and checks its shape. A request that passes says only a
kind of target (`agent`, `dind`, `service`) and a `cwd` relative to the
worktree. Before a person is asked to approve it (step 4), the dispatcher has
to know which container it would run in, and has to refuse what no verdict
may allow (decision 7), so a card never shows a command that could not run or
must not.

**Decision.**
1. *`accounts[].dind`, an optional key.* The account's dind container's name,
   validated like `container`: absent, or a non-empty string; anything else
   fails the config load naming the account. It is never guessed from the
   account's or the container's name. `config.example.yaml` carries
   `dind: dind-cuenta1` and `dind: dind-cuenta2`; the host's `config.yaml` is
   not tracked and gains the key by hand. An account with no `dind` refuses
   the `dind` and `service` kinds with a reason ("account X has no dind, so a
   dind target cannot be resolved"), never an error.
2. *Resolution is pure, `command_targets.resolve`.* Given an accepted request,
   the account the phase ran on, the task's writer worktree
   (`{projects_root}/{slug}/worktrees/{task_id}/work`), the running services
   and the account list, it answers `(resolved, None)` or `(None, reason)`.
   `resolved` is `{"kind", "container", "cwd"}`, plus `"service"` for the
   service kind. `agent` resolves to `accounts[].container`, `dind` and
   `service` to `accounts[].dind`, the service named inside it. `cwd` is the
   worktree joined with the request's `cwd` and normalized; it must stay
   inside the worktree by whole path segment (so `work-evil` beside `work` is
   outside), or the request is refused under the rule `cwd-outside-worktree`.
3. *Never-targets, by construction and by check.* Only the phase's own
   account's two names and the project's own services can come out. On top of
   that, `_never_target` refuses a container that is another account's
   `container` or `dind` (a config that hands two accounts one dind), a
   container named like a harness service, and a service named like one. The
   harness services are `HARNESS_SERVICES`: `dispatcher`, `api`, `collector`,
   `console`, `config-writer`, `caddy`, `registry-mirror`, and `board` and
   `board-service` for the planned board service, whose compose name is not
   fixed yet. The host is never a target because no kind resolves to it.
4. *The project's services are told apart by their compose label.* The one
   reader, `running_services(dind, project_dir)`, runs `docker ps` inside the
   dind (through `docker_exec.run_docker_exec`, 30 s timeout) with the labels
   `com.docker.compose.project.working_dir` and `com.docker.compose.service`.
   `project_services`, its pure parser, keeps a service only when its working
   dir is the project's dir (`/data/projects/<slug>`) or under it by whole
   segment, which covers a compose run from a worktree and excludes
   `myproj-other` and containers with no compose labels. The project name
   label is not used: compose derives it from the directory's basename, so
   two worktrees of different projects can share it. If the reader fails
   (no docker, a timeout, a nonzero exit) the request is refused "service
   list unavailable", never accepted on a guess. It is called lazily, at most
   once per check, and only when a service request needs it; tests stub it.
5. *The denylist is `DENYLIST`, a tuple of named rules in code*, whose
   docstring says it changes only under an ADR. `denylist_problems(argv,
   kind, cwd, worktree)` answers every rule broken, each as
   `"denylist rule <name>: <why>"`. Binaries are matched by basename at every
   argv position, so `/usr/bin/sudo`, `timeout 60 git push` and
   `docker compose exec app git push` are caught. The rules:
   - `remote-git`: git's global options are skipped, with their values
     (`-C x`, `-c k=v`, `--git-dir=...`), then `push`, `fetch`, `pull`,
     `clone`, `ls-remote`, `send-pack` and `fetch-pack` are refused; `git
     remote` with anything but nothing, `-v`, `--verbose` or `get-url`; and
     `git submodule update`.
   - `docker-socket`: `docker.sock`, `DOCKER_HOST` and `DOCKER_CONTEXT`
     anywhere in argv, for every kind. For `agent`, any `docker`,
     `docker-compose`, `podman` or `nerdctl` (file it for the dind). For
     `dind` and `service`, docker is the point and is allowed, but not aimed
     at another daemon: the global options `-H`, `--host`, `-c`,
     `--context` and `--config` before the subcommand, and `docker context`,
     are refused.
   - `credential-path`: each argv item is split on `/`, `=` and `:` and its
     segments compared whole: `.env`, any `.env.*` (`.env.example` too: a
     name is not proof of its content), `.claude`, `.claude-account`,
     `.ssh`, `git-credentials`, `.git-credentials`, `.netrc`, `id_rsa`,
     `id_ed25519`, `id_ecdsa`, `id_dsa`, and the pairs `.data/credentials`
     and `run/secrets`. Substrings pass: `README.env.md`, `my.env`,
     `id_rsa.pub`, `docs/ssh.md`, `src/claude.py`.
   - `privilege`: `sudo`, `su`, `doas`.
   - `shell-command`: `sh`, `bash`, `zsh`, `dash`, `ksh`, `ash` or `fish`
     with a leading option carrying `c` (`-c`, `-lc`, `-l -c`) or
     `--command`, refused outright: the command string is one argv item no
     rule can read. `bash script.sh` passes. Other interpreters' `-c`
     (`python -c`) are not refused; the denylist is not a sandbox, and the
     person still reads the argv before approving.
   - `cwd-outside-worktree`: the resolved `cwd` (decision 2).
   Matching every position means a bare word such as `grep docker` from the
   agent kind is refused; that is the price of not parsing commands, and a
   request can be filed again in another form.
6. *Where it runs.* `_checked_command_requests` runs ADR 117's check, then
   `command_targets.check(accepted, refused, account, accounts, worktree,
   read_services)`, which resolves each accepted request and applies the
   denylist. A request refused by either moves to the refused list as
   `{"request", "reason"}`, after the step-2 refusals; the rest gain a
   `resolved` key. A phase on no known account refuses every request. The
   result is computed once in `run_phase`, kept on
   `DispatchResult.command_requests`, recorded in the envelope and used by
   `_filed_command_requests`, so the envelope stays the one place later steps
   read. When nothing is left accepted the cycle does not block, as in ADR
   117.
7. *Step 4 runs the same denylist again.* `denylist_problems` and `resolve`
   are exported for the step that runs the command, which re-resolves from
   the account that resumes (decision 5) and re-applies the denylist before
   `docker exec`.
8. *`--permission-prompt-tool` (decision 8) is deferred.* The agent image
   pins `ARG CLAUDE_CODE_VERSION=2.1.296` (`docker/agent/Dockerfile`),
   installed with `npm install -g @anthropic-ai/claude-code@...`. Whether that
   version's `--permission-prompt-tool` or permission-request hook fits is
   not checked here (no container command in this task); the choice moves to
   step 4 or later.

Out of scope: verdicts, running commands, resuming, the allowlist, the
Approvals card and the notification (steps 4 to 6); building
`--permission-prompt-tool`.

**Consequences.** A request reaches the envelope either refused with a reason
or with a target a card can show. Two gaps stay. `_run_gates` still reads only
ADR 117's check, so a request refused at this step still skips the gates; and
the rendered handoff's *Command requests* section still shows the step-2
verdict. The dind container (`docker/compose/docker-compose.agents.yml`)
mounts only `/var/lib/docker`, not `/data/projects`, unlike the plan's *What
exists today*: a `dind` or `service` target's `cwd` is recorded as the
worktree path, and step 4 has to decide how a command run in the dind reaches
the tree (a bind mount, or `docker compose --project-directory` from the agent
side). The `board` names in `HARNESS_SERVICES` are a guess until the board
service is built.

## ADR 119 — a command request's verdict is given from the CLI, runs the argv with no shell, and resumes the phase's own session

**Status:** accepted (T-074, 2026-10-18; building step 4 in the dispatcher
first, through a CLI verdict, without waiting for the board service, was the
user's ruling the same day). Builds step 4 of `docs/plans/command-requests.md`
(decisions 4 and 5) for *Run once* and *Deny*. The plan gains an *Amended*
and a *Built* note under step 4 and *Built* notes under decisions 4 and 5; the
rest of its text is not edited.

**Context.** ADR 117 ends a cycle blocked on `command_request` when a phase
files a command it was refused, and ADR 118 resolves each accepted request
to a container and a `cwd` and runs the denylist on it. Nothing unblocked
such a task: the plan put the verdicts in the board service's queue and
worker, which do not exist yet. The operator needs to answer a request now,
and the board service's worker will need the same step later.

**Decision.**
1. *`dispatch command-verdict <task> <n> run|deny --project <slug>
   [--comment TEXT]`.* `<n>` is the 1-based index into the accepted list of
   the filing envelope's `command_requests`. The CLI calls one function,
   `command_run.run_command_request(cfg, task_id, slug, n, verdict,
   comment=None, by="operator") -> CycleOutcome`, which the board service's
   worker is to call too. A refusal raises `VerdictRefused` with a reason,
   which the CLI prints and exits 1 on; otherwise the exit follows the
   outcome as `run-task` does (blocked 1, held 75, split 3).
2. *The filing block.* The newest `saved_at` among the implementador's and
   the cartografo's envelopes that recorded any request is the block. Each
   of these is refused before anything runs or is recorded: a verdict other
   than `run` or `deny`; a task not `blocked` with `blocked_reason:
   command_request`; no filed requests on record; an `n` out of range; an
   `n` on a block whose requests were all refused at filing (a refused
   request has no number and is never the operator's to run); a block that
   already has a verdict; a session not on record; and, on *run*, a request
   that no longer passes its check (decision 4).
3. *One verdict per block, recorded first.* The task file gains
   `command_verdicts`, a list of `{n, verdict, by, at, comment, role,
   filed_at}`, `filed_at` being the envelope's `saved_at`. A block matched by
   role and `filed_at` that already has one is refused, naming it: the
   verdict resumes the phase, and its other requests are moot once the phase
   has gone on. A request filed again by the resumed phase is a new envelope,
   so a new block. The verdict is recorded before the command runs, so a
   crash past that point can never run it twice.
4. *Run re-checks on the account that resumes.* The `resolved` key is
   dropped and step 3's `command_targets.check` runs again with that
   account, its writer worktree and the dind's running services, so a
   stopped service or a config edit since filing refuses it ("no longer
   passes its check: <reason>"). *Deny* runs nothing and is not checked.
5. *No shell.* The argv runs through `docker_exec.run_docker_exec(container,
   cwd, argv)`, that is `docker exec -w <cwd> <container> <argv...>` as list
   items, so a `;` or a `$(...)` is an argument. A `service` target runs
   `docker compose exec -T <service> <argv...>` in its dind from the
   resolved `cwd`, so compose finds the project the agent started there (the
   dinds mount `/data/projects` at the agent's path since this task's base).
   The timeout is `COMMAND_TIMEOUT_SECONDS = 300` and the output cap
   `OUTPUT_CAP_BYTES = 64 KiB`, module constants; past the cap the rest is
   dropped and `TRUNCATED_MARKER` says how many bytes.
6. *The log.* `commands/<n>.log` under the task's scratch dir, where the
   resumed phase can read it, written atomically. Its header lines are
   `argv: <JSON list>`, `target: <kind> <container>`, `service: <name>` for a
   service, `cwd: <path>`, then `exit: <code>`, or `timeout: after 300s, no
   exit code` on a timeout; then `--- output ---` and the (capped) output,
   stdout before stderr.
7. *The resume.* `run_phase` gains `resume_session_id`, `prompt` and
   `account`, and `dispatch_phase` gains `only_account`, which counts every
   other account as tried, so a pinned call that cannot get its account ends
   `no_account` and the task is held. The envelope now records the phase's
   `session_id` and `account`; for an envelope written before, the newest
   usage line of that role and round with a session is read. *Run*'s prompt
   carries the request's number, its argv as JSON, the target and `cwd`, the
   exit code or the timeout, and the output in a fence, or the log's path
   when the output passed the cap. *Deny*'s carries the argv and the comment,
   or `denied, no comment`, and says not to run it another way.
8. *The cycle goes on as after that phase.* `run_task_cycle` is split so the
   resume reuses its code: `_cycle_rounds(..., resumed=(round, result))` for
   an implementador starts the rounds from the result it returned (the gates,
   then the revisor, and on to the auditor), and `_cycle_from_arquitecto`
   follows a resumed cartografo. A resumed phase that files a request again
   blocks again, read the way a cycle reads it. The resume opens and closes
   the cycle with `open_cycle` and `close_cycle`, as `run-phase` does, with no
   scheduler.

Out of scope: *Allow for this role* and the allowlist (step 5), the Approvals
card and any api route (step 6), the board service, and a resume on another
account than the phase's.

**Consequences.** A task blocked on a command request can be unblocked by the
operator, and the board service's worker has one function to call. A resume
held after the verdict (its account not idle) leaves the verdict recorded, so
a second verdict is refused; the phase is then run again with `run-phase`,
in a fresh session. The
timeout kills the `docker exec` client, not the process inside the container.
The resume carries no write window, as `run-phase` does not.
`--permission-prompt-tool` (ADR 118, decision 8) stays deferred.

## ADR 120 — a verdict whose resume was held is resumed with `dispatch command-resume`, never by running the command again

**Status:** accepted (T-076, 2026-10-18). Follows ADR 119; builds the held
resume of step 4 of `docs/plans/command-requests.md` (decision 5). The plan
gains a dated note under step 4 and under decision 5; the rest of its text is
not edited.

**Context.** ADR 119 records a verdict before its command runs and takes one
verdict per block, so a command never runs twice. A resume that then finds
the phase's account taken is held, and nothing could give that verdict again:
the operator's only way on was `run-phase`, in a fresh session that had lost
the context the resume was for. A held resume of the cartografo was worse: it
was read as an optional phase that failed, and the cycle handed the task on
to the arquitecto as if the mapping had run.

**Decision.**
1. *`dispatch command-resume <task> --project <slug>`*, calling
   `command_run.resume_command_verdict(cfg, task_id, slug) -> CycleOutcome`.
   It resumes the session of the block's verdict with the prompt that verdict
   was resumed with, and carries the cycle on as `command-verdict` does. It
   never runs the command: *run*'s prompt is rebuilt with `run_prompt` from
   what was recorded, *deny*'s with `deny_prompt` from the recorded comment.
   The exit is `command-verdict`'s (blocked 1, held 75, split 3); a refusal
   prints its reason and exits 1.
2. *Refusals*, as `VerdictRefused` before anything is opened or run: the task
   is not blocked on `command_request`; the current block (the newest filing
   envelope, as ADR 119 reads it) has no verdict; its verdict already has a
   `resumed_at`; the phase's session is not on record; a *run* verdict whose
   log is missing, unreadable or for another argv.
3. *What a run came to is kept on the verdict.* Right after the run,
   `tasks.update_command_verdict` adds `ran: {exit_code, timed_out,
   truncated, log, resolved}` to the entry. The output stays in
   `commands/<n>.log`, after its `--- output ---` line, so it is not copied
   into the frontmatter. An entry without `ran` (written by T-074, or by a
   run that stopped between its log and `ran`) is read from the log's header,
   which names the argv, the target, the `cwd` and the exit or the timeout;
   truncation is read from `TRUNCATED_MARKER` in the output.
4. *`resumed_at` is set when the resumed phase took the task's lock*
   (`ctx.took_lock` after `run_phase`), before the cycle goes on, by
   `command-verdict` and `command-resume` alike. That is the point where the
   session has been handed to an account and may run:
   - not before it: a resume held for no account, or stopped by another
     run's lock, never ran the session, and keeps its verdict resumable;
   - not after the cycle: a crash between the lock and the write leaves the
     task `in_progress`, since `acquire_lock` already moved it off its block,
     and `command-resume` refuses it on status. So a session is never
     resumed twice, and a held one is never stranded.
   A resumed phase that ran and failed keeps its `resumed_at`; the task is
   then moved on with `run-phase`, as any failed phase is.
5. *An entry without `resumed_at`* (every T-074 entry) is read as not
   resumed; the task's status is then the guard. A T-074 verdict that did
   resume moved the task off its block, so it is refused there; one whose
   resume was held is resumable.
6. *A held resume puts the block back.* A pinned call refused by a rate
   limit took the lock and gave it back before ending `no_account`, which
   leaves the task `in_progress` with no owner. When the resume is held and
   no one owns the task, it is marked blocked on `command_request` again, so
   `command-resume` can find it. A task another run owns is left alone.
7. *A pinned phase that found its account taken is held.* `run_phase` reads
   `no_account` on a call with `account` as held, whatever `fatal` says, so a
   held cartografo resume stops the cycle instead of handing it on.
8. The CLI's held message for either command names `command-resume <task>
   --project <slug>` when the task awaits a resume (`awaits_resume`), and
   `run-phase` otherwise.

**Consequences.** A verdict whose resume was held is resumed later in its own
session with the same prompt, and the command runs once whatever happens to
the resume. An exception inside `dispatch_phase` after it took the lock leaves
the task `in_progress` with no owner and `resumed_at` unset; `command-resume`
refuses it on status, and the operator moves it on with `run-phase`. That is
the conservative side: the session may have partly run. The resume is still
on the phase's own account only.

## ADR 121 — *allow* runs a request once and writes it to a per-project, per-role allowlist outside every agent's reach, merged into that role's phases

**Status:** accepted (T-077, 2026-10-18). Follows ADR 119 and ADR 120; builds
step 5 of `docs/plans/command-requests.md` (decision 6). The plan gains a
dated note under decision 6 and under step 5, and an *Answered* note under
the open question on prefixes; the rest of its text is not edited.

**Context.** ADR 119 built *run once* and *deny*. A command a role needs in
every phase (`uv sync`, the project's test runner) would be filed, blocked on
and run once in each cycle. Decision 6 says *allow* writes it to the
project's list for that role, as the `Bash(...)` pattern the CLI takes, and
the dispatcher merges that list with the global `allowed_tools`. Decision 7
says no verdict overrides the denylist, so an entry must never let a phase
run what filing would refuse.

**Decision.**
1. *`dispatch command-verdict <task> <n> allow [--prefix N]`.* *Allow* does
   what *run* does (the re-check, one run, the log, the resume, ADR 119 and
   120) and, before the run, writes one standing entry for the phase's role
   in the task's project. The verdict entry on the task records the pattern
   as `allowed`, and the resume prompt tells the phase it may run that
   pattern itself from its next phase on. A held *allow* is resumed with
   `command-resume` like a *run*, with the same prompt and no second run or
   entry.
2. *The pattern is the exact command by default.* `Bash(<argv joined by
   spaces>)`. `--prefix N` writes `Bash(<first N tokens>:*)` instead. The
   flag exists because some commands a role needs differ in their tail on
   every call (`uv run pytest tests/x.py -q`, `npm test -- --grep y`): an
   exact entry for each would be filed again every time, and the one way to
   stop that would be widening the global list, which decision 6 forbids. It
   is opt-in, with a count rather than a free-form pattern, so the prefix is
   always a prefix of the argv the operator is looking at, and the operator
   chooses how far it reaches. `--prefix` with any verdict but *allow*, or
   below 1, is a parser error; above the argv's length it is refused.
3. *Only the phase's own commands.* *Allow* is refused unless the request
   resolved to kind `agent`: `--allowed-tools` governs the phase's own Bash,
   in its own container, so a `dind` or `service` command has nothing it
   could stand for. The refusal says to give *run* instead.
4. *What can be written.* Every token must match `[A-Za-z0-9_./@%+=,:-]+`:
   no whitespace, quote, parenthesis, glob, `*`, `$`, backtick, `~`, `#`,
   `!`, braces, `;`, `&`, `|`, `<` or `>`, and no empty token, so the shell
   the CLI matches against reads each one as the word the argv had, and `:*`
   cannot occur inside one. The first token may not hold `=` (an assignment
   to the shell) or start with `-`. The tokens must pass
   `command_targets.denylist_problems` as kind `agent`, the rules of filing,
   with the cwd rule given nothing to judge. An argv that fails any of these
   is refused as `VerdictRefused`, before the verdict is recorded: nothing
   runs and nothing is written. The operator can still give *run*.
5. *What a prefix may be* (`allowlist.prefix_problems`), on top of 4, since
   `:*` lets the phase append anything after it:
   - at least two tokens, and none after the first is an option (its value
     would be left open);
   - the head is not a command that runs another one from its arguments: the
     shells, `sudo`/`su`/`doas`, the docker binaries, `env`, `xargs`,
     `timeout`, `nice`, `nohup`, `exec`, `eval`, `find`, `ssh`, `rsync`, the
     interpreters (`python*`, `pypy*`, `node`, `deno`, `perl`, `ruby`, `php`,
     `lua`, `Rscript`, `awk`, `sed`, ...), the editors and `npx`/`uvx`/`bunx`;
   - a launcher spelt in more than one token (`uv run`, `npm exec`, `poetry
     run`, `bundle exec`, ...) is judged by the program after it, and a prefix
     that ends at the launcher is refused;
   - a `git` prefix names one of its local subcommands (`status`, `diff`,
     `log`, `add`, `commit`, ...), not `push`, `config`, `remote` or
     `submodule`, so `git` alone and `git -c` never stand.
   The whole argv is checked as in 4 as well, so a prefix is only offered on
   a command that could itself be allowed.
6. *Where the list lives.* `<state_dir>/allowlists/<slug>.json`, one file per
   project, keyed by role, each entry `{pattern, by, at, from: {task_id,
   role, n, filed_at}}` with `at` in UTC ISO. `state_dir` is the dispatcher's
   own volume, which no agent container mounts. Not the hive: phases write to
   it (`acceptEdits` and `--add-dir` on the hive root), so a phase could add
   its own entry. Not the project's repo, for the same reason, and because a
   clone would carry it. Writes take an `flock` on `<slug>.lock` and replace
   the file atomically (a temp file and `os.replace`), as the ADR numbering
   does. Allowing a pattern already on the role's list keeps the one entry.
7. *The merge.* `_phase_permission_flags(cfg, role, slug)` builds the list a
   phase is given: the global `allowed_tools` (and `mapping_allowed_tools`
   for the cartografo, ADR 115) first, then the project's entries for that
   role, with no repeat. Every call that runs a role's own work goes through
   it with the task's slug, the fresh phase and its retries (a missing
   handoff, a shrink, a gate fix, a review that wrote) alike, and a resume
   with `resume_session_id` is a `dispatch_phase` call too, so it gets the
   same list. A call with no slug gets the global list unchanged, and the
   global list is never written to.
8. *Each stored entry is checked again on the way in*
   (`allowlist.stored_pattern_problems`): a pattern ending in `:*` by 5, any
   other by 4. A hand-edited, corrupt or since-forbidden entry is skipped and
   logged as a warning, and an unreadable file adds nothing; the phase still
   runs with the global list.
9. *`dispatch allowlist --project <slug> [--role R]`* prints the entries, one
   per line: role, pattern, who and when, the task and request it came from.
   `--role R --revoke <pattern>` removes one, under the same lock; an unknown
   pattern exits 1, `--revoke` without `--role` is a parser error.
10. `learnings.harness_fingerprint` still hashes the global list only: an
    entry is project data, not a change to the harness.
11. `test_an_unknown_verdict_is_refused` gave `allow` as its unknown verdict;
    it now gives `approve`.

**Consequences.** A command a role keeps needing is decided once per project
and role, and the decision can be listed and revoked. What stays open:
- An exact entry is the filing rule and no more: what an operator could run
  once, they can allow. It runs exactly that argv.
- A prefix cannot stop what the phase appends. `Bash(npm test:*)` runs a
  `package.json` script the phase can edit, and `Bash(uv run pytest:*)` runs
  any test file it writes, so a prefix on a project's own runner amounts to
  letting the phase run its own code there. That is why the exact command is
  the default and the prefix is the operator's explicit choice.
- The lists are only in the CLI; the Approvals card (step 6) offers the same
  choice.

**Ruled out.** A free-form pattern on the CLI (it could name what the argv
never was); storing the list in the hive or in the project's repo (a phase
can write both); writing an entry into the global `allowed_tools`; allowing a
`dind` or `service` command (the flag does not reach those containers).

## ADR 122 — `GET /api/command-requests`: the hive's filed requests, each with its state and verdict, read through `command_run` and with no output

**Status:** accepted (T-078, 2026-10-18). Follows ADR 117, ADR 119, ADR 120
and ADR 121; builds the data half of step 6 of
`docs/plans/command-requests.md`. The plan gains a dated note under step 6;
the rest of its text is not edited. The card that shows it is T-079.

**Context.** A request is filed in a phase's handoff envelope, and a verdict
on it is a `command_verdicts` record on the task (ADR 119); `command_run`
answers which block a task is held on and whether its verdict waits on a
resume (ADR 120). The CLI is the only place an operator sees any of it.
Step 6 puts the decision in Approvals, and a front card needs a read first.
The read sits next to the logs of commands that ran, and those can hold a
secret the command printed.

**Decision.**
1. *The path.* `GET /api/command-requests`: plural and kebab-case like
   `/api/tasks` and `/api/roadmap`. It is hive-wide like `/api/roadmap`,
   because a request belongs to a task and the tasks are in the hive, not in
   a checkout. Basic auth and the envelope are the same as on every route,
   and an unknown parameter is a 400.
2. *The filters.* `?project=<slug>` is checked like on the roadmap (an
   unknown slug is a 404) and leaves out only a task whose file names
   another project. `?state=` is one of `pending`, `awaiting_resume`,
   `resumed`, `denied`; anything else is a 400. It narrows `requests` only.
3. *The answer.* `{"requests": [...], "refused": [...]}`. A request row is
   `task_id`, `project`, `title` (null: a task file has none, as on the
   roadmap), `role`, `filed_at` (the envelope's `saved_at`, the key a verdict
   names its block by), `n` (1-based over the accepted, as `command-verdict`
   takes it), `argv` (the exact list), `target` (`kind`, `container`,
   `service`, `cwd` as filing resolved them), `agent_reason` (the agent's
   own words, untrusted), `state`, `verdict` and `commands`.
4. *Which rows, and in what state.* Each filing role's envelope on record is
   one block.
   - A block with no verdict is a row per accepted request, `pending`, while
     the task is blocked on it (`command_run._check_blocked` and
     `_filed_block`, the CLI's own checks). Once the task has moved on with
     no verdict, it is no row: nothing is left to decide.
   - A block with a verdict is one row, for the request the verdict names. A
     verdict covers the whole block, so its other requests are moot.
   - That row is `awaiting_resume` while `command_run.awaits_resume` holds,
     for a *deny* too. Otherwise it is `denied` for a *deny*, and `resumed`
     for a *run* or *allow*, with `verdict.resumed_at` null when the task was
     moved on another way.
   - A verdict whose block's envelope a later phase overwrote is still a
     row. Its `argv` and `agent_reason` are null, and its `target` comes from
     the run's own `resolved`, or is null for a *deny*.
5. *The verdict.* `verdict`, `by`, `at`, `comment`, `allowed` (ADR 121),
   `resumed_at`, and `ran` as `{exit_code, timed_out, truncated, log}`.
   `log` is `commands/<n>.log`, relative to the task's scratch dir. No
   output is ever served, nor the absolute path or the run's `resolved`.
   `by` is null when it holds an `@`: the CLI writes `operator`, and a
   person's address must not land on every screen that lists requests. The
   envelope's `session_id` and `account` are never read into a row.
6. *The commands.* `commands` is `{run, allow, deny, resume}`, each a CLI
   line or null.
   - For `pending`: `dispatch command-verdict <task> <n> run|deny --project
     <slug>`, and `allow` only for a request resolved to kind `agent`, the
     one kind *allow* takes.
   - For `awaiting_resume`: `dispatch command-resume <task> --project
     <slug>`.
   - Tokens are `shlex.quote`d. With no project known (the task names none
     and the hive has more than one checkout), the slug is the literal
     `<project>`.
7. *Refused requests are listed apart,* in `refused`, and only for the block
   a task is blocked on now. Each row has `task_id`, `project`, `title`,
   `role`, `filed_at`, `argv` (null unless a list of strings), `target` as
   the agent wrote it (`kind`, `service`), `agent_reason` and
   `refused_reason`. There is no `n` and no commands, since nothing can be
   run. Apart, because a block of only refused requests would otherwise be
   invisible on the card, yet mixing them into `requests` would offer
   verdicts the CLI refuses. Only the live block's, because a past block's
   refusals were seen in its own handoff and decide nothing now.
8. *The order.* `pending` first, then by `filed_at`, oldest first, then by
   task id and `n`. What waits on the operator comes first, oldest first.
   `refused` is ordered by `filed_at` and task id.
9. *Read-only and bounded.* No write, no docker, no subprocess: `_recheck`,
   which calls docker, is not used. A task file or envelope that will not
   read, or that has the wrong shape, is a warning naming its path, and the
   rest of the answer still comes back. A `command_verdicts` entry that is
   not a mapping makes the task file a bad one. At most
   `MAX_COMMAND_REQUEST_TASKS` (2000) task files are read, by sorted id,
   with a warning, as on the roadmap.

**Consequences.** The card can show what waits on the operator and how to
answer it with no second copy of `command_run`'s rules. What stays open:
- The answer offers commands to copy, not buttons. A verdict from the board
  is a write and a docker exec, which this read does not do.
- A block whose envelope was overwritten keeps only its verdict, so an old
  row has no argv. Keeping the requests on the verdict record would fix it,
  as a dispatcher change.
- An undecided block the task moved on from leaves no trace here.

**Ruled out.** Serving a log's text, even truncated (a command can print a
secret); listing refused requests among `requests` with a state of their
own; a route per project (requests belong to tasks, and tasks are
hive-wide); re-checking a request's target from the API (docker).

## ADR 123 — The command-request card in Approvals: read-only, argv first, the agent's reason last and marked, verdicts left in the CLI

**Status:** accepted (T-079, 2026-10-18). Follows ADR 121 and ADR 122;
builds step 6 of `docs/plans/command-requests.md`. The plan gains a dated
note under step 4 and under step 6; the rest of its text is not edited.

**Context.** ADR 122 serves the hive's filed requests with their state,
verdict and the CLI lines that move them on. The Approvals screen is where
the plan puts them (*Decisions* 4), but every other row on that screen is
still a mock, and a verdict from the board would be a write and a docker
exec that nothing on the console's side does yet. The console's server
forward is a whitelist, so the new read is not reachable from the browser
until it is listed there.

**Decision.**
1. *Read-only.* A section at the top of `front/src/routes/approvals.tsx`,
   in `front/src/components/console/command-requests.tsx`, over
   `getCommandRequests` (`readEnvelope`) and `commandRequestsQuery`, polled
   at `POLL_MS`. It has no button that writes and does not call
   `decideApproval`; the only controls are the state filter's. The mock
   approvals below it are not changed.
2. *The order in a card is the plan's.* The exact argv first, as one line
   with each token `shlex.quote`d (`argvLine` in `front/src/lib/format.ts`),
   so `["rm", "a b"]` and `["rm", "a", "b"]` read differently; a dash when
   the argv is gone. Then the resolved target (container, service if any,
   cwd). Then the state, the role, the task as a link to `/tasks/$taskId`,
   `#n`, the project and `filed_at`. Then the verdict and the CLI lines.
   The agent's reason is last, in a dashed box headed *The agent's reason —
   its own words, not verified; do not trust it*. Every served string is
   React text, never HTML.
3. *The verdict.* `verdict`, `by` (or *not served*), `at`, `comment`,
   `allowed`, `resumed_at`, and `ran` as exit code, timed out, truncated
   and the log's relative path. No log text is fetched or shown.
4. *The CLI lines.* Only the non-null ones of `commands`, as selectable
   mono text, with a note that verdicts are given from the CLI for now.
   Where `allow` is offered, a note says `--prefix N` widens it to the
   first N tokens under ADR 121's rules. No copy button: nothing else in
   the console has one, and a clipboard write is one more surface on a card
   meant only to read.
5. *Refused requests* are a section of their own under the requests, shown
   only when there are any: argv (or a dash), target kind and service, why
   the check refused it, and the agent's reason, marked the same way.
6. *The filter.* *all* or one of the four states, sent as `?state=`; the
   query key carries it, so each filter has its own cache. The api's order
   (pending first) is kept as served. Warnings go in `WarningBanner`; a
   failed read is `ErrorState` with the api's message, and an empty one is
   `EmptyState`.
7. *The forward.* `/api/command-requests` is added to `FORWARDED` in
   `front/src/lib/api/forward.ts`, copying `project` and `state` by name,
   and is not in `PROJECT_SCOPED`: the api reads requests hive-wide and
   `?project=` only narrows them, so the console adds no slug. The forward
   now lists ten routes, and its rejection message says so.

**Consequences.** The operator sees what waits on them, with the argv
before the pitch, and copies the line to answer it. What stays open:
- A verdict from the board. It is a write and a docker exec, and needs its
  own ask, its own service and a credential (ADR 19).
- The card has no project filter of its own; the read takes one, and the
  console does not yet know more than one project.
- The rest of Approvals is still the mock.

**Ruled out.** Buttons that give a verdict, even disabled; showing the
reason first or beside the argv; joining the argv on spaces; fetching any
part of a run's log; a copy button with no pattern for one in the console;
scoping the read to `CONSOLE_PROJECT` in the forward.

## ADR 126 — `phase` on the task file rides on the lock: written with it, cleared with it, and a stale one is the reaper's

**Status:** accepted (T-081, 2026-10-18). Step 6 of
`docs/plans/board-service.md`. Narrows ADR 26 and ADR 28 and reverses neither:
ADR 28 named the record that was missing and said the fix was dispatcher
state, and this is that state, for the role only. ADR 26's refusal to grow a
role field on `/api/tasks` stood on the file having none; the file now has
one, so serving it is `_task` serving `TaskFile`'s fields, as ADR 59 has it.
The card stays refused.

**Context.** The board's five role lanes need to know which role is running
on an `in_progress` task. ADR 28 found that nothing records it: the dispatcher
holds the role while a phase runs and writes only a heartbeat, and the
handoff files name the phase that *ended*. The plan puts a `phase` key in the
task file, written as each phase starts and cleared as it ends. The question
this task had to answer is what clears it when the phase does not end
normally, including a dispatcher that is killed with the phase running.

**Decision.**
1. *The key.* `TaskFile.phase`, a role name or `None`. `read_task_file`
   reads it and `write_task_file` writes it only when set, after
   `command_verdicts`, so a file without one keeps its bytes.
2. *It goes with the lock, not on writers of its own.* `tasks.acquire_lock`
   takes `phase` and writes it with `owner` and `heartbeat`, replacing
   whatever was there, so a lock taken without one leaves none.
   `release_stale_lock`, `handoff` and `mark_blocked`, the three writers that
   drop the lock, clear it. `refresh_heartbeat`, `set_resolved_debt` and
   `mark_merged` leave it.
3. *The dispatcher.* `dispatch_phase` passes `phase=role` to the
   `acquire_lock` it already makes. Every way out of that function already
   drops the lock: the exception handler, a rate limit, an expired login and
   a failed exec call `release_stale_lock`. A phase that succeeds returns
   with the lock held, and the caller's `tasks.handoff` (`run_phase`,
   `run_draft_specs`) drops it. So no new call site exists, and none can be
   forgotten.
4. *A killed dispatcher.* It leaves `owner`, `heartbeat` and `phase` together,
   and what clears the lock clears the role: `reap_expired_locks` at the next
   `open_cycle` for an `in_progress` task whose heartbeat is past the TTL,
   `dispatch release-account` by hand, or the next `acquire_lock`, which
   writes its own role. Until then the api's `lock_expired` is `true` beside
   it, and that is how a reader tells a dead phase from a running one. No
   second clock and no startup sweep: a role with a separate lifetime from
   the lock would be a second answer to "is this task held".
5. *The api.* `/api/tasks` and `/api/tasks/<id>` serve `phase` on every row,
   `null` when the file has none. It is read through `read_task_file`, and
   nothing is derived.

**Consequences.**
- The board can split In progress into its lanes from
  `status == "in_progress"` and `phase`. That is step 8, a front task.
- A task between two phases of one cycle is `pending` with no `phase`, since
  `run_phase` hands off to `pending`. A reaped task is `in_progress` with no
  `phase` and no heartbeat
  (`docs/learnings/a-reaped-lock-is-in-progress-with-no-heartbeat.md`). The
  board needs a place for the second one that is not a role lane.
- `reap_expired_locks` only reaps `in_progress`. A phase killed on a task in
  any other status keeps its stale lock and role until `release-account` or
  the next lock, as its lock already did. This entry does not change that.
- Of ADR 28's list, only the role is now written. The phase's start time,
  model and duration are still nowhere. The heartbeat is refreshed, so it is
  not a start time.
- A draft set's file carries `phase: arquitecto` while `draft-specs` runs,
  through the same lock.

**Ruled out.**
- A `set_phase` / `clear_phase` pair of writers called around each phase.
  That is two more call sites per exit path, and one missed is a role that
  never clears.
- Clearing a stale role on the api side, or hiding it when `lock_expired` is
  true. The api serves the file.
- A startup sweep that clears every `phase`. That would wipe the role of a
  phase another dispatcher process is running.
- Recording the role in `state_machine` instead. The board reads the task
  file, and `owner` is already there.
- Writing it to a board card (ADR 26).
## ADR 124 — The board's write side is the board service, a root-level package and a service of its own; the git credential and both pushes stay on the dispatcher

**Status:** accepted (T-080, 2026-10-18). Settles the decision
`docs/plans/front.md` *Tier 3* binds to the tier-3 opening task, as step 4
of `docs/plans/board-service.md` asks, and the credential question
`docs/plans/review.md` leaves to the same step. Follows ADR 59, ADR 75 and
ADR 78. The plan's text is not edited.

**Context.** `front.md` keeps the api write-free and a Node app in its own
top-level directory, and says neither rule places a Python service that
writes. `board-service.md` section 5 already names one: the board service
is both the agents' MCP server and the console's write side, with the hive
mounted read-write and no docker socket, and the api stays GET-only. What
it leaves open is where the code and the process live, and `review.md`
leaves open where the git credential for *approve*'s base push and the
task-branch push of C-5 as amended live, saying only that neither the api
nor the board service may hold it. ADR 78 has since answered the
credential itself: the push uses what the fetch already uses.

**Decision.**
1. *A root-level package, `board_service/`.* Beside `config_writer/`, not
   under `observability/` (which stays the read side) and not in `front/`
   (Node). It has its own `Dockerfile`, built from the repo root, and its
   own entry in `pyproject.toml`'s `include`, as ADR 75 placed the
   config writer.
2. *Its own compose service.* `board-service`, one process serving both the
   MCP tools of section 6 and, from step 5, the console's HTTP write
   routes. The console reaches it on `console_net`; the phases on
   `ia_harness_net`. It mounts the hive, `config.yaml` and `config.d`, and
   nothing else: no docker socket, no `.data/`, no projects, no keys.
3. *It reads and writes through `dispatcher/tasks.py`.* The service
   imports the module; it has no parser or writer of its own. The api keeps
   importing the same module for its reads and stays GET-only.
4. *Reads only, for now.* T-080 serves `board_list` and `board_get` and
   writes nothing; the hive is mounted `:ro` until step 5 brings the first
   write, the per-task lock with it.
5. *The git credential and both pushes stay on the dispatcher's side.*
   The base push after *approve* and the task-branch push after a phase
   that commits are the dispatcher's, with the credential ADR 78 names (the
   project's deploy key or the mounted https:// token the fetch uses). The
   board service never mounts a key, a token, a project checkout or
   `.data/`, and never runs git. A verdict written through the service is a
   change to the task's files; the push it leads to is the dispatcher's.
6. *The dispatcher learns of the service's writes by reading the hive*, as
   it reads tasks today: a file the service wrote is in the hive the next
   time the dispatcher reads it. No signal, socket or queue from the
   service to the dispatcher. One that is needed for latency is its own
   ADR.

**Consequences.** The write side has a place before it has a write, and the
credential has one owner. What stays open:
- The compose entry and its invariant tests (T-080.md holds the exact
  entry); a change to `docker/compose/` needs the user's approval.
- The per-task lock of section 5, which step 4 was to bring: it guards
  writes, and comes with the first write in step 5.
- Whether the console's write routes are the same Flask app as the MCP
  endpoint or a second blueprint on the same process; step 5 decides.

**Ruled out.** The writes in the api (it stops being write-free, which is
what lets it be reachable at all); a module under `observability/`; the
service holding a git credential or pushing; a signal from the service to
the dispatcher before a latency problem asks for one; a queue-and-worker
pair before there is a write slow enough to need it.

## ADR 125 — The board service speaks stateless MCP over HTTP from its own handler; the role is a query parameter checked on every call

**Status:** accepted (T-080, 2026-10-18). Builds the transport and the role
check of `docs/plans/board-service.md` sections 5 and 6, and their read
rows. Follows ADR 124.

**Context.** Section 5 has the agents reach the service over MCP's
streamable HTTP, with the role in the URL (`?role=<role>`) and no
authentication; section 6 lists which tools each role gets. The project
has no MCP library among its dependencies, and adding one is a network
install. Two read tools and a handshake are a small part of the protocol.

**Decision.**
1. *One `POST /mcp`, stateless, JSON answers only.* Each POST carries one
   JSON-RPC 2.0 message. `initialize` echoes the client's
   `protocolVersion` when it is one of 2024-11-05, 2025-03-26, 2025-06-18
   or 2025-11-25, and offers 2025-11-25 otherwise; the capabilities are
   `tools` with `listChanged: false`. `ping`, `tools/list` and
   `tools/call` are answered; a notification or a response gets 202 with
   no body; any other method is -32601. No `Mcp-Session-Id`, no event
   stream: `GET` and `DELETE` on `/mcp` are 405. A batch is -32600 and a
   body that is not JSON is -32700. `GET /health` is for compose.
2. *Any `Origin` header is refused with 403*, the spec's guard against DNS
   rebinding. No browser is a client: the console reaches the service from
   its server, and Claude Code sends no Origin.
3. *The role is `?role=`, and it is a label, not a credential.* It must be
   `operator` or one of `PHASE_ROLES`; a missing or unknown role, or any
   other query parameter, is 400, so a misspelt URL fails loudly instead
   of serving no tools. `ROLE_TOOLS` is section 6's table: `operator` and
   `arquitecto` get `board_list` and `board_get`; every other role gets an
   empty `tools/list`. `tools/call` checks the role again on every call,
   and a tool outside the role's set is the same -32602 as an unknown one.
4. *A tool's failure is a result, not a protocol error.* A bad argument,
   an unknown argument, a missing task, a path or draft id and an
   unreadable or unmigrated file are `isError: true` with the reason as
   text. A result is its JSON as one text block and, the same object, as
   `structuredContent`.
5. *`board_list`* reads every task file through `context_transfer` and
   `tasks`, sorted by id and clamped at 2000, and serves `id`, `title`,
   `status`, `place`, `epic` and `merged` (a boolean). `place` is
   `backlog` for `proposed`, `deferred` and `dropped` (none written before
   step 5) and `board` otherwise. Filters `place`, `status`, `epic` and
   `text` combine; `text` is a case-folded substring of the id, the title
   and the description. A file that will not read, or is unmigrated, is a
   warning naming it, and the rest of the list is served.
6. *The title.* No task carries one yet. A frontmatter `title` is served
   when there is one; otherwise the first non-blank line of the
   description's *Goal*, cut to 100 characters; otherwise `null`. The api's
   roadmap rows keep `null`; the board's rows are for a model to scan.
7. *`board_get`* serves the whole frontmatter (YAML dates and other values
   JSON cannot carry made into text), the description verbatim or `null`,
   `place`, and the comment count: the non-blank lines of
   `board/<id>/comments.jsonl`, 0 when the file is absent.

**Consequences.** Any MCP client that speaks streamable HTTP can list and
read the board, and the role check is one table. What stays open:
- No test runs a real MCP client against the endpoint; the protocol is
  pinned by the handler's tests against the spec's shapes.
- The per-phase `--mcp-config` the dispatcher writes (step 7) and the
  operator's `.mcp.json` (T-080.md holds the exact change).
- If a later tool needs server-sent notifications or progress, the
  transport grows an event stream and this ADR is superseded.

**Ruled out.** Adding the `mcp` package as a dependency for two tools; a
session id the service would have to keep; the stdio transport (the
service is a container the phases reach over a network); the role as a
header (the URL is what `--mcp-config` and `.mcp.json` carry); serving an
unknown role no tools rather than refusing it; parsing `comments.jsonl` to
count it.

## ADR 128 — Each phase gets the board's `--mcp-config` from the role table; the URL is a read-only config key, on by default and able to be turned off

**Status:** accepted (T-083, 2026-10-18). Builds step 7 of
`docs/plans/board-service.md`. Follows ADR 125.

**Context.** ADR 125 left the per-phase config open. The service takes the
role from `?role=` and refuses an unknown one, so each phase needs a URL
written for its own role. A committed `.mcp.json` cannot do this, because
every clone under `.data/projects/` would read it under one role. Section 6
gives board tools only to `operator` and `arquitecto`, and the operator is
not a phase. The CLI also asks before an MCP tool runs unless the tool is in
`--allowed-tools`, and under `-p` an unanswered ask is a denial. A resume is
a new process and inherits no flags.

**Decision.**
1. *One table, in the dispatcher.* `ROLE_TOOLS` and `OPERATOR` move to
   `dispatcher/board_mcp.py`, which imports nothing from the package, and
   `board_service/app.py` imports them from there. The service already
   imports `dispatcher`, so the dispatcher could not import the service
   back. With one copy, the roles that get a config and the roles the
   service serves cannot drift apart.
2. *Only a role with tools gets the server.* `_phase_permission_flags` adds
   `mcp_config` for a role whose `ROLE_TOOLS` entry is non-empty, which today
   is the arquitecto, and that includes the draft-specs run. Every other
   phase's command line is unchanged. The value is inline JSON, one http
   server named `board`, at `<board_mcp_url>?role=<role>`, with the role
   URL-encoded.
3. *The tools go in the allowed tools.* `mcp__board__board_list` and
   `mcp__board__board_get` are appended after the configured list and the
   project allowlist, and a name already there is not added again. Without
   them every call would be a denial under `-p`.
4. *It rides with the permission flags.* `exec_claude` takes
   `mcp_config` and emits `--mcp-config <json>` before `--add-dir` and
   ahead of `-p`, since the flag is variadic. Because the flags dict is
   splatted into every phase call, a resume restates it as well. The
   `/usage` probes get none of this.
5. *`board_mcp_url` is a read-only config key.* It defaults in code to
   `http://board-service:8792/mcp`, the compose name on `ia_harness_net`,
   where the agent containers are. `null`, `""` or whitespace turns it off.
   The value is stripped, it must be http or https, and a query or fragment
   is refused, since the service refuses any parameter but `role`. It is
   read-only in `CONFIG_KEYS` because it is a service address and a grant.
   The name is not `vibe_kanban_mcp_url`, which `_refuse_board_keys`
   refuses (ADR 59).

**Consequences.** The arquitecto's phases can list and read the board once
the service is up. If the service is down, its tools are missing for that
phase, and the phase goes on. What stays open:
- `--strict-mcp-config` is not passed, so a project's own `.mcp.json` in a
  clone still loads beside the board. Passing it would also drop servers a
  project relies on. That is the user's call.
- The learnings' `harness_fingerprint` still hashes the configured
  `allowed_tools` only. The board names are fixed per role and change with
  the code, not the config.
- No test runs the real CLI or a live service; the command line and the
  URL are pinned, and the URL is checked against the service's own handler.

**Ruled out.** A temp file per phase for the config (inline JSON needs
nothing on disk inside the agent container); a committed `.mcp.json`; a
config for every role with an empty `tools/list`; a second copy of the role
table in the dispatcher; letting the operator edit the URL from the console;
a URL with its own query string.
## ADR 127 — The backlog statuses, `priority` and `source` on the task file, and a per-task write lock that is not the ownership lock

**Status:** accepted (T-082, 2026-10-18). Step 5a of
`docs/plans/board-service.md`: the task model and the dispatcher's side of
the backlog. The write tools are step 5b. Reconciles the per-task lock of
the plan's section 5 with ADR 126 and reverses neither: the `owner` and
`heartbeat` in the frontmatter stay the record of who holds a task, and
`phase` still rides on them.

**Context.** Section 4 of the plan splits tasks into a backlog, which the
dispatcher may not take, and the board, which it may. Section 5 asks that
every write take a per-task lock. Until now nothing told a backlog item from
a task: `open_cycle` refused only an unmet dependency or a malformed epic,
and the frontmatter had no priority or source. The writers were atomic
(`_write_atomic`) but did read-modify-write with no lock, so two processes
writing one task could lose an update. With one writer process that was
rare; step 5b adds a second one, the service.

**Decision.**
1. *The statuses.* `proposed`, `deferred` and `dropped` are the backlog
   (`tasks.BACKLOG_STATUSES`). `pending` is what a person accepts an item
   to (`ACCEPTED_STATUS`). The board statuses are unchanged.
2. *The fields.* `TaskFile.priority` (`high`, `normal`, `low`) and
   `TaskFile.source` (`operator`, `arquitecto`, `debt`), both `None` when
   unset. `write_task_file` writes them only when set, so a file without
   them keeps its bytes. An unset priority reads as `normal`
   (`effective_priority`). 5a does not require a source on a backlog item.
3. *Validated on read, not refused on write.* `field_problems` names a value
   outside the lists. `dispatch_problems` adds the backlog refusal to it.
   `open_cycle` and `queue.plan` refuse on either, and `board_list` returns
   the field problems as warnings. The module's writer does not refuse a
   bad value it did not set: a hand edit must not wedge the heartbeat or a
   verdict write. 5b's tools validate what they set.
4. *"Anything but `pending`" means the backlog.* `open_cycle` refuses the
   three backlog statuses, not every status but `pending`. A blocked retry
   (`command_run`), an `in_progress` resume and `run-phase` all go through
   it, and refusing them would break those paths. `acquire_lock` refuses a
   backlog status too (`BacklogTaskError`), as a backstop for a caller that
   skips `open_cycle`. It still takes a blocked task.
5. *The write lock is a separate lock.* `tasks.task_lock(hive_dir, id)`
   takes an exclusive `flock` on `<hive root>/locks/<id>.lock` for one
   read-modify-write. It is held for milliseconds and says nothing about
   ownership. Every writer in the module that reads and rewrites a task
   takes it (`@_locked`), and so do the creates in `file_split` and
   `accept_drafts`, `close_epic`'s rewrite of the parent, and each task in
   `migrate`. `acquire_lock`'s owner check and its write are now one step.
6. *Where the file lives.* Outside `tasks/` and `board/`, because both are
   listed (`list_task_ids`, `_next_task_number`) and a lock file must not
   read as a task. The lock files are kept, never removed, since removing
   one races a waiter that has it open.
7. *Who can take it.* The file is created `0644` (an explicit `fchmod`,
   whatever the umask) and opened read-only, so a process of another uid
   that can only read the hive can still lock it.
8. *Re-entrant per thread.* `flock` locks belong to an open file
   description, so a second open in the same thread would deadlock on
   itself. A thread-local set of held paths makes a nested take a no-op.
9. *One order.* Parent, then child, never the reverse: `file_split` holds
   the parent's lock while it creates the children, and `close_epic` takes
   only the parent's. `record_merge` calls `mark_merged` and `close_epic`
   one after the other, not nested.

**Consequences.** A deployment gets a `locks/` directory beside the hive's
`tasks/` and `board/`, created by the first write. The api and the
read-only board service take no lock: they read files that `_write_atomic`
replaces whole. When the service starts writing in 5b, it needs the hive
mounted read-write, `locks/` included. A writer that does not use the
module (a hand edit) is not serialized, as before.

**Ruled out.** Refusing a bad `priority` or `source` in the writer; refusing
every status but `pending` in `open_cycle`; using the frontmatter's
`owner` as the write lock (a read-modify-write would have to take ownership
of a task some phase already holds); a lock file beside the task in
`tasks/` (it would be listed); `fcntl.lockf` or `O_EXCL` lock files that
are removed after use (the first is per process, so a thread in the same
process would not wait; the second leaves a stale file behind a crash);
requiring a `source` on every backlog item before 5b's tools set one.

## ADR 129 — The board's write tools: four writers in `dispatcher/tasks.py` under `task_lock`, served over MCP and HTTP, with the source and author taken from the role

**Status:** accepted (T-084, 2026-10-18). Step 5b of
`docs/plans/board-service.md`: `backlog_propose`, `task_edit`,
`task_comment` and `backlog_accept` (section 6). Builds on ADR 124 (the write
side is this service), ADR 125 (stateless MCP), ADR 127 (the backlog
statuses, `priority`, `source` and `task_lock`) and ADR 128 (one role table).
Reverses none of them. `tags` and `rank` (5c) and debt in the backlog (5d)
are not part of it.

**Context.** Until now the service only read. Section 6 gives it four writes
and says who may call each. Section 5 says one module writes the hive and
every write takes the per-task lock. The plan leaves open a few points: where
a title lives, what makes a create idempotent, what `backlog_accept` does to
an epic, what "not started" means for `task_edit`, and the shape of the
console's endpoints.

**Decision.**
1. *One module writes.* The writers are `tasks.propose`, `tasks.edit_task`,
   `tasks.add_comment` and `tasks.accept`. `board_service/app.py` checks the
   arguments against each tool's schema, calls the writer, and maps its
   errors. It writes no file itself.
2. *Every problem at once, nothing written.* A writer checks everything
   before it writes and raises one `BoardWriteError` listing every problem.
   An epic is all or nothing. `NoTaskError` is its subclass for an id with
   no task file. A write the filesystem refuses (`OSError`, such as a
   read-only mount) becomes a tool error naming the hive, not a 500.
3. *The source and the author come from the role.* `backlog_propose` sets
   `source` to the caller's role, and `task_comment` sets `author` the same
   way. Neither takes them as arguments: a schema with
   `additionalProperties: false` refuses one. `source` is therefore always
   set on a proposal, as ADR 127 left for this step. `debt` is set only by
   the dispatcher's own call to the module (5d).
4. *The title is a frontmatter field.* `TaskFile.title`, written only when
   set. A create is idempotent on it within its scope: outside any epic for
   a proposal, among the siblings for an epic's task. A proposal whose title
   an existing task has answers that task with `created: false` when it asks
   for the same thing. It finishes a run that died part way: a missing
   description, or a missing task of an epic. It is refused when it asks for
   something else. `task_edit` keeps titles unique in the same scope.
5. *Creates are serialized.* `propose` holds `task_lock` on the reserved
   name `_create` (`CREATE_LOCK`) while it checks the title, picks the next
   free `T-<n>` and writes. `accept_drafts` takes the same lock, so the two
   never pick one id. The order is `_create`, then the parent, then each
   child. A new task's file is written before its description, so a retry
   finds it and fills in the description.
6. *An epic.* `tasks` of 2 to 4 entries, each with a title, a description
   and `after` (1-based positions of earlier entries). The parent is
   `T-<n>` with `split_into`. Its tasks are `T-<n>-S<k>`, as `file_split`
   names them, each `proposed` with `epic` set. An epic takes no
   `depends_on`.
7. *Descriptions.* A proposal and an edit need the sections *Goal* and
   *Done when*, the same check the module already makes. They need no
   *Surface* or *Out of scope*: those belong to the drafts of
   `accept_drafts`, not to a backlog item.
8. *`task_edit` before start only.* A backlog item has not started. A
   `pending` task has not started while no dispatcher holds it, nothing is
   merged and its task file has no handoff. An epic has not started while
   none of its tasks has. Anything else is refused. `depends_on` names tasks
   that exist, without a cycle. A task of an epic depends only on its
   siblings, and an epic depends on nothing.
9. *`backlog_accept`.* It moves a task from `proposed` or `deferred` to
   `pending`, `deferred` or `dropped`, with an optional `priority`. Asking
   for the status a task already has is a no-op, so a retry is safe. A no-op
   that would change the priority is refused, since that belongs to
   `task_edit`. `dropped` is final: proposing again makes a new item. A task
   on the board is refused.
   - An epic accepted becomes `status: epic`. Its `proposed` tasks become
     `pending`, and a `deferred` one stays out.
   - An epic dropped drops its backlog tasks.
   - An epic deferred goes alone.
   - A task of an epic is accepted on its own only once its epic is.
10. *Comments.* One JSON line `{"at", "author", "text"}` per comment is
    appended to `<hive root>/board/<id>/comments.jsonl`. A comment on a diff
    also carries `file`, `line` and `commit`: all three, or none. The append
    is one `write` on an `O_APPEND` file under the task's lock. Any task with
    a task file may be commented on, whatever its status.
11. *Who.* The operator gets all six tools. The arquitecto gets the reads,
    `backlog_propose` and `task_comment`; when it wants a task changed, it
    says so in a comment. Every other role gets none. A tool the role
    lacks is -32602 on every call, as for the reads.
12. *The console's endpoints.* `POST /api/backlog` answers 201 for a create
    and 200 for an idempotent repeat. `PATCH /api/tasks/<id>` edits.
    `POST /api/tasks/<id>/comments` answers 201. `POST /api/tasks/<id>/accept`
    accepts. They call the same tool functions as the operator, with the
    same schemas. The id comes from the path only, and a `task_id` in the
    body is refused. Refusals return `{"error": ...}`:
    - 403 for any `Origin`;
    - 400 for any query parameter, a body that is not a JSON object, or a
      tool refusal;
    - 404 for no such task.

**Consequences.** The service now writes the hive, so compose mounts its
`.hive` read-write, `locks/` included (ADR 127), with config still `:ro`, and
puts it on `console_net` as well as `ia_harness_net`, for the console's
writes. Both were approved by the user on 2026-10-18. The arquitecto's phases get
`mcp__board__backlog_propose` and `mcp__board__task_comment` in their
allowed tools through ADR 128's table. The console screens, the api serving
`priority`, `source` and `title`, and `tags` and `rank` come later.

**Ruled out.**
- A `source` or `author` argument: an agent could claim to be the operator.
- `backlog_accept` or `task_edit` for the arquitecto: accepting is a
  person's act (section 4).
- Writing the title only into the description's heading: it would leave
  idempotency to parsing prose.
- A create lock per title: the id allocation needs one lock across all
  titles anyway.
- Undropping a dropped item.
- Editing a task after it started.
- An HTTP route that takes the role from the request: the console is the
  operator's.
- Writing directly from `board_service`.

## ADR 130 — Tags, a sparse rank inside the priority bucket, and the Ready order as pure functions served by `board_list`

**Status:** accepted (T-085, 2026-10-18). Step 5c of
`docs/plans/board-service.md`: `tags`, `rank` and the *Ready* order
(section 4). Builds on ADR 127 (`priority` as the bucket, `task_lock`) and
ADR 129 (the writers in `dispatcher/tasks.py`, `CREATE_LOCK`, the schemas).
Reverses none of them. Debt in the backlog, its import and `impact` (5d) are
not part of it.

**Context.** Section 4 puts each item in a priority bucket and, inside it, at
a position the user sets by dragging. It also defines *Ready*: what can be
picked up now, in the order it would be picked up, where a dependency takes
the bucket of what waits on it and an epic's tasks take the epic's bucket.
It adds `tags`, a free list. The plan leaves open how a position is stored,
what "take the bucket" means when the task's own bucket is higher, where the
order is served, and whether a proposal that waits on a task lifts it.

**Decision.**

1. *`tags` is a free list, checked on read and normalized on write.* Each tag
   is a non-blank string of at most `MAX_TAG_CHARS` (50), at most `MAX_TAGS`
   (20) per item. The writer trims each tag and keeps the first of repeats,
   in order. Tags are case-sensitive. `backlog_propose` and `task_edit` set
   them; `[]` on an edit clears them; an epic's tasks take the epic's tags.
   A bad list on disk is a `board_list` warning and is served normalized.
2. *`rank` is a sparse integer, the position inside the bucket.* Ranks are
   handed out `RANK_STEP` (1024) apart, so one write moves one item between
   two neighbours. The highest rank is `MAX_RANK`, `2**53 - 1`, the largest
   integer JSON carries exactly. A missing or bad rank sorts as 0, so tasks
   from before this step come first, in id order. Ties break by the id in
   natural order (`T-3` before `T-10`).
3. *A new position is the end of the bucket, under `CREATE_LOCK`.* The end
   is the highest rank among every task, backlog and board, whose own
   `priority` is that bucket, plus one step. A proposal, an accepted draft,
   and an accept or edit that changes the bucket without giving a rank all
   take it. An epic's task `k` takes the epic's rank plus `k` steps. Every
   write that hands out a rank holds `CREATE_LOCK` before the task's lock,
   so two writes never take the same end.
4. *`task_edit` sets `rank`, and the schema bounds it.* `rank` is an integer
   from 0 to `MAX_RANK`; the schema check now enforces `minimum` and
   `maximum`, and the writer checks again and refuses a bool. A proposal
   takes no `rank`. Both schemas stay closed and the role table is
   unchanged.
5. *Ready is pure, in `dispatcher/tasks.py`.* `effective_buckets` raises
   each task to the highest bucket of what waits on it, transitively, and
   each epic's task to the epic's bucket. It only raises, so a high task in
   a low epic stays high, and it ends on a cycle. Backlog items lift
   nothing: a proposal is not a commitment, and it would let anyone with
   `backlog_propose` reorder the board. `ready_order` takes the pending
   tasks whose every dependency is known and merged, by effective bucket,
   then rank, then id.
6. *`board_list` serves it with the smallest surface.* Each row gains `tags`
   and `rank`. The backlog rows are listed by bucket, then rank, then id,
   after the board rows by id. A top-level `ready` holds the listed ids in
   the Ready order. It is computed over every task read, so a filter narrows
   it and never reorders it.

**Consequences.** The console can show the backlog in its real order and
drag inside a bucket with one `task_edit`. The order is a function the
dispatcher can call when it picks work. Changing a task's `priority` puts it
at the end of the new bucket. The tuples in `changed` gain `tags` and
`rank`, and a bucket change reports `rank` too.

**Ruled out.**
- A dense position renumbered on each move: one drag would rewrite the whole
  bucket, under many locks.
- Fractional or string ranks: floats run out of precision silently, and a
  string order is harder to check on read.
- A lower bucket for an epic's high task: "take the epic's bucket" is read
  as a floor, so an epic never demotes its own work.
- A separate `board_ready` tool: one more tool and one more role row, for a
  list `board_list` already has to read.
- Renumbering when a gap runs out: left to the console's drag (step 8),
  which knows the neighbours.

## ADR 131 — Debt entries as `proposed` backlog items, linked by `debt_id`, with their priority from the auditor's `impact`

**Status:** accepted (T-086, 2026-10-18). Step 5d of
`docs/plans/board-service.md`, the last part of step 5: debt in the backlog,
the one-time import of `docs/debt/`, and `impact`. Builds on ADR 127
(`priority`, `task_lock`), ADR 129 (`source`, the writers, `CREATE_LOCK`)
and ADR 130 (`tags`, the rank at the end of the bucket). Keeps ADR 59: no
index row is mirrored onto a board card, and `card` stays `none`.

**Context.** Section 4 of the plan puts known debt in the backlog next to
proposed work, tagged `debt`, so it is ranked and picked like the rest
instead of living only in `docs/debt/README.md`. It asks for an `impact`
that sets the item's priority, and a one-time import of the entries the
index already holds. It leaves open who judges the impact, where it is
written, how an item points at its entry, when a new entry is filed, and
what a second filing or a resolved entry does.

**Decision.**

1. *`impact` is a debt index column, written by the auditor.* It is the last
   column, after `card`, and it holds `risk`, `cost` or `cosmetic`. It is
   read by header name only, so a row from before the column, or a short
   row, reads as `none`, and so does any other value. The auditor's prompt
   and the filing note ask for it. The board does not judge it: the auditor
   has just read the code the entry is about.
2. *The impact maps to a priority.* `risk` is `high`, `cost` is `normal`,
   `cosmetic` is `low`, and `none` is `normal` (`debt.IMPACT_PRIORITY`,
   `DEFAULT_IMPACT_PRIORITY`). An item lands at the end of that bucket, as
   any proposal does (ADR 130).
3. *An item points at its entry with `debt_id`.* The task frontmatter gains
   `debt_id`, an entry id like `T-065-D1`, checked on read
   (`tasks.is_debt_id`) and served by `board_list` and `board_get`, where a
   bad value is a warning and is served as written. `card` is kept, with its
   meaning: ADR 59 retired the board link, and the link now runs the other
   way, from the item to the entry.
4. *One writer files an entry, idempotent on `debt_id`.*
   `tasks.propose_debt` holds `CREATE_LOCK`, looks for a task with that
   `debt_id` at any status, and creates one only if none exists: status
   `proposed`, `source: debt`, `tags: [debt]`, the priority from the impact.
   An item the operator dropped stays dropped and is not filed again. A
   lost description is written again. `tasks.file_debt` files a list of
   rows, skips resolved ones, and has a dry run.
5. *The item says what the entry says.* `debt.backlog_item` builds the
   title from the id and the **what** (cut to `MAX_TITLE_CHARS`), the
   `## Goal` from the **what**, the **where** and a pointer to the entry's
   file, and `## Done when` from the **fix**.
6. *The dispatcher files new entries after the auditor.* Once the auditor's
   round is done, in a cycle and in a final `run-phase`, the dispatcher
   reads the index from the writer's worktree and files each entry the
   dispatcher numbered in that task (`_mirror_filed_debt`). An entry the
   index does not show is filed from its declaration with no impact, and a
   warning. A resolved row is not filed. A failure is a warning and never
   fails the cycle: the index stays the record.
7. *`dispatch import-debt` files what is already there.* It reads a
   project's index in a container, as the dispatcher reads it
   (`--project`), or a file on the host (`--index`), and files every open
   entry through the same writer. `--dry-run` prints what it would file and
   writes nothing. Because the writer is idempotent, the import can run
   before or after cycles have mirrored entries, and twice.

**Consequences.** Debt shows in the backlog with a priority, a rank and the
`debt` tag, and the operator accepts or drops it like any proposal. Each
auditor round adds one index read. The 36 rows of this repository's index
gained an `impact` cell; the only open one, `T-065-D1`, is `cost`. Resolving
an entry does not close its item: the operator drops it.

**Ruled out.**
- Writing the item's id into the index's `card` cell: the index lives in the
  project's branch and the hive outside it, so a cycle would have to commit
  to the project for a board write. `debt_id` keeps the link on the side the
  board owns.
- Judging the impact on the board, or from keywords in the row: the auditor
  has the context, and a guess would set priorities nobody chose.
- Filing at `numbering` time, before the auditor: the row and its impact do
  not exist yet.
- Closing or dropping an item when its entry is marked resolved: a status
  change the operator did not make, from a cell any task can edit.
- A `debt` status or a separate debt list: `source`, `tags` and `debt_id`
  carry it, and the backlog's order already applies.

## ADR 132 — The queue tops up from the Ready order after the listed tasks, picked once at plan time

**Status:** accepted (T-087, 2026-10-18). Lets the dispatcher's queue use the
board's Ready order (ADR 130: the bucket lifted by what waits on it, then the
rank, then the id). Keeps ADR 79 and charter C-4: what runs is what the
confirmation screen showed, and a resume never picks. Keeps every check
`plan` makes and every rule the queue runs by: merged dependencies, surface
and whole-repo exclusivity, the limit.

**Context.** Until now a queue was exactly the task ids the operator typed,
reordered only so that a dependency inside the queue runs first. The board
now keeps an order of what is ready to start, and the operator ranks the
backlog there. The queue did not read it, so the ranking had no effect on
what ran. Open questions: whether the Ready order replaces the operator's
list or adds to it, when it is read, and what happens to a ready task the
queue's checks would refuse.

**Decision.**

1. *`run-queue --ready N` adds the first N ready tasks.*
   `queue.plan(..., ready=n)` reads `tasks.ready_order` over every task file
   (`read_all_tasks`), the order `board_list` serves. It takes, in that order,
   the first `n` tasks that are not already listed. `--task-id` becomes
   optional when `--ready` is given. A negative count is refused, and so is a
   queue left with no task.
2. *The operator's list keeps its order.* Listed tasks come first, as
   typed. Picked tasks follow, in Ready order. `_ordered` still moves a task
   only as far as a dependency inside the queue requires. A picked task has
   its dependencies merged, so it is never held back. It moves ahead of the
   listed tasks only when one of them depends on it.
3. *A ready task the checks would refuse is passed over.* The same checks run
   on it as on a listed task (`_entry`: a description, in sections, and
   `dispatch_problems`). A refusal does not refuse the queue. The task is
   skipped, the next ready one is taken, and the reason goes into the
   queue's `passed_over`. A listed task that fails a check still refuses the
   queue.
4. *The pick is read once, before the confirmation screen.* Each picked entry
   carries `from_ready` and is shown with `[from Ready]`. The passed-over
   reasons are shown under "Passed over from the Ready order". Both are
   saved in the queue file. A resume runs the saved entries and never reads
   the Ready order again. Both new fields have defaults, so a queue file from
   before them loads.
5. *Running is unchanged.* The scheduler, `_next`, `_waits`, the write
   windows and the limit act on the entries' order as before. Only ready
   tasks are picked: a task that becomes ready while the queue runs is left
   for the next queue.

**Consequences.** Ranking the backlog on the board now decides what an
unlisted queue runs. A queue can be planned from the board alone
(`--ready N` without `--task-id`). The Ready order is read once per plan.
The front does not start queues, and its board is unchanged.

**Ruled out.**
- Reordering the operator's list by the Ready order: the list is the
  operator's explicit choice, and it is what C-4 confirms.
- Picking again on resume or while the queue runs: the run would differ from
  the confirmed one.
- Refusing the whole queue for one ready task with a bad file: the operator
  did not list it, and the screen names it.
- Picking tasks whose dependencies merge during the queue: what the screen
  shows would then depend on how the run goes.
## ADR 133 — The read api serves the backlog fields on task rows, and the Ready order at `/api/ready`

**Status:** accepted (T-088, 2026-10-18). Brings `observability/api/app.py`
up to ADR 130 and ADR 131, which added `tags`, `rank`, `debt_id` and the
debt index's `impact`, and served them from `board_service/app.py` only.
Keeps ADR 5 (never-500, a bad file is a warning), ADR 21 (`body` only on the
detail route) and ADR 59 (no card).

**Context.** The front reads the read api, not the board service. After
T-085 and T-086 a task row there still had the eight fields plus `phase`, so
a screen could not show a task's tags, its place in its bucket or the debt
entry it came from, and could not order the Ready column without computing
it a second time. The debt route already served `impact`: it returns
`debt.index_rows` whole, and T-086 added the column there.

**Decision.**
1. *Every task row gains five keys, shaped as `board_list` shapes them.*
   `priority` is `tasks.effective_priority` (`normal` when the file names
   none), `source` and `debt_id` are the file's, `tags` is
   `tasks.normal_tags` ([] for none), and `rank` is the file's. `priority`
   and `source` ride along because a rank means nothing without its bucket
   and `debt_id` belongs with `source: debt`. They are added in `_task`, so
   `/api/tasks` and `/api/tasks/<id>` keep the same keys, the detail route
   adding only `body`.
2. *A bad value is a warning, and the row is still served as written.*
   Both task routes add `"<path>: <problem>"` for each of
   `tasks.field_problems`, the one validator the board service also uses.
   Nothing is mended here: a hand edit's `urgent` is served as `urgent`.
3. *`GET /api/ready` returns the Ready column as task ids, in order.* It is
   `tasks.ready_order` over every task file that reads, the function the
   board service and the queue use, so there is one order. It reads with its
   own loop rather than `read_all_tasks`, because that drops an unreadable
   file without a word. Here such a file is a warning, and so is a bad
   field. It takes no parameters. It is not `/api/tasks/ready`, which would
   be the detail route for a task named `ready`.
4. *`impact` is served as `index_rows` reads it.* A typo or a missing column
   is `none`, with no warning, as `docs/debt/README.md` defines it. A check
   here would be a second parser of the index.

**Consequences.** The change is additive: new keys and one new route, and
no key changed meaning. Exact-dict tests of a task row gained the five keys.
The front's `Task` and `DebtEntry` types do not declare the new keys yet
(nor `phase`), so no screen shows them until the front is changed.

**Ruled out.**
- Serving only `tags`, `rank` and `debt_id`: a rank without its bucket does
  not order anything.
- Serving the cleaned values only (the bucket and rank `ready_order` sorts
  by): the board service serves the file's values beside a warning, and two
  services answering one task two ways would be the bug.
- A `ready` key or a `?ready` filter on `/api/tasks`: it changes the list
  route's envelope, or adds a parameter that orders instead of filtering.
- Warning on an `impact` typo: ADR 131 reads it as `none` by design.
## ADR 134 — An epic closes once every task of it is settled, merged or dropped

**Status:** accepted (T-089, 2026-10-18). Amends the closing rule of
`docs/plans/task-split.md` section 3 and ADR 129 point 9. Keeps the lock
order of ADR 129 and ADR 130: `CREATE_LOCK` before a task's lock, and a
parent's lock never taken under a child's.

**Context.** `tasks.close_epic` closed an epic only when every id in its
`split_into` had a `merged` block, and only `dispatcher.record_merge` ran
it. A task of an epic can be dropped (`backlog_accept` to `dropped`, before
or after the epic is accepted) and can be left deferred when the epic is
accepted (ADR 129 point 9). A dropped task never merges, so its epic stayed
`status: epic` for good. No tool moves a task once it is on the board, so
nothing could close it by hand either.

**Decision.**
1. *Settled is merged or dropped.* `tasks.is_settled` is true for a task
   with a `merged` block or with `status: dropped`. Dropped is final, so a
   dropped task will never hold its epic open again.
2. *A deferred task keeps its epic open.* So do pending, running, blocked,
   proposed, and `done` without a merge. A deferred task is a decision the
   person has not made yet. Closing its epic under it would strand it:
   `accept` takes a task of an epic only while the epic is `status: epic`,
   and `epic_problems` refuses to run one under a closed epic. The person
   settles it by accepting it or by dropping it, and the drop closes the
   epic (point 4).
3. *What the epic becomes.* `status: done` when at least one of its tasks
   merged. `status: dropped` when none did, since nothing of it shipped.
   `dropped` is a final backlog status, so the epic sits with the dropped
   items and is never run. Its lock, body and other fields stay, as before.
4. *Dropping a task of an epic runs the close.* `tasks.accept`, after it
   writes a task of an epic to `dropped`, runs `close_epic` once both its
   locks are let go: the create lock and the task's. `close_epic` then takes
   only the parent's lock, so the order of ADR 129 holds. A drop that is a
   no-op runs it too, so a retry makes a close that a crash cut off. When it
   closes the epic, the result has `epic_closed` with the epic's id. The
   merge paths still run it after `mark_merged`.
5. *Never with an open task.* `close_epic` reads every task of the epic
   first and writes nothing if one is not settled. It reads the parent
   again under its lock and closes only a parent still `status: epic`.

**Consequences.** An epic whose last open task is dropped closes at that
drop. One whose last open task merges closes at the merge, as before, even
when a sibling was dropped. Two triggers that race each write their own
task before they run the check, so the later one sees both. Dropping a
task while its epic is still in the backlog closes nothing, since the epic
is not `status: epic`. `record_merge`'s line still says "every sub-task is
merged", which is loose when a sibling was dropped.

**Ruled out.**
- A deferred task that stops counting: the epic would close with work the
  person meant to come back to, and that work could then never be accepted
  or run.
- `done` for an epic none of whose tasks merged: it would show as shipped
  work that never shipped.
- Leaving an all-dropped epic open: no tool moves a task on the board, so
  it would stay `epic` for good.
- Closing from `_accept_epic`: it moves only epics in the backlog, which
  `close_epic` does not close.
## ADR 135 — The read api serves a task's comments at `/api/tasks/<id>/comments`

**Status:** accepted (T-090, 2026-10-18). Serves step 8 of
`docs/plans/board-service.md` (section 8, the task page's comment thread).
Keeps ADR 5 (never-500, a bad file is a warning), ADR 11 (the warnings cap)
and ADR 54's 404 posture for a read under `/api/tasks/<task_id>`.

**Context.** `tasks.add_comment` appends `{"at", "author", "text"}`, plus
`file`, `line` and `commit` on a diff anchor, to the task's
`comments.jsonl`. The board service only counts those lines (`board_get`'s
`comment_count`), and no route served them, so the console's task page could
not show the thread. No reader in `dispatcher/` parses the file.

**Decision.**
1. *`GET /api/tasks/<task_id>/comments`, no parameters.* A 200 is the
   usual envelope, `{"data": {"task_id", "comments"}, "warnings"}`, with
   the comments oldest first, in file order. The envelope is kept and not
   flattened to a top-level `comments`, because every 200 of this api is
   `{data, warnings}` and the console's client rejects any other 200.
2. *404 as on the detail route.* An id that is not bare, or one with no
   task file, answers 404 in the detail route's words, even when a
   comments file exists. `comments_path` refusing an id is a 404 too.
3. *Absent file, empty thread.* No `comments.jsonl` is `comments: []` with
   no warning: `add_comment` creates it on the first comment. A file that
   will not open is `data: null` with a warning, because an empty thread is
   a different claim.
4. *A bad line is skipped and named.* A line that is not JSON, not an
   object, or without a string `at`, `author` and `text` is skipped with
   `"<path>: line <n>: <problem>, skipped"`. `<n>` is the file's line
   number. Blank lines are skipped without a word. A diff anchor that is
   not all three keys, or not a string `file`, an integer `line` and a
   string `commit`, is dropped with a warning, and the comment is served
   without it.
5. *Only the six known keys cross.* Anything else on a line is dropped.
   Nothing is mended or invented.
6. *Bounded twice.* `MAX_COMMENTS` (500) caps the comments served, keeping
   the newest, with a warning. `MAX_COMMENTS_BYTES` (1 MiB) caps what is
   read: over it, the file's newest end is read from a whole line, and a
   warning says how many bytes were not read. The prefix is still scanned
   in chunks to count its newlines, so line numbers stay the file's.

**Consequences.** One new route and no change to any other. The parser
lives in `observability/api/app.py`, so the board service's count and this
route can disagree on a file with bad lines: the count is every non-blank
line, the thread only the comments that read.

**Ruled out.**
- A top-level `{"task_id", "comments", "warnings"}`: it breaks ADR 5's one
  envelope and the console's `readEnvelope`.
- A comments key on `/api/tasks/<task_id>`: a thread only grows, and ADR 21
  keeps growing records off the routes that are read often.
- A 200 with an empty thread for an id with no task file: it contradicts
  the detail route the page opens first.
- Skipping a comment for a broken anchor: its text is still readable.

## ADR 136 — The console forwards the backlog's writes to the board-service, behind its own CSRF check

**Status:** accepted (T-091, 2026-10-18). Step 8 of
`docs/plans/board-service.md`, sections 4, 6 and 8. Extends ADR 24's
whitelist and ADR 112's second upstream. Reads ADR 133 for `/api/ready` and
ADR 135 for the comments read.

**Context.** The backlog screens need four writes: propose, edit, comment
and accept. The board-service makes them (ADR 129) and listens on
`console_net`. The browser cannot reach it, and must not: it takes no token,
and it refuses any request with an `Origin` header, so a page in a browser
could never call it directly anyway. `lib/api/forward.ts` already forwards
reads to the api and the settings routes to the config writer. Its 405 for
"reads only" stood between the browser and any other write.

**Decision.**
1. *A third upstream, four pairs.* `forward.ts` matches exactly `POST
   /api/backlog`, `PATCH /api/tasks/<id>`, `POST /api/tasks/<id>/comments`
   and `POST /api/tasks/<id>/accept`, before the 405. Every other method on
   those paths keeps its old answer: 405 on an api path, 404 on a path the
   api does not have. The id is decoded and re-encoded as one segment, as on
   the read routes. No query parameter crosses.
2. *Its address.* `BOARD_SERVICE_URL`, default `http://board-service:8792`,
   the compose service on `console_net`, trailing slashes trimmed. No token:
   the board-service has none to check. A 5000 ms timeout.
3. *The CSRF check is the console's.* The session-less console has no
   cookie to protect, but the board-service writes the hive, so a page on
   another site must not be able to make the operator's browser write it.
   The forward refuses, without calling the board-service: a body whose
   content type is not `application/json` (415, which a plain HTML form or
   a CORS-simple request cannot send); a `Sec-Fetch-Site` that is present
   and not `same-origin` (403); an `Origin` that is present and not the
   request's own origin (403). A request with neither header passes: a
   browser sends at least one on a cross-site `POST` or `PATCH`.
4. *Nothing of the browser's goes upstream.* Only `Content-Type:
   application/json` and the body bytes, capped at 64 KiB (413 over it, by
   the declared length or while reading). Not `Origin`, which the
   board-service would refuse, nor a cookie or an `Authorization`.
5. *Its answer passes back unchanged:* status (201 included), body and
   content type. The board-service's results are bare, not the envelope, so
   `client.ts` reads them with a reader that keeps `readEnvelope`'s JSON and
   `{error}` checks and nothing else.
6. *Failures name the service, not its address.* "The board-service could
   not be reached." is a 502 and a timeout is a 504. The address goes to the
   server log only.
7. *The comments path is shared by method.* `GET /api/tasks/<id>/comments`
   is the api's read (ADR 135), forwarded like the usage route: one
   segment, no parameter, no project. `POST` on the same path is the
   board-service's write. `/api/ready` is forwarded with no parameter and
   no project. That makes twelve api routes.
8. *No mock.* `getReady`, `listComments` and the four writes are real
   `fetch`es in every mode, as the settings writes are (ADR 113). Their
   mutations refetch `tasks`, `ready`, and the task's own `task` and
   `comments` queries when they settle.

**Consequences.** The console can write the backlog with no new credential.
The 405's message still says "the console forwards reads only", which is
true of the api's paths it is answered on. A non-browser client on
`console_net` that sends neither header passes the CSRF check, the same as
it could call the board-service directly. Compose needs no change: the
console is already on `console_net`, and the default address is the
service's name there.

**Ruled out.**
- The browser calling the board-service: it refuses every `Origin`, and it
  would have to be published past loopback.
- A token for the board-service: it has none, and the console holding one
  would add a secret without a check behind it.
- A CSRF token: there is no session to bind it to. The content type,
  `Sec-Fetch-Site` and `Origin` checks need no state.
- Forwarding any method on the matched paths: an exact list keeps a new
  board-service route from reaching the browser by accident.
- 503 for an unreachable board-service, as the config writer's: 503 there
  also means "not configured", and the board-service has no such state, so
  an unreachable one is a 502 gateway failure.

## ADR 137 — The api's task rows carry the epic fields, `merged` and a title

**Status:** accepted (T-092, 2026-10-18). Step 8 of
`docs/plans/board-service.md`. Extends ADR 133: the read api and the
board-service answer one task the same way.

**Context.** The console's epic view groups sub-tasks under their epic and
names each row. `_task` in `observability/api/app.py` served neither the
epic fields nor a title, and `board_service/app.py:board_list` already
served `epic`, `merged` and `title` for the same files.

**Decision.**
1. *Four keys on `_task`, so on `/api/tasks` and `/api/tasks/<id>`.*
   `epic` and `split_into` are `TaskFile`'s, served as written: `null` and
   `[]` when absent. `merged` is `bool(task.merged)`, as `board_list`
   serves it. `title` is the board-service's rule.
2. *One title rule.* `_title` moves to `dispatcher/tasks.py:display_title`,
   with its cut `DISPLAY_TITLE_CHARS` (100). It takes the raw frontmatter
   `title` (`TaskFile.title` is that value), so the api needs no second
   frontmatter read. `board_service/app.py:_title` and `TITLE_CHARS` stay as
   thin names for it. Both images copy `dispatcher/`.
3. *A non-list `split_into` is served verbatim, with no warning here.*
   `field_problems` is the backlog fields' check and feeds
   `dispatch_problems`, which the dispatcher refuses a task on; adding to it
   would change the dispatcher. The dispatcher
   names the problem in `tasks.epic_problems`.

**Consequences.** A task row has eighteen keys on the list route. A later
task adds them to the console's `Task` type.

**Ruled out.**
- A copy of `_title` in the api: two rules drift, and ADR 133 asks for one
  answer.
- Serving the `merged` record: what the reader asks is whether it merged.
- An api-only `split_into` warning: the board-service would not give it,
  and the two services would disagree about the same file.

## ADR 138 — The console's Backlog screen reads `/api/tasks` and writes through the board-service

**Status:** accepted (T-093, 2026-10-18). Step 8 of
`docs/plans/board-service.md`. Builds on ADR 136 (the forward) and ADR 137
(the epic fields on a task row). Replaces the Backlog screen's mock.

**Context.** `front/src/routes/backlog.tsx` read a fixture list with a
completion toggle that no service had. The board-service now takes the
backlog's verdicts (`POST /api/tasks/<id>/accept`) and rank edits
(`PATCH /api/tasks/<id>`), and the api serves every field the screen needs.

**Decision.**
1. *One read.* The screen reads `tasksQuery` (`/api/tasks`) and keeps the
   `proposed`, `deferred` and `dropped` rows. No backlog-only route: the
   board and the backlog are the same files.
2. *The board's order, copied.* `front/src/lib/backlog.ts:compareTasks` is
   `dispatcher/tasks.py:order_key`: bucket (`high`, `normal`, `low`; any
   other value is `normal`), then rank (a missing or bad one is 0, so an
   unranked item sorts *first* in its bucket), then id in number order.
3. *Epics group their tasks.* A backlog item whose `epic` names another task
   is drawn under it. The head is the epic's own row when it is in the
   backlog, or a header row when the epic is on the board (`status: epic`)
   or was not served.
4. *Verdicts.* Accept sends `{to: "pending", priority}` with the row's
   priority select (default: its bucket), defer `{to: "deferred"}` (on a
   proposed item only), drop `{to: "dropped"}` after an inline confirm. A
   dropped item shows `final` and no action. A task of an epic cannot be
   accepted until its epic has `status: epic`, which is the board-service's
   own refusal, shown before the click. No completion action: an item is
   finished on the board, not here.
5. *Reorder writes `rank`.* Up and down move a top-level, non-dropped row
   past the next visible row of its bucket. The rank is computed over the
   whole bucket, rows hidden by a filter included: the midpoint between the
   new neighbours when they leave room, else the bucket is renumbered
   `RANK_STEP` (1024) apart and only the changed ranks are written, one
   `PATCH` after another. A reorder never crosses buckets; that is a
   priority change.
6. *Filters.* Status (dropped unticked by default), tag (`debt` is one) and
   source (`none` for a task that records none). The options are the values
   the backlog holds. A debt item's `debt_id` links to `/debt`.
7. *Errors in a toast.* A refusal's message (`ApiError`, ADR 136) goes to
   `toast.error`; a success names the task and the verdict.
8. *The mock goes.* `listBacklog`, `setBacklogCompleted`, `backlogQuery`,
   `BacklogItem` and its fixture are deleted. `actionBackendQuery` stays:
   other screens read it.

**Consequences.** The Backlog is the seventh screen on the real api. The
front `Task` type carries `epic`, `split_into` (as `unknown`, narrowed by
`splitInto`), `merged` and `title`.

**Ruled out.**
- A drag-and-drop library: a new dependency for what two buttons do.
- Renumbering on every move: one write is enough while there is room, and
  every write is a file change in the hive.
- Moving dropped rows: dropped is final, and its rank decides nothing.
- Parallel rank writes: a refusal mid-renumber would leave an order nobody
  chose; in sequence it stops at the first refusal.

## ADR 139 — The task page shows the description's sections, the comments, the phase and the epic view

**Status:** accepted (T-094, 2026-10-18). Step 8 of
`docs/plans/board-service.md`. Builds on ADR 136 (the forward), ADR 137 (the
epic fields) and ADR 138 (`splitInto`, `isEpic`). No new route.

**Context.** `front/src/routes/tasks.$taskId.tsx` showed the description as
one block of text in the header and no comments, phase or epic.

**Decision.**
1. *The description's sections.* `front/src/lib/description.ts:parseDescription`
   mirrors `parse_description`: the same five headers, a duplicate or text
   before the first section refused, a blank required section missing, the
   dispatcher's wording for each reason. Parsed, each section is a heading
   over `Markdown`, in the dataclass order; unparsed, the raw text is shown
   as written with the reason above it.
2. *Comments.* The thread reads `commentsQuery` and shows author, time,
   text and the `file:line@commit` anchor when all three are set. The form
   posts `{text}` only: the board-service sets the author. It refuses a blank
   text and one over `MAX_COMMENT_CHARS` (20,000) before the post, counting
   code points as the dispatcher does; a refusal from the service goes to
   `toast.error`.
3. *The header* carries the title and, when `epic` is set, a link to the
   epic. The description moved to its own region.
4. *Phase.* A field shows the running phase, flagged `stale` when the api
   serves `lock_expired: true`, and "between phases" when none is set.
5. *The epic view.* On a task with `isEpic(t)` or `status: epic`, the page
   lists the children in `splitInto` order from one `tasksQuery` read, no
   read per child: title, status, merged or not, and "after T-x" for each
   `depends_on`. A child the list does not hold is shown as missing.
   Progress is "n of m merged", m being the children less the dropped ones;
   the dropped and the missing are counted apart, and a missing child stays
   in m, since nothing says it settled.

**Consequences.** The task detail carries everything step 8 asked of it; the
screen count does not change.

**Ruled out.**
- A separate epic route: `routeTree.gen.ts` is generated, and the task page
  already reads every field an epic has.
- Reading each child's `/api/tasks/<id>`: one list read holds every row.
- An author field in the form: the service, not the browser, says who wrote.

## ADR 140 — The board: Ready in the dispatcher's order, role lanes from the phase, one swimlane per epic

**Status:** accepted (T-095, 2026-10-18). Step 8 of
`docs/plans/board-service.md`, the Board row. Builds on ADR 126 (the phase on
the lock), ADR 133 (`/api/ready`), ADR 137 (the epic fields) and ADR 139 (the
epic progress rule). Replaces ADR 26's five status columns and ADR 28's
banner. No new route.

**Context.** `front/src/routes/index.tsx` drew the five statuses as columns,
backlog and epic rows included, with In progress as one column and a banner
saying the role lanes had no record behind them. ADR 126 put the running
phase on the lock and ADR 133 served the Ready order, so both now exist.

**Decision.**
1. *Board items only.* A backlog status (`proposed`, `deferred`, `dropped`)
   is not drawn, and an epic row (`status: epic` or `isEpic`) heads a
   swimlane instead of being a card.
2. *Placement* is `front/src/lib/board.ts:columnOf`, first match wins:
   `merged: true` is Merged; `pending` is Ready when `/api/ready` names it,
   else Waiting; `in_progress` with a phase in `ROLES` and a lock not expired
   is that role's lane; any other `in_progress` (no phase, an expired lock, a
   phase no role names) is "No live phase", with a chip saying "between
   phases", "stale <role>" or the phase; `blocked` and `conflict` are their
   own columns; anything else (`done` not merged, `drafted`, an unknown word)
   is Other, shown only when used, with the status on the card. A status the
   dispatcher does not write is also a warning.
3. *Awaiting review* is a column with no status behind it
   (`docs/plans/review.md` is unbuilt), so it is always empty and says "not
   built". There is no integrador lane: neither the dispatcher nor `ROLES`
   has that role.
4. *A failed ready read is not Waiting.* An error, or the api's envelope with
   a "no ready order" warning, folds Ready and Waiting into one "Pending,
   ready unknown" column, with a broken-read line.
5. *Order.* Ready keeps `/api/ready`'s order; every other column, and the
   epic lanes, use `compareTasks`. The no-epic lane is last.
6. *Lanes.* A card's lane is its `epic` field, else the epic whose
   `split_into` lists it. An accepted epic keeps its lane when empty; an
   epic the read did not serve still gets a lane, its head named as missing.
   The header links to the epic and shows "n of m merged" by ADR 139's rule.
7. *Reads.* One `tasksQuery` and one `readyQuery`, beside the accounts and
   debt reads the board already had. A client-side column filter, which
   covers the roles, joins the search and the account filter.

**Consequences.** The banner is gone: the lanes it said could not exist now
do. A card shows the title, falling back to the description.

**Ruled out.**
- Computing Ready in the browser from `depends_on`: the dispatcher's order
  is served, and a second copy could disagree with it.
- Treating a failed ready read as an empty one: every pending task would
  look blocked on a dependency.
- A *Start development* button: that is step 9.

## ADR 141 — The Debt screen pairs each open entry with its backlog item

**Status:** accepted (T-096, 2026-10-18). Step 8 of
`docs/plans/board-service.md`, the Debt row. Builds on ADR 131 (`source:
debt` and `debt_id`) and ADR 17 (the open/resolved partition). No new route.

**Context.** `/debt` showed the index alone. Since ADR 131 the dispatcher files
each open entry as a backlog item carrying its `debt_id`, so the screen can say
what became of it.

**Decision.**
1. *The pairing is the dispatcher's.* `front/src/lib/debt.ts` matches on
   `debt_id` alone, as `dispatcher/tasks.py:find_debt_item` does, over one
   `tasksQuery` read.
2. *States.* No item is "not imported"; `proposed`, `deferred` or `dropped`
   is "not accepted"; any board status is "accepted", naming it, with
   `merged: true` shown as merged; a status the dispatcher does not write is
   named verbatim, not counted either way. Each item links to
   `/tasks/$taskId`, as the Backlog screen does.
3. *Two items with one `debt_id`* are both drawn, under a warning. The
   dispatcher takes the first; the screen does not pick.
4. *Open entries only.* Only the Open partition gets the item column;
   resolved rows are unchanged.
5. *A failed tasks read* is a broken-read line, and each item cell says
   "pairing unavailable". The index still renders.

**Consequences.** The screen still writes nothing. Accepting, deferring and
dropping stay on the Backlog screen.

**Ruled out.**
- Matching on `source: debt` too: the dispatcher does not, and a hand-edited
  source would hide a pairing it keeps.
- Showing "not imported" while the tasks read is down: that is a claim about
  items the read never saw.

## ADR 142 — The action queue is files under `<state_dir>/actions/`, written by the board service and drained by one `worker` service that alone holds the socket

**Status:** accepted (T-097, 2026-10-19). A design: it builds nothing, and
the tasks under *The split* build it. Settles "a queue and a worker", which
`docs/plans/server-deployment.md` (*Decisions*, 5, and step 3 of *The order
across the plans*), `docs/plans/board-service.md` (*Starting development*,
step 9), `docs/plans/front.md` (*Tier 3*), `docs/plans/main-thread.md`
(*Decisions*, 4), `docs/plans/command-requests.md` (step 4, *Amended*) and
`docs/plans/projects.md` (*Decisions*, 2 and 4) each name and none shapes.
Reads ADR 75 (no database), ADR 79 and ADR 83 (`dispatcher/queue.py`),
ADR 119 and ADR 120 (the verdict function), ADR 124 (the board service, and
its *Ruled out*: no queue-and-worker before a write needs one; starting a
cycle from the console is that write) and ADR 136 (the console's forward).

**Context.** The console must start things that need the docker socket: a
task's cycle, its cancel, a command request's verdict, a main-thread turn,
and later the account and project actions. Charter C-8 and `board.md`
(*Actions go through a queue, not a socket*) put the socket out of every web
process: the api stays `GET`-only and the board service has none (ADR 124).
C-4 lets nothing spend quota that a person did not authorize, and C-12 makes
*Start development* and a command-request verdict such authorizations. Two
facts of the stack shape the answer. Every agent container mounts `.hive`
read-write (`docker/compose/docker-compose.agents.yml`), so a file there is
one an agent can write. And no agent mounts `dispatcher_state`, which the api
already mounts read-only and the `dispatcher` service read-write.

**Decision.**

1. *The worker is a compose service of its own, `worker`.* Built from
   `docker/dispatcher/Dockerfile`, running a new verb, `dispatch worker`,
   with `restart: unless-stopped`, behind a `worker` profile so a stack runs
   without it until the operator opts in. Its mounts are the `dispatcher`
   service's (the socket, `dispatcher_state`, `.hive`, `claude_shared`, the
   project keys, `config.yaml` and `config.d`), its network is
   `ia_harness_net` only, and it publishes no port: nothing calls it, it
   reads files. It is the one long-running service with
   `/var/run/docker.sock`; the `dispatcher` profile service stays the
   operator's one-shot CLI. The api, the board service, the console and
   the config writer never get the socket.
2. *One worker at a time.* `dispatch worker` takes a non-blocking `flock` on
   `<state_dir>/actions/worker.lock` and exits 2, naming the lock, when it
   is held. The kernel drops the lock with the process, so a crash never
   wedges the queue (the reason T-008-D1 chose `flock` over `O_EXCL`). Each
   loop it rewrites `<state_dir>/actions/worker.json`, `{pid, started_at,
   beat_at}`, so a reader can say that no worker is running.
3. *The queue is files under `<state_dir>/actions/`, not the hive.* Four
   directories: `tmp/`, `pending/`, `claimed/` and `done/`, one JSON file
   per action named `<id>.json`. Not the hive, because an agent can write
   there, and a file an agent can write must never start a run (C-4).
   - *Who writes.* The board service creates an action: it writes the
     record to `tmp/` and renames it into `pending/`. That is all it ever
     does to the queue. Every later move and rewrite is the worker's:
     `pending/` → `claimed/` by `os.rename` (the claim), a rewrite of a
     record in place by write-and-replace, and `claimed/` → `done/` when it
     ends. One creator and one mover per file, so no two processes race
     for one rename.
   - *Mounts.* The board service gets one new mount,
     `dispatcher_state/actions` read-write at `<state_dir>/actions`, and
     nothing else of the state dir: not the account files, the usage log
     or the allowlists, which ADR 121 keeps out of every agent's reach and
     which a service agents can call must not hold either. The api reads
     the queue through its existing `/state:ro`.
   - *The directory is the state's authority.* A record in `pending/` is
     waiting, in `claimed/` is running, in `done/` is finished; its
     `status` names how it finished.
4. *`dispatcher/queue.py` stays separate, and lends parts.* ADR 79's queue
   is a list the owner confirmed in one yes, under C-4 *Amended*, with an
   order, a limit and a resume, kept in one file the CLI process rewrites
   whole. An action is one request from the console, under C-12, written by
   another process. Wrapping a start as a one-task queue would bring the
   yes prompt, the topological order and `--resume` along with it, and a
   second process writing `queues/<id>.json` would race the first. What is
   shared is code: the worker's `start` calls `run_task_cycle` with
   `authorized=True` and a `may_start_phase` hook (ADR 79, 3), asks whether
   an account may take a phase with the check `queue.py` uses
   (`_account_open`, made public when built), and step 9's limit counts
   spend with `queue.measured_tokens`.
5. *The record.* One JSON object, `version: 1`:
   - `id`: `a-<UTC %Y%m%dT%H%M%S%f>Z-<4 hex>`, so name order is the order
     taken;
   - `kind`; `project` (a slug, or null for an account kind); `task_id`
     (or null); `params`, the kind's own fields (below);
   - `actor`: who asked. `operator` until sign-in exists
     (`server-deployment.md`, *Decisions*, 1), then the user's name;
   - `note`: free text or null, `start` only, no longer than a comment;
   - `created_at`, `claimed_at`, `started_at`, `finished_at`: UTC ISO
     strings or null. `started_at` is written, by replace, just before the
     first call that can spend quota or change state outside the queue;
   - `status`: `pending`, `running`, `done`, `failed`, `cancelled` or
     `interrupted`; `waiting`: null, `project`, `thread` or `account`, set
     by the worker on a pending record it passed over;
   - `result`: the kind's outcome on `done` (for `start` and
     `command_verdict`, the `CycleOutcome`'s status and blocked reason);
     `error`: one line on `failed` and `interrupted`, else null.
6. *The kinds.* In scope now:
   - `start` — `task_id`, `project`; `params.limit` reserved as null until
     board-service step 9 sets it;
   - `cancel` — `params.target`, the action id it cancels;
   - `command_verdict` — `task_id`, `project`; `params` `n`, `verdict`
     (`run`, `deny` or `allow`), `comment`, `prefix`;
   - `turn` — `params.thread`, `params.text`.

   Named for later, not built by the split below: `account_add`,
   `account_login`, `account_pause`, `account_remove`,
   `account_delete_volume`, `project_clone`, `project_init`,
   `project_keygen`, `project_check`. The board service refuses an unknown
   kind with 400, and the worker moves one it finds to `done/` as `failed`,
   "unknown kind", never skipping it in silence.

   *How a kind is added.* An entry in one registry in
   a new actions module in the dispatcher package, `KINDS`, giving the kind's `params`
   validation (run by the board service on write and by the worker on
   claim), its handler, its serial key (decision 8), whether it spends
   quota, and what recovery does with it (decision 7); a board-service
   route that creates it; and tests of all three. Every new kind comes
   with its own ADR, and one that spends quota or reaches a credential
   names the charter entry that authorizes it.
7. *Claiming and crash recovery.*
   - *The loop.* Every 2 seconds the worker lists `pending/` in name order.
     A `cancel` is run at once. Any other kind is claimed only when its
     serial key is free and, for `start`, an account may take a phase;
     otherwise the worker sets `waiting` and passes on to the next, so a
     busy project does not hold up another project's start. A claim is the
     rename, then a rewrite with `status: running` and `claimed_at`, then a
     thread running the handler, as `run-queue --parallel` runs cycles in
     threads (ADR 83). The handler's end rewrites the record and renames
     it to `done/`.
   - *Before the first loop*, the worker settles every record left in
     `claimed/`, which only a dead worker leaves, by its kind's rule:
     - `start` with no `started_at` goes back to `pending/`: its cycle never
       opened, so nothing was spent. With `started_at` it is `interrupted`
       and never run again (ADR 79, 5): running it again is a new *Start*
       by a person (C-4). The dead cycle's task lock is the reaper's
       (ADR 126).
     - `command_verdict` is `interrupted`: ADR 119 records the verdict
       before the command runs, so the function would refuse a second
       call. The error names `dispatch command-resume` (ADR 120).
     - `turn` is `interrupted`; the thread's log shows the turn cut off.
     - `cancel` is run again; it is idempotent.
     - A later kind declares its own rule. One that spends quota, or that
       makes or deletes a key, a volume or a container, is never run again.
   - *Idempotency at the door.* The board service holds a `flock` on
     `<state_dir>/actions/write.lock` while it checks and creates, and
     refuses with 409 a `start` for a task that already has a `start` in
     `pending/` or `claimed/`, a `command_verdict` for a task that already
     has one there, and a `start` for a task not in *Ready* (status
     `pending`, every dependency merged, not a backlog item). The worker
     checks again on claim, and `open_cycle` and ADR 119's one verdict per
     block are the last word.
   - *Cancel.* A `cancel` whose target is in `pending/` moves the target to
     `done/` as `cancelled`: nothing of it ran. On a running `start` it
     makes the cycle's `may_start_phase` answer no: the phase that is
     running finishes, the cycle is held at the next phase boundary as the
     limit holds it (ADR 79, 3), and the start ends `cancelled`, its result
     naming the last phase that ran. Going on is a new *Start*. On a running
     `turn` it kills the exec (`main-thread.md`, *Decisions*, 4). On a
     running `command_verdict`, or a target already in `done/`, the cancel
     ends `failed` and changes nothing. This is *the dispatcher's own stop*
     that `board-service.md` names for a start whose phase has begun,
     reached through the queue.
8. *Rules the worker keeps.*
   - *One cycle per project.* `start` and `command_verdict` share the serial
     key `project:<slug>`, since a verdict resumes a cycle; `turn` has
     `thread:<id>`; the account and project kinds will take
     `account:<name>` and `project:<slug>`. At most one claimed action per
     key, any number of keys at once, so two projects run side by side
     (`projects.md`, *Decisions*, 5).
   - *The note.* When the worker takes a `start` with a `note`, it writes
     it once as a comment by the action's `actor` (`operator` today) in the
     task's `comments.jsonl` through `dispatcher/tasks.py`, records
     `noted_at` in `result`, and only then sets `started_at`; a start sent
     back to `pending/` by recovery is not commented twice.
   - *Verdicts.* `command_verdict` calls
     `command_run.run_command_request(cfg, task_id, project, n, verdict,
     comment, by=actor, prefix=prefix)`, the function T-074 built for this
     worker, and a `VerdictRefused` ends the action `failed` with its
     reason. The verdict is the owner's C-12 act; the worker carries it.
   - *Only a person's act fills the queue.* The board service creates
     actions from its console routes alone, never from an MCP tool and never
     for a role. Because it also listens on `ia_harness_net`, where the
     agents are (ADR 124), its action routes require a bearer,
     `BOARD_ACTIONS_TOKEN`, that only the console holds and adds in its
     forward; without the variable they answer 503 and the rest of the
     service runs. The worker creates no action, retries none and starts
     nothing on a timer (C-4).
   - *The spending limit* is board-service step 9's to decide. This ADR
     only reserves `params.limit` and names the `may_start_phase` hook as
     where it is checked.

**The split.** In build order, one surface each. `after` names what must be
merged first.

1. The actions module (new), the contract. The record, the id, `KINDS` with
   the four kinds' validation, `create` (tmp then rename, under
   `write.lock`, with the refusals of decision 7), `read_all` (tolerant: a
   file that does not parse is a warning, not an error), and the worker's
   primitives `claim`, `rewrite`, `finish` and `recover`. Tests: a create
   is atomic and lands in `pending/`; ids sort in creation order; a claim
   succeeds once; each refusal; each kind's recovery rule; an unknown kind.
2. The worker: `dispatch worker`, the lock, the heartbeat, the loop, the
   serial keys and `waiting`, and the `start` and `cancel` handlers. After
   1. Tests, with `run_task_cycle` faked: a second worker exits 2; one
   claimed start per project and two projects at once; a start waits for
   an account; the note is commented once; a cancel on pending and on
   running; a start interrupted before and after `started_at`.
3. The board service's routes: `POST /api/tasks/<id>/start` (body
   `project`, `note`) and `POST /api/actions/<id>/cancel`, behind
   `BOARD_ACTIONS_TOKEN`, answering 201 with the record. After 1. Tests:
   the token, 503 without it, each 409, the record written.
4. The read api: `GET /api/actions`, the pending records in take order with
   `waiting`, the claimed ones, the newest `done/` ones up to a limit, and
   the worker's heartbeat with a `stale` flag, in the `{data, warnings}`
   envelope. After 1. Tests: each state, a bad file as a warning, no
   heartbeat file.
5. Compose: the `worker` service of decision 1, the board service's
   `actions` mount, `BOARD_ACTIONS_TOKEN` for the board service and the
   console, and the README and `scripts/configure.sh` lines. A change to
   `docker/compose/` needs the user's approval. After 2 and 3. Tests: the
   compose invariants (the socket in `worker` and `dispatcher` only; the
   board service mounts `actions`, not `/state`).
6. The console: the forward of the two action routes with the token, and
   *Start development* on the Board's *Ready* cards with the note box.
   What the confirmation shows and the limit are board-service step 9's,
   decided in that task. After 3 and 4. Tests: the forward's exact
   pairs and token; the button hidden for a queued or running task.
7. The console's `/queue` screen over `GET /api/actions`, with *Cancel* on
   each pending or running start and a line when the worker's heartbeat is
   stale. After 4 and 6. Tests: each state, empty, stale, a failed read.
8. The worker's `command_verdict` handler. After 2. Tests: the call with
   the actor as `by`, a refusal as `failed`, recovery as `interrupted`.
9. The board service's verdict route, `POST
   /api/tasks/<id>/command-requests/<n>/verdict`. After 3 and 8. The
   buttons in Approvals are `command-requests.md` step 6's.
10. The worker's `turn` handler, after 2, once `main-thread.md`'s composer
    has its charter ruling; its route and screen are that plan's steps.

**Consequences.**
- The console can start a cycle with no socket in any web process, and the
  queue is legible on disk: `ls` shows what waits, what runs and what ended.
- A start reaches the worker within about two seconds. A signal from the
  board service to the worker is its own ADR, when that is too slow.
- The worker's project rule sees only the actions it claimed. A cycle the
  operator starts by hand with `run-task` or `run-queue` on the same
  project is invisible to it until task cards carry `project`
  (`projects.md`, *Decisions*, 3).
- `done/` grows by one file per action. Pruning it is a task of its own
  when its size asks for one.
- Nothing runs while the `worker` profile is off; the heartbeat lets the
  `/queue` screen say so rather than show a queue that never moves.

**Ruled out.**
- The queue in the hive: every agent mounts it read-write, and an agent's
  file would start a run no person authorized (C-4).
- A database or SQLite table: ADR 75 keeps the console's writes in files,
  the api already reads files, and a rename is an atomic claim.
- Storing actions in `dispatcher/queue.py`'s `queues/<id>.json`, or making a
  start a one-task queue: decision 4.
- The socket in the board service, or actions run inline in it or in the
  api: C-8, and ADR 124.
- The board service mounting the whole state dir: it would reach the
  allowlists and the account files.
- The board service moving `pending/` files itself to cancel: two movers
  of one file race; the worker is the only mover.
- Several workers with leases: a lease expires under a cycle of hours. One
  worker and a `flock` that dies with it.
- A process per action: the worker's lock already says which claimed
  records are orphans, and threads are how ADR 83 runs cycles.
- Re-running any action that may have spent quota after a crash: C-4.
- An MCP tool that enqueues: `main-thread.md` step 6, under its own ADR.

## ADR 148 — The worker keeps its handlers, runs cancels first, and ends a start the shutdown held as `interrupted`

**Status:** accepted (T-100, 2026-10-19). Narrows ADR 142 where it left
room for step 2 of its split (`dispatcher/worker.py`); it supersedes
nothing.

**Context.** ADR 142 decision 6 says the `KINDS` registry gives each kind's
handler, but `dispatcher/actions.py` is the module the board service will
import, and a handler that runs a cycle needs the whole dispatcher core
behind it. Decision 7 says a `cancel` "is run at once", while the loop
lists `pending/` in name order, and a cancel is always named after its
target. And the worker's stop on SIGTERM is "let running handlers reach a
boundary", which ADR 142 does not name a status for.

**Decision.**

1. *The handlers live in the worker.* `dispatcher/worker.py` holds
   `HANDLERS`, kind name to handler, for `start` and `cancel`.
   `Kind.handler` in `KINDS` stays `None`: `actions.py` imports nothing of
   the dispatcher core, so the board service can import it without the
   socket's code. A kind with no entry in `HANDLERS` (`command_verdict`
   and `turn` today) is left in `pending/` untouched, its `waiting` unset.
2. *Cancels first.* Each pass handles every pending `cancel` before it
   claims anything else, so a cancel sent for a pending start finds it
   still pending and nothing of it runs, even when both are new in the
   same pass. A cancel runs on the loop's own thread: it only moves files
   and sets a flag.
3. *A cancel is idempotent on a rerun.* A target already in `done/` as
   `cancelled` by this cancel's id (`result.cancelled_by`) ends the cancel
   `done`; any other finished target ends it `failed`.
4. *Shutdown holds, then interrupts.* On SIGTERM or SIGINT the worker stops
   claiming and every running start's `may_start_phase` answers no, so the
   phase in flight finishes and the cycle is held before the next one. A
   start held that way ends `interrupted`, never `pending`: it has
   `started_at`, and going on is a new *Start* by a person (C-4, ADR 142
   decision 7). How long the stop waits is the compose service's
   `stop_grace_period`, split item 5's to set.
5. *The account check is paced.* `queue.account_open` can run a `/usage`
   probe on every parked account. The worker asks it only for a start whose
   serial key is free, and after a closed answer does not ask again for
   `queue.WAIT_POLL_SECONDS`; the start waits with `waiting: account`
   meanwhile.

**Consequences.**
- A kind's handler is added in two places, `KINDS` for its contract and
  `HANDLERS` for its run; the empty `Kind.handler` slot is dead until a
  later task removes it or this ADR is superseded.
- A start that waits for an account may wait up to five minutes after one
  frees.
- Stopping the worker mid-cycle costs the rest of that cycle a new *Start*.

**Ruled out.**
- Filling `Kind.handler` at import of `worker.py`: it mutates another
  module's frozen registry, and whether it is set would depend on import
  order.
- Sending a held start back to `pending/` on shutdown: it may have spent,
  and a restart would run it again with no person's act (C-4).

## ADR 151 — The action routes: the project is the body's, Ready is read across the one hive, and the token is checked before the body

**Status:** accepted (T-102, 2026-10-19). Narrows ADR 142 where it left
room for step 3 of its split (`board_service/app.py`); it supersedes
nothing.

**Context.** ADR 142 names the two routes, their body (`project`, `note`),
the bearer `BOARD_ACTIONS_TOKEN` and the 503 without it. It does not say
which Ready a start is checked against when task cards carry no `project`
(its *Consequences*, and `docs/plans/projects.md` *Decisions*, 3), what a
cancel's body is, when the token is read, or in which order the guards
answer. Split item 6, the console's forward, is built against whatever
this task answers.

**Decision.**

1. *The project is the body's.* A start's body is a JSON object with
   `project` (required, the slug rule of `actions.validate`) and `note`
   (optional, text or null), and no other key: anything else is 400. The
   service's config names no project, so there is nothing to default to.
   `params` is not taken from the body; `actions.create` writes
   `{"limit": null}` until board-service step 9 sets it.
2. *Ready is the one hive's.* The `ready(project, task_id)` the route
   passes to `actions.create` is `task_id in
   tasks.ready_order(tasks.read_all_tasks(hive_dir))`, over the service's
   own `hive_tasks_dir`; `project` is not read, because no task card
   carries one. A task id the hive has no file for is not in Ready, so
   it is 409 like any other. The slug is not checked against
   `projects_root` either: the board service does not mount the
   projects, and a wrong slug fails at the worker's `open_cycle`.
3. *A cancel's body is `{}`.* The target is the path's id, checked by
   `actions.validate` (400 when it is not an action id). The route does
   not look for the target on disk: the worker's cancel names a missing
   or finished one in its `failed` line (ADR 148, 3), and the board
   service stays a creator that reads nothing of the queue but its lock.
4. *The guards answer in this order:* an `Origin` is 403; then the token
   (unset or empty 503, a missing, non-Bearer or wrong bearer 401 with
   `WWW-Authenticate: Bearer`); then a query string 400; then a body that
   is not a JSON object 400; then `ActionRefused`, its own status. So a
   caller without the token learns nothing about the body it sent.
5. *The token is read once,* from the environment when `create_app`
   builds the app, as the config is; changing it is a restart. The
   comparison is `hmac.compare_digest` on encoded bytes, with an empty
   presented value refused before it, in the service's own module:
   `observability/auth.py` is not in the board service's image. The token
   is never logged or put in an answer.
6. *The queue directories are made on the first create,* by
   `actions.create`, never at app build: a service whose state mount is
   absent (step 5 not yet built) still starts, and its other routes run.

**Consequences.**
- A start for a task of another project's hive is refused 409 until task
  cards carry `project`; there is one hive today, so none exists.
- The console's forward (split item 6) sends `{"project", "note"}` and
  `{}`, with the bearer, and reads 201 with the record or `{"error"}`.
- Rotating `BOARD_ACTIONS_TOKEN` restarts the board service.

**Ruled out.**
- The project from config: none is named there, and `projects.md`'s
  record does not exist yet.
- A 404 for a cancel whose target is not on disk: it reads `claimed/` and
  `done/`, which ADR 142 gives the board service no reason to read, and
  races the worker's moves anyway.
- Importing `observability/auth.py`: a new `COPY` in the board service's
  image for one comparison.

## ADR 157 — The console forwards the two action routes with the bearer, and the start's project is the console's

**Status:** accepted (T-105-S1, 2026-10-19). Builds ADR 142 split item 6
on ADR 151's contract; narrows ADR 151 *Consequences* for the console's
side. Supersedes nothing.

**Context.** ADR 151 makes a start's `project` the body's, required, with
no default on the board service. The console's forward (ADR 136) stands
between a browser and the board service; ADR 24 already rules that
`?project=` on the api's reads is the console's `CONSOLE_PROJECT` and never
the browser's. The two action routes are also the first board writes that
carry a credential, `BOARD_ACTIONS_TOKEN`.

**Decision.**

1. *The project is the console's.* `forward.ts` writes the upstream start
   body as `{"project": CONSOLE_PROJECT, "note": <note or null>}`. The
   browser's body must be a JSON object whose only key is `note` (a
   string, null, or absent); any other key, `project` included, or a body
   that is not a JSON object, is the console's 400 and nothing is sent.
   With `CONSOLE_PROJECT` unset a start is a 503 naming it.
2. *A cancel sends `{}`* whatever the browser's body held, after the same
   checks.
3. *The token is the console's server half's,* `BOARD_ACTIONS_TOKEN` in
   `server-env.ts`, read like `CONFIG_WRITER_TOKEN` (empty or whitespace is
   unset), never a refusal to boot, never in an answer or a log. Unset, the
   two routes answer a 503 naming it and nothing is sent.
4. *Order:* the route match (undecodable id 404), then `forwardBoard`'s
   CSRF checks (415, 403, 403), then the token 503, then (start only) the
   project 503, then the 64 KiB bound (413), then the body's shape (400).
   Upstream headers are `Content-Type: application/json` and
   `Authorization: Bearer <token>` only.
5. `GET /api/actions` joins `FORWARDED` with `limit` copied and no
   `?project=`: the queue is the hive's, not a project's (ADR 142).

**Consequences.**
- A browser cannot start a task of another project through the console
  until `docs/plans/projects.md` gives the console more than one; that
  change re-opens part 1 rather than inheriting it.
- A console without `CONSOLE_PROJECT` keeps every read and loses only the
  start.

**Ruled out.**
- Passing the browser's `project` through: the console would then hand a
  page the choice ADR 24 keeps on the server.
- A refusal to boot without the token: every other screen works without it,
  as ADR 112 ruled for the writer's token.

## ADR 160 — The Ready card's *Start development*: one new action-queue read, a start known by kind and task id, and a button outside the card's link

**Status:** accepted (T-105-S2, 2026-10-19). Builds the board half of ADR
142 step 9 on ADR 157's forward and the api's `GET /api/actions` (ADR 142
step 4). Supersedes nothing.

**Context.** `docs/plans/board-service.md` *Starting development: a button
on the board* puts *Start development* on each Ready card and hides it for a
task already queued or running. The console already has a `/queue` draft
(`queue.tsx`) over a mock `listActions` whose `QueuedAction` shape is not the
api's, keyed `["actions"]`. `TaskCard` is one `<Link>`, so a button inside it
would navigate, and the card is mounted in tests with no `QueryClient`. The
queue record carries `project`, but the browser never learns
`CONSOLE_PROJECT` (ADR 24, ADR 157).

**Decision.**

1. *A second, real reader of the queue.* `getActionQueue()` reads
   `/api/actions?limit=1` through `readEnvelope` into `ActionQueue`
   (`pending`, `claimed`, `done`, `done_total`, `worker`), each record an
   `ActionRecord` as `dispatcher/actions.py:create` writes it. Its query key
   is `["action-queue"]`, not `["actions"]`: the draft's key holds a
   different shape, and one key with two `queryFn`s would hand either
   screen the other's rows. `listActions`, `actionsQuery`, `enqueueAction`
   and `QueuedAction` stay as they are until the Queue screen is built.
2. *A start is matched by `kind` and `task_id` only.* A record with
   `kind === "start"` and the card's `task_id` in `pending` makes the card
   *Queued*; in `claimed`, *Running*. `project` is not compared: the browser
   has no project to compare it with, and there is one hive (ADR 151, 2).
   `done` is not read: a finished start leaves the queue, and the card's
   column then says where the task is.
3. *The card's states* (Ready column only, never Waiting nor the `pending`
   column of a failed ready read):
   - actions read loading: no button and no label;
   - a pending start: *Queued*, plus *waiting for an account* or *waiting
     for the project* when `waiting` is `account` or `project`, another
     non-null value printed as served; no button;
   - a claimed start: *Running*; no button;
   - neither: the button;
   - actions read failed: the button, and one `BrokenBanner` line saying the
     queued state cannot be shown and that the board-service refuses a
     duplicate start (409), so a second press starts nothing twice.
   The api's `warnings` on the read join the screen's `WarningBanner`.
   Nothing about the worker's heartbeat, a cost, a pool or a limit is shown.
4. *The button is outside the link.* `TaskCard` stays presentational and
   takes an optional `footer` node, drawn after the `<Link>` inside one
   bordered wrapper. The start control is its own component, mounted by
   `BoardPage` into that slot, so the card's tests need no query client.
5. *The confirmation is `components/ui/dialog.tsx`,* not `alert-dialog.tsx`:
   an alert dialog's action closes it, and a refusal must leave it open with
   the note kept. It names the task, says that confirming queues its cycle,
   and holds one optional note. A blank or whitespace note is sent as
   `null` and is never a problem; any other note is checked by
   `commentProblem`, and Start is disabled while it has one or while the
   write is in flight. Success is `toast.success` and closes the dialog;
   a refusal is `toast.error` with the served message, as Backlog's.
6. *After the write,* settled or refused, `["action-queue"]` and `["tasks"]`
   are invalidated, so the card shows *Queued* from the queue itself and
   not from the 201's record.

**Consequences.**
- The board polls one more route at `POLL_MS`.
- When `/queue` is wired it should read `getActionQueue` and retire the
  draft's mock and its `["actions"]` key in the same change.
- Two consoles of different projects on one hive would each show the
  other's start as *Queued*; that waits for `docs/plans/projects.md`.

**Ruled out.**
- Rewriting `listActions` and `QueuedAction` in place: it reshapes
  `queue.tsx`, which is step 7's.
- Hiding the button when the queue read failed: a working start would be
  lost to a read error, and the 409 already stops a duplicate.
- Showing *Queued* from the mutation's own result: the poll is the
  record, and a cancel from elsewhere would leave the card lying.

## ADR 163 — The Queue screen reads the real action queue with twenty finished records, and cancels a start behind a dialog

**Status:** accepted (T-107, 2026-10-19). Builds ADR 142 split item 7 on
ADR 157's forward and ADR 160 part 1's reader; carries out ADR 160's
consequence that `/queue` retires the draft's mock and its `["actions"]`
key. Supersedes nothing.

**Context.** `/queue` drew a mock (`listActions`, `QueuedAction`, a verb bar
and an enqueue dialog) whose shape is not the api's. `GET /api/actions`
serves `pending` (take order), `claimed`, the newest `limit` of `done`
(default 50, at most 500), `done_total` and `worker` (`heartbeat`, `stale`,
`stale_after_seconds`), and the forward copies `limit` (ADR 157, 5). The
Board reads it with `limit=1` under `["action-queue"]`. The board-service's
`POST /api/actions/<id>/cancel` answers 201 with a *cancel* action it queued,
not with the start cancelled: the worker does the cancel. Nothing enqueues
any other verb from the console.

**Decision.**

1. *One reader, a parameter.* `getActionQueue(limit = 1)` reads
   `/api/actions?limit=<limit>`; the Board's call and `actionQueueQuery`
   stay as they are. The screen's query is `actionQueueListQuery`, key
   `["action-queue", "list"]`, `getActionQueue(QUEUE_DONE_LIMIT)` with
   `QUEUE_DONE_LIMIT = 20`, polled at `POLL_MS`. The key sits under
   `["action-queue"]` so ADR 160's invalidation after a start refetches it.
2. *Three groups, every kind.* *Pending · n* in the order served, *Running ·
   n* for `claimed`, and *Finished* with `n` or, when `done_total` is larger
   than the list, `n of done_total`. Each group with no rows says so in one
   muted line. A row shows `kind`, `task_id`, `project`, `actor`, `note` and
   an age that ticks: pending since `created_at` (*queued*), running since
   `claimed_at` (*claimed*), finished since `finished_at` (*finished*). A
   null task, project, note or timestamp is `Absent` (*no task*, *no
   project*, *no note*, *not recorded*). A `cancel` row also prints its
   `params.target` when that is a string. A pending row with `waiting`
   prints *waiting for an account*, *waiting for the project*, or another
   value verbatim, as ADR 160 part 3 words it. A finished row has a status
   pill (`done` success, `failed` destructive, `interrupted` warning,
   `cancelled` and anything else muted, verbatim) and its `error`, when
   non-null, on its own destructive line.
3. *The states.* Loading is one muted line. A failed read is `ErrorState`
   in place of the groups, naming `GET /api/actions`. All three lists empty
   with `done_total` 0 is one `EmptyState`: nothing has been queued, and a
   Ready card's *Start development* on the Board queues one. The api's
   `warnings` render in `WarningBanner`. `pending`, `claimed` or `done` that
   is not a list is `Malformed` in that group's place and the others still
   render (`docs/ui.md` *A value of the wrong shape is named*).
4. *The worker line.* When the read succeeded and `worker.heartbeat` is
   null: a `Banner` tone `warning` saying no worker has written a
   heartbeat, so nothing is being taken from the queue. When `heartbeat` is
   present and `stale` is true: the same banner saying its heartbeat is
   older than `stale_after_seconds` seconds, with the age of `beat_at` when
   that is a string. Staleness is the api's judgement, never recomputed
   (`docs/ui.md` *Staleness is served, never computed*). It shows beside the
   empty state too.
5. *Cancel.* Only on a `kind === "start"` row in Pending or Running.
   `cancelAction(id)` is `POST /api/actions/<id>/cancel` with body `{}`
   through `readBare`; `cancelActionMutation(qc)` invalidates
   `["action-queue"]` and `["tasks"]` on settle, success or refusal. The
   confirmation is `components/ui/dialog.tsx`, as ADR 160 part 5's: it names
   the action id and the task, says the worker carries the cancel out, and
   its confirm is disabled while the write is in flight. Success is
   `toast.success` (the cancel was queued) and closes the dialog; a refusal
   is `toast.error` with the served message and also closes it, since
   there is no input to keep.
6. *What goes.* The verb bar, the enqueue dialog, `VERBS`, the screen's use
   of `actionBackendQuery`, and then `listActions`, `enqueueAction`,
   `commandLine`, `actionsQuery`, `QueuedAction`, `ActionVerb` and
   `mockActions`, which have no other caller. `actionBackendQuery` and
   `pingActionBackend` stay for app-shell, approvals and tokens.

**Consequences.**
- The console has no screen that enqueues an arbitrary verb; a start is the
  Board's and a cancel is this screen's.
- A toast's *success* means the cancel was queued; whether the start ended
  `cancelled` is read off the next poll.
- The screen polls one more `GET /api/actions` at `POLL_MS` while open.

**Ruled out.**
- A second reader function: the two would differ by a number.
- Keying the screen `["actions"]`: a start's invalidation would miss it.
- `alert-dialog.tsx`: its action closes before the write settles, so the
  in-flight state could not be shown.

## ADR 166 — The `worker` compose service copies the dispatcher's mounts, the board service mounts `actions` alone, and `BOARD_ACTIONS_TOKEN` is optional

**Status:** accepted (T-108, 2026-10-19). Step 5 of ADR 142's split, in
`docker/compose/docker-compose.yml` and `docker/compose/docker-compose.coolify.yml`.
Narrows ADR 142 decisions 1, 3 and 8; supersedes nothing.

**Context.** ADR 142 named the `worker` service, the board service's one
new mount and the bearer its action routes check (ADR 151, ADR 157), and
left their compose spelling to this step. Two spellings are choices a later
edit could undo without knowing they were made: how the worker's mounts
relate to the `dispatcher` service's, and whether `BOARD_ACTIONS_TOKEN` is
required like `CONFIG_WRITER_TOKEN` or optional like `CONSOLE_PROJECT`.

**Decision.**
1. *The worker's volumes equal the dispatcher's, entry for entry*, in both
   files, and its `depends_on` and (empty) environment are the
   dispatcher's too. It differs in `profiles: ["worker"]`,
   `restart: unless-stopped` and a real `command`,
   `["--config", "/app/config.yaml", "worker"]`. It sits after `dispatcher`
   in the Coolify file, above the agent pairs, so `render_coolify_compose`
   copies it verbatim. The docker socket is mounted by `worker` and
   `dispatcher` and no other service.
2. *The board service mounts `../../dispatcher_state/actions:/state/actions`,
   read-write*, beside its three existing mounts and never the whole
   `dispatcher_state`: `/state` is `state_dir` in `config.example.yaml`, so
   `<state_dir>/actions` resolves inside the container with no config key.
   The host directory does not exist until the first `up`; docker creates
   it as root, which both services run as, so no step makes it beforehand.
3. *`BOARD_ACTIONS_TOKEN` is spelt `${BOARD_ACTIONS_TOKEN:-}`* on
   `board-service` and `console` and reaches no other service. Both start
   without it and answer 503 on the action routes only, so an unset token
   is a stack with no Start or Cancel, not one that fails to boot; the bare
   form would make compose warn on every command for a feature the operator
   may not have opted into (the `worker` profile is opt-in as well).
   `scripts/configure.sh` generates it with `gen_token` beside
   `CONFIG_WRITER_TOKEN`.

**Consequences.**
- A mount added to `dispatcher` must be added to `worker`;
  `tests/integration/test_compose_invariants.py` fails until it is.
- An `.env` written before this ADR has no token: the console's Start and
  Cancel answer 503 until it is added and both services restart.

**Ruled out.**
- The bare `${BOARD_ACTIONS_TOKEN}`: neither holder refuses to start without
  it, so compose's warning would be one the operator learns to ignore.
- Creating `dispatcher_state/actions` from `scripts/configure.sh`: docker
  makes it on first mount, owned by the root both services run as.

## ADR 172 — A comment count counts the lines a reader shows as comments, by one rule in `dispatcher/tasks.py`

**Status:** accepted (operator, out of cycle, 2026-10-19). Amends ADR 125
step 7 (`board_get`'s comment count) and the context of ADR 135 ("no reader
in `dispatcher/` parses the file").

**Context.** ADR 125 defined `board_get`'s `comment_count` as the non-blank
lines of `board/<id>/comments.jsonl`, and `tasks.add_comment` returned the
same count. The read api's thread (ADR 135) skips a line that is not a
comment — not JSON, not an object, or without a string `at`, `author` and
`text` — with a warning. A file with a bad line therefore counted more
comments than the console's thread showed (`docs/implementations/T-090.md`,
*Pending*).

**Decision.**
1. *`tasks.parse_comment_line`* is the one rule for what a line is: a JSON
   object with a string `at`, `author` and `text`. The read api's `_comment`
   calls it and keeps its own diff-anchor handling, which never drops a
   comment; the problem words are unchanged.
2. *`tasks.count_comments`* counts the non-blank lines that rule accepts.
   `board_get`'s `comment_count` and `add_comment`'s `comment_count` are
   that count; absent is still 0 and unreadable still `null` with a warning.
3. *A skipped line is a `board_get` warning*, `<path>: line <n>: <problem>`,
   the read api's words for the same line. `add_comment` returns no
   warnings and names none.

**Consequences.**
- The count equals the thread's length except when the read api's own
  bounds cut the thread (`MAX_COMMENTS_BYTES`, `MAX_COMMENTS`), which it
  names in its own warnings; the count reads the whole file.

**Ruled out.**
- Keeping a raw line count beside the parsed one: no caller wants the
  number of lines that are not comments, and the warnings name them.
## ADR 169 — A thread's models are a list in config: `thread_models`, replaced per account by `models`

**Status:** accepted (T-109, 2026-10-19). Settles `docs/plans/main-thread.md`
*Open questions*, *Model per thread*, as the user ruled on 2026-10-19.
Supersedes nothing.

**Context.** The plan's *Decisions* 2 lets a thread pick its model, and left
how the list of models an account may use is known to step 1. The CLI has no
call that lists them, and probing an account spends quota.

**Decision.**
1. *`thread_models`*, top level in `config.yaml`: a list of model IDs in
   `default_model`'s form. Absent or null, it is `[default_model]`, taken after
   the overrides merge, so an override of `default_model` moves it too.
2. *`models`*, optional on an entry of `accounts`: the same shape, and when
   given it replaces `thread_models` for that account, whole.
   `AccountConfig.models` is `None` when absent, and
   `Config.thread_models_for(account_name)` answers the account's list or
   `thread_models`; it raises `KeyError` for an unknown account.
3. *Checked at load*, `ValueError` naming the key: a list (not a string), not
   empty, each entry a non-empty string. Entries are stripped; a duplicate is
   refused, so a list never shows one model twice. Nothing is probed: a model
   the account cannot use fails at its turn (step 2), not here.
4. *`CONFIG_KEYS` row*: `thread_models` is `editable`, kind `list`, like
   `fallback_roles`; the merged result is what the checks of 3 run on, so a
   bad override is refused with ADR 106's message. `accounts` stays
   read-only, so `models` is edited in `config.yaml` only.

**Consequences.**
- The console's model picker (step 4) reads `thread_models_for` through a
  route step 3 adds; this task serves no list over HTTP.
- `default_model` itself is not required to be in `thread_models`: a thread
  then defaults to the list's first entry (ADR 170, 2).

**Ruled out.**
- Probing `/model` or a test turn per account: it spends quota for a list
  the operator already knows.
- A per-account map at the top level (`thread_models: {cuenta1: [...]}`):
  two places would name accounts, the disagreement `_load_primary_account`
  in `dispatcher/config.py` already refuses for the primary.

## ADR 170 — Thread records: `dispatcher/threads.py`, the main thread is the id `main`, two board-service routes

**Status:** accepted (T-109, 2026-10-19). `docs/plans/main-thread.md` step 1,
narrowed: no primary override (*Decisions* 3 stays `primary_account`).
Builds on ADR 169; supersedes nothing.

**Context.** *Decisions* 2 names a thread record's fields and that there is
one main thread, the console's centre, with `task` empty; how the main thread
is marked and when it is made was left to this task, under the rule that a
second one can never exist. The record is written by the board service and
read by step 2's worker and step 3's api.

**Decision.**
1. *The store.* `dispatcher/threads.py`, beside `dispatcher/tasks.py`, owns
   `<hive root>/threads/<id>.json` (`context_transfer.hive_root`, so a sibling
   of `tasks/`, `board/` and `locks/`). Functions: `create`, `read_thread`,
   `read_threads` and `update`, all taking `hive_dir` (the hive tasks dir, as
   every `tasks.py` function does) and the `Config` where accounts matter.
   Every write is `context_transfer._write_atomic` after
   `_makedirs_readable`, under `tasks.task_lock(hive_dir, "_thread-<id>")`:
   the hive's existing write lock, under a name no task id can take.
   `read_threads` lists `*.json` (never the `.tmp` leftovers), returns
   `(records, warnings)`, the main thread first and then by `created`, and
   turns a file that does not parse or is not a JSON object into a warning
   naming it. `read_thread` raises `NoThreadError` for an absent id and
   `UnreadableThreadError` for a file that does not parse.
2. *The record*, exactly *Decisions* 2's fields: `id`, `title`, `project`,
   `account`, `model`, `effort`, `segments`, `created`, `status`, `task`.
   Times are UTC ISO-8601 (`datetime.now(timezone.utc).isoformat()`, a `now`
   argument for tests). On create, `account` defaults to `primary_account`,
   else the first account; `model` to `default_model` when it is in that
   account's `thread_models_for`, else the list's first; `effort` to
   `default_effort`. Each may be given, and is refused (`ThreadRefused`)
   when it is an unknown account, a model not in the account's list, or not
   one of `EFFORT_LEVELS`. `status` starts `open`; the statuses are `open`
   and `archived`. `segments` starts as one
   `{session_id: null, account, model, effort, from: created}`.
3. *Ids.* An ordinary thread's id is `th-<YYYYmmddTHHMMSSZ>-<6 hex>`, made
   by `create`; a file already there under the lock is a `ThreadRefused`, not
   an overwrite. A path id matching neither form is `NoThreadError`, so no
   path component is built from an id the module did not make.
4. *The main thread is the id `main`*, and no field marks it: a second one
   cannot exist because there is one file name for it, and the check and the
   write are one step under `_thread-main`. It is made on request, by
   `create(..., main=True)` (the console asks when step 3's read shows none),
   never at boot or by a reader: neither service writes the hive on start,
   and the read api cannot write. A second `main=True` raises
   `ThreadExistsError`. Its `task` and `project` are null and cannot be given
   with `main`; its title defaults to `Main thread`. It cannot be `archived`,
   because the console opens on it.
5. *Ordinary threads* need a `title` (non-empty after strip, at most
   `tasks.MAX_TITLE_CHARS`). `project` is optional, null or a slug by
   `actions._slug_problem`'s rule. `task` is optional, null or the id of a
   task file that exists (`context_transfer.task_file_path`). Neither changes
   after create.
6. *Update* takes `title`, `account`, `model`, `effort`, `status`, at least
   one. A value equal to the current one is no change. When `account`,
   `model` or `effort` change, however many at once, one segment is appended
   with the new triple, `from` now, and the last segment's `session_id`
   (the session lives on `claude_shared`, *Decisions* 2). A new account
   re-checks the model, the given one or else the current one, against the
   new account's list, and refuses rather than picking another: moving
   quota and model is the person's call. `title` and `status` open no
   segment. Nothing here sets `session_id`; step 2's worker adds that writer.
7. *Routes*, in `board_service/app.py`, beside `/api/backlog` and not behind
   `BOARD_ACTIONS_TOKEN` (they spend no quota): `POST /api/threads`, body
   `title`, `project`, `account`, `model`, `effort`, `task`, `main` (a JSON
   boolean), 201 with the record; `PATCH /api/threads/<id>`, body `title`,
   `account`, `model`, `effort`, `status`, 200 with the record. The guards and
   their order are `_http`'s: a browser `Origin` 403, any query 400, a body
   that is not a JSON object 400; then an unknown field 400, a field of the
   wrong JSON type 400, a null taken as left out, an empty PATCH 400.
   `ThreadRefused` is 400, `ThreadExistsError` and `UnreadableThreadError`
   409, `NoThreadError` 404, an `OSError` from the write 503. Every error is
   `{"error": <reason>}`.

**Consequences.**
- Step 3's api reads through `read_threads`/`read_thread`. Its views may be
  named `threads`; import the module as `thread_store`, as `tasks` is
  imported as `task_store` (`docs/learnings/the-apis-views-shadow-a-module-imported-as-tasks.md`).
- Step 2's worker owns the first `session_id` and the new-session segment
  (*Decisions* 7); both are writers it adds to this module.
- An account removed from `config.yaml` leaves records naming it; reading
  them still works, and an update re-checks only what it changes.

**Ruled out.**
- A `main: true` field: a field can be set on two files; a name cannot.
- Making the main thread when the board service starts, or on the first
  read: a write at boot or from a reader, for a record nobody asked for.
- Picking another model silently when the account changes: the person would
  find the thread on a model they did not choose.

## ADR 177 — `GET /api/threads` serves every record as written: main first then by `created`, archived ones included

**Status:** accepted (2026-10-19). `docs/plans/main-thread.md` step 3, the
list route only; `GET /api/threads/<id>/events` waits for the conversation
log's format (T-110). Builds on ADR 170; supersedes nothing.

**Context.** Step 3 serves the thread records ADR 170 keeps under
`<hive root>/threads/` to the console's selector (*Decisions* 5). Left open:
the order of the list after the main thread, and what to do with an archived
thread.

**Decision.**
1. *The route.* `GET /api/threads` in `observability/api/app.py`, behind the
   api's auth, answering the usual envelope. `data` is the list of records,
   each served whole as `dispatcher/threads.py` wrote it (`id`, `title`,
   `project`, `account`, `model`, `effort`, `segments`, `created`, `status`,
   `task`): the current triple is the record's top-level `account`, `model`
   and `effort`, and `segments` is the history the separators are drawn from.
   The module is imported as `thread_store`, so the view can be named
   `threads`. The reading is `thread_store.read_threads`; no parsing here.
2. *The order* is `read_threads`'s: the main thread first, then by
   `created`, oldest first. It is stable: a thread keeps its place in the
   selector while the person talks to another. "Most recently active" would
   need the last turn's time, which lives in the conversation log step 2
   writes and is not built, and it would reorder the selector under the
   person's pointer.
3. *Archived threads are served* with `status: "archived"`, and the console
   filters them. The route takes no query parameters (any is a 400, as on
   every route here). The list is a handful of small records, so one answer
   serves both the selector and a later "archived" view, and nothing is left
   out of a read without the reader seeing why.
4. *Bad files.* A record that does not parse or is not a JSON object is a
   warning naming it (`read_threads`), and the rest are served. A threads
   path that cannot be listed (not a directory, no permission) is an empty
   list and one warning, never a 500; an absent one is an empty list and no
   warning. No main thread is an answer too: the console then asks the board
   service to make it (ADR 170, 4).

**Consequences.**
- The compose service already mounts the hive root at `/data/.hive:ro`, so
  the route needs no new mount.
- A record's shape is not re-checked here: a hand-edited field is served as
  written, as a task's frontmatter is.

**Ruled out.**
- Filtering archived threads by default behind an `include_archived` flag: a
  parameter to validate for a list this small, and a default that hides rows.
- Reading the files in the api itself: a second copy of the format.
## ADR 176 — The primary override is the config writer's `primary_account`, read live by the picker

**Status:** accepted (2026-10-19). `docs/plans/main-thread.md` step 1, the
part ADR 170 left out: the primary override. Builds on ADR 75 and ADR 170;
supersedes nothing.

**Context.** *Decisions* 3 has the console change the primary by writing an
override "through the board service", with `balancer.md`'s meaning (the
primary is last in the pool and keeps its reserve), applied to the next pick,
never moving a running phase. Most of it already existed: `primary_account`
is an editable key of the config writer (ADR 75), so a save is validated by
`load_config` (an unknown name is a 422), atomic (a temp file and
`os.replace`, the previous version kept), locked (`flock` on
`overrides.lock`) and logged. What did not hold was the timing. ADR 75 makes
an override take effect on the next dispatcher invocation, and the queue's
dispatcher is one long run that loaded its config once: the primary would
move only on a restart.

**Decision.**
1. *The write is the config writer's.* The console moves the primary with
   `PUT /settings/primary_account` and returns it to `config.yaml`'s with
   `DELETE`, as for any editable key. No board-service route writes
   `config.d/overrides.yaml`: ADR 75 gives that file one writer. That is how
   this entry reads "through the board service": through the console's
   service for settings, behind the same authentication.
2. *The picker reads it live.* `pick_idle_account` calls
   `dispatcher.config.refresh_primary(cfg)` before it lists the idle
   accounts. It re-reads the overrides file and, when the name changed,
   re-marks `is_primary` on every account and sets `cfg.primary_account`, in
   place and under one lock, since the queue's cycles share the `Config`. So
   the ranking, the reserve and the week pacing all follow on the next pick.
   A phase already running keeps the account it was given.
3. *Only `primary_account` is live.* Every other override still applies when
   the dispatcher starts, as ADR 75 says. `load_config` records where it read
   from (`Config.overrides_path`, left out of equality) and `config.yaml`'s
   own primary (`Config.config_primary_account`). A `Config` built by hand
   has no path and keeps its primary.
4. *A bad override falls back, with a warning.* At start an override
   `load_config` refuses is still an error. On a pick, an override naming no
   configured account, or a file that cannot be read or parsed, makes the
   primary `config.yaml`'s (none when it names none), and the log says why,
   once per change of problem. A missing file or key is `config.yaml`'s
   primary without a warning.

**Consequences.** Moving the primary from the console takes effect on the
next pick, with no restart, and the change is in `changes.jsonl` like any
save. The board service and the api still read their config once, at boot,
so until they restart a new thread defaults to the old primary's account
(ADR 170, 2) and the api shows the old mark. Making those live is not this
entry's.

**Ruled out.**
- *A board-service route writing the override.* A second writer of
  `overrides.yaml`, outside the writer's lock and log.
- *Reloading the whole config on each pick.* It would make every key live,
  which ADR 75 did not decide, and a bad save of any key could then fail a
  pick.
- *Failing the pick on an unknown name.* The writer refuses one already, so
  only a hand edit gets there, and stopping the queue for it is worse than
  ranking by `config.yaml`'s primary.
## ADR 173 — A `turn` runs one streamed `claude -p` on the thread's own account, read-only by flags, and logs it between two harness lines

**Status:** accepted (T-110, 2026-10-19). ADR 142 split item 10,
`docs/plans/main-thread.md` step 2. Builds on ADR 142 (decisions 6–8), ADR
148, ADR 169 and ADR 170; supersedes nothing. The `Kind.handler` slot
(`docs/debt/T-100-D1.md`) stays as it is.

**Context.** `turn` is in `actions.KINDS` (serial key `thread:<id>`,
recovery `interrupt`, `spends_quota`) with no entry in `worker.HANDLERS`, so
it stays pending. The plan fixes the transport (`stream-json`, `--resume`,
`conversations/<thread>.jsonl`, *Decisions* 4 and 6) and the rule that the
thread writes no code (`docs/plans/balancer.md`, *The conversational thread
does not write code*), and leaves the flags, the log's own lines and a
cooling account to this ADR. ADR 142 decision 6 asks a kind that spends
quota to name what authorizes it.

**Decision.**

1. *What authorizes a turn.* The person's message, sent from the console's
   conversation, authorizes that one turn and nothing more: C-1 and C-8, as
   *Amended* 2026-10-11, make the console's thread the one conversation,
   and C-12 *Amended* the same day puts that conversation in the console.
   Under C-4 the worker runs it once: it never retries a turn, resumes one
   that failed, or starts one of its own, and C-4's handoff-resume amendment
   does not reach the main thread.
2. *The handler.* `worker.HANDLERS["turn"]`. It reads the record with
   `threads.read_thread(cfg.hive_tasks_dir, thread)`; `NoThreadError` or
   `UnreadableThreadError`, a `status` of `archived`, an `account` that is
   not in `cfg.accounts`, or a parked account (5) ends the action `failed`
   with one line of error, before anything is logged or spent. The turn
   runs on the record's `account`, `model` and `effort` (its current
   values, which are the last segment's), resuming the last segment's
   `session_id` when it has one. A turn whose thread already has one
   running stays pending with `waiting: thread`.
3. *The command.* Built in `dispatcher/docker_exec.py` and run through the
   same `docker exec -w <workdir> <container>` argv `run_docker_exec`
   builds, but with `subprocess.Popen` so lines are read as they come.
   No second path to the socket. In order:

   `sh -c 'echo "$$"; exec "$@"' ia-harness-turn timeout --kill-after=30
   1800 claude [--resume <id>] --model <m> --effort <e> --permission-mode
   dontAsk --permission-prompts none --allowed-tools <allow…>
   --disallowed-tools Write Edit NotebookEdit -p --output-format
   stream-json --verbose -- <text>`

   The text sits last, after `--`, so a message that begins with `-` is
   read as the prompt and never as a flag that lifts the no-code set.

   - The `sh` prints its pid on the first line and `exec`s the rest, so
     that pid is the in-container `timeout`; the script is a literal and
     the argv goes in as positional arguments, so nothing is interpolated.
     The first line is read as the pid and never logged.
   - `timeout` bounds a turn at `TURN_TIMEOUT_SECONDS = 1800` in the
     container, as a phase's is bounded (killing the host client does not
     stop the process inside).
   - `dontAsk` denies every call no rule allows, and `--permission-prompts
     none` says nobody answers a prompt, so no SDK host could grant one;
     `--disallowed-tools` denies the three writers outright,
     above any allow rule or mode. Not `cfg.permission_mode` and not
     `cfg.allowed_tools`: those are the phases' grants.
   - The allowlist, `TURN_ALLOWED_TOOLS`: `Read`, `Glob`, `Grep`,
     `Bash(ls*)`, `Bash(pwd)`, `Bash(wc*)`, `Bash(git status*)`,
     `Bash(git branch --list*)`, `Bash(git rev-parse*)`. No `--mcp-config`
     (the dispatch tool is step 6), no `--plugin-dir`, no `--add-dir`.
   - `--verbose` because the CLI refuses `-p` with `stream-json` without it.
   - The workdir is `<projects_root>/<project>` for a thread with a project
     and `projects_root` for one without. A thread's project never changes
     (ADR 170, 5), so every turn of a thread runs in one directory, which
     is where the CLI keeps the session `--resume` looks up.
4. *The log.* `<hive root>/conversations/<thread>.jsonl`
   (`context_transfer.hive_root`), made with `_makedirs_readable`, opened
   for append, one line per write and flushed at once, never rewritten. Two
   writers are added to `dispatcher/threads.py`: `conversation_path` and
   `append_conversation`. One turn at a time per thread (the serial key) is
   what keeps the lines of two turns apart; recovery runs before the loop.
   - First, the harness line `{"type": "ia_harness", "event":
     "turn_start", "at", "action", "actor", "text", "account", "model",
     "effort", "session_id"}`: the person's message, which the stream does
     not echo, and the session resumed (null on a fresh one). It is written
     just after `started_at`, just before the exec.
   - Then each stdout line of the stream, verbatim, when it parses as a
     JSON object. A line that does not is written as `{"type":
     "ia_harness", "event": "unparsed", "at", "line"}`. No stream event has
     the type `ia_harness`.
   - Last, `{"type": "ia_harness", "event": "turn_end", "at", "action",
     "status", "error", "session_id"}`, with the action's final status.
     Every `turn_start` gets one `turn_end`: from the handler, or from
     `Worker.recover` for a turn left claimed with a `started_at` (8). A
     turn refused before it started writes neither.
5. *A parked account.* When `state_machine.get_state` of the thread's
   account is in `PARKED_STATES` (pre-cooldown, cooling or login expired),
   the turn ends `failed`, naming the account and its state, and nothing
   runs. The worker never picks another account and never waits: moving the
   thread is the person's act (`main-thread.md` *Decisions* 3), and a turn
   that waited would run hours later on its own. No `/usage` probe is run
   for it. The worker's pool-wide account check, which is for a start
   (ADR 148, 5), does not apply to `turn`. A turn takes no holder slot in
   the account's state file: a conversation and a phase share an account
   (`balancer.md`).
6. *The session.* After the stream, the `session_id` of its `system`/`init`
   event, when it differs from the one the turn resumed (null included), is
   written into the thread's last segment by `threads.update(...,
   session_id=...)` under the thread's lock. The new keyword opens no
   segment and is not a field of the board-service PATCH route. A session
   recorded is what the next turn resumes.
7. *How it ends.* A `result` event with `is_error` false ends it `done`,
   `result` `{session_id, total_cost_usd, usage, num_turns}` copied from
   that event. With `is_error` true, or no `result` event, it ends `failed`:
   the error is the event's `subtype` and `result` text, or the exit status
   and the stderr tail, on one line. The CLI's `result` stays in the log
   either way. A refusal for rate limit parks nothing here.
8. *Cancel, crash, shutdown.*
   - `Running` gains `kill`, set by the handler once it has the pid: a
     `docker exec <container> kill -TERM <pid>` (coreutils `timeout` passes
     the signal on), then the host client is waited for. A `cancel` of a
     claimed `turn` sets `cancelled` and calls `kill` (ADR 142, 7) and
     itself ends `done`, `target_status: running`; the handler checks
     `cancelled` again once `kill` is set, so a cancel that came first still
     stops it. The turn ends `cancelled`, its `turn_end` says so.
   - After a crash, `actions.recover` already ends a claimed `turn`
     `interrupted` with "interrupted before the turn finished", never run
     again. `Worker.recover` then writes the `turn_end` for each one that
     has `started_at`.
   - On SIGTERM or SIGINT, after the loop stops claiming, the worker calls
     `kill` on every running turn before it joins; each ends `interrupted`
     with the error "the worker stopped during the turn". A turn has no
     boundary inside it to hold at, as a start has between phases (ADR 148,
     4), and letting it run on would depend on the stop grace period and
     leave a `claude` running unseen in the container. It never goes back
     to `pending/` (C-4).

**Consequences.**
- A message to a cooling account fails at once; the console shows the
  error and offers the move, and sending again is a new turn.
- The thread can read but not run a program or read git history: `git log`,
  `git show` and `git diff` all take `--output=<file>`, which writes. A
  wider read set is a later ADR.
- A turn's cost is in its `result` and in the log, not in a task's usage
  log (ADR 53): a turn belongs to no task.
- Step 3's api reads `conversations/<thread>.jsonl`; a reader groups a turn
  by its `turn_start` and `turn_end`, and may meet a final line cut short.

**Ruled out.**
- Sending a turn to another account, or waiting on a cooling one: the
  person's call (*Decisions* 3).
- `acceptEdits` with the three writers denied: it leaves the CLI's own
  read-only Bash set and every other non-prompting tool to the CLI's
  version; `dontAsk` with an allowlist is the set this ADR names.
- Killing the turn by `pkill -f` on its session id: a fresh turn has none
  in its argv.
- Rewriting the log's stream lines into a harness envelope: a reader should
  parse what the CLI wrote.

## ADR 178 — The board service and the api read the primary override live too

**Status:** accepted (2026-10-19). `docs/plans/main-thread.md` step 1, the
gap ADR 176 left: its *Consequences* kept the board service and the api on
the primary they loaded at boot. Builds on ADR 170 and ADR 176; supersedes
nothing.

**Context.** ADR 176 made `pick_idle_account` call `refresh_primary(cfg)`, so
the dispatcher follows a primary the console moves with no restart. The
board service and the api load their config once, in `create_app`, and two
of their answers read the primary: a new thread with no account defaults to
`cfg.primary_account` (ADR 170, 2), and `GET /api/accounts` serves each
account's `is_primary`. Until a restart, both answered with the old primary.

**Decision.**
1. *The thread default reads it live.* `dispatcher/threads.py`'s
   `_default_account` calls `refresh_primary(cfg)` before it reads
   `cfg.primary_account`. It is the one read in the thread records, so the
   board service's `POST /api/threads` follows the override, and so would any
   other caller of `create`. An account given in the body is not touched.
2. *The api's mark reads it live.* `GET /api/accounts` calls
   `refresh_primary(cfg)` once per request, before it builds the rows, so
   every row's `is_primary` comes from the same read.
3. *Still only the primary.* Every other override applies when each process
   starts, as ADR 75 and ADR 176 say. The fallbacks are ADR 176's: a missing
   file or key is `config.yaml`'s primary with no warning, and an unusable
   override is `config.yaml`'s with a warning in that service's log.
4. *The mounts already reach the file.* In `docker/compose/docker-compose.yml`
   both services mount `config.d` read-only at `/app/config.d`, beside
   `/app/config.yaml`, which is where `load_config` looks by default. The
   mount is of the directory, not of the file, so the config writer's
   `os.replace` is seen on the next read. No compose change.

**Consequences.** A primary moved from the console shows on the next thread
created and on the next read of the accounts, with no restart of either
service. Each such request reads one small file more. The board service's
routes that never read the primary (`PATCH /api/threads/<id>`, the board's
writes) do not re-read it.

**Ruled out.**
- *Reloading the config per request.* It would make every key live in two
  more processes, which ADR 75 did not decide, and a bad save of any key
  could then fail a request.
- *Calling `refresh_primary` in the board service's route instead.* The read
  is in `_default_account`; refreshing next to it keeps a future caller of
  `create` from repeating the gap.

## ADR 179 — `worker.HANDLERS` is the one registry of a kind's handler; `actions.Kind` has no `handler` slot

**Status:** accepted (T-101, 2026-10-19). Resolves `docs/debt/T-100-D1.md`.
It supersedes the words "its handler" in ADR 142 decision 6, *How a kind is
added*, and the sentence "`Kind.handler` in `KINDS` stays `None`" in ADR 148
decision 1, with the first bullet of ADR 148's *Consequences*. The rest of
ADR 142 and ADR 148 stands.

**Context.** ADR 142 decision 6 has the `KINDS` registry in
`dispatcher/actions.py` give each kind's handler. ADR 148 decision 1 put the
handlers in `dispatcher/worker.py` `HANDLERS` instead, because a handler
calls the dispatcher core and `actions.py` must stay importable by the board
service without it, and left `Kind.handler` declared and `None` on every
kind. Nothing read the slot. A dead field that reads as the place to wire a
handler is where a later task would put one, and the worker would never look
there (T-100-D1).

**Decision.**
1. *The slot is removed.* `actions.Kind` carries a kind's contract only: its
   `params` validation, its serial key, whether it spends quota and what
   recovery does with it (ADR 142 decisions 7 and 8). It has no `handler`
   field.
2. *One registry for the run.* A kind's handler is an entry in
   `worker.HANDLERS`, kind name to handler, and nowhere else. ADR 142
   decision 6's *How a kind is added* reads: an entry in `KINDS` for the
   kind's contract, an entry in `worker.HANDLERS` for its run, a
   board-service route that creates it, and tests of all three. A kind in
   `KINDS` with no entry in `HANDLERS` is left in `pending/` untouched, as
   ADR 148 decision 1 says.

**Consequences.**
- Adding a kind still touches two modules, `actions.py` and `worker.py`,
  now with nothing in the first that suggests otherwise.
- `actions.py` still imports nothing of the dispatcher core.

**Ruled out.**
- Keeping the slot with a comment pointing at `worker.HANDLERS`: a field
  nothing sets or reads, kept only to be explained.
- Moving the handlers into `KINDS`: ADR 148 decision 1 and its *Ruled out*
  still hold; `actions.py` would import the core, or `worker.py` would
  mutate another module's frozen registry at import.

## ADR 180 — `GET /api/threads/<id>/events` serves the conversation log by line number

**Status:** accepted (2026-10-19). Builds the second route of
`docs/plans/main-thread.md` step 3 on the log ADR 173 decision 4 defines.

**Context.** ADR 173 decision 4 writes a thread's conversation to
`<hive root>/conversations/<thread>.jsonl`, append-only: a `turn_start`
harness line, the stream's lines verbatim, a `turn_end` line. The screen
(step 4) needs to read it and then follow it while a turn runs, without
re-reading what it already has. A reader may meet the last line half
written.

**Decision.**
1. *A row is a line.* The answer is `{"n", "event"}` per line: `n` the line's
   number from 1, `event` the line parsed as written. The route does not
   group lines into turns or reshape the stream's events; the log's format is
   ADR 173's, and the screen reads it.
2. *The cursor is the line number.* `since=<n>` serves the lines after line
   `n` (0, the default, is from the start; a negative one reads as 0);
   `limit` bounds the rows, 500 by default and at most 2000, the clamp
   reported in `warnings`. A follower sends the last `n` it has. The log is
   append-only, so a line's number never changes.
3. *A torn last line waits.* The piece after the last newline is being
   written and is not served; the next read, once it ends, serves it under
   its number. A whole line that does not parse to a JSON object is a row
   with a null `event` and a warning, so the numbering stays whole.
4. *Ids.* An id `conversation_path` refuses (not `main`, not one the
   harness makes) is a 400, as `/api/phases` answers a malformed id, and no
   path is built from it. A well-formed id with no log yet is an empty list,
   not a 404 (ADR 27); the read creates nothing. An unreadable log is an
   empty list with a warning, not a 500.

**Consequences.**
- Every request reads the whole file. A thread's log is a conversation, not
  a phase's stream, so this is cheap for now; a byte-offset index or a
  seek from a remembered offset is the later optimization if logs grow.
- The route is read-only and behind the api's auth like the others.

**Ruled out.**
- A byte-offset cursor: exact and seekable, but meaningless to a reader of
  the rows and broken by the `errors="replace"` decode of a bad byte.
- Grouping lines into turns server-side: it would fix a second format beside
  ADR 173's, and a running turn has no `turn_end` to group by.
- A 404 for a well-formed id with no log: a new thread has none until its
  first turn, and ADR 27 answers an id from a list with an empty 200.

## ADR 181 — `POST /api/threads/<id>/turns` queues a `turn` behind the actions bearer; a message mid-turn queues, and *stop* is the cancel route

**Status:** accepted (T-112, 2026-10-19). The board-service half of
`docs/plans/main-thread.md` step 5, and the route ADR 142 decision 6 asks
of a new kind (`turn`, whose handler ADR 173 built). Builds on ADR 142,
ADR 151, ADR 170 and ADR 173; supersedes nothing. The composer and the
stop button are a later front task.

**Context.** `actions.KINDS["turn"]` validates `params` `{thread, text}`
and serialises on `thread:<id>`, and `worker.HANDLERS["turn"]` runs it,
but nothing writes one except by hand. The plan leaves two things to
step 5 (*Open questions*): whether a message sent during a turn can
interrupt it, and how big a message may be. `_validate_turn` only asks
for a non-empty string, so it would take a message of blanks or of any
length.

**Decision.**

1. *The route.* `POST /api/threads/<thread_id>/turns`, through the same
   `_action` the start, cancel and verdict routes use, so ADR 151
   decision 4's guards answer in its order (Origin 403, no token 503,
   bad bearer 401, query 400, body not an object 400). It calls
   `actions.create(state_dir, "turn", params={"thread": <path id>,
   "text": <body text>}, actor="operator")`: `project`, `task_id` and
   `note` are null. The thread's project is the record's, read by the
   worker (ADR 173, 3), and a body cannot name another. The answer is
   201 with the created record, as the start's is.
2. *What authorizes it.* A turn spends quota, so it is behind the actions
   bearer and not with the thread routes (ADR 170 put those outside it
   because they spend none). The person's message, sent from the
   console's conversation, authorizes that one turn and nothing more:
   `docs/charter.md` C-1 *Amended* 2026-10-11 and C-8's closing ruling of
   that day make the console's thread the one conversation, C-12
   *Amended* the same day puts it in the console, and C-4 is met because
   a person sent this
   message and the worker runs it once (ADR 173, 1).
3. *The body.* A JSON object with `text` and no other key.
   - Any other key is 400 `unknown field '<key>'`.
   - `text` missing, null, not a string, empty or only whitespace is 400
     `text must be a non-empty string`. A message of blanks would spend a
     turn on nothing. The text is stored as sent, never stripped.
   - `text` longer than `MAX_TURN_TEXT_CHARS = 20_000` characters
     (`len` of the string) is 400 `text is longer than 20000 characters`.
     The worker passes the text as one argv element of `docker exec`
     (ADR 173, 3), and Linux refuses one argument over 128 KiB: 20,000
     characters of four-byte UTF-8 is 80,000 bytes, under it with room.
     It is also `MAX_COMMENT_CHARS`, the hive's other limit on a
     person's free text. The constant lives in `board_service/app.py`;
     `_validate_turn` is not changed.
4. *The thread.* After the body, the route reads the thread with
   `threads.read_thread(hive_dir, thread_id)`:
   - `NoThreadError` (no file, or an id the module did not make) is 404
     with its message, as `PATCH /api/threads/<id>` answers;
   - `UnreadableThreadError` is 409, as the thread routes answer;
   - a `status` of `archived` is 409 `thread <id> is archived`.
   The read is outside the queue's `write.lock`. A thread archived
   between the read and the claim is refused again by the worker, which
   ends the turn `failed` before anything runs (ADR 173, 2). The route
   checks neither the account nor its quota state: that is the worker's,
   at claim time, when it can still change.
5. *A message mid-turn queues.* No 409 for a second turn: `turn` is not
   in `actions._ONE_PER_TASK`. Two posts on one thread make two pending
   records with the same serial key `thread:<id>`; the worker runs them
   one after the other in creation order, the second `waiting: thread`
   while the first runs.
6. *No interrupt.* A message never interrupts the running turn, and no
   flag or route offers it. *Stop* is the existing
   `POST /api/actions/<id>/cancel` with the running turn's action id,
   unchanged: the route queues a `cancel` whose `params.target` is the
   turn, and the worker kills a claimed turn or cancels a pending one
   (ADR 173, 8). Stop-and-send is the person's two acts, a cancel and a
   new message.

**Consequences.**
- The console's composer (a later front task) posts `{"text"}` with the
  bearer, through the forward ADR 157 built for the other action routes,
  and reads 201 with the record or `{"error"}`.
- A queued turn runs on whatever account the thread names when it is
  claimed, not when it was sent: a thread moved in between moves its
  queued turns with it.
- Messages sent while a turn runs pile up as separate turns, each one
  spending; nothing merges them.
- A message longer than 20,000 characters is refused at the door; one
  that passes can still fail at the worker for another reason, which
  the action's `error` says.

**Ruled out.**
- Interrupting the running turn when a message arrives: it would throw
  away a turn already paid for, and the cancel route already stops one
  on the person's word.
- A 409 for a second turn while one is queued: the plan's default is to
  queue (*Decisions*, 4).
- The limit in `actions._validate_turn`: the worker would then fail a
  record already queued under an older limit, and the board service is
  the one creator today. A later creator (the MCP tool, step 6) that
  writes a turn takes the same constant or moves it.
- Checking the account's quota state at the door: it changes before the
  claim, and ADR 173 decision 5 already refuses a parked account then.
## ADR 184 — The chat dock is the main-thread screen, and how it reads a thread's log

**Status:** accepted (T-111, 2026-10-19). Builds `docs/plans/main-thread.md`
step 4 on the routes of ADR 177 and ADR 180.

**Context.** Step 4 is the screen: the harness's threads and their
conversations, read-only, with a switch between the text and the text with
its commands. The console already had a chat dock (`AppShell`'s `ChatPanel`,
the `c` key) drawn over `mockThreads`, a draft with a composer that sent
nowhere. The log is ADR 173's format, served as ADR 180's `{"n", "event"}`
rows: `turn_start` and `turn_end` lines of the worker's around the CLI's
stream-json events, verbatim.

**Decision.**
1. *The screen is the dock.* `components/console/chat-panel.tsx`, not a new
   route: a route would regenerate `routeTree.gen.ts`, and the dock is beside
   every screen, which is where step 5's composer goes. The draft's composer
   and `mockThreads` are gone.
2. *Two reads, through the forward.* `GET /api/threads` and
   `GET /api/threads/<id>/events` (`limit`, `since`) go to the api under its
   bearer, which makes fifteen forwarded routes; any other method on them is
   a 405, an id that does not decode a 404. The list is a query polled every
   `POLL_MS`; the log is `use-thread-events.ts`, which reads `since=0` and
   then `since=<last n>`, appends, follows a full page at once, and keeps
   what it has when a read fails.
3. *The selector.* `main` first, then the open threads, then the archived
   ones marked so, newest first within each; each with its account and
   model. With no `main` the dock says the board service opens it, at step 5.
4. *One history.* Every segment of a thread is one scroll (`lib/thread.ts`,
   `mapThread`). A `turn_start` is the person's message, its `text` and
   `actor`; an `assistant` event's text blocks are the model's. A
   `turn_end` closes the turn with its status; a turn with none is
   *running*, or *ended without a turn_end* when the next `turn_start` comes
   first. A failed turn shows its error, or an error `result`'s.
5. *The switch.* *Text*, the default, is the above. *Text and commands* adds
   each `tool_use` as its name and one short argument (the first of
   `command`, `file_path`, `path`, `pattern`, `url`, `query`, `description`,
   else the input's JSON, clipped to 80 characters), its `tool_result`
   behind a click, marked *error* by `is_error` and *denied* when the turn's
   `result` names it in `permission_denials`. The choice is remembered in
   `localStorage` (`ia-harness.thread-view`), every access in a try/catch.
6. *The separators.* A light one where the account, model or effort of a
   `turn_start` differs from the one before, naming what changed. A heavy
   one, *the model does not remember anything above this line*, where a
   turn resumes no session or another session than the last one known (from
   the previous turn's `turn_end`, else its `result`, else its `init`).
   Neither above the first turn.
7. *The header.* Context is the last `result`'s `usage` (input plus cache
   read plus cache creation) over the largest `modelUsage` context window;
   cost is the sum of `total_cost_usd`.
8. *A row it cannot read is shown, not dropped.* A null `event`, an
   `unparsed` line, or an `assistant` event whose content is not a list is
   an item of its own with its line number, and the history goes on.
9. *Read-only, and it says so.* No composer and no stop button: the dock
   names step 5 and the board service as where they land (`docs/ui.md`, *A
   region with no route says which route, and when*).

**Consequences.**
- `result.usage` sums a turn's calls, so the context figure can overstate
  what the next call carries; it is an upper bound until the stream gives a
  per-call figure the dock can read.
- The mapping is the front's alone; the api still serves the log as written
  (ADR 180), and a new event type shows nothing until `mapThread` learns it.

**Ruled out.**
- A `/threads` route: a regenerated route tree, and a screen the composer
  would have to leave the dock to reach.
- A React Query for the log: its data is replaced whole on each fetch, and
  the log grows from a cursor.
- Dropping unreadable rows: the history would hide a broken log.

## ADR 187 — The thread's one write is `task_start`: a `start` queued inside a running turn whose message names the task

**Status:** accepted (T-113, 2026-10-20). `docs/plans/main-thread.md`
step 6, and `docs/plans/balancer.md` *How a front reaches all this* and
*The conversational thread does not write code*. Builds on ADR 128,
ADR 142, ADR 151, ADR 173 and ADR 181; supersedes nothing.

**Context.** A turn (ADR 173) runs a read-only `claude -p` and passes no
`--mcp-config`, so the thread can neither read the board nor start work
except by asking the person to press *Start development*. The balancer
plan gives it one way to have work done: an MCP tool that writes to the
same action queue the console writes to, so the queue keeps one shape
and the worker stays the only holder of the socket. `docs/charter.md`
C-4 (and its 2026-10-15 amendment) lets the owner's word in the
conversation authorize a run; C-12 *Amended* 2026-10-11 keeps a verdict,
not a message, as what authorizes a run *from the board*. Neither lets
the model start one on its own. The MCP endpoint's `?role=` is not a
credential (ADR 125).

**Decision.**

1. *A role and a tool.* `dispatcher/board_mcp.py` gains
   `THREAD = "thread"` and the row
   `ROLE_TOOLS[THREAD] = ("board_list", "board_get", "task_start")`. No
   other row changes: the operator and the arquitecto do not get
   `task_start`, and the thread gets no other write. The board service
   adds `THREAD` to `KNOWN_ROLES` (it is not a phase role, so
   `dispatcher.dispatcher`'s phase flags never give it to a phase).
2. *The turn is in the URL, not in the arguments.* `board_mcp.mcp_config`
   takes a keyword `turn=None`; given, the query is
   `?role=thread&turn=<action id>`. The worker writes it, so the model
   cannot choose it. The service's `POST /mcp` answers the JSON-RPC
   error it answers a bad query today (400, `INVALID_REQUEST`) when the
   role is `thread` and `turn` is missing or does not match
   `actions.ID_RE`, and when any other role carries `turn`. Every
   request of the thread role carries it, `initialize` and `tools/list`
   included.
3. *`task_start`'s arguments.* `task_id` (string, required) and
   `project` (string, optional), `additionalProperties: false`, checked
   by the existing `_check`. A missing `project` is the thread record's
   `project`; when that is empty too, `actions.create` refuses it
   (`project must be a non-empty string`).
4. *How it respects C-4: what it refuses.* Before it queues anything,
   in this order, each a `ToolError` (an `isError` result the model
   reads, never a JSON-RPC error):
   - no `BOARD_ACTIONS_TOKEN` set in the service: `the action routes are
     off: no BOARD_ACTIONS_TOKEN is set`. The token's absence turns the
     queue's writers off, the tool with them;
   - the `turn` names no action in `claimed/` whose `kind` is `turn`,
     `status` `running` and `actor` `operator`: `no running turn <id>`.
     A dispatch only happens while a turn a person sent is running, and
     a queued, finished or forged turn id starts nothing;
   - the running turn's `params.text` does not name `task_id` as a
     whole token (case-insensitive; the characters on either side are
     not letters, digits, `_` or `-`, so `T-1` does not match `T-10` or
     `T-1-S1`): `task <id> is not named in the owner's message for this
     turn`. The owner's word must name the task; "start the next one"
     is answered by the model naming it and the owner saying so in the
     next message. This is the mechanical half of "never by the model
     on its own": the model cannot start a task the person did not
     name in the message that is running. The other half — that the
     message asked for a start and not, say, a status — is the model's
     reading, as it was the host session's when it ran `dispatch
     run-task` on the user's word, and the tool's description says to
     call it only when the owner's message asks for that task to be
     started.
   - the thread record (from the turn's `params.thread`) is unreadable,
     missing or archived: its message, as `http_turn` words it.
5. *Same queue, same refusals.* Then it calls `actions.create(state_dir,
   "start", project=…, task_id=…, params={"limit": None, "thread":
   <thread id>, "turn": <turn action id>}, actor="thread", note=None,
   ready=<the same Ready check http_start passes>)`. The Ready lambda
   moves into one helper both callers use. `_validate_start`, the
   `_ONE_PER_TASK` 409 and the readiness 409 answer as they answer the
   console; an `ActionRefused` becomes a `ToolError` with its reason, an
   `OSError` one with `the action queue could not be written: <exc>`.
   The tool's result is the created record, as the start route's 201
   body is.
6. *What the queued record carries.* `actor: "thread"` (never
   `operator`), `note: null`, `params.thread` and `params.turn`.
   `actions._validate_start` takes `params` keys `limit`, `thread` and
   `turn`, and ties them to the actor: with `actor == "thread"`,
   `params.thread` must be a non-empty string and `params.turn` an
   action id; with any other actor neither key may be present. The
   worker validates on claim (`actions.claim`), so a record that breaks
   this fails there too. The worker's `_start` is unchanged: it claims
   and runs this record like the console's.
7. *How the console finds the message.* `params.turn` is the id the
   conversation log's `turn_start` line carries as `action` (ADR 173),
   so a queued start links to the message that authorized it; the
   `tool_use`/`tool_result` pair in the same log holds the created
   record's `id` and `task_id`, so the dispatch line links to the
   action and its task (`main-thread.md` *Decisions*, 5). Drawing that
   link is a front task.
8. *The turn gets the server.* `docker_exec.turn_command` takes
   `mcp_config: str | None = None` and puts `--mcp-config <json>` after
   `--permission-prompts none`, ahead of the variadic lists and `-p`.
   `worker._turn`, when `cfg.board_mcp_url` is set, passes
   `board_mcp.mcp_config(cfg.board_mcp_url, THREAD, turn=<the turn's
   action id>)` and `allowed_tools = [*TURN_ALLOWED_TOOLS,
   *board_mcp.allowed_tools(THREAD)]`. With `board_mcp_url: null` it
   passes neither and the argv is today's. `TURN_DISALLOWED_TOOLS` and
   the read-only `Bash` patterns do not change.
9. *No cancel tool.* The thread cannot queue a `cancel`. A start it
   queued is cancelled from the console's queue (ADR 163) or by the
   owner asking and then pressing it there; a cancel tool would be a
   second write to guard for no case the console does not cover.

**Consequences.**
- A turn's model sees `mcp__board__board_list`, `mcp__board__board_get`
  and `mcp__board__task_start`, and can read the board in every turn.
- One message may start several tasks if it names each; each is its own
  start, its own Ready check and its own 409. There is no per-turn cap.
- A start the thread queued spends quota exactly as a console start
  does, with `params.limit` null as the console's is.
- The `turn` query is a binding, not a credential, as `role` is not
  (ADR 125): something on `ia_harness_net` that learns a running turn's
  id can call the tool while that turn runs, still only for a task the
  owner's message names and only with the token set.

**Ruled out.**
- Thread id and turn id as tool arguments: the model would choose them.
- A start that waits for a console confirmation: the owner's word in the
  conversation is a C-4 authorization, and the plan's dispatch is the
  thread's own.
- A new kind (`thread_start`): the worker, the queue screen and the
  cancel path would each need it, and the record is a `start`.
- A parallel creator in the board service: it would skip
  `_validate_start`, `_ONE_PER_TASK` and the lock.

## ADR 190 — The dock's composer and *stop*: a seventh board pair with its own body cap, queued turns read from the action queue, and the dispatch line's links

**Status:** accepted (T-114, 2026-10-20). The console half of
`docs/plans/main-thread.md` step 5, on ADR 181's route and ADR 184's dock;
the dispatch link `main-thread.md` *Decisions*, 5 and ADR 187 decision 7
leave to a front task. Builds on ADR 136, ADR 157, ADR 163, ADR 173, ADR
181, ADR 184 and ADR 187; supersedes nothing. ADR 184 decision 9 (no
composer) is what this one ends.

**Context.** The dock reads a thread's log and says it is read-only. The
board service queues a turn at `POST /api/threads/<id>/turns` (ADR 181)
behind the actions bearer, and a turn is stopped by the cancel route with
its action id. The log has no line for a turn until the worker claims it
and writes `turn_start` (ADR 173, 4), so a queued message is not in the
log at all; it is in the action queue, which `GET /api/actions` serves
whole for `pending` and `claimed`, `params` included. The forward caps a
board write at `MAX_BOARD_BODY_BYTES = 64 KiB`, and the route takes up to
`MAX_TURN_TEXT_CHARS = 20_000` characters, counted by Python's `len`,
which is code points.

**Decision.**

1. *The forward's seventh pair.* `POST /api/threads/<id>/turns` goes to the
   board service's same path, matched in `boardTarget` before the read-only
   check like the six of ADR 136 and ADR 157, with `forwardBoard`'s CSRF
   checks (415, then 403 for `Sec-Fetch-Site`, then 403 for `Origin`) and
   the actions bearer: 503 naming `BOARD_ACTIONS_TOKEN` when it is unset.
   No `CONSOLE_PROJECT` is needed: a turn's project is the thread's (ADR
   181, 1). The id is decoded (an undecodable one is a 404, as for the
   others) and must then be `main` or match
   `th-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}` — `dispatcher/threads.py`'s
   `MAIN_ID` and `_ORDINARY_ID`, written again in TypeScript — or the
   console answers 404 naming the path, and nothing crosses. It is
   re-encoded into the target as the task ids are. Every other method on
   the path keeps its answer: a `GET` is the whitelist's 404, any other
   write the read-only 405. A `POST` with an id the harness does not make,
   which was the read-only 405, is now that 404. The body crosses
   unchanged under the console's `Content-Type`, because the route already
   refuses an unknown key, a missing or blank `text` and a long one with
   its own 400 (ADR 181, 3), and the forward checking them again would be
   a second copy of the route's rules.
2. *The body cap.* The pair is read against `MAX_TURN_BODY_BYTES = 128 *
   1024`, not `MAX_BOARD_BODY_BYTES`, because the largest text the route
   takes does not fit 64 KiB once encoded. The composer sends
   `JSON.stringify({text})`: one code point is at most six bytes there —
   `\u0001`-style escapes for the control characters JSON has no short
   escape for, and a lone surrogate, which `JSON.stringify` writes as
   `\udXXX` and Python decodes as one code point; a quote or a backslash
   is two bytes, any other BMP character at most three, an astral one four
   bytes for one code point. So 20,000 characters is at most 120,000
   bytes, plus the 11 of `{"text":""}`, which is 120,011, under 131,072.
   The other six pairs keep 64 KiB. The console never answers 413 for a
   text the route would take; a text the route would refuse for length is
   its 400, shown in the dock (5). The composer does not count characters
   itself: the route is the one place the limit is enforced, and the dock
   shows its refusal.
3. *What the dock reads.* Besides `/api/threads` and the log (ADR 184), the
   dock reads `actionQueueQuery`, the Board's `["action-queue"]` query
   (`limit=1`, polled every `POLL_MS`), and takes from `pending` and
   `claimed` the records with `kind` `turn` and `params.thread` equal to
   the selected thread's id, oldest `created_at` first.
   - *Queued.* Each such record whose `id` no `turn_start` in the log
     carries as `action` is drawn after the history, as the person's
     message (`params.text`) marked *queued* (pending) or *starting*
     (claimed), each with its own *Cancel*. A record the log already
     shows is not drawn twice; once the worker writes `turn_start`, the
     history draws it as ADR 184 does.
   - *Running.* `mapThread` keeps the `turn_start`'s `action` on the
     turn it opens; a turn still open (*running…*) with an `action` gets a
     *Stop* button on its status line. An open turn without one (a log
     older than ADR 173's format) gets none.
   - *Stopping.* While a `cancel` record in `pending` or `claimed` has
     `params.target` equal to a turn's id, that turn's button reads
     *stopping…* and is disabled. The worker's `turn_end` (`cancelled`) or
     the record leaving the queue ends it; nothing is held in the dock's
     own state.
   - *The worker.* When the queue's `worker.stale` is true, one line above
     the composer says the worker is not running, so a queued message
     waits. A failed queue read is one line saying so, and the composer
     still sends.
4. *Stop is `cancelAction`.* *Stop* and *Cancel* call
   `cancelActionMutation` with the turn's action id: the same
   `POST /api/actions/<id>/cancel` the Queue screen uses (ADR 163), which
   kills a claimed turn and cancels a pending one (ADR 173, 8). No dialog:
   the Queue screen's dialog guards a start, a whole cycle; a turn the
   person is watching is stopped with one click, and the turn above it
   is kept. Both turns can be stopped: each has its own button. A cancel
   the board service refuses (no token, the 503; an unreachable service)
   shows in the dock beside the composer's error, by the same rule as 5. A
   turn that finished before the cancel reached it is not a refusal: the
   cancel is queued and the worker ends it with the target's status (ADR
   142, 7), and the history already shows how the turn ended.
5. *The composer.* Under the history: a textarea and a *Send* button.
   `Ctrl`+`Enter` or `⌘`+`Enter` sends; `Enter` is a new line. *Send* is
   disabled while the text is blank or only whitespace and while a send is
   in flight. It calls a new `sendTurn(threadId, text)` in `client.ts`
   (`readBare`, `POST /api/threads/<id>/turns`, body `{text}`, answering
   the created `ActionRecord`), through a `sendTurnMutation` in
   `queries.ts` whose `onSettled` refetches `["action-queue"]`, so the
   queued message is drawn by 3 as soon as the send settles. On success the
   text is cleared. On a refusal the text stays, and the error is one line
   above the composer: the `ApiError`'s message, which for a board-service
   refusal is the route's own (`text is longer than 20000 characters`,
   `thread … is archived`, the 503 naming `BOARD_ACTIONS_TOKEN`), until
   the next send or a change of thread. A message mid-turn is sent the
   same way and queues (ADR 181, 5); the composer is never disabled
   because a turn runs.
6. *The draft.* The unsent text is kept per thread id in the dock's own
   state, so switching the selector neither loses a draft nor sends it to
   the other thread. It is not written to browser storage: the dock
   closing loses it, which is what closing a box does.
7. *Archived threads.* A thread whose `status` is `archived` gets no
   textarea and no *Send*: one line says the thread is archived and takes
   no new turn. Its queued records, if any, still show with *Cancel*. With
   no thread at all there is no composer either; the no-`main` notice
   says that opening a thread is the board service's `POST /api/threads`
   (ADR 170), which the console does not forward yet.
8. *The dispatch line.* In *Text and commands*, a `tool_use` whose name
   ends in `__task_start` (`mcp__board__task_start`, ADR 187) and whose
   `tool_result` is not an error and parses as a JSON object with string
   `id` and `task_id` is a dispatch: `mapThread` gives the tool item a
   `dispatch: {actionId, taskId}`, and the line shows two links, the task
   (`/tasks/$taskId`) and the action id (`/queue`, which lists it; the
   Queue screen has no per-action anchor). Both are TanStack `Link`s, so
   following one keeps the dock open; a result that does not parse, or an
   error, is the plain tool line ADR 184 draws.
9. *The notice.* ADR 184's read-only banner is removed, and the dock's
   module comment, `client.ts`'s and `forward.ts`'s say what this ADR adds:
   seven board pairs, the turn pair's cap, and the turn's bearer.

**Consequences.**
- A queued message shows only while the action queue answers; a turn the
  worker has started shows from the log either way.
- Two people on two consoles see each other's queued messages: the queue
  is hive-wide, and so is the thread.
- Stopping a turn throws away the text the model was writing; the
  history keeps what the log already has, ended *cancelled*.
- The dock's tests mount `ChatPanel` under a router where a dispatch line
  is drawn, because a `Link` needs one.
- *What seeds a new session* and *Talk about this task*, which the plan
  also puts at step 5, are not built here: both need a write that makes a
  thread or a session, which no route offers to the console. Step 5 is
  marked built for the composer and *stop*, with those two named open.

**Ruled out.**
- Counting characters in the composer against a copy of
  `MAX_TURN_TEXT_CHARS`: a second copy of a limit the route enforces, and
  the cap above is what keeps the console from refusing first.
- Raising `MAX_BOARD_BODY_BYTES` for every pair: the six other writes
  have no text that large.
- Rebuilding the body in the forward, as the start's is (ADR 157): the
  start's body is rebuilt because the project is the console's; a turn's
  body has nothing of the console's to add.
- A new api route for a thread's queued turns: the action queue already
  serves them.
- Drawing a sent message from the mutation's answer until the queue
  refetches: the refetch is awaited before the send settles, so there is
  no gap to fill.
- Storing the draft in `localStorage`: allowed, but nothing asks for it.

## ADR 193 — The Debt screen points an open entry at a task in play whose Surface its where falls on

**Status:** accepted (2026-10-20). The two items T-096 (ADR 141) left
pending on the Debt screen, built by a subagent of the operator's session
under `CLAUDE.local.md`'s quota rule, not by a dispatched cycle, so there is
no `docs/implementations/` record. Builds on ADR 139 and ADR 141; supersedes
nothing.

**Context.** `docs/plans/board-service.md` section 5 asks the Debt screen to
say when an open entry's *where* falls on the surface of a task already in
play, so the person can fold the debt into that task instead of opening a
new one, and to let the filter find an entry by its backlog item and its
state. ADR 141 linked each open entry to its item and left both out. The
data is already on the screen: `Task.description` holds the *Surface*
section (ADR 139), and the *where* cells carry backticked paths.

**Decision.**
1. `front/src/lib/debt.ts` mirrors `dispatcher/surface.py`: a literal entry
   holds itself and what is under it, by path segment; a glob matches as
   globs do, and a static prefix between globs holds what is under it.
   `APPEND_ONLY`, the not-a-path rule and the extension rule are copies of
   that module's.
2. A task is in play when it is accepted and not merged. A task with an
   unknown status, a description whose *Surface* will not parse, or a
   surface with no path is left out.
3. A *where* cell is read from its backticked tokens, each cut at the first
   `:` (a line number). A cell with no backticks is one token, as
   `_unwrap_code` reads it. Prose is not read.
4. Two departures from `surface.py`: a surface with no path matches
   nothing here (there it overlaps everything, which would point every
   entry at it), and `APPEND_ONLY` paths are set aside on both sides, since
   two tasks appending to the same log do not share a surface.
5. The entry's own backlog item is not pointed at.
6. The pointer sits under the item cell (`data-testid=in-play-<id>`) and
   links each task.
7. The filter matches the item id and the state words, *not imported*
   included, once the tasks have loaded; before that it matches what it
   matched under ADR 141.

**Consequences.**
- The screen still writes nothing; folding an entry into a task stays the
  person's call.
- A *where* written in prose is a false negative. The mock fixtures have
  none.
- The mirror has to follow any change to `surface.py`.

**Ruled out.**
- A surface with no path meaning the whole repo: every open entry would
  point at it.
- Reading paths out of prose: guesses, and false positives the person
  would learn to ignore.
- A backend field for the overlap: the screen has what it needs, and the
  board-service would compute it for one screen.

## ADR 194 — What seeds a new session of a thread

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/main-thread.md` left open which durable state a fresh
segment of a thread is given at its start, and how big it may be
(`token-economy.md`). A session is fresh only on a thread's first turn, or
when no session id was recorded; every later turn resumes it. The thread's
board read tools reach the card and the board, not the handoff trail in the
task file or the note the person gave when starting the task.

**Decision.**
1. A turn whose segment has no `session_id` builds a seed
   (`worker._session_seed`); a resumed turn builds none.
2. A thread on a task gets, in this order, the task's description, the note
   of the latest `start` of that task that had one, and the `## <role>`
   handoff trail of its task file. A missing or empty part is left out.
3. A thread on no task, `main` included, gets no seed.
4. The budget is `TURN_SEED_MAX_CHARS` (12,000 characters), header and footer
   included. The description and the note are cut from the end, the trail
   from the start so the latest phase stays; every cut is marked with
   `TURN_SEED_CUT`, and a part with no room left is dropped.
5. The seed goes in front of the first message, between an `[ia-harness: ...]`
   header and footer, rather than as `--append-system-prompt`: in the message
   it is part of the conversation the session resumes, and nothing depends on
   whether the CLI keeps a system-prompt flag across `--resume` (not tested).
6. `turn_start.seed` is null or `{text, chars, truncated, parts, unread}`;
   `turn_start.text` stays the person's own text, so older lines still read.
7. A part that cannot be read is logged with a warning and named in `unread`;
   the turn still runs. When no part is left, the seed is null and the
   warning is the only record.

**Consequences.**
- A seed costs about 3k tokens at most, once per session.
- The log shows exactly what the model was told.
- A new session after compaction gets a seed with no extra code.
- On a long trail the oldest phases drop first.

**Ruled out.**
- `--append-system-prompt`: see 5.
- Re-sending the seed on every turn: the resumed session already has it.
- The `handoffs/<role>.json` payloads: the trail in the task file is what
  the phases wrote for each other.
- Reading the note from `comments.jsonl`: the `start` record is where it is
  given.
- A pointer to the board tools for a thread on no task: they describe
  themselves.
- Failing the turn on an unreadable part: the seed is a convenience.
- Logging only a size or a hash: it would not show what the model was told.

## ADR 195 — The dock creates, renames, re-picks and archives threads; the task page talks about a task

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** The dock could read threads, send turns and stop them (ADR 184,
190), but nothing in the console opened, renamed, re-picked or archived a
thread, and `docs/plans/main-thread.md` step 5's *Talk about this task* was not
built. The board service already has `POST /api/threads` (201 with the record)
and `PATCH /api/threads/<id>` (200), with no bearer (ADR 170): the title is
required and at most 200 characters, `task` must name a task file that exists,
`main: true` makes the id `main`, `main` cannot be archived, and account, model
and effort fall back to the primary account, its default thread model and
`default_effort`. The front holds no list of models per account.

**Decision.**
1. The forward carries `POST /api/threads` and `PATCH /api/threads/<id>` to the
   board service, matched by method and path before the read-only check, with
   the CSRF checks of the other board writes and no bearer. The `PATCH` id is
   checked against `THREAD_ID` as a turn's is; `GET /api/threads` stays the
   api's list. `forward.test.ts` covers both pairs and their refusals.
2. `client.ts` gains `createThread` and `updateThread`, bare like `sendTurn`;
   `queries.ts` gains their mutations, which put the answered record into the
   cached `["threads"]` list so the dock can select a new thread before the
   refetch, and refetch the list either way.
3. *New thread* in the dock takes a title, an optional account (from
   `/api/accounts`) and an optional effort; *the main thread* is offered only
   when no `main` exists. The model is the account's default.
4. *Edit* changes the title, model and effort and sends only the keys that
   changed; the model is typed, since the front has no list of models.
5. *Archive* and *Reopen* send the status. `main` has neither. Archived threads
   stay listed and marked.
6. A refused create or edit shows in its form and keeps what was typed; a
   refused archive or reopen shows by the composer.
7. `openThread(id)` (`hooks/use-console.ts`) sends `OPEN_THREAD_EVENT`, a window
   event like `FOCUS_SEARCH_EVENT`; `AppShell` opens the dock and passes
   `select={threadId, n}`, where `n` counts the asks so the same thread can be
   selected again.
8. The task page's *Talk about this task* creates a thread with `task` set to
   the task's id, titled `"<id>: <title>"` cut to 200 characters (the id alone
   when there is no title), and opens the dock on it. A refusal shows next to
   the button. What the new session is first told is the worker's (ADR 194).
9. `EFFORT_LEVELS` moves to `types.ts`, shared by the Models screen and the
   dock.

**Consequences.**
- A thread's lifecycle is handled in the console, and any screen can open the
  dock on a thread with one call.
- Each click of *Talk about this task* makes a new thread; an open one on the
  same task is not reused.
- A mistyped model is caught only by the board service's refusal.
- Changing an existing thread's account still needs another client.
- The test helper `render-route.tsx` answers `{bare, status}` for writes whose
  answer has no envelope.

**Ruled out.**
- A React context or store to reach the dock: the window event needs no
  provider and follows `FOCUS_SEARCH_EVENT`.
- A model list in the front: it would drift from the account settings the
  board service reads.
- Changing the account in *Edit*: kept to title, model and effort.
- Waiting for the refetch before selecting a new thread: the selector would sit
  on the wrong thread for one poll.

## ADR 196 — *Talk about this task* reopens the open thread on the task

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 195 built *Talk about this task* on the task page: each click
posts `POST /api/threads` with `task` set and opens the dock on the answer. Its
consequences said an open thread on the same task is not reused, so every click
left one more thread on the task in the dock's selector. The thread list the dock
reads, `GET /api/threads` (`threadsQuery`), already carries each thread's `task`,
`status` and `created`.

**Decision.**
1. The button reads `threadsQuery`. When the list has a thread whose `task` is
   this task and whose status is not `archived`, a click opens the dock on it
   with `openThread` and posts nothing; with several, the most recently
   `created`. Otherwise it creates as ADR 195 decision 8 says.
2. While the list is first read the button is disabled, so a click in that
   moment cannot make a duplicate. When the read failed it falls back to
   creating, so a broken list does not take the button away.
3. An archived thread on the task is ignored: it is not offered for reopening,
   and a click makes a new thread. The dock's *Reopen* (ADR 195 decision 5)
   still reaches it.
4. The match is on `task` alone, as the list is served; the thread's project is
   not compared.

**Consequences.**
- This amends ADR 195's consequence: a click reuses the open thread on the task,
  and a second open thread on it is made only from another client.
- Archiving a task's thread is how to start it afresh from the task page.
- A thread opened elsewhere since the list's last poll is not seen until the
  next one, so a click in that window can still create a second thread.
- While the list's first read retries (the query client's default retries), the
  button stays disabled for as long.

**Ruled out.**
- Offering to reopen an archived thread: a second control by the button for a
  case the dock already covers.
- Creating while the list is still loading: the duplicate this ADR exists to
  stop.
- Waiting on a failed list: the button would stay dead until the api answered.
- A board-service lookup of a task's thread: the list the dock polls already
  holds what the choice needs.
## ADR 197 — The dock's execution thread: a started task's cycle, read only

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/main-thread.md` step 7 (*Decisions* 8) asks the dock
for an execution thread: under the conversation threads, one entry per started
task in play, showing the task's cycle in one scroll. A phase runs
`claude -p --output-format json`, so it leaves no stream log as a thread turn
does; the hook events the collector stores are the only record of what a phase
did between its start and its handoff. `/api/events` cannot serve them: it
answers the newest rows first and filters by nothing a task owns. The rest is
already served: the task and its body (the phases' `## role` sections and their
`**Dispatcher gates**` lines), `/api/tasks/<id>/usage` (each phase's role,
round, account, model and session ids), `/api/actions` (the `start` records
and their notes) and `/api/threads`. The slots (projects.md *Decisions* 6) are
not built here.

**Decision.**
1. One read-only route, `GET /api/tasks/<id>/events?since=&limit=`, in
   `observability/api/app.py`: the hook events whose payload's `cwd` sits under
   `/worktrees/<id>/`, oldest first, after event `since`, at most 500 by
   default and 1000 at most (a larger `limit` is clamped with a warning). An id
   that is not a bare filename, or names no task file, is a 404; no events
   database is `[]` and a warning. The forward carries it like the other task
   reads. `useTaskEvents` walks it from a cursor on `useThreadEvents`'s model.
2. A task is in play while it is not merged, not dropped and not an epic, and
   it runs a phase, is `in_progress`, `blocked`, `conflict` or `done` (done and
   not yet merged), or has a `start` pending or claimed in the queue. A plain
   `pending`, a backlog status and a `start` already finished do not count.
3. The selector lists them in an `Executions` group under the threads, the
   running ones first and then in board order, each as
   `<id> · <title> — running <phase>` or `— <status>`. An entry leaves when the
   board says the task is merged or dropped; a chosen one that leaves gives the
   dock back to the first thread.
4. Choosing one shows, oldest first: the start note; then each phase as a
   segment with its role, round, account and model; its events under the
   thread's *Text* / *Text and commands* switch (prompts, notifications and a
   `Stop`'s last message under *Text*; the paired tool calls and the session
   markers added under the other); its handoff, rendered as markdown; and its
   gates as chips. The segments are the body's sections in order, a usage group
   no section took placed by its time, and the running phase last. An event goes
   to the phase whose session it carries, else to the phase whose time window
   holds it, else to the running phase, else to a closing *unattributed*
   segment.
5. The start note is read from the queue's records only, the latest `start` of
   the task with a note among the pending, claimed and the last 20 done. When
   none is there the view says so and points at the task's comments.
6. The view is read only: no composer. Its header links to the task page and,
   when a thread is about the task (`task` is its id, not archived), offers
   *Open its thread*, which shows it in the dock as `openThread` does. Talking
   about a task stays *Talk about this task* (ADR 195).
7. Nothing here spends quota: every read is a GET on what the collector and the
   board already hold.

**Consequences.**
- A phase's activity can be followed from the dock while it runs, with no new
  writer and no change to how phases are run.
- The attribution is a heuristic: a phase whose usage was not recorded keeps
  its events only by time, and a hook payload with no `cwd` is not found at all.
- A phase's own words appear only where a hook carried them (the prompt, a
  `Stop`'s `last_assistant_message`); its intermediate assistant text is not in
  the hook events.
- A start note older than the queue's last 20 done records is not shown here.
- The dock polls the task's events while an execution is shown, one cursor read
  per poll.

**Ruled out.**
- A stream log for phases (`--output-format stream-json`): it changes how
  every phase runs and is the dispatcher's, not a view's.
- Filtering `/api/events` in the front: it answers newest first and would have
  to read the whole table to find a task's rows.
- One combined route answering the task, its usage, its notes and its events:
  each part is already served, and the new route stays one read.
- Reading the start note from the task's comments: the `start` record is where
  it is given, and the comments are on the task page.
- A composer on the execution: the phases are not talked to; a thread about the
  task is.

## ADR 198 — The slots: one derivation in `front/src/lib/slots.ts`, drawn in the dock and in the *Start development* confirmation

**Status:** accepted (2026-10-20). Designed by T-115's arquitecto.

**Context.** `docs/plans/projects.md` *Decisions*, 6 (build step 6) and
`docs/plans/main-thread.md` *Decisions*, 8 (the second half of step 7) ask the
console to show how many phases can run at once and why: one slot per account,
what is on each, and which started tasks wait and on what. They name two places:
the dock's selector, and the *Start development* confirmation. Every fact is
already served. `GET /api/accounts` gives each account's `state`,
`current_task` (`current_task_id` in the console, ADR 17), `rate_limited_at`,
`quota_cooldown_seconds`, `last_probe`, `is_primary` and `reserve_pct`.
`GET /api/tasks` gives each task's `phase` (ADR 126). `GET /api/actions` gives
the `start` records, pending or claimed, with their `project` and the worker's
`waiting` word (ADR 142, 160). Execution threads (*Decisions*, 8, first half)
are not in this base: the dock's selector lists thread records only. ADR 197 is
left unused.

**Decision.**
1. **One pure module, `front/src/lib/slots.ts`.**
   `deriveSlots(accounts, tasks, queue)` takes the three reads' `data`:
   `accounts` is `Account[]`, `tasks` is `Task[] | null` (`null` when that
   read failed or has not answered), and `queue` is `unknown`, guarded as
   `front/src/lib/thread.ts` guards it. It answers a `SlotsView`,
   `{ slots, free, waiting, started }`, where `waiting` (the list of 3) and
   `started` (the count of 6) are `null` when `queue` is not a queue object.
   `standingLine(view: SlotsView | "loading" | "accounts-failed")` builds the
   confirmation's sentence; the caller decides the two string arms from the
   accounts read. Both places import these two functions, and no component
   re-derives a slot. Tests are in `front/src/lib/slots.test.ts`.
2. **A slot per account, never more.** The order is the pool's: workers first,
   then the primary, then by name. An account with `max_concurrent_phases`
   above 1 is still one slot, because the api serves one `current_task` per
   account.
   - **Open** when `state` is `IDLE` or `BUSY`. The state is the
     dispatcher's judgement of "under its threshold", so it is read and never
     re-decided from `last_probe`. `IDLE` is *empty*. `BUSY` carries its task:
     `current_task_id`, the task row's `phase` (the role) and the project of
     the claimed `start` record for that task. Each one missing is its own
     `Absent` mark: "task not recorded", "phase not served", "project not
     recorded".
   - **Closed** otherwise, with what closed it and its reset:
     - `COOLING_DOWN` is *refused by the provider*. Its reset is
       `rate_limited_at + quota_cooldown_seconds`, epoch seconds, drawn with
       `formatClock`. It is `null` when `rate_limited_at` is.
     - `PRE_COOLDOWN` is *parked over its ceiling*. Its reset is the raw
       `last_probe` clause (`session_reset` and/or `week_reset`) of each
       window at or over its ceiling, by the pool screen's `>=` test, shown
       verbatim and never parsed. With none, it is absent: "no reset
       recorded".
     - `AUTH_EXPIRED` is *login expired*. It has no reset, because it needs the
       operator (ADR 76).
     - `state: null` is *state unreadable*, with no reset.
   - **The primary** is marked on its slot as
     `primary · <reserve_pct>% reserve`. The slot says a phase there spends
     the conversation's quota. It is open or closed by the same rules.
   - `free` counts the open, empty slots.
3. **What waits.** These are the `start` records, with a claimed one winning
   over a pending one for the same task, as on the Board (ADR 160), in served
   order with claimed records first. A task that an open slot carries is in a
   phase and is not listed. Every other task is listed with one reason:
   - **Waiting for an account**: a pending record whose `waiting` is
     `"account"`. The worker's word is the fact, so this is not re-derived from
     the slots. It carries the *soonest reset*, the earliest epoch reset among
     the closed slots. With no closed slot it reads "every slot is full", and
     with closed slots but no epoch reset it reads "no reset time to compare".
   - **Between phases**: a claimed record whose task row has `phase: null`.
   - **Queued**: any other pending record. The worker's `waiting` word is shown
     verbatim when it is set (`project` today, ADR 142's serial key).
   - **In `<phase>`, on no account**: a claimed record whose task has a
     `phase` that no slot carries, such as a stale lock or an unreadable
     account row.
   - **Unknown**: a claimed record whose task row is missing, or `/api/tasks`
     failed.
4. ***Waiting for the project* is left out** as a named reason, because
   `docs/plans/projects.md` step 3 is not built. It is added in `deriveSlots` as one more
   reason kind. A pending record with `waiting: "project"` maps to it, and so
   does a pending start whose project equals a claimed start's project, once
   step 1 puts `project` on the task row. Then `standingLine` counts only the
   starts of the same project and names them "of this project". Until then that
   record shows as *Queued*, with the worker's word.
5. **The dock.** A *Slots* region, `SlotsPanel` in
   `front/src/components/console/slots.tsx`, sits directly under the dock's
   selector row, above the forms and the thread header. It has one line per
   slot, then the waiting list, which is omitted when empty. It reads
   `accountsQuery`, `tasksQuery` and `actionQueueQuery` (already polled, shared
   keys). Its states are:
   - "Reading the slots…" until accounts answers.
   - "/api/accounts did not answer, so the slots are not known" when that read
     fails. Nothing else is drawn.
   - "No account is configured" with no accounts.
   - With the queue failed, the slots are drawn and "/api/actions did not
     answer, so what waits is not known" replaces the list.
   - With tasks failed, the phase marks read "phase not known".
   The region is small (two accounts, two lines) and is not collapsible. When
   execution threads land in the selector, the region stays above them.
6. **The confirmation.** The *Start development* dialog (`StartControl` in
   `front/src/routes/index.tsx`) draws `standingLine` as one paragraph between
   the description and the note. The sentence is `<n> account(s), <f> free`,
   adding `, the primary among them` when a free slot is the primary, then `; `
   and one of:
   - `this task would queue behind <k> started task(s)` when `k`, the pending
     and claimed `start` records, is above 0. The worker serializes starts per
     project and this console sends one project (ADR 157).
   - `this task would start now` when `f` is above 0.
   - `this task would wait for an account`.
   With no account the sentence is "No account is configured; no slot can take
   this task". With accounts failed it is "/api/accounts did not answer, so the
   free slots are not known". With the queue failed it is
   `<n> accounts, <f> free; /api/actions did not answer, so where this task
   would stand is not known`. While loading it is "Reading the slots…". It never
   names a time. The Board already reads all three queries.
7. **Tests.** `front/src/lib/slots.test.ts` covers an open empty slot, an
   open busy slot with phase and project, a closed cooling slot with its epoch
   reset, a parked slot with its clause, the primary with its reserve,
   *Waiting for an account* with the soonest reset and with none, *Between
   phases*, *Queued* with `project`, and every `standingLine` arm. A component test of the dock checks
   `SlotsPanel` drawn from stubbed reads: slots, the waiting list and the
   accounts-failed state. One in `front/src/routes/-index.test.tsx` checks the
   confirmation's line.
8. **Plans.** `docs/plans/projects.md` step 6 and `docs/plans/main-thread.md`
   step 7 are marked built by T-115 and ADR 198, step 7's second half by name. Both say
   *Waiting for the project* is open, as 4 says. This is done when the code
   lands, not before.

**Consequences.**
- A start the worker holds behind another start of its project reads
  *Queued — project*, not a named reason, until step 3.
- `standingLine`'s count is conservative: it counts every started task,
  whatever its project, since the browser does not know the console's project.
- A parked slot's reset is the CLI's clause and is not compared, so *Waiting for
  an account*'s soonest reset only weighs provider cooldowns.
- A `BUSY` account holding two phases shows one.

**Ruled out.**
- A backend route for slots: every fact is served (task constraint).
- Re-deciding "under its threshold" from `last_probe`: the state already says
  it, and two judgements could disagree.
- Parsing the probe's reset clauses: a guessed time.
- Deriving *Waiting for an account* from the slots: the worker's `waiting` is
  what holds the start.


## ADR 199 — The project record, and `project` on every card

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/projects.md` step 1. A project was a directory under
`projects_root` and nothing more: its settings were `config.yaml`'s, the same
for every project, and a task did not say which project it belonged to (the run
named it). The plan's *Decisions* 1 asks for a record per project, and its open
question left where the record lives to this step.

**Decision.**
1. The record is `<hive>/projects/<slug>.json`, in the hive beside `tasks/`:
   the volume the dispatcher, the board service and the api already mount (the
   dispatcher does not mount the projects). It holds `slug`, `title`, `remote`
   in SSH form (or null), `base_branch`, `key` (the deploy key's fingerprint and
   created time, never the key), `settings`, `created` and `status`
   (`active` or `archived`).
2. `dispatcher/projects.py` is the one reader, validator and writer. The record
   is strict: an unknown key, a bad slug, a remote not in SSH form or a setting
   of the wrong type is a named `RecordError`. The writer never writes over a
   record unless asked to replace it (it links a temporary file into place, so
   two writers cannot both win). The board service is its writer on a person's
   act once its routes exist (steps 4 and 5); until then only `migrate` writes.
3. The settings a record may carry are `allowed_tools`, `merge_on_done`,
   `gates_enabled`, `gates_test_timeout_seconds` and `push_task_branch`. Each
   one the record names wins over `config.yaml`'s; each it leaves out is
   `config.yaml`'s. A project with no record runs on `config.yaml` alone, so a
   harness with one hand-configured project runs unchanged.
4. `push_task_branch` is recorded and validated, and read by nothing: the
   task-branch push it governs is not built.
5. A record that does not parse blocks the project's work: `run_task_cycle`,
   the saved split and `run_single_phase` log `task <id>: project <slug>: ...`
   and do not start, and the pre-merge check refuses with the same words. They
   do not fall back to `config.yaml`.
6. Tasks, epics and backlog items carry `project`, a slug, in their task file.
   A card with none belongs to the harness's one pre-existing project.
   `board_list` and the api's task rows serve it, null when unset. A split's
   subtasks inherit their parent's.
7. `dispatch migrate` gains a second half, idempotent like the first: it lists
   the clone directories under `projects_root` (through an agent container,
   which mounts them), writes a record for each that has none (title the slug,
   remote and base branch read from the clone, empty settings, the key's
   fingerprint when one is on file), never writes over one that exists, and
   stamps `project` on the cards that name none. The stamp is `--project SLUG`,
   or the one record when there is exactly one; with several and no
   `--project` it stamps nothing and exits 1. A card whose cycle is in flight is
   refused, as the first half refuses it. A second run prints `nothing to
   migrate`.

**Consequences.**
- The plan's open question *Where the record lives* is settled: the hive.
- Settings differ per project from here on, but only by editing a record by
  hand until the console writes them.
- A broken record stops one project, named in the log, instead of running it on
  settings nobody chose for it. The task is not marked blocked: the cycle does
  not open.
- `migrate` needs an agent container to list the clones. With no account
  configured, or a listing that fails, it writes no record, still stamps against
  the records already there, and exits 1.
- The front does not show `project` yet; the console and the sidebar are later
  steps, as is one cycle per project.

**Ruled out.**
- The record in the clone: it would travel with the repository, but a phase
  could write it.
- Guessing the default project when there are several clones: a card stamped
  with the wrong project is worse than one stamped later.
- Falling back to `config.yaml` on a broken record: the record says this
  project's settings differ.
- Ignoring unknown keys in the record: a typo would be a setting that silently
  does nothing.

## ADR 203 — The board service writes the project record

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 199 left a gap: its decision 2 names the board service as the
record's writer "once its routes exist", and its consequences say settings
differ per project "only by editing a record by hand until the console writes
them". Until now a record was made by `dispatch migrate` and changed by hand.
`docs/plans/projects.md` step 1 asks for the record; its *Decisions* 2 makes
the deploy key `project-keygen`'s, and *Decisions* 4 puts archived projects
behind a toggle, which is the record's `status`.

**Decision.**
1. Two routes on the board service. `POST /api/projects` creates a record and
   answers 201 with it. `PATCH /api/projects/<slug>` changes one and answers
   200 with the record as written. There is no read route: reads are the api's
   side, and no delete: a project leaves the console by being archived.
2. A create takes `slug` (required), `title` (default the slug, as `migrate`
   writes it), `remote` (SSH form or null), `base_branch` (default null) and
   `settings` (default none, so `config.yaml` decides each). `created` is the
   writer's clock, `status` is `active` and `key` is null. A slug with a record
   file, readable or not, is refused 409: the writer links the record into
   place, so two creates of one slug cannot both win.
3. A change takes `title`, `remote`, `base_branch`, `settings` and `status`
   (`active` or `archived`). `settings` merges into the record's: a setting
   named with a value is set, one named with null is dropped, so it is
   `config.yaml`'s again, and one not named is kept. A slug with no record is
   404; a record on disk that does not read is 409 and is not rewritten, since
   rewriting it would mean guessing what it said. An empty body is 400. The
   read, the merge and the write are under an `flock` on
   `.hive/projects/.<slug>.lock`, so two changes cannot lose one; the lock file
   is dotted and not `.json`, so `slugs()` never lists it.
4. `slug`, `key` and `created` are not settable: the slug is the record's file
   name (a project is not renamed), the key is `project-keygen`'s and the
   record only ever holds its fingerprint (*Decisions* 2), and `created` is when
   the record was written. Each is refused 400 with its reason, so no secret
   and no key reaches the record through these routes.
5. One check. `dispatcher/projects.py` gains `create` and `change`, additive
   beside `migrate`; both build the record's whole JSON object and hold it to
   `parse`, the check `read` uses, then write through `write` (a temporary file
   linked or renamed into place). What the route accepts is what the
   dispatcher reads, and a refusal is the reader's own words: "its remote
   'https://…' is not in SSH form (git@host:owner/repo.git)", "it has fields no
   record has: owner", "its merge_on_done is 'yes', not true or false".
6. Behind the `BOARD_ACTIONS_TOKEN` bearer, with the action routes' guards in
   order (ADR 151): a browser `Origin` is 403, no token configured is 503 ("the
   project routes are off"), a missing or wrong bearer is 401, a query
   parameter or a body that is not an object is 400. A record decides a
   project's `allowed_tools` and `merge_on_done`, so a phase on the network
   must not be able to write one, which the CSRF checks alone of the task
   writes would allow. The guards are one helper the action routes now share.
7. No MCP tool. No role in the plan writes a record: these are a person's
   writes, from the console or a shell with the token.

**Consequences.**
- ADR 199's gap is closed on the board service's side: a record is created and
  its settings changed without `dispatch migrate` or a hand edit.
- The console cannot call them yet: its proxy adds the bearer only to the
  method and path pairs it names, and these are not among them. Wiring them
  and the form is a front task (step 5 of the plan).
- A record is written whether or not its clone exists. The board service does
  not mount the clones; the `clone` action of step 5 is the worker's.
- The record's `remote` check is the reader's, unchanged: it is not loosened
  to accept an HTTPS URL, and not tightened here either.

**Ruled out.**
- A second validator in the route: two checks drift, and a record the route
  accepted could then stop its project at the dispatcher.
- Turning an HTTPS remote into SSH form on the way in: `migrate` does that for
  what it reads from a clone, but a person's input that is wrong is refused
  with the reason, not rewritten.
- A `PUT` of the whole record: it would make the client send `key` and
  `created`, which it does not own.
- Setting `status` on create: a new project is active; archiving one that never
  ran is a change like any other.
## ADR 204 — *Waiting for the project* in the slots, and the project on every card

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/projects.md` step 6 left *Waiting for the project*
open (ADR 198): the worker already holds a start back with `waiting:
"project"` while a start of the same project runs (`dispatcher/worker.py`,
the serial key `project:<slug>`), but the console drew it as *Queued —
project*, and it could not name the task holding the project because the task
rows did not carry `project`. ADR 199 put `project` on the api's task rows,
null when a card names none. ADR 198, decision 4, said how the reason is added.

**Decision.**
1. The front's `Task` row type carries `project: string | null`, what the api
   serves (ADR 199). Nothing else is added to the type.
2. A card on the board, a row in the backlog and the task page's field panel
   show the slug, small and secondary, through one primitive, `ProjectMark`
   in `front/src/components/console/primitives.tsx`. A null project draws the
   absent mark *no project*; a value that is not a string draws the malformed
   mark. Neither is replaced by a guessed project.
3. `deriveSlots` (`front/src/lib/slots.ts`) gains one reason kind,
   `"project"`, for a pending start whose `waiting` is `"project"`. Its line is
   *Waiting for the project*, with the detail *behind T-…* naming the first
   claimed start of the same project when the queue shows one, and no detail
   otherwise. The dock's slots panel draws it as it draws the other reasons.
4. `standingLine` takes the task's project. When the queue holds starts of
   that project, the *Start development* confirmation says *this task would
   queue behind N task(s) of this project* (`projects.md`, *Decisions*, 6);
   otherwise it counts every start, as before.

**Consequences.**
- A start the worker holds for its project reads as such in the dock, and the
  operator sees which task it waits behind.
- The reason comes from the worker's word alone; the front only adds the
  holder's name from the queue it already reads.
- No time is shown for when the project frees: none is known.
- The sidebar and the `/p/$slug/` routes (step 4) and the worker's rule
  itself (step 3) are not part of this.

**Ruled out.**
- Deriving *Waiting for the project* from two starts sharing a project: the
  worker decides what holds a start back, and a guess would contradict it.
- Guessing when the project frees, or a time for the holder's cycle.
- A backlog column for the project: the slug sits beside the title, so the
  columns and their widths stay as they are.
## ADR 200 — One cycle per project: a `flock` per project, taken in `open_cycle`

**Status:** accepted (2026-10-20), T-116.

**Context.** `docs/plans/projects.md` step 3 and *Decisions*, 5. The worker
serialised its own starts on the serial key `project:<slug>`
(`dispatcher/actions.py` `KINDS`, ADR 148) and wrote `waiting: "project"` on
one it held back, but the key lived in the worker's memory. A `run-task`,
`run-phase`, `command-verdict`, `command-resume` or `run-queue` on the same
project saw none of it, so two of them ran two cycles on one clone.

**Decision.**
1. **The lock.** `dispatcher/project_lock.py`: an exclusive, non-blocking
   `flock` on `<state_dir>/project-locks/<slug>.lock`, held by an open file
   descriptor for as long as a cycle runs. A process that dies closes the
   descriptor and the kernel gives the lock back; nothing reaps it, and the
   file's presence means nothing. The holder writes one JSON line into the
   file once it holds the lock (`task_id`, `via`, `pid`, `host`, `since`) and
   empties it before letting go; it is read only to name the holder to a
   start that was refused, never to decide whether the project is busy. A
   refused take raises `ProjectBusy`, carrying that record (or none, if the
   holder has not written it yet).
2. **Where it is taken.** In `open_cycle`, after every refusal it already
   makes and before it writes the description: the one function the four
   cycle entry points (`run_task_cycle`, `run_single_phase`,
   `run_command_request`, `resume_command_verdict`) pass through. It is
   released in `close_cycle`, whichever way the cycle ended, including the
   early return for a foreign task lock. A caller that already holds the
   project's lock passes it to `open_cycle` (`project_lock=`), and then the
   cycle neither takes nor releases it.
3. **What "running" means.** The project is busy from the cycle's opening to
   its end: through every phase and every revision round inside the cycle,
   and the auditor's merge when there is one. The task is then *Awaiting
   review* (or blocked, held, split) and the project is free. A later cycle of
   the same task — the request-changes round of `review.md` once it is built,
   a `run-phase --round`, a verdict's resume — comes through `open_cycle`
   again and takes the lock again.
4. **The worker** takes the lock itself, in `Worker._consider`, for a kind
   whose serial key is `project:<slug>` (`start`, `command_verdict`): after
   its in-memory key check and before its account check. Busy, the action is
   left pending as `waiting: "project"`, as today, and taken on a later pass
   once the project frees; an account then closed releases the lock and waits
   as `account`; a claim that loses its race releases it. The held lock goes
   with the action to its handler thread, which passes it to the cycle and
   releases it when the handler returns. The start on a project another
   process holds therefore waits exactly like one behind the worker's own.
5. **A CLI start on a busy project exits.** `run-task`, `run-phase`,
   `command-verdict` and `command-resume` print one line on stderr,
   `project <slug> is busy: task <id> holds it (<via>, pid <pid>, since
   <since>); <task> was not started`, and exit `EXIT_HELD` (75, "try again
   later"). Nothing was written for the refused task. Exiting rather than
   waiting, because a verb typed for one task now that sits silent behind an
   unknown cycle for an hour looks like a hang, the console cannot see or
   cancel it, and the worker already is the place where a start waits.
   `command-verdict` refuses before it records the verdict, so the verdict
   can be given again.
6. **`run-queue` waits, and holds the project for its whole run.** A queue is
   already a thing that waits (for accounts, for merges). `queue.run` takes
   its project's lock once before its first cycle, polling every
   `WAIT_POLL_SECONDS` with one log line naming the holder while it is busy,
   passes the held lock to every cycle it starts, and releases it when it
   returns. That is one rule for `--parallel 1` and above.
7. **How it meets ADR 83.** Under `--parallel N` above 1 the queue's N
   cycles share the one lock the queue took, so the lock neither serialises
   them nor deadlocks them: none of them takes it. Within the queue, ADR 83's
   write windows per surface still decide which cycles write at once. Toward
   everything outside the queue, the project is busy while the queue runs:
   the parallel queue is the one sanctioned case of more than one cycle on a
   project, and only with its own scheduler.
8. **Two projects** take two different files and run side by side, in one
   process (two worker threads) or in two.
9. **The epic, one task at a time.** *Start epic* (`board-service.md`,
   *Per epic*) is not built. When it is, this rule is what makes it run one
   task of the epic at a time: every start it queues names the epic's
   project, so each one after the first waits as `project` until the cycle
   before it ends with its task awaiting review. *Start epic* needs no lock
   of its own.

**Consequences.**
- The worker's in-memory `project:<slug>` key stays: it is what keeps a
  second start of one project out of the worker's own `running` without a
  `flock` on every pass, and the `flock` is what covers every other process.
- A cycle opened by a library caller with no lock passed takes its own, so no
  entry point can skip the rule by calling the cycle functions directly.
- `flock` holds across the containers that mount the state directory from one
  host's file system. A state directory on NFS would not hold it.
- A queue holds its project between its cycles too, so a worker start on that
  project waits for the whole queue, not only its running cycle.
- The project lock comes before every other lock: it is taken before any
  phase and never inside one, and never waited on in the kernel, so no path
  waits on it while holding the task, base or surface lock.

**Ruled out.**
- A file whose presence means busy, or a heartbeat lock like the base lock
  (ADR 80): a crash leaves it behind and something has to reap it.
- A CLI start that waits: see decision 5.
- A blocking `flock`: every taker either reports busy or polls on its own
  cadence, so no thread sleeps inside the kernel holding something else.
- The lock per cycle under a parallel queue: two of the queue's own cycles
  would then exclude each other and the queue would run one at a time.

## ADR 205 — The project record never holds a credential in its remote

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** The record's remote check (ADR 199, ADR 203) accepts only SSH
form: scp-like `user@host:path` or `ssh://authority/path`. It already refused
https, http, git:// and file:// remotes, but three gaps remained. An
`ssh://user:password@host/...` URL passed, because the authority pattern took
any non-slash text. A token used as the scp user (`ghp_...@github.com:o/r.git`)
passed. And the "not in SSH form" refusal echoed the whole remote, so
`https://user:token@github.com/...` put the token in the board's 400, in the
dispatcher's log and in any error a person read. The harness authenticates
https through git's `store` credential helper mounted at
`/run/secrets/git-credentials` (`docs/plans/projects.md`), never through the
URL, so a credential in a record is always a leak and never a configuration.

**Decision.**
1. **The rule.** A remote's userinfo, for any scheme, may be only a bare
   login (`[A-Za-z0-9._-]+`): `git@github.com:o/r.git`,
   `ssh://git@host/r.git` and `ssh://host/r.git` stay valid. A userinfo with a
   `:` (a password) or any other character is a credential. In a URL the
   userinfo runs to the last `@` of the authority, so a password holding `@`
   is caught whole; in scp form it runs to the first `@`, so a path holding
   `@` is not mistaken for one.
2. **Token-shaped users.** A bare login that starts with a known token prefix
   (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`, `glpat-`, any case)
   is a credential too: it is a token used as a user name.
3. **http(s) remotes.** They stay refused for their form, as before. When they
   carry a userinfo, that userinfo is treated as a credential and the
   refusal says so; a bare https URL with no userinfo is still named in full
   in the "not in SSH form" message, because it holds no secret.
4. **Redaction.** No message shows the credential: the refusal names the
   problem and shows the remote with its userinfo as `***`
   (`ssh://***@github.com/o/r.git`). `projects.redact_remote` does it, and
   `projects.has_credential` is the test; both live beside the one check in
   `dispatcher/projects.py`, not in a second validator.
5. **Every path.** The check is in `_problems`, so it runs on create and
   change (the board's 400), and on read: a stored record holding a
   credential is a broken record, so `read` raises its RecordError, `change`
   its BrokenRecord (the board's 409) and the dispatcher's cycle blocks and
   logs it, all with the userinfo redacted.
6. **`dispatch migrate`.** `ssh_remote`, which maps a clone's remote to the
   record's form, already dropped an https userinfo when it rewrote the URL
   to `git@host:path`. It now strips the password of an `ssh://user:pw@` URL
   and keeps the user, and gives no remote (null) for a token-shaped user, so
   migrate never writes a credential and never fails on one.

**Consequences.**
- A record that held a credential by hand is reported as broken until a
  person fixes the file; the board cannot change it, since change reads the
  record first.
- The token prefixes are a heuristic: a token from another forge used as a
  bare login passes. A password always has a `:` and is always caught.

**Ruled out.**
- Rewriting a person's input in create or change (dropping the password and
  keeping the rest): ADR 203 refuses what the reader would refuse rather than
  fixing it silently, and a person who pasted a token should learn it.
- A second validator for the board routes: the one check stays the reader's.
- Accepting https with a userinfo-free URL as a record remote: out of scope
  here; the harness's remotes are SSH (ADR 199).
## ADR 206 — The console forwards the project record's writes

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 203 put two routes on the board service, `POST /api/projects`
and `PATCH /api/projects/<slug>`, behind the `BOARD_ACTIONS_TOKEN` bearer, and
`docs/plans/projects.md` step 1 said the console's proxy did not carry them.
The front reaches the board service only through `front/src/lib/api/forward.ts`,
which holds the bearer on the server (ADR 136, ADR 157), so without a forward no
screen can write a record.

**Decision.**
1. `forward.ts` forwards the two pairs onto the same paths, making eleven
   board-service pairs. They are matched by method and path before the api's
   read-only check, as the other nine are: every other method on those paths
   keeps the answer it had (a `GET` is still the api whitelist's 404, a `PUT`
   or `DELETE` still the 405).
2. Both carry `Authorization: Bearer BOARD_ACTIONS_TOKEN`, added on the
   server; the browser's own `Authorization` never crosses, and the token is in
   no response and no log. Without the token both are the console's 503, whose
   message names `BOARD_ACTIONS_TOKEN` and the project routes, sending nothing.
   They need no `CONSOLE_PROJECT`: the record names its own slug.
3. The slug of a `PATCH` is decoded and checked against `PROJECT_SLUG`,
   `dispatcher/tasks.py`'s `_PROJECT_SLUG` written again, as a thread id is
   checked against `THREAD_ID` (ADR 190, ADR 195). An undecodable slug, or one
   the harness would not take (a leading dot, a `/` once decoded), is the
   console's 404 and never crosses; a good one is re-encoded into the target.
4. Everything else is the other board writes': the CSRF checks first (415,
   then 403 for `Sec-Fetch-Site` and `Origin`), the 64 KiB cap (413), the body
   crossing unchanged under the console's own `Content-Type`, and the board
   service's status, body and content type passed back, so its 400, 401, 404,
   409 and 503 reach the browser in its own words. 504 and 502 for a board
   service that did not answer.
5. `client.ts` gets `createProject(body)` and `changeProject(slug, body)`,
   bare like the other board writes, typed by `ProjectRecord`,
   `CreateProjectBody` and `ChangeProjectBody` in `types.ts`. No screen calls
   them yet.

**Consequences.**
- The two project routes and the three action routes share one token, so a
  console that can start a task can also write a record. ADR 203 chose that
  bearer for the routes; the forward follows it.
- The slug rule now lives in two places, like the thread id's. A change to
  `_PROJECT_SLUG` has to change `PROJECT_SLUG` too, or the console refuses a
  slug the service would take, or forwards one it would refuse in its own
  words.

**Ruled out.**
- Forwarding without checking the slug and letting the board service refuse
  it: the other path parameters are checked here, and a slug that decodes to
  `a/b` should not reach another service's router.
- A UI in this change: the plan's console step is its own surface.
## ADR 207 — Start epic: one start per task, queued at once in the board's order

**Status:** accepted (2026-10-20).

**Context.** `docs/plans/board-service.md`, *Starting development* and *Per
epic*, and `docs/plans/projects.md` step 3: *Start epic* queues one start per
task of the epic, each waiting as `project` until the one before reaches
*Awaiting review*, so it needs no lock of its own (ADR 200, decision 9). The
single-task start (`POST /api/tasks/<id>/start`, `dispatcher/actions.py`
`create`) already existed; nothing queued an epic's tasks together.

**Decision.**
1. **The route.** `POST /api/epics/<epic_id>/start`, behind the
   `BOARD_ACTIONS_TOKEN` bearer and the same refusals as the task start (no
   token configured → 503, wrong bearer → 401, a browser `Origin` → 403, a
   query string → 400, an unknown body field → 400). The body takes
   `project`, `note` and `limit`. 201 returns `{"epic", "queued", "not_ready"}`:
   the records written, and the epic's tasks that were not in *Ready* and so
   were not queued.
2. **What it covers.** The epic's `split_into`; nested epics are already
   refused by the board's epic checks, so there is no recursion. A missing id
   is 404, a task whose status is not `epic` is 409 `X is not an epic`, and an
   epic with no task in *Ready* is 409 `epic X has no task in Ready`.
3. **All or none.** `actions.create_starts` checks every start under one
   `write.lock` before it writes any: one already queued for a task, or a task
   no longer in *Ready*, refuses the whole press with the single-task start's
   409 and writes nothing. A second press is therefore refused, not repeated.
4. **The order is the board's.** The tasks are queued in `ready_order`, the
   *Ready* column's order (priority, then rank), and their action ids are made
   strictly increasing within the batch: the worker scans `pending/` by file
   name, so the first start in the board's order is the first it takes. This
   closes the one gap found: two ids drawn in the same instant could sort in
   the wrong order, and `create_starts` draws again until each id is greater
   than the one before.
5. **No lock of its own.** Every start names the epic's project, so the
   worker takes the first and leaves each later one pending as
   `waiting: "project"` until the cycle before it releases the project lock in
   `close_cycle`, which is when its task is *Awaiting review* (or blocked,
   held, split). The earliest pending start is taken first, so the order
   holds.
6. **Each start says it is the epic's.** `params.epic` names the epic (the
   validator accepts it on a `start` and requires a task id), so a later *Stop
   epic* and the queue screen can find the epic's starts.
7. **The limit and the note.** The spending limit is not built: `limit` must
   be null (`params.limit` is null on every start), and a number is 400 `the
   spending limit is not built yet`. The note, when given, is copied onto
   every start.

**Consequences.**
- Only the tasks in *Ready* at the press are queued. A task that becomes
  *Ready* later as its dependencies merge is not queued by this press: it is
  named in `not_ready`, to be started on its own or by a later press. The
  design's "each later task as its dependencies merge" is not built.
- A task added to the epic after the press is not covered, by design.
- Sub-tasks the arquitecto splits a covered task into are not queued here.
- The worker does not re-check *Ready* when it claims a start; a task moved
  out of *Ready* after the press still runs when its turn comes, as with the
  single-task start.
- A held outcome that is not a cancel or a stop ends the start done and frees
  the project, so the epic's next task can start while the first is held
  mid-cycle.
- Another process (`run-task`, `run-queue`) can take the project between two
  of the epic's cycles; the epic's next start then waits for it like any other.
- *Stop epic*, the spending limit, the console's *Start epic* button and the
  queue screen are not built.

**Ruled out.**
- A watcher that queues the next start when the previous task reaches
  *Awaiting review*: ADR 200's lock already orders them, and a watcher would be
  a second scheduler to keep in step with it.
- A lock per epic: the project lock already serialises every cycle on the
  project.
- Queuing the tasks that are not in *Ready*: the worker would start them
  before their dependencies merged.
## ADR 208 — The project sidebar and the `/p/$slug/` routes, read-only

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/projects.md` step 4: a sidebar that lists the
projects, and each project's screens under `/p/$slug/`, filtered by slug,
with the harness's screens unchanged (*Decisions*, 4). No route the console
reads lists the project records: the board service holds their writes
(ADR 203) and neither it nor the observability api serves a list. Every task
row carries `project` (ADR 199, ADR 204), null for a card that names none.

**Decision.**
1. The console's project list is derived from `/api/tasks`: each distinct,
   non-empty `project` on a task, in slug order (`deriveProjects`,
   `front/src/lib/projects.ts`). A project is shown by its slug; there is no
   title to show until a read route serves the records. No backend route is
   added for it.
2. A task whose project is null belongs to no project here. It shows on the
   harness's own screens only, as before.
3. The sidebar (`app-shell.tsx`) has a *Projects* section above a *Harness*
   section. *Harness* holds the screens, their order and their `g` shortcuts as
   they were. *Projects* lists one link per slug to `/p/$slug`, and the open
   project lists its *Board* and *Backlog* beneath it. It says when the tasks
   are still being read, when no task names a project, and when the read
   failed. Nothing in it makes or edits a project.
4. Three routes: `/p/$slug/` (the board), `/p/$slug/backlog` and
   `/p/$slug/tasks/$taskId`. Each mounts the harness's own component
   (`BoardPage`, `BacklogPage`, `TaskDetailPage`, exported from their route
   files) inside `ProjectScopeContext` set to the slug. A screen reads the
   scope and narrows its tasks with `scoped(tasks, project)`. There are no
   copies of the screens; with no scope (`null`) they draw exactly what they
   drew. A task link drawn inside a scope (`TaskLink`) stays in the project:
   `/p/$slug/tasks/T-…`.
5. Not found: a slug that no task names, once the tasks are read, draws a plain
   *No project named …* with a link back to the harness's board; a task page
   for a task of another project, or of none, draws *T-… is not a task of …*.
   A failed or pending read is shown as such, not as not-found.
6. The sidebar's list is one more `useQuery(tasksQuery)` observer on every
   screen. It shares the board's cache key, so the board and backlog read
   once; a screen that did not read `/api/tasks` before now does. Three tests
   that counted every request were narrowed to leave that read out
   (`-models`, `-approvals`) or to allow it (`-tasks`, the epic view).
7. `front/src/routeTree.gen.ts` was regenerated by the router's vite plugin,
   running the dev server briefly; it was not edited by hand.

**Consequences.**
- A project that has a record but no task yet does not appear in the
  sidebar, and a project's title is not shown. Both wait for a read route.
- The project's debt and learnings are not under `/p/$slug/`: the api scopes
  them by `CONSOLE_PROJECT` on the server (`forward.ts`), not per request, so
  narrowing them needs the api first.
- In a project's backlog, a reorder ranks within the rows on screen, which are
  the project's own.
- Links inside the task page other than the task's own (its epic, its
  dependencies) and the board's slots view stay global.

**Ruled out.**
- A backend list route for this step: the step is the front's navigation, and
  the tasks already name every project that has work.
- Copies of the board and backlog for a project: they would drift from the
  harness's screens.
- Treating a null project as the harness's own project slug: the slug is not
  known to the console, and guessing it would put cards under the wrong name.

## ADR 209 — The project records read route, and the sidebar from it

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 208 built the sidebar and the `/p/$slug/` routes from the
`project` field of `/api/tasks`, because no route served the project records
(`.hive/projects/<slug>.json`, ADR 199): the sidebar showed slugs only, and a
project with no task yet was neither listed nor reachable. ADR 203 decision 1
left the reads to the api's side; the board service holds the writes.

**Decision.**
1. The observability api serves `GET /api/projects` and
   `GET /api/projects/<slug>` (`observability/api/app.py`), read-only. A row is
   the record as `ProjectRecord.to_dict()` writes it (`slug`, `title`,
   `remote`, `base_branch`, `key`, `settings`, `created`, `status`), no other
   field. The list is hive-wide, in slug order, archived records included with
   their `status`; the console filters them.
2. Every served `remote` passes `redact_remote` (ADR 205). The loader already
   refuses a record whose remote carries a credential; the redaction is the
   second lock, so a record written before ADR 205 or by hand cannot leak one
   through this route.
3. A record that does not load (bad JSON, a field out of shape, a credential in
   the remote) is left out of the list and named in `warnings`, the way the api
   reports other broken hive files. The detail route answers it with
   `data: null` and the warning; a slug that is not a valid slug, or has no
   file, is a 404. Neither route takes a parameter; an unknown one is a 400.
4. The console forwards both reads (`forward.ts`: eighteen routes now), hive-wide,
   with no `?project=`: the records are the hive's, not one project's. The
   client has `listProjects()` and `getProject(slug)`; `projectsQuery` polls at
   four times the board's interval, since records change rarely.
5. The sidebar lists the active records (`useProjectList`, `listedProjects`,
   `front/src/lib/projects.ts`), in slug order, by title, the slug when the
   title is empty, with the slug kept as the link's `title` attribute. A
   project with no task is listed; an archived one is not.
6. Only when the records read fails does the list fall back to ADR 208's: the
   slugs the tasks name. The sidebar says so under the list (*Project records
   unread: listed from the tasks, by slug.*). With both reads failed it says
   neither answered.
7. `ProjectGate` knows a slug from a record (archived ones too, still reachable
   by URL) or from a task naming it (`useProjectKnown`). It draws the not-found
   only when both reads have settled, one answered, and neither knows the slug;
   while a read is pending, or with both failed, the screen is drawn.
8. A read counts as failed while it has no data and has errored at least once
   (`errorUpdateCount`), not only while `isError`: in TanStack Query v5 a query
   with no data goes back to `pending` on each refetch, and each newly mounted
   observer refetches it, so testing `isError` alone swapped the gate's screen
   in and out on every remount, a render loop.
9. The new front tests are in `front/src/routes/-p.records.test.tsx`, apart from
   `-p.test.tsx`, whose tests now run on the tasks fallback (`/api/projects`
   unserved there). `-models` and `-approvals` leave the sidebar's
   `/api/projects` read out of their counts, as ADR 208 did for `/api/tasks`.

**Consequences.**
- Every screen makes one more read, `/api/projects`, shared by the sidebar and
  the gate under one cache key.
- A project recorded but archived is not in the sidebar; there is no toggle to
  show the archived ones.
- The sidebar shows no counts of running or waiting tasks, and no debt or
  learnings per project.
- ADR 205's refusal for a remote "not in SSH form" quotes the remote in its
  message. A remote with a token in a query string but no userinfo is refused
  by that check, and its warning then carries the remote as written.
- *New project* is not part of this step.

**Ruled out.**
- Serving the records from the board service: it holds the writes (ADR 203)
  and the api already reads the hive for the console.
- Scoping the list by `CONSOLE_PROJECT`: the list is what picks a project.
- Dropping the tasks-derived list: an api older than this route would leave
  the sidebar empty.
## ADR 210 — The project's debt, learnings and roadmap under `/p/$slug/`

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 208 left the project's debt and learnings off `/p/$slug/`:
the console's forward set `?project=` from `CONSOLE_PROJECT` on every read of
`/api/debt`, `/api/learnings` and `/api/roadmap`, never from the browser
(ADR 24). The api itself already took `?project=` on all three, checked
through `_project` (ADR 41 part 2): an unknown slug is its 404, and with
several checkouts a read that names none is its 400. Debt is read from the
clone's own index under `projects_root`, and a clone with no index answers
`[]` with the warning *no readable debt index*. Learnings are the hive's
entries that apply to the slug. The roadmap's plans are the clone's
`docs/plans/`, and its tasks are the hive's, narrowed to the task's `project`.

**Decision.**
1. No api change. A pytest pins that each project reads its own clone's
   debt, and that a clone with no index is the empty list, not another
   project's rows.
2. The forward (`forward.ts`, `PROJECT_SCOPED`) takes the browser's
   `project` for these three reads when it matches `PROJECT_SLUG`, and passes
   it on. A value that is not a slug is the console's 400 *… is not a project
   slug.*, never a fall-back to `CONSOLE_PROJECT`, which would answer another
   project's rows under this one's name. With no `project`, the read gets
   `CONSOLE_PROJECT` as before, so the harness's screens are unchanged. This
   narrows ADR 24's "never from the browser" for these three reads only.
3. `listDebt`, `listLearnings` and `getRoadmap` take an optional project, and
   `debtQuery`, `learningsQuery` and `roadmapQuery` are keyed by it
   (`["debt", project]`, …), so a project's rows and the harness's never
   share a cache entry.
4. The Debt, Learnings and Roadmap screens read `useProjectScope()` and pass
   it on. Under a scope, the title names the slug, task links are `TaskLink`
   (they stay in the project), and the debt pairing with cards is narrowed to
   the project's tasks with `scoped`. The task page reads its project's debt
   and learnings the same way. With no scope they draw what they drew.
5. Three routes, `/p/$slug/debt`, `/p/$slug/learnings` and
   `/p/$slug/roadmap`, mount `DebtPage`, `LearningsPage` and `RoadmapPage`
   inside `ProjectGate`, as `/p/$slug/backlog` does. The project's roadmap
   reuses the harness route's `validateSearch`, and the filter now navigates
   to `"."` so it stays on whichever path it is drawn on.
6. The open project in the sidebar lists *Debt*, *Learnings* and *Roadmap*
   under *Board* and *Backlog*.
7. A project whose clone has no index, no learnings or no plans shows the
   same empty state as the harness's screen. Nothing is filled in.
8. `front/src/routeTree.gen.ts` was regenerated by the router's vite plugin
   (`bun run dev`). The dev server then stopped on ENOSPC from its file
   watcher, after the plugin had written the tree. It was not edited by hand.

**Consequences.**
- `ProjectGate` draws the screen while `/api/tasks` is in flight, so a slug
  that turns out unknown can send one read of its own before the not-found
  draws. The api answers it 404, and nothing from it is shown.
- A debt row's task id links to `/p/$slug/tasks/T-…` under a project. A debt
  whose id names a task of another project then lands on that task page's
  not-found.
- Learnings are hive-wide entries that apply to the slug, not files in the
  clone, and the roadmap's tasks are the hive's narrowed by `project`. These
  are the api's rules, unchanged here.

**Ruled out.**
- Copying `project` by name in `FORWARDED`: the slug check is the only thing
  between the browser and a path the api builds under `projects_root`.
- A fall-back to `CONSOLE_PROJECT` for a malformed slug: it would show the
  console project's rows under the asked name.
- Copies of the three screens for a project: they would drift from the
  harness's own.
## ADR 211 — Stop epic drops the epic's waiting starts, and the epic's page has both buttons

**Context.** ADR 207 built `POST /api/epics/<id>/start`, backend only: one
start per task of the epic in *Ready*, in the board's order, the note copied
to each and `params.epic` naming the epic. `board-service.md`'s *Per epic*
also asks for *Stop epic*, which "drops what is queued and leaves running
phases alone", and for the two buttons on the epic's view. The queue already
has a cancel (ADR 142, ADR 148): a `cancel` action the worker runs, which
drops a pending target and stops a claimed one. Stopping a running phase is
exactly what *Stop epic* must not do.

**Decision.**
1. `POST /api/epics/<id>/stop` (`board_service/app.py:epic_stop`), behind the
   actions bearer as the other creators, with an empty body (anything else is
   400). 404 for no such task, 409 for a task that is not an epic.
2. It reads the queue under one hold of `write.lock`
   (`dispatcher/actions.py:create_epic_cancels`): the starts whose
   `params.epic` is the epic, pending or claimed. It queues one `cancel` per
   pending start that no pending cancel already names, and names the claimed
   ones as left running. The answer is `{epic, cancels, dropped, left}`, the
   last two as `{action, task_id}`. 409 when the epic has no start queued, or
   when every pending one already has its cancel (a second press before the
   worker ran the first). With only claimed starts, nothing is written and the
   answer is 201 with empty `cancels`.
3. The cancels carry `params.only_pending: true`, a new optional key of the
   cancel record that must be `true` when present. The worker drops a pending
   target as before; a target claimed by the time the cancel runs is not
   stopped: the cancel ends `failed`, "target … was taken before the stop
   reached it; it is left running". The worker stays the only mover of
   records; the route moves no file.
4. A start queued for one of the epic's tasks by itself (*Start development*)
   has no `params.epic` and is not the epic's: the route leaves it, and the
   console does not count it.
5. The console forwards both epic routes (`forward.ts`, now thirteen board
   pairs): *Start epic* as a task start, its body rebuilt to
   `{project: CONSOLE_PROJECT, note}` and anything but `note` refused with 400;
   *Stop epic* as a cancel, the body sent as `{}`. `client.ts` has
   `startEpic` and `stopEpic`, `queries.ts` the two mutations, which refetch
   the queue and the tasks.
6. The epic's task page (`task-epic.tsx`, `epic-actions.tsx`) has *Start
   epic* and, while the queue holds a start of the epic or could not be read,
   *Stop epic*, with a count of the epic's starts waiting and running.
   *Start epic*'s confirmation names the children in *Ready*, in
   `/api/ready`'s order, that would be queued and the ones that would not,
   shows `lib/slots.ts`'s standing line for the first of them (ADR 198,
   ADR 204), never a time, and takes the optional note. It says the spending
   limit is not built and that the route refuses a number (400); with no
   child in *Ready* its *Start* is disabled. *Stop epic*'s confirmation names
   the waiting starts it would drop and the running ones it leaves. Each
   toast reports what the route answered, counts included; a refusal is a
   toast with the route's error.

**Consequences.**
- A start the worker takes between the press and the cancel's run keeps
  running; the queue shows that cancel as failed with the reason above.
- What the dialogs list is the console's read of `/api/ready` and
  `/api/actions` at the moment; the route decides on its own read, and the
  toast reports that one.
- The buttons are on the epic's task page only, not on the board's swimlane
  header.
- Still open (`board-service.md`, *Per epic*): queuing each later task as its
  dependencies merge, the arquitecto's sub-tasks, and the epic's spending
  limit.

**Ruled out.**
- Reusing the plain cancel for *Stop epic*: a start claimed between the read
  and the worker's run would have its phase stopped, which the plan rules
  out.
- Moving the pending start files from the route: the worker is the only
  mover (ADR 142).
- Counting a child's own single start as the epic's: the person queued it on
  its own, so *Stop epic* would drop what it did not start.
## ADR 212 — New project and its deploy key from the console

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** `docs/plans/projects.md` step 5: make a project from the console,
its record, its deploy key and its checkout. The record writes exist
(ADR 199, ADR 203) and refuse a remote that carries a credential (ADR 205);
the console forwards them (ADR 206). `project-keygen` makes a key from the CLI
(`dispatcher/project_keys.py`); the worker runs queued actions (ADR 148) and
ADR 207 showed how a kind is added end to end. *Decisions*, 2 says the board
service writes a `keygen` action and the worker runs `ssh-keygen` into the
credentials volume; the board service never holds a key.

**Decision.**
1. Four action kinds, `clone`, `init`, `keygen` and `check`, each with
   `project` set to the slug, `task_id` null and no remote: the worker reads
   the remote from the record, so no URL sits in an action file. Only `keygen`
   takes a param, `replace` (a bool). A second pending or claimed action of the
   same kind for the same project is refused (409).
2. `clone` and `init` hold the project (`project:<slug>`) like a start, and are
   not rerun after a crash: a half-made directory is the person's to look at.
   `keygen` is not rerun either, since it may have written the pair; `check`
   only reads, so it is.
3. `clone` refuses a record with no remote and re-checks the remote for a
   credential (`projects.has_credential`) before git sees it; `init` refuses a
   record with a remote. Both refuse when `<projects_root>/<slug>` exists,
   whatever is in it, and leave it as it is. They run in `cleanup_container`'s
   container under the project's base lock, and give what they made to the
   owner of `projects_root`, as `update_project_branch` does. A clone uses the
   same access as every fetch (`_remote_access`): an SSH remote goes with the
   project's deploy key or is not tried. `base_branch` is checked out when the
   record sets one. An init makes one empty commit, authored `ia-harness`, so a
   task's branch has a HEAD to be cut from.
4. `keygen` calls `project_keys.generate`, the CLI's own function, at
   `cfg.project_keys_dir`, then writes the fingerprint into the record's `key`
   (`projects.record_key`, its only writer). The action's result is
   `{public, fingerprint, replaced}`: the public half only. An existing key
   without `replace` fails with that said.
5. `check` is `git ls-remote --heads` with the project's key, run in
   `projects_root` so it needs no clone. It is done either way, with
   `{reachable, detail}`: a remote that does not answer before the key is on
   GitHub is the expected answer, not a failed check. clone and init end
   `done` with `{detail}` or `failed` with git's words.
6. The board service queues them at `POST /api/projects/<slug>/{clone,init,
   keygen,check}`, behind the actions bearer like the other action routes; a
   slug with no record file is a 404, an unknown field a 400. The console
   forwards the four paths (`PROJECT_SETUP` in `forward.ts`), POST only, the
   slug checked, the body unchanged; `client.ts` has `cloneProject`,
   `initProject`, `keygenProject` and `checkProject`.
7. The console's *New project* screen is `/projects/new`, linked from the
   sidebar's *Projects* section. It asks for a slug, a title and either a remote
   or *Empty repository*. With a remote the order is record, keygen, the public
   key and fingerprint with the instruction to add it as the repository's
   deploy key (write access said from the record's `push_task_branch`, or that
   the harness default decides when it sets none), *Check*, *Clone*. This puts
   the key before the clone, not after as *Decisions*, 4 lists it: a record's
   remote is SSH only and the clone reaches it with that key. With no remote it
   is record, then init. A failed keygen offers a replacing key. Each outcome is
   read from `GET /api/actions` by the id its queueing answered.

**Consequences.**
- The worker writes the key where `cfg.project_keys_dir` says, as the CLI
  does; that is the credentials volume only where the worker runs with it
  mounted there.
- `check` tells whether the key can read the remote, not whether it may push.
- The screen finds an action among the queue's pending, claimed and newest
  twenty finished; an outcome older than that shows as not in the answer.
- The form does not queue the project's bootstrap (step 3's
  `bootstrap-project`), and a record whose clone or init failed stays; the
  form cannot be resumed for it after a reload.
- A new project shows in the sidebar only once a task names it, until the
  sidebar reads the records.

**Ruled out.**
- The remote in the action's params: it would be a second copy of the record's,
  and an action file is not checked for credentials as the record is.
- Clone before keygen: the clone would fail on every SSH remote.
- The board service running `ssh-keygen` itself: it would hold the key, which
  *Decisions*, 2 keeps in the dispatcher's volume.

## ADR 213 — A remote not in SSH form is refused without quoting it

**Context.** ADR 205 refuses a project record whose remote carries a password
or token in its userinfo, and shows that remote redacted. A remote not in SSH
form is refused too, but its message quoted the remote as written, on the
grounds that a remote with no userinfo holds no secret. ADR 209 noted the gap:
a token in a query string or a fragment is not userinfo, so `redact_remote`
leaves it, and the warning `/api/projects` serves for a broken record carried
it.

**Decision.** The refusal names the form only: "its remote is not in SSH form
(git@host:owner/repo.git)". The record's slug still names which record it is.

**Consequences.**
- No part of a remote that fails the SSH check reaches a message, however the
  token is placed.
- The operator no longer sees which URL was stored; they read it from the
  record file on the host.

**Ruled out.**
- Redacting query strings and fragments as well: it would be a second list of
  places a token may hide, and an SSH remote has neither, so nothing is lost by
  quoting none.
## ADR 214 — `check` tells write access; the bootstrap stays out of the form

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 212 left two things of `docs/plans/projects.md` step 5 open:
the form does not queue the project's bootstrap (*Decisions*, 4, step 3), and
`check` tells whether the deploy key can read the remote, not whether it may
push. A project whose `push_task_branch` is on needs the key's *Allow write
access*; the person learns it is off only when the first task's push fails.

**Decision.**
1. After a remote has answered `ls-remote`, `check` tries a push that writes
   nothing: in a scratch repository (`mktemp -d /tmp/ia-write-check.XXXXXX` in
   the container, removed after), `git init`, one empty commit authored
   `ia-harness`, then `git push --dry-run --porcelain -- <remote>
   HEAD:refs/heads/ia-harness/write-check-<12 hex>` with the project's access
   (`_remote_access`, as every fetch). A dry-run push opens `git-receive-pack`
   on the remote, which is where GitHub refuses a read-only deploy key
   ("The key you are authenticating with has been marked as read only", exit
   128); it then stops before sending the pack, so no ref and no object reach
   the remote. Measured on 2026-10-20 with git 2.43 against a local bare
   repository behind a stand-in `ssh`: exit 0, the bare's refs and objects
   unchanged; with a refusing `git-receive-pack`, exit 128. The test suite does
   the same against a local bare.
2. The result is `{reachable, detail, writable}`. `writable` is true when the
   dry-run push exits 0, false when it does not (git's last words in
   `detail`), null when the write was not tested: the remote did not answer,
   the check could not be run, or the scratch repository could not be made.
   `reachable` and `detail` keep their meaning, so a reader of ADR 212's shape
   is unchanged; `detail` gains the push's sentence after a `;`. The check
   takes no lock, as before: it writes only its own scratch directory.
3. The form reads it against the record's `push_task_branch`: a refused push
   is a warning to tick *Allow write access* and check again when the project
   pushes (or when the record leaves it to the harness default), and an
   expected answer when it does not. A null `writable`, and a check result
   from a worker older than this ADR, read "write access was not tested".
4. The bootstrap is not queued by the form, and no `bootstrap` action is
   added. `bootstrap-project` (`dispatcher/cli.py`) spends no quota and runs
   no phase: it only runs `mkdir -p <projects_root>/<slug>` in the container.
   After a clone or an init that directory exists, so it would do nothing;
   queued before them it would make a root-owned empty directory, and both
   refuse a directory that exists (ADR 212, decision 3). Step 3 of
   *Decisions*, 4 is subsumed by step 1.

**Consequences.**
- `writable: true` says the remote accepts this key at `git-receive-pack`. It
  does not see branch protection or rulesets, which GitHub applies when a real
  update of a protected ref arrives; the probe names a new branch no rule is
  likely to match.
- A push refused for another reason (the network dropping between the
  `ls-remote` and the push) reads as `writable: false` with git's words in
  `detail`; checking again tells which.
- The probe was measured with the host's git 2.43; the agent image carries
  2.39, where it was not run (no docker in this work). The flags it uses are
  older than both.

**Ruled out.**
- `git ls-remote --upload-pack=git-receive-pack`: over SSH it asks the same
  service, but over HTTPS git ignores the option and it reads, so a read-only
  token would look writable.
- `git push --dry-run --all` from the empty scratch repository: with no
  branch it exits 1 before reaching the remote.
- Deleting a branch that does not exist (`:refs/heads/<name>`): in the
  measurement it exited 0 in a case the probe could not explain, so its exit
  status was not trusted to tell a refusal.
- A real push of a throwaway branch and its deletion: it writes the remote,
  fires its webhooks and, on a failure between the two, leaves a branch.
- A `bootstrap` action running `bootstrap-project`: it would only make the
  directory clone and init make, and building something more under that name
  would be a substitute for a bootstrap the harness does not have.

## ADR 215 — The write probe is measured on the agent image's git 2.39.5, and reads the exit status alone

**Status:** accepted (2026-10-20, T-117-S2).

**Context.** The write check's probe, `_probe_push` in
`dispatcher/docker_exec.py`, is ADR 214's. That ADR measured it with the
host's git 2.43 only, and the agent image, where the worker runs it, carries
2.39.

**Decision.**
1. Measured on 2026-10-20 in the agent container, `git version 2.39.5`, with
   the probe's own argv (`git push --dry-run --porcelain -- <remote>
   HEAD:refs/heads/ia-harness/write-check-<12 hex>`) from a scratch repository
   holding one empty commit:
   - a local bare reached through a stand-in `ssh` that runs the remote
     command, and the same bare as `file://`: exit 0, stdout `To <remote>`,
     `*<TAB>HEAD:refs/heads/ia-harness/write-check-…<TAB>[new branch]`,
     `Done`, stderr empty; the bare's refs unchanged.
   - a stand-in `ssh` that writes GitHub's read-only refusal to stderr and
     exits 1: exit 128, stdout empty, stderr `ERROR: The key you are
     authenticating with has been marked as read only.`, `fatal: Could not
     read from remote repository.`, then git's two-line *Please make sure you
     have the correct access rights* hint.
   - a remote that is not there: exit 128, stdout empty.
   These are the exit statuses ADR 214 measured on 2.43, so `_probe_push`
   reads 2.39 correctly as it is: no parsing changes.
2. The probe keeps reading the exit status alone and does not parse the
   porcelain lines. A ref-level rejection is the one case the porcelain would
   add, and on 2.39.5 it exits 1 with a `!` line (measured as a
   non-fast-forward dry-run onto an existing branch), so the exit status
   already tells it as refused.
3. The suite gains a test where real git meets the refusal: the push goes
   over SSH to a stand-in `ssh` that refuses, instead of the pre-made
   `CompletedProcess` the existing refusal test returns. The real-git tests run
   on whatever git the suite runs on, which is the agent image's in a phase,
   so both readings are covered on 2.39 by the suite itself.

**Consequences.**
- A pre-receive hook does not refuse a dry-run push: the measurement with one
  that exits 1 on the bare got exit 0 and `[new branch]`, since a dry-run
  sends no commands for a hook to see. So a hook cannot stand in for a
  read-only key in a test; a refusing `ssh` (or receive-pack) can.
- git's hint lines reach `detail` too: `_output_tail` keeps the last 500
  characters of stderr, folded to one line.

**Ruled out.**
- Parsing the porcelain's `*`/`!` lines: it would add a second reading that
  agrees with the exit status in every measured case.

## ADR NNN — *New project* reads its actions by id and resumes from its url

**Status:** accepted (2026-10-20). Built by a subagent of the operator's session.

**Context.** ADR 212 left two things of `docs/plans/projects.md` step 5 open.
The form found each setup action's outcome in `GET /api/actions`, which
serves the newest twenty finished: a `keygen` that twenty other actions
finished after was never seen as done, and the form waited on it for good.
And the form's place (the record, the ids its queueing answered) lived only
in the component's state, so a reload lost it: the record stayed, the form
came back empty, and a second *Create project* was refused with a 409.

**Decision.**
1. `GET /api/actions/<action_id>` serves one action's record, looked for in
   `pending/`, `claimed/` and `done/` in that order, the order the worker
   moves a record in, so a record moved between two looks is found in the
   later one. An id not of the queue's shape (`actions.ID_RE`), or one no
   record names, is a 404 and is never made into a path; a file that does not
   read is `data: null` with the reason, as `/api/projects/<slug>` answers a
   broken record. It takes no parameter (a 400 otherwise) and reads through
   `/state:ro`.
2. The console forwards it: `GET /api/actions/<id>` with the id's own shape
   checked in `forward.ts` before anything is sent, `client.getAction`, and
   `actionQuery(id)` under `["action-queue"]`, so the invalidation every
   queue write makes refetches it. It polls while the record is pending or
   claimed and stops once it is finished.
3. The form keeps its place in the url: `?slug=` as soon as the record is
   written, then each queued action's id under its kind, `keygen`, `check`,
   `clone` or `init`, each navigation replacing the entry it was on. A reload
   reads the record back by that slug (`projectQuery`, `GET
   /api/projects/<slug>`) and each action by its id, and draws the form where
   it was: the inputs locked on the record's values, the public key, the
   check's answer, the clone's. The record is the source of what was written
   and the queue of what ran; nothing is kept in the browser.
4. A record in the url with no first action (`keygen` for a remote, `init`
   for none) is a page left between the two writes: the form says so and
   offers the action's button. An id the queue has no record of says that,
   named, instead of waiting; a slug whose record does not read back says
   that and offers no *Create project*.

**Consequences.**
- `front/src/lib/project-setup.ts` and its `findAction` over the list are
  gone: the list is no longer how the form reads.
- The resume is the url's. Another tab, or the form opened again from the
  sidebar, starts empty; a `check` or `clone` id lost that way is not
  recovered, though it ran. Only the first action is offered again, and a
  `keygen` queued again for a project whose key was made fails as ADR 212
  says, with the replacing key offered.
- The bootstrap is still not an action (ADR 214), so there is nothing of it
  to resume.

**Ruled out.**
- Raising `/api/actions`' limit: an outcome would still fall out, later.
- `localStorage`: a second source beside the record and the queue, which can
  disagree with both and is not on another machine.
- Deriving the actions from the record: a record names no action, and
  listing the queue for a project's setup kinds has the same limit as 1.
