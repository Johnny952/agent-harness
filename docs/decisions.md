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

**Status:** accepted (T-011). Narrows ADR 18, which decided *that* they are served and left the shape open; nothing in ADR 18 is reversed.

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

**Status:** accepted (T-011). Narrows ADR 17, which admitted the body with `depends_on` and named `/api/tasks` for both; the admission stands and the route for one of them does not.

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
