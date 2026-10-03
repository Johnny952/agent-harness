# UI definitions for the console

The vocabulary the console in [`front/`](../front/) has to be consistent
about. It is binding: a screen that contradicts an entry here is wrong, not
different.

What belongs here is decided by one question, and
[`docs/plans/front.md`](plans/front.md) *Where a UI definition lives* is where
that test is written out: **does a second screen have to match this to be
right?** If it does, it is an entry here. If it is true of one screen only, it
belongs to that screen's task. If it has a consequence outside the console — a
route, a field, a credential, where a parser lives — it is an ADR in
[`docs/decisions.md`](decisions.md) or a ruling in
[`docs/charter.md`](charter.md). An entry here that can only be honoured by
changing a route is an entry filed in the wrong place.

This file is **edited in place**, unlike `docs/decisions.md`. A vocabulary
whose current state has to be reconstructed by reading superseded entries in
order is not a vocabulary; git holds the history. An ADR is owed for a change
in exactly one case: when the change makes a screen that has already shipped
wrong.

Every entry names what set it. **Set by: the `front/` import** means the entry
records what the console already does rather than something this project
decided and then built: `front/` arrived generated, was adopted under
`docs/charter.md` C-8, and these entries are its vocabulary written down so the
next screen has something to match. A task that changes one of them puts its
own id there instead.

## Entries

### Dark, dense, tabular

The console is a dark-first operations surface read at a glance, not a
document. Body text is 13px at 1.45, `font-variant-numeric: tabular-nums` is on
globally, the corner radius is `0.375rem`, and `html` declares
`color-scheme: dark` so native controls and scrollbars follow. Inter carries
prose, JetBrains Mono carries anything that is an identifier or a number a
human will compare against another number.

Density is a constraint, not a style: a screen that needs more room earns it by
showing fewer rows, not by growing the type.

**Set by:** the `front/` import. Source of truth: `front/src/styles.css`.

### Colour comes from a token, never from a literal

Every colour in the console is an oklch custom property declared on `:root` in
`front/src/styles.css` and mapped into Tailwind through `@theme inline`. A
screen uses the semantic name — `background`, `foreground`, `muted`,
`muted-foreground`, `border`, `primary`, `destructive`, `warning`, `success`,
`info` — and never an inline colour.

The reason is not tidiness. Semantic names are what let a later task change
what "warning" looks like in one place and have twelve screens agree; a literal
is a screen that has opted out of the vocabulary.

**Set by:** the `front/` import. Source of truth: `front/src/styles.css`.

### One hue per role, everywhere a role is named

The five roles each own a hue, and it is the same hue on every screen:
cartógrafo 205, arquitecto 285, implementador 155, revisor 85, auditor 15, as
`--role-<name>`. A role is rendered through `RoleBadge`, and `roleColorVar` and
`roleGlyph` in `front/src/lib/format.ts` are the only mapping.

Role colour is not a status colour. A revisor badge is amber because the
revisor is amber, not because something needs attention, and no screen may read
meaning into the pair of them.

**Set by:** the `front/` import. Source of truth: `front/src/styles.css`,
`front/src/lib/format.ts`, `front/src/components/console/primitives.tsx`.

### The tone of a state

`success` is a terminal good outcome — a task `done`, a phase that finished.
`warning` is something that still works and will not keep working — an account
in `PRE_COOLDOWN`, a lock whose heartbeat has expired, a gate note, an open debt
row. `destructive` is a failure that already happened — `blocked`, a blocking
finding, a refused write. `info` is work in flight — `in_progress`, a running
action. Anything the harness simply has not said is `muted`, and that is the
next entry. A task that is `pending` is `muted` too: nothing has happened to it
yet, which is not the same news as something that finished.

The four task states are the four the harness writes — `pending`,
`in_progress`, `blocked`, `done` — and not the four a fixture imagined;
`docs/decisions.md` ADR 26 says why there is no `queued` and why
`in_progress` carries no role. A status outside those four is possible, because
the api serves what the task file says: it renders `muted` with the string
verbatim rather than unstyled.

