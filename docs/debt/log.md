# Debt log

What the auditor says about each task's rows, one paragraph per task, in the
order they were filed. Append yours at the end and never edit an earlier one:
this file merges by union (`docs/decisions.md` ADR 297). "The first reason
above" in the paragraphs means the `card` paragraph of `docs/debt/README.md`:
no card exists.

The two `T-010` rows are `none` for a second reason as well, worth naming
because it is not the same gap. That cycle was recovered one phase at a time
with `run-phase` after the implementador hit `phase_timeout_seconds`, so
`dispatcher/dispatcher.py:_file_accepted_debt` — which assigns the ids and opens
the cards before the auditor runs — never ran at all. The ids were re-derived
by the auditor from `dispatcher/debt.py:entry_id`, in the order the declarations
were made: `T-010-D1` is the one the revisor accepted in round 1, `T-010-D2` is
the revisor's own proposal (its W1), ruled on by the auditor because the revisor
could not rule on itself. If cards for either turn out to exist, these two
`card` cells are the ones to correct.

The two `T-011` rows are `none` for the first reason above and, for `T-011-D2`,
for a third worth naming because it is structural rather than an accident of how
that cycle was driven. `T-011-D1` arrived with a filing note, which is the normal
path, and is `none` only because this harness has no board. `T-011-D2` is the
revisor's own proposal: `dispatcher/debt.py:accepted` reads the implementador's
declarations filtered by the revisor's rulings, so a debt the revisor raises about
its own round has no route to `_file_accepted_debt` and gets no card and no id
from the dispatcher, `--final` or not. The auditor ruled on it and gave it the
next id in the same sequence, exactly as `T-010-D2` was handled.

`T-012-D1` is `none` for the first reason and for a fourth: no role filed it at
all. T-012's run stopped for quota after the implementador's round 1, so its
auditor — the role that writes this table — never ran, and there is no
declaration, no ruling and no filing note behind the row. It was found by the
by-hand review of that branch and written here by the operator, with the first
free id in T-012's sequence on the `T-010` precedent. It also breaks the *files
your task touched* rule above, deliberately: the review was reading `front/`,
the defect is in `dispatcher/`, and the loss had already happened — this row is
the only record of it that survives.

`T-013-D1` is `none` for the first reason above — this harness has no board, so
there is no card for any row here to point at. Nothing else about it is unusual:
it was declared `found` by the implementador, accepted by the revisor in round 1
and filed by the auditor with the ids in a filing note — `T-011-D1`'s path, and
the one this table is shaped for. T-013's cycle did stop early all the same: the
revisor's four findings were applied by hand in commit `aba17b2` because the
account was rate-limited before a round 2 could run, so no implementador ever
read them. That changed nothing about this row, because the declaration and the
ruling were both already made.

`T-014-D1` is `none` for the first reason above, and its cycle is worth a line
because the row above it is about this exact failure mode. T-014's run ended for
quota immediately after the revisor's round 2 approved, so its auditor was
resumed by hand with `run-phase --final` instead of being run by the cycle. That
path files debt the way the cycle does, off what the earlier phases left on
disk: `dispatcher/dispatcher.py:_file_accepted_debt` runs before the phase, so
this id was assigned by the dispatcher and handed to the auditor in a filing
note — `T-011-D1` and `T-013-D1`'s path, not the hand-derived one the row above
had to use. Nothing else about it is unusual: the implementador declared it
`introduced` in round 1, the revisor accepted it in round 1 and re-verified it
in round 2, and the auditor filed it.

`T-014-D2` is `none` for the first reason above and for the fourth, the same way
`T-012-D1` is: no role filed it at all. It was written by hand on `main`, out of
cycle, the same day the change it describes narrowed the gate, so there is no
declaration, no ruling and no filing note behind it, and the id is hand-derived
— ADR 35's gate landed in `bc07f4e`, a hand commit from before the T-008
sequence existed, so it has no sequence of its own and `T-014-D1` was taken. It
breaks the *files your task touched* rule above deliberately, on `T-012-D1`'s
precedent: the stale sentences are in twelve docs the change never opened, and
narrowing the scope is exactly what stops anyone from being told about them
again. One thing about its detail file is worth knowing before editing it: the
dead paths it names sit inside a fenced block rather than in backticks, because
this directory is one of the six the narrowed gate reads whole on every task,
and a dead path cited here would be a finding for ever.

