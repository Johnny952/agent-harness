# A TypeScript type over an api-served value is an annotation, and `front/` has no runtime guard behind it

**When it applies:** you are rendering, indexing or iterating a value
`observability/api/` read off a file and serves **verbatim** — a `saved_at`, a
handoff payload's list, a task file's frontmatter field — inside a `front/`
screen. Also when a field that was fixture-only becomes api-served: every render
of it that was previously fed by `mock/fixtures.ts` is newly reachable with
whatever is on disk.

**Status:** unconfirmed — one instance found by T-013's revisor and fixed by
hand, a second found by T-013's auditor and still open. Neither was *executed*:
`front/` has no runner ([T-013-D1](../debt/T-013-D1.md)), so both were derived
by reading. Shared-inbox entry:
`/data/.hive/learnings/inbox/T-013-formatclock-throws-where-agoseconds-returns-null.md`.

## Symptom

Two throws, same shape. The first, from
`front/src/lib/format.ts:formatClock` over a `saved_at` that is not a
timestamp, which is V8's:

```
RangeError: Invalid time value
```

The second, from `PhaseList` in `front/src/routes/tasks.$taskId.tsx` over a
`handoff.changed` that is a string rather than an array:

```
TypeError: lines.map is not a function
```

The only `errorComponent` in the console is on the root route
(`front/src/routes/__root.tsx`), so either throw replaces the **whole page**
rather than the region — the opposite of what `docs/ui.md` *Absent, empty and
broken are three different things* requires of a secondary read.

## Why

The api serves what the harness wrote and does not judge it. That is
`docs/decisions.md` ADR 10 as a rule and ADR 27 as this route's application of
it: `observability/api/app.py:_phase` passes `saved_at` and `handoff` straight
out of the JSON file, and `tests/observability/test_api.py` *pins* that a
`saved_at` holding a mapping comes back on the row with no warning. The payload
travels whole and unpromoted, so its key set and its value types belong to
`dispatcher/handoff.py`, not to the route.

And the payload's own types are not guaranteed either.
`dispatcher/handoff.py:parse` takes the CLI's `structured_output` when there is
one, and otherwise `json.loads` of the model's text — a role with no schema, an
older image — with no validation past `isinstance(payload, dict)`. The
dispatcher's own renderer knows this: `handoff.py:_lines` is
`if not isinstance(value, list): return []`, and `_pairs` skips an item that is
not a dict. The console's `HandoffPayload` declares `changed?: string[]` and
compiles to nothing.

So an interface in `front/src/lib/api/types.ts` describes what the harness
*usually* writes. It is the same fact as
[a-taskfile-fields-type-is-an-annotation](a-taskfile-fields-type-is-an-annotation.md)
on the Python side — `read_task_file` serves a scalar `depends_on` as a scalar —
one layer further out, where there is no `isinstance` to add and no test that
would notice.

## What to do

Check which helper you are calling and what it does with a value it cannot
read. In `front/src/lib/format.ts`, `agoSeconds`, `formatAge` and `formatClock`
are all guarded now and return the same `—` sentinel; `splitStatus` is not, and
it reads a *card's* `status` — it is callerless under ADR 25 and the first caller
it gets owes it the same check. Everywhere else, guard at the render:
`Array.isArray(lines)` before `.map`, a presence check before a property, and
the `—` sentinel or an `Absent` rather than a throw — `docs/ui.md` *The tone of
a state* already says a value the harness has not said is rendered muted and
verbatim.

Do **not** fix it by validating in the route. ADR 10 and ADR 27 both rule that a
value this service cannot judge is the harness's news to report rather than this
service's to hide, and ADR 27's closing rule is that the api not serving
something is no reason for it to start. The guard belongs where the render is.

## The instance still open

`PhaseList` in `front/src/routes/tasks.$taskId.tsx` takes `lines: string[]`,
tests `lines.length === 0`, and calls `lines.map`. A non-empty string passes the
length test and fails the map. It is fed six payload keys — `changed`,
`verified`, `pending`, `risks`, `paths`, `learnings` — each guarded with `?? []`
for *absent* and not for *wrong-typed*; `paths` additionally renders
`${p.path} — ${p.holds}` over items it does not check are objects, which does
not throw but prints `undefined`. Found by T-013's auditor, after the branch's
other instance had already been fixed, and left in place: the auditor's commit
scope is `docs/` only (`dispatcher/project_docs.py:_COMMIT_SCOPES`), so a fix
from there could not have landed.

## Evidence

T-013. `front/src/lib/format.ts` (`agoSeconds` against `formatClock` as the
branch stood before commit `aba17b2`), `front/src/routes/tasks.$taskId.tsx`
(`PhaseRow`, `PhaseList`), `front/src/routes/__root.tsx` (`errorComponent`),
`front/src/lib/api/types.ts` (`HandoffPayload`),
`observability/api/app.py:_phase`,
`tests/observability/test_api.py:test_a_saved_at_of_the_wrong_type_does_not_raise_in_the_sort`,
`dispatcher/handoff.py` (`parse`, `_lines`, `_pairs`). The first instance is
revisor round 1's finding 1 in
`/data/.hive/tasks/T-013/revisor-round-1-findings.md` and was fixed in commit
`aba17b2`; the second is *The instance still open* above, which is its record —
it was found after that commit and no round was left to take it.
