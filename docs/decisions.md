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
