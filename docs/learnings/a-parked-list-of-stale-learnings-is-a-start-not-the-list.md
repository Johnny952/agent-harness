# A parked list of stale learnings is where the auditor starts, not the list

**When it applies:** you are the auditor and a note under the task's scratch
directory in `/data/.hive/tasks/` names the `docs/learnings/` entries your
task made stale; or you are a writer phase parking that note.

**Status:** unconfirmed — proposed by T-031's revisor, and hit by T-031's
auditor in the same cycle.

## What happened

T-031's arquitecto parked a note naming two entries to amend and two as
"unaffected, checked". The revisor found a third stale entry the note
omitted, `render-writes-a-third-line-shape-under-split`, whose *Rule* still
said no role's schema offers `split`. The auditor then found that one of the
two "unaffected" entries, `nothing-writes-blocked-to-a-task-file`, lists
what `open_cycle` refuses and says a missing `blocked` does not mean the run
finished: T-031 added a refusal and an outcome to both.

## Why

The note is written before the build, from the design, by the phase that
knows the change least concretely. Writer phases do not edit
`docs/learnings/`, so nothing corrects the note as the build lands.

## Rule

Grep `docs/learnings/` for the claim the task changed — the symbol, the
test name, the words "inert", "nothing calls", "until step" — before
trusting the parked list in either direction.

## Evidence

T-031's parked note and its revisor's review, item 5 of *Worth knowing*, both
in that task's scratch directory. The four entries amended by T-031's
auditor each say so under **Status**.
