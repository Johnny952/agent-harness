---
status: design
approved: 2026-10-23
built: null
supersedes: []
---

# Debt triage — known follow-up work is a sub-task, not debt

**Status:** design, approved by the user on 2026-10-23. Nothing here is built.
The implementing task assigns the ADR its number (*Proposed ADR* below).

## 1. Why the index keeps growing

`docs/debt/` held 55 entries on 2026-10-23. Read one by one, they fall into
four kinds, and only one of them is debt in the sense the index was built for
(`docs/debt/README.md`: work deliberately not done, with a cost of leaving it):

| Kind | What it is | Examples |
|---|---|---|
| Real debt | A shortcut the task took knowingly, or a defect it found in what it touched | T-125-S1-D1, T-133-S1-D1, T-133-S2-D1, T-136-D1, T-143-D1; most of T-008 to T-039 |
| Follow-up work | The next slice of the same goal, left out because the arquitecto narrowed the surface | T-100-D1, T-146-D2, T-150-D1, T-152-D1, T-154-S1-D1, T-162-D1 (now T-163) |
| Gated work | Work that cannot start until a plan step or another task lands | T-146-D1 (server-deployment step 2), T-147-D1, T-153-D1, T-157-D1 |
| Cosmetic finds | Stale counts, citations, names, one line each, of the family the task was already fixing | T-065-D1, T-123-D1, T-130-D1, T-131-D1, T-132-D1 |

From T-065 on, *Why it stayed* is almost always "outside the surface". Three
rules in a row produce that:

1. The arquitecto keeps a task to one surface (`docs/plans/token-economy.md`
   P4) by **narrowing** it, and what it cut becomes the implementador's
   `debt`, instead of a `split` (`docs/plans/task-split.md`, ADR 60).
2. The revisor's `debt_rulings` offer `accepted`, `rejected` and `blocks`.
   "Outside the surface" is always defensible, so follow-up work is accepted
   as debt.
3. The dispatcher files every accepted entry as a `proposed` backlog item
   (`_mirror_filed_debt`, ADR 131). Gated work lands there too, as if it were
   ready to start.

So the next slice of a goal reaches the backlog as debt, carries a debt file
written as a defect, and loses the link to the task that should come before
it. Gated work clutters the proposals with items no one can start.

## 2. Recommendations

In priority order. R1 is the one the user approved explicitly.

### R1 — Follow-up work is a sub-task

- **Before the work starts.** If the goal needs more than one surface, the
  arquitecto splits it. It does not narrow the goal and leave the rest as
  debt. The `split` mechanism already exists. What is missing is a prompt
  line that rules out narrowing.
- **During the work.** An epic's sub-task cannot be split again (ADR 60), and
  an implementador can find the next slice halfway through. For that case the
  revisor gets a fourth ruling, `follow_up`: "not debt, it is the next task".
  - A `follow_up` entry gets no debt file and no index row.
  - The dispatcher files it as a `proposed` backlog item with a new source
    `follow_up`, `depends_on` the current task, and normal priority.
  - The description is built from the declaration's `what` / `where` / `fix`,
    the way `debt.backlog_item` builds one today.

### R2 — Mechanical fixes sweep the whole repo

- A task that fixes a count, a citation, a name or a path fixes every copy of
  it in the repo, even outside its surface.
- A mechanical fix is not a second surface, because it changes no contract.
- The revisor rejects debt that only says "the same fix, elsewhere"
  (`rejected` already means "fix it this round").

### R3 — Gated debt waits

- A debt declaration gets an optional `blocked_by`: a task id, or a plan step
  such as `docs/plans/server-deployment.md step 2`.
- **A task id.** The backlog item is filed with `depends_on` set to that task.
- **A plan step.** No item is filed. The row stays in the index, and its
  *where* says what it is gated on. The task that builds the step reads the
  index (it already does, by trigger) and either resolves the row or files it
  then.

### R4 — The revisor polices the family

- The revisor rejects cosmetic debt from the same family as the task's own
  fix (R2).