The tone of a state is a cross-screen definition because the same state appears
on the board, on the task detail and in the pool, and an operator who has to
re-learn what amber means per screen is reading three products.

**Set by:** the `front/` import; amended by T-012 for the served state
vocabulary and the tone of `pending`. Source of truth:
`front/src/components/console/primitives.tsx` (`StatusPill`, `gateTone`).

### Absent, empty and broken are three different things

- **Absent** is one field the harness has not recorded. It renders through
  `Absent`, in muted type, reading *not recorded* by default. It is never `—`,
  never `null`, never an empty cell, and never zero: a blank cell and a
  recorded zero are different facts and the console does not merge them.
- **Empty** is a query that succeeded and matched nothing. It renders through
  `EmptyState`, and its body names *what would have been here* — "no debt
  declared yet", not "no results". An empty state that does not name its own
  subject is a bug.
- **Broken** is a query that failed. It renders through `ErrorState` with what
  failed and what the operator can do, and it never degrades into an empty
  state: a screen that shows "nothing to see" when the api refused is lying.

Which read broke decides how much of the screen it takes. A screen's *own*
read — the one the screen exists to show — takes the screen, and that is
`ErrorState` in place of the content. A *secondary* read does not: a join, a
filter's options, a count beside each row all leave a page worth rendering
around them, and blanking it would hide the rows that are fine in order to
report the one that is not. Those report through `BrokenBanner` above the
content where the region they feed is diffuse — a chip on every card, the
options behind one `Select` — and through an `ErrorState` inside the region
where that region is a discrete block of its own. Either way the slot the read
fed stops claiming a fact: it is not `Absent`, because *not recorded* is
something the harness told us and this is the absence of an answer.

`BrokenBanner` is tone `danger` where a partial's banner is tone `warning`,
and it sits above it, because the two say different things and broken outranks
partial. A warning is a shortfall the api itself declared and the row beside it
is still true; a read that never answered leaves every field it fed a guess.
Each line names the route and what its silence costs, so an operator looking at
a card with no debt chip knows the chip is missing rather than the debt.

A **warning** is a fourth thing and is none of those three. Rows together with a
non-empty `warnings` is a *partial*: it shows both, and the next entry says how.
A region that turns a warning into an empty state, or into an error state, has
lost the one thing the api went to the trouble of telling it.

This is the entry most screens get wrong, which is why it runs long rather than
to one paragraph.

**Set by:** the `front/` import; the partial added by T-012 under
`docs/decisions.md` ADR 16; the secondary-read rule and `BrokenBanner` by the
by-hand review of T-012, which found four such reads on three screens failing
into silence. Source of truth:
`front/src/components/console/primitives.tsx` (`ErrorState`, `BrokenBanner`),
`front/src/routes/index.tsx`, `front/src/routes/pool.tsx`,
`front/src/routes/tasks.$taskId.tsx`.

### Loading is per page, not per region

A screen that has not got its reads back yet says so once, in one line of muted
text where the content goes, and not once per region. ADR 14's state table
makes the region the unit for *Absent*, *Empty* and *Broken*, because those are
facts about one field or one query and they can differ down a screen. Loading
is not: it is a fact about the first paint, and regions resolving one after
another is the queries' timing on an operator's screen rather than anything
about the harness.

A refresh is not a load. A query that already has data and is refetching keeps
showing the data — the timestamp in the header is what says how old it is, by
*Staleness is served, never computed*. This is why the screens read `isLoading`
and not `isFetching`.

This entry records what the console already does rather than something decided
and then built: every screen under `front/src/routes/` that has a loading state
at all answers it with one line for the whole page, `models.tsx` included,
which ORs two reads into one. It is written down so the next screen has to
match, and a screen that genuinely needs loading per region — one region behind
a slow route the rest of the page should not wait on — changes this entry
rather than diverging from it quietly.