`T-015-D1` is `none` for the first reason above and nothing else: it was declared
`found` by the arquitecto and again by the implementador as one entry, accepted
by the revisor in round 1, and filed by the auditor with the id in a filing note
— `T-011-D1`, `T-013-D1` and `T-014-D1`'s path, and the one this table is shaped
for. Two things about that cycle are worth a line. Its own *Fix* needs the
lint's own nine file names, and a phase can read them off the lint itself:
`cd front && bun install --frozen-lockfile` and `bun run lint` are commands a
card grants, as T-016's did on 2026-10-09 and all four phases of T-018's did
again, and the lockfile plus bun's install cache on the projects mount are what
make that install reproducible (`docs/decisions.md` ADR 46, narrowing ADR 24).
This paragraph used to say the opposite — that no phase of this project can run
`bun` and a worktree has no `node_modules`, citing an inbox entry that now
carries `status: refuted` — which was the whole of `T-018-D1`, resolved by
T-019; the dispatcher's test gate is no longer the only observer of the count
this row quotes (`docs/decisions.md` ADR 39). And there is no `T-015-D2`,
though T-015's revisor did raise a proposal of its own:
that `docs/debt/T-013-D1.md` *How it was resolved* and that entry's row here
still said `eslint .` **now** exits zero with ten warnings. The auditor ruled it
resolved rather than filed, because the fix was one clause in two files that
phase was already opening, and a row would have outlived the problem. Both now
carry it, naming T-015 and ADR 40. The reasoning is
`docs/learnings/a-stale-count-in-a-dated-paragraph-is-not-a-finding.md`; the
route a revisor self-proposal takes is
`/data/.hive/learnings/inbox/T-011-a-filing-note-does-not-cover-a-revisor-self-proposal.md`.

`T-016-D1` is `none` for the first reason above and nothing else, and its cycle
took the path this table is shaped for: declared `found` by the arquitecto and
again by the implementador as one entry, accepted by the revisor in round 1 and
re-affirmed in rounds 2 and 3, and filed by the auditor with the id in a filing
note — `T-011-D1`, `T-013-D1`, `T-014-D1` and `T-015-D1`'s path. There is no
`T-016-D2`: the revisor raised no proposal of its own across three rounds, and
the two things the auditor found while filing were corrections to records rather
than deferred work. Both are recorded where they were wrong rather than as rows
— the row above, whose confirmed file name the lint contradicts, and
`docs/learnings/fronts-lint-turns-a-long-line-into-an-error-not-a-warning.md`,
which told a phase it could not run the lint it had just run. The reasoning for
amending rather than filing is
`docs/learnings/a-stale-count-in-a-dated-paragraph-is-not-a-finding.md`.

The three `T-017` rows are `none` for the first reason above and nothing else,
and all three took the path this table is shaped for: declared `introduced` by
the implementador, accepted by the revisor, and filed by the auditor with the
ids in a filing note. Two things about that cycle are worth a line. All three
are `introduced` rather than `found`, which is unusual here and is a property of
what the task was: `T-017-D1` is the seam the fix itself moved — extracting each
branch into a testable component left the *argument* untested — and `T-017-D2`
and `T-017-D3` are both inside `front/src/routes/tail.tsx`'s new arm, raised by
the revisor in round 1, left standing on purpose in round 2 so that the round
whose findings were all in `docs/ROADMAP.md` would leave the code diff
byte-identical, and then declared. And the two tail rows are cheapest taken with
the first: both want a test that can observe something only a route render
reaches, which is `T-017-D1`'s helper. The row above them is resolved by the
same task, which is why four `T-017`-era rows sit together here — one closed and
three opened in its place, two of them polish on a screen the card did not name.
`T-017-D1` and `T-017-D2` were then taken together, as that sentence advises,
by the operator out of cycle on 2026-10-10, and `T-017-D3` after them the
same day, on the route-render helper they left behind.