- The revisor rules `follow_up` on follow-up work (R1).
- `accepted` stays for real shortcuts and found defects.

### R5 — One-time triage of the open entries

The entries already filed are not rewritten, because the index is append-only
(ADR 297). The operator handles them with the user's go, as an operator step
and not a dispatched task:

- **Follow-up entries.** The backlog item is re-titled as the task it is and
  given its `depends_on`. Where it was already taken (T-162-D1 → T-163), it
  is left alone.
- **Gated entries.** The backlog item is dropped and the index row stays.
- **Cosmetic entries.** They are swept by one small task under R2.

## 3. How the implementation is split

The work spans three surfaces, so it is an epic. The order matters:
- the schema comes first, because the prompts describe it;
- the dispatcher filing comes with the schema field it reads.

| Sub-task | Surface | Builds | After |
|---|---|---|---|
| S1 | `follow_up` ruling | Adds `follow_up` to `_DEBT_RULINGS_PROPERTY` in `dispatcher/handoff.py`, `debt.RULINGS` and `tasks.SOURCES`. The dispatcher files `follow_up` entries as backlog items with `depends_on` the current task and skips them in the auditor's filing note. Tests. Appends the ADR. | — |
| S2 | `blocked_by` on a debt declaration | Adds the optional field in `dispatcher/handoff.py`. `_mirror_filed_debt` / `tasks.file_debt` set `depends_on` for a task id and file nothing for a plan step. `dispatch import-debt` behaves the same. Tests. | S1 |
| S3 | Role prompts and the debt rules | `project_docs._ARQUITECTO`: no narrowing, split instead (R1). `_IMPLEMENTADOR`: R2 and `blocked_by` (R3). `_REVISOR`: `follow_up` and the family rule (R4). `_AUDITOR`: `follow_up` entries are not filed. Matching rules in `docs/debt/README.md`. The prompt tests that pin those strings. | S1, S2 |

R5 is the operator's, after S3 merges.

## Proposed ADR

## ADR <next> — Known follow-up work is a sub-task, not debt

**Status:** proposed (`docs/plans/debt-triage.md`).

**Context.** Most entries filed from T-065 on are not debt.
- They are the next slice of the task's own goal, cut by narrowing the
  surface (P4).
- Or they are work gated on a plan step.
- Or they are one-line copies of the fix the task was making.

Each one was accepted as debt, given a debt file and filed as a ready backlog
proposal (ADR 131). The follow-up lost its order, and the backlog filled with
items that cannot start.

**Decision.**
1. The arquitecto does not narrow a goal that spans surfaces. It returns a
   `split` (ADR 60).
2. The revisor gets a fourth debt ruling, `follow_up`.
   - The entry is filed as a `proposed` backlog item with source `follow_up`
     and `depends_on` the task that declared it.
   - It gets no debt file and no index row.
3. A debt declaration may carry `blocked_by`.
   - A task id becomes the backlog item's `depends_on`.
   - A plan step files no backlog item. The index row is the record until the
     step is built.
4. A mechanical fix (count, citation, name, path) is applied to every copy in
   the repo. It is not a second surface. The revisor rejects debt that defers
   one.
5. `accepted` debt means a deliberate shortcut, or a defect found in what the
   task touched. Nothing else.

**Consequences.**
- The index shrinks to real debt.
- Follow-up work shows up in the backlog in order, behind the task that
  revealed it.
- Gated work stops being proposed before it can run.
- The revisor's rulings grow to four. `tasks.SOURCES` gains `follow_up`.
- Old rows are left as written (ADR 297). R5 of the plan handles their
  backlog items once.

**Ruled out.**
- *Letting an epic's sub-task split again.* This reopens the depth limit
  ADR 60 set on purpose. A filed follow-up with `depends_on` gives the same
  order without nesting.
- *Dropping the auto-filing of ADR 131.* Real debt still needs to reach the
  backlog. Only what is filed, and with what dependency, changes.
- *Rewriting the existing entries.* The index is append-only, and their
  backlog items are what gets cleaned up.