**Set by:** the `front/` import, recorded by the by-hand review of T-012 after
the task bound to the question shipped without settling it. Source of truth:
`front/src/routes/index.tsx`, `front/src/routes/pool.tsx`,
`front/src/routes/models.tsx`.

### Staleness is served, never computed

Whether a lock is stale is `lock_expired` on a task row, computed by the api
against the expiry window it holds in its own config. No screen compares a
heartbeat against a number of its own: `docs/decisions.md` ADR 18 deleted
`HEARTBEAT_STALE_S` for this, and a console that re-derives staleness is
computing, on stale input, a value it was handed.

The field has three values and they are three different renderings, through
`HeartbeatDot`:

- `true` — `destructive`, reading `stale <age>`, with the absolute timestamp in
  the `title`. The age is the console's own subtraction and ticks; the
  *judgement* is the api's.
- `false` — `muted` with a `success` dot and the age. A live lock is not good
  news, it is ordinary news.
- `null` — nothing to judge, and two facts share it. With no `heartbeat` it is a
  task nobody holds, and renders through `Absent` as *no lock*. With a
  `heartbeat` that is not a timestamp it is a hand-edited card, and the string
  renders verbatim beside the dot, because an unparseable heartbeat is the
  harness's news to report and not the console's to hide
  (`docs/decisions.md` ADR 10, and ADR 8 for the board doing the same).

An account has no heartbeat of its own. The lock an account holds is the lock on
the task it is running, which is a join the console does across two routes it
already calls — ADR 17 — and an account with no current task renders `Absent`
rather than a dot.

**Set by:** T-012. Source of truth:
`front/src/components/console/primitives.tsx` (`HeartbeatDot`),
`front/src/lib/format.ts`.

### A region with no route says which route, and when

A region whose query has no route behind it renders an `EmptyState` whose body
names the missing route and the tier it lands in — "`/api/phases`, tier 2 of
`docs/plans/front.md`" — and never a fixture, never a blank, and never the
wording of an empty harness. `docs/decisions.md` ADR 19 is the rule; this entry
is what it looks like, because more than one screen has such a region and an
operator must never be unable to tell a quiet harness from an unwired console.

The distinction to hold on to: *this harness has not done that yet* and *this
console cannot see it yet* are different sentences, and only the first one is an
`EmptyState` about the harness. A region waiting on a route is an empty state
about the console.

Where a region has no shape of its own to fill — a filter over a dimension
nothing supplies, a column that would always be empty — the control goes rather
than being disabled, and a `Banner` with tone `info` above the content names the
route. A control that cannot act is the same mistake as a fixture: it tells an
operator something is available when it is not.

**Set by:** T-012; the paragraph above applied to the pool's `Release` button
by the by-hand review of T-012, which removed it. Source of truth:
`front/src/components/console/primitives.tsx` (`EmptyState`, `Banner`),
`front/src/routes/index.tsx`, `front/src/routes/pool.tsx`,
`front/src/routes/tasks.$taskId.tsx`.

### Times are absolute, ages are relative, and ages tick

A timestamp the operator may need to correlate with a log is rendered absolute,
through `formatClock`. A duration is `formatDuration`. An age — how long since
a heartbeat, since a refresh — is relative and rendered through `formatAge`
over `agoSeconds`, and it **ticks**: `useNow` re-renders it on its own interval
so an age never goes stale behind a poll it is not driving.

Anything clock-dependent renders only after hydration. The server has a
different now than the browser, and `RefreshedAt` exists because a server-
rendered age mismatches on the first paint.

**Set by:** the `front/` import. Source of truth: `front/src/lib/format.ts`,
`front/src/hooks/use-console.ts`,
`front/src/components/console/app-shell.tsx`.

### One shell, one nav, one set of chords