`T-018-D1` is `none` for the first reason above and nothing else, and it took
the path this table is shaped for: declared `found` by the implementador in
round 1, accepted by the revisor in round 1, and filed by the auditor with the
id in a filing note. Two things about it are worth a line. It is the only row
here that declares a sentence of *this file* false — the `T-015-D1` paragraph
above — which is why that paragraph carried a clause pointing back at this row
rather than being rewritten until T-019 had an ADR to point it at instead: the
other half of the same claim was in an append-only ADR, and correcting one
surface while the other stands is how the two came to disagree in the first
place. `docs/decisions.md` ADR 46 is that narrowing, and all four surfaces
moved in one commit. And there is no `T-018-D2`, though T-018's revisor raised
a second finding of its own, a third copy of the same stale fact in
`front/README.md`'s paragraph above *Writing a test*. It is
named inside this row's *Fix* instead of filed, on the T-015 precedent two
paragraphs up: one stale fact in four places is one task's work, and a second
row would have to be resolved by the same commit as the first — which is what
T-019 did.

`T-019-D1` is `none` for the first reason above and nothing else, and it took
the path this table is shaped for: declared `found` by the arquitecto and again
by the implementador in round 1 as one entry, accepted by the revisor in round
1, and filed by the auditor. One thing about its cycle is worth a line, because
it is the first here driven outside the dispatcher: T-019's round 2, both of its
reviews and its audit ran as subagents in the operator's own session while no
harness account had quota, so no filing note was handed to this auditor and the
id is `dispatcher/debt.py:entry_id`'s, derived by hand on the `T-010`
precedent — the first and only declaration of the cycle, so `D1`. If a card for
it turns out to exist, this `card` cell is the one to correct. It is also the
row `T-018-D1` left behind: ADR 46 lists both bullets among the four surfaces
it deliberately did not move, and this row is where that decision is owed.

`T-020-D1` is `none` for the first reason above and nothing else, and it took
the path this table is shaped for: declared `introduced` by the implementador
in round 1 and again in round 2, accepted by the revisor in both, and filed by
the auditor with the id in a filing note. One thing about it is worth a line.
It is `introduced` in an unusual sense — nothing in `observability/` or
`front/` changed, and neither file was opened. What moved is the *meaning* of a
number those two surfaces already serve and print: ADR 20 reasoned the shape of
the account row from `_threshold_for` being the one place a ceiling is decided,
and ADR 48 made the primary answer to two, only one of which is configured. So
the row is declared by the task that narrowed the premise rather than by the
task that will fix the surface, which is also why its *Fix* is a task and not
an edit: the paced value is a property of one probe, and ADR 18 leaves a
probe's own numbers unserved until something persists one.

`T-024-D3` is `none` for the first reason above and for the fourth, the same way
`T-012-D1` and `T-014-D2` are: no role filed it at all. It was written by hand
by the operator on 2026-10-10, out of cycle, in the same change that resolved
`T-024-D1` under `docs/decisions.md` ADR 54, so there is no declaration, no
ruling and no filing note behind it, and the id is hand-derived as the next free
one in T-024's sequence. It is the half of `T-024-D1`'s *Fix* that resolution
did not do — the screen, which needs a card and touches `front/` — split off
rather than left as a clause in a resolved row, because a resolved row is not
read as a trigger and the work would have gone unseen.

There is no `T-025` row, though T-025's revisor raised a proposal of its own:
three doc lines outside that card's four paths that the change had made stale —
`docs/README.md`'s `task-split.md` row and `docs/plans/token-economy.md`'s
Status paragraph, both saying P4 is not built, and the `T-014-D1` row above with
its detail file, both saying `render` writes only `- ` and `**Bold**` lines. No
filing note named an id for it, the implementador declared nothing, and the
auditor ruled it resolved rather than filed, on the T-015 precedent: each fix
was one clause in a file the auditor owns or that any task may correct out of
cycle, and a row would have outlived the problem. All four now name T-025.

