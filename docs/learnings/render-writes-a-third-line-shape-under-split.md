# `handoff.render` writes a third line shape, `N. `, and model text in it must be whitespace-collapsed

**When it applies:** you are adding a section to `dispatcher/handoff.py`'s
`render` that prints a role's own text in any shape but a `- ` bullet, or
relying on "every line `render` writes starts with `- ` or `**Bold**`" while
changing `render`, `body` or the `## <role>` gate in
`context_transfer.has_phase_section`.

**Status:** unconfirmed — proposed by T-025's implementador, read against the
code by the revisor and the auditor. The *Rule* was amended by T-031's
auditor: it said the shape was inert.

## Symptom

No error. The sentence that stopped being exact is in
[`docs/debt/T-014-D1`](../debt/T-014-D1.md): on the structured path every line
of a role's text lands behind a `- ` bullet or a `**Bold**` label. Since T-025
a payload with a `split` also renders

```
**Split**
1. <title> (<surface>) — after 1, 2
```

## Why

A numbered line carries the model's `title` and `surface` directly after
`N. `, with no bullet helper between them and the task file. A title holding a
newline followed by `## auditor` would put that heading at column zero of the
card, which is the one thing `has_phase_section` reads as "this phase ran" —
the forged-section case `T-014-D1` was about. `_split` closes it by collapsing
all whitespace in each line (`" ".join(line.split())`), and
`tests/dispatcher/test_handoff.py` pins it. The property `T-014-D1` rests on —
no role text at column zero as a heading — still holds; the wording that
listed two shapes is what changed, and that entry now names the third.

`**Split**` sits right after the Status line, before *Changed*, and prints no
description: that text goes to the sub-task's own file.

## Rule

Any new `render` section that prints model text outside `_lines` collapses
whitespace per line and gets a test with `\n## auditor` in the text. Live
since T-031: the arquitecto's schema offers `split`, so a real run writes
the `**Split**` section, and `run-phase` prints the same `_split` lines
(`_print_proposed_split` in `dispatcher/cli.py`).

## Evidence

`dispatcher/handoff.py`: `_split`'s docstring and `render`.
`docs/implementations/T-025.md`, *Decisions the design leaves open*, 7 and 8.