Every screen renders inside `AppShell`, which owns the 186px rail, the
`harness/console` wordmark, the chat dock and the keyboard map. A screen does
not draw its own chrome and does not register a global key.

The map is `g` then a letter, armed for 1200ms: `b` board, `a` approvals,
`p` pool, `t` tokens, `s` session logs, `l` live tail, `d` debt, `n` learnings,
`q` queue, `k` backlog, `m` role models. Outside the chord, `c` toggles the
chat dock, `/` focuses search and `Escape` closes or blurs. A new screen takes
a free letter and adds a row; it does not invent a second gesture.

**Set by:** the `front/` import. Source of truth:
`front/src/components/console/app-shell.tsx`.

### A degraded backend is a banner, not a blank screen

When part of the harness is unreachable the console keeps rendering what it
still has and says what it lost, in a `Banner` above the content. The shipped
case is the action backend: the reads stay live and only the controls that
write go inert.

The general rule follows from `docs/decisions.md` ADR 16: every read answers
`{"data", "warnings"}`, and a screen that lists rows shows the warnings it was
given. A warning the api went to the trouble of producing and the console
swallowed is the failure mode this entry exists to prevent.

Concretely, on every screen that lists rows:

- The warnings render in **one** `Banner`, tone `warning`, above the content and
  below the page header and any filter bar — one line per warning, in the order
  the api sent them, in a region that scrolls if there are many. **Every** one
  renders: the api already caps the list at a hundred and spends the last slot
  tallying the rest, so a console that truncates a second time is dropping
  something nothing will mention again.
- A screen that makes several reads concatenates their warnings into that one
  banner. Three banners stacked is three queries' implementation detail on an
  operator's screen.
- The rows still render beside it. A warning never takes a region to its error
  state and never replaces its rows — that is the partial of *Absent, empty and
  broken*.
- A screen that lists rows and has nowhere to put this is unfinished, which is
  the criterion ADR 16 hands the revisor.

**Set by:** the `front/` import, under ADR 16; the four rules above by T-012,
which wired the first five reads that can produce a warning. Source of truth:
`front/src/components/console/app-shell.tsx`,
`front/src/components/console/primitives.tsx` (`Banner`, `WarningBanner`).

### The four utilities, and what they are for

`front/src/styles.css` defines exactly four custom utilities and they are the
vocabulary for the shapes below the component level: `mono` for identifiers and
figures, `panel` for a bordered region with its own background, `label-xs` for
the small uppercase label above a value, `kbd` for a key rendered inline. A
screen that needs a fifth adds it here and to this entry, rather than inlining
a one-off class.

**Set by:** the `front/` import. Source of truth: `front/src/styles.css`.

## Not decided here

Nothing is open here at the moment. A cross-screen question that an arquitecto
should not answer speculatively is listed in this section and bound to the task
that first needs it in `docs/plans/front.md` *Decisions this tier's tasks make*,
and it is struck off here when that task answers it.

Three have been listed and all three are now answered. They are kept as a
record of where each answer went, because the binding is the part that is easy
to lose:

- ~~**Loading per region or per page**~~ — ADR 14's first open edge, bound to
  the Board screen's task. That task was T-012, and it wired the five reads
  without touching the question, which left the pointer with no owner. The
  by-hand review of T-012 closed it the way the entry test asks: per page, as
  every screen already did, written down as *Loading is per page, not per
  region* above.
- ~~**The inert `Release` button**~~ — whether the pool shows a control nothing
  serves, bound to the Pool screen's task. That task was also T-012, which left
  the button as it found it. The by-hand review removed it under *A region with
  no route says which route, and when*, the entry T-012 itself wrote: the
  button is gone and a `Banner` with tone `info` names the route it waits on
  and the CLI verb that does the job today.
- ~~**Whether the live tail re-terminates the stream**~~ — answered outside
  this file, because it had a consequence outside the console:
  `docs/decisions.md` ADR 22. It consumes `/api/events` rather than terminating
  anything.