The two `T-026` rows are `none` for the first reason above and nothing else,
and both took the path this table is shaped for: declared `introduced` by the
implementador in round 1 and again in round 2, accepted by the revisor in both,
and filed by the auditor with the ids in a filing note. Both had the same
fix horizon, the second half of step 1 of `docs/plans/board-service.md`, and
both were resolved by T-027, which built it. There is no `T-026-D3`: the revisor raised
no proposal of its own, and its round 1 findings were doc edits the
implementador made in round 2.

The three `T-028` rows are `none` for the first reason above, now as the rule
and not the accident, and took the path this table is shaped for: declared by
the arquitecto and again by the implementador in rounds 1 and 2, accepted by
the revisor in both, and filed by the auditor with the ids in a filing note.
`T-028-D1` and `T-028-D2` are `introduced`: they are the surfaces of the
removed seam that the card kept out of scope. `T-028-D3` is `found`, and it is
the gap the removal uncovered rather than made — the card was the only place a
blocked run was ever recorded. The same audit corrected three older rows'
prose instead of filing anything: `T-008-D1` here and in its file, and
`T-008-D3`'s file, each cited the deleted client module by path. There is no
`T-028-D4`: the revisor raised no proposal of its own.

T-029 filed nothing: no phase declared debt and the auditor was handed no id.
It built half of `T-028-D3`'s fix, the writer and not the call, so that row
was amended and stayed open until T-030.

T-030 resolved `T-028-D3` and filed no row: no phase declared debt and the
auditor was handed no id. Its revisor did raise a proposal of its own, that
the root `README.md`'s exit-code table and `merge-task` passage say nothing
of the dependency and epic gate, the merged record or the `blocked` status.
A revisor's own proposal has no route to an id from the dispatcher, and the
auditor ruled it part of `T-028-D2` instead of a row beside it: the same
file, already stale, with the same fix, one docs task on the README. That
row and its file are widened and both name T-030.

T-031 filed no row: no phase declared debt and the auditor was handed no id.
Its card sent the README's missing exit-3 row, `file-split` verb and
`run-phase` sentence to `T-028-D2` rather than to the README, so that row and
its file are widened again and both name T-031. The revisor's notes that are
not gates — `_file_split` catching only `tasks.SplitError`, `file-split`
filing the last saved handoff unchecked, the arquitecto's handoff budget
against four full descriptions — are in `docs/implementations/T-031.md`
*Audit* and not here: none was declared, and none has an id.

T-032 resolved `T-028-D2` and filed no row, though one debt was declared and
accepted: the root `README.md`'s *Future work* still lists built things as
open (item 3's *Logging setup*, item 3's task with no account going
`blocked` where `docs/decisions.md` ADR 52 made it held, item 1's project
docs, gates and learnings as undesigned). The arquitecto and the
implementador's round 1 declared it and the revisor accepted it in both
rounds, but the implementador's round 2 handoff declared nothing, and
`_file_accepted_debt` in `dispatcher/dispatcher.py` reads only the
implementador's last handoff. So the dispatcher assigned no id, the auditor
was handed none, and an auditor files only what it is handed. It is
recorded in `docs/implementations/T-032.md` *Audit* and in that phase's
handoff, for a person to file or drop; the trap is
`docs/learnings/a-debt-declared-only-in-an-earlier-round-gets-no-id.md`.

T-033 resolved `T-028-D1`. Its arquitecto and implementador declared one
debt, `found`: `Task.description` in `front/src/lib/api/types.ts` is typed
`string` and the api serves `null` for a task with no `description.md`.
The revisor accepted it and it is filed as `T-033-D1`. The auditor read the
three renders of the field and none throws on `null` today, so the row's
cost is the one a later edit would meet, not a page that is broken now.

T-034 resolved `T-033-D1`. Its arquitecto and implementador declared one
debt, `introduced`: the task page's header says *no description* for a
`null`, and no test covers that arm, because no test file exists for
`front/src/routes/tasks.$taskId.tsx` and the card ruled out a new harness
for it. The revisor accepted it and it is filed as `T-034-D1`, with the id
in a filing note. The revisor raised no proposal of its own, so there is no
`T-034-D2`.

