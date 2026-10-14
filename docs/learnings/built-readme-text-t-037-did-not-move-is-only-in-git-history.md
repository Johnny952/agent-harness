# Built README text T-037 did not move is only in git history

**When it applies:** you are re-pointing a citation of the root `README.md`
*Future work* or *Known gaps* text written before T-037, and are about to name
`docs/plans/backlog.md` as where that text lives now.

**Status:** unconfirmed — T-039's arquitecto, implementador and revisor all
said it, which is one source; the auditor checked it with a grep of the
backlog and the README.

## Symptom

None: a citation reading "now in `docs/plans/backlog.md` item 2" looks right
and no gate checks a heading or a phrase. T-039's own card made that claim
about the `usage` key names, and they are not in the backlog.

## Why

T-037 moved only the *open* text of each *Prioritized* item into
`docs/plans/backlog.md`, word for word, and cut the README to one status line
per piece (`docs/implementations/T-037.md`, *Classification*). Prose from a
piece it classed built or planned went nowhere: item 2's lead, *Record usage
per phase* with its `usage` key names, the 23–35K figure and *compact between
tasks* exist only in the README as it read at T-037's base, `7cc44ca`.

## Rule

Grep the backlog for the cited phrase before naming it as the new home; if it
is not there, cite the README item "as it read before T-037" with
`git show 7cc44ca:README.md`, or the ADR or plan that quotes it.

## Evidence

T-039: `grep -c -E "cache creation|23–35K|compact between tasks"` over
`docs/plans/backlog.md` and `README.md` printed 0 for both, while
`git grep` at `7cc44ca` finds all three inside item 2
(`docs/implementations/T-039.md`, *Verified after the change*).
