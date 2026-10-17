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