T-035 resolved `T-034-D1`: the task page has a test file that reads its
header, and `PageHeader` has a case with a node as `subtitle`. Its
arquitecto and implementador declared no debt, and the auditor was handed
no id. Its revisor did raise a proposal of its own: `front/README.md`
*Writing a test* showed `renderRoute` with three arguments and listed four
`-` test files. The auditor ruled it resolved rather than filed, on the
T-015 and T-025 precedent: the fix was one sentence and one file name in a
doc, and both are now there. So there is no `T-035-D1`.

T-036 resolved no row. Its implementador declared one debt, `found`: the
present docs' scope reads the debt directory whole, resolved entries
included. The revisor accepted it and it is filed as `T-036-D1`, with the
id in a filing note. It is the first row here about this directory itself:
each file filed here adds pairs to the count the row describes, this one
too. The revisor raised no proposal of its own, so there is no `T-036-D2`.

T-037 resolved no earlier row. Its implementador declared one debt,
`introduced`: the docs index got no row for the backlog the task created.
The revisor accepted it and it is filed as `T-037-D1`. The auditor took the
fix in `docs/README.md` in the same task, so it is filed resolved, on the
T-015, T-025 and T-035 precedent of a small doc fix. The revisor raised no
proposal of its own, so there is no `T-037-D2`.

T-038 resolved `T-036-D1`: `dispatcher/project_docs.py` now names the debt
index among the present docs and the debt directory among the records
(`docs/decisions.md` ADR 62), so this table is read whole on every task and
each entry file, open or resolved, only for the lines a task adds to it.
That ends the growth the row described: a file filed here no longer adds to
the present docs' count, a row still does. No phase declared debt and the
auditor was handed no id. The revisor raised one non-blocking note, that
`docs/implementations/T-038.md` overstated how much of an entry file its row
repeats; the auditor ruled it a wording fix in that note, made there, and
not a row. So there is no `T-038-D1`.

T-039 resolved no earlier row. Its arquitecto and implementador declared one
debt, `found`: `docs/README.md` called the project's own spec under
`docs/superpowers/` vendored and not to be edited. The revisor accepted it and
it is filed as `T-039-D1`. The auditor took the fix in `docs/README.md` in the
same task, so it is filed resolved, on the T-037 precedent of a small doc fix.
The revisor raised no proposal of its own, so there is no `T-039-D2`.

T-065's implementador declared one entry, `found`: the timing-flaky
`test_a_whole_repo_task_writes_alone`, already a learning since T-061/T-062.
The revisor accepted it and it is filed open as `T-065-D1`; `dispatcher/` was
outside the task's surface. It was resolved by hand on 2026-10-19: the test fixed the order
the windows came in, not just that they did not overlap.

T-100 resolved no earlier row. Its arquitecto and implementador declared one
debt, `introduced`: the unused `Kind.handler` slot. The revisor accepted it and
it is filed open as `T-100-D1`, impact `cosmetic`: ADR 148 already says where
handlers go.
It was resolved by T-101 on 2026-10-19: the slot is removed and ADR 179
makes `worker.HANDLERS` the one registry.

T-123 resolved no earlier row. Its arquitecto and implementador declared one
debt, `found`: `docs/plans/autonomy.md` still describes the one-phase-per-account
`--parallel` ceiling ADR 252 replaced. The revisor accepted it and it is filed
open as `T-123-D1`, impact `cosmetic`: the plan was outside the task's surface.

T-125-S1 resolved no earlier row. Its arquitecto and implementador declared one
debt, `introduced`: the approve's stale check calls the private
`docker_exec._rev_parse`. The revisor accepted it and it is filed open as
`T-125-S1-D1`, impact `risk`: `docker_exec.py` was outside the task's surface.

T-130 resolved no earlier row. Its arquitecto and implementador declared one
debt, `found`: `forwardBoard`'s doc comment still carries two stale route
counts. The revisor accepted it and it is filed open as `T-130-D1`, impact
`cosmetic`: the task's Goal named only the file's header comment.

T-131 resolved `T-130-D1`. Its arquitecto and implementador declared one debt,
`found`: `server-env.ts`'s comment and a `forward.test.ts` test name still
count four backlog writes. The revisor accepted it and it is filed open as
`T-131-D1`, impact `cosmetic`: the task's Goal named only `forwardBoard`'s
doc comment.

T-132 resolved `T-131-D1`. The operator's review at merge found one more
debt, `found`: `server-env.ts` still counts the actions token's routes as two.
It is filed open as `T-132-D1`, impact `cosmetic`: the task was scoped to the
backlog writes' count.

T-133-S1 resolved no earlier row and declared no debt. The operator's review
at merge found one, `introduced`: the worker's resolve copies the CLI's and
calls the private `_base_lock`. It is filed open as `T-133-S1-D1`, impact
`risk`: `dispatcher.py` and `cli.py` were outside the task's surface.

T-133-S2 resolved no earlier row and declared no debt. Its implementador,
revisor and auditor all flagged a risk that no phase could file, because no
debt id was handed to them: *Resolve conflict* sits in the diff-gated verdict
form. The operator filed it at merge as `T-133-S2-D1`, impact `risk`.

T-134 resolved `T-133-S1-D1` and declared no debt.

T-135 resolved `T-133-S2-D1` and declared no debt. It ran as an operator
subagent, not a dispatched cycle, while T-134 held the project.

T-137 resolved no earlier row and declared no debt. It moved these per-task
paragraphs out of `README.md` into this file and made the index append-only
(ADR 297): from here on a paragraph is appended here, never in `README.md`.

T-136 resolved no earlier row and declared one, `introduced`: the two other
inbox writers do not take the new carry lock. It is filed open as
`T-136-D1`, impact `risk`: safe under today's ownership rule, but a lost
update once two writers can reach one entry.

T-138 resolved `T-136-D1` with a resolution row (ADR 297): both writers now
take each entry project's carry lock (ADR 300). It declared no new debt;
the writers ADR 300 leaves unlocked are named in its Consequences, safe
because none of them races `carry` on `carried_by`.

T-139 resolved `T-132-D1` with a resolution row (ADR 297): both
`server-env.ts` comments now name the actions token's routes by `boardTarget`'s
`action` and `forwardBoard`'s doc comment, with no count. It declared no new
debt.

`T-143-D1` is `none` for the first reason above. The revisor accepted it as a
wrong status text; the auditor filed it as `risk` because the same
`_cooldown_remaining` also drives `release-account`'s refusal guard, which
lapses at `quota_cooldown_seconds` even when `retry_at` is later.
T-142 resolved no earlier row and declared one, `introduced`: the read api's
`in_phase_table` still answers for the unfiltered learnings table now that a
phase is handed only the rows its task names (ADR 315 decision 5). Filed open
as `T-142-D1`, impact `cost`: a misleading column, nothing lost.

T-145 resolved `T-143-D1` and declared no new debt: `dispatcher/operator.py`
now takes a refusal's hold from `dispatcher._refusal_hold_until` (ADR 318), so
`dispatch status` and `release-account`'s guard both honour a recorded
`retry_at`. `docs/plans/balancer.md` still quotes the old "of cooldown left"
text; it is a plan, a record, and left as written.

T-144 resolved `T-142-D1` with a resolution row (ADR 297): `/api/learnings`
takes `?task=` and answers `in_phase_table` through the dispatcher's own
`select` and `task_context` for that task (ADR 321). It declared no new debt.

T-146 built step 1 of `docs/plans/server-deployment.md`, sign-in in the board
service (ADR 324), and filed two entries its revisor accepted: `T-146-D1`, the
failure throttle that is per user and not per address until the console
forwards `X-Forwarded-For`, and `T-146-D2`, verdicts still authored by
`operator` rather than the signed-in user. It resolved none.

T-152 let `run-queue --resume` take a new `--limit` and `--parallel` (ADR 336)
and filed one entry its revisor accepted: `T-152-D1`, a `limit_reached` task
that re-runs from the arquitecto because no cycle starts at a later phase. It
resolved none.
