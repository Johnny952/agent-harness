# `bun run lint --fix` applies the prettier format in a phase that has the front scripts

**When it applies:** your card grants `bun run lint` in `front/`, the lint is
red on `prettier/prettier` errors in lines you wrote, and you are about to
hand-apply the reflow (as
[prettier-rewraps-jsx-text-children](prettier-rewraps-jsx-text-children.md)
says) or reach for `bunx prettier`.

**Status:** confirmed by T-120, whose implementador ran it to apply the format
— first reported by T-105-S2's implementador; its auditor
ran `bun run lint --fix` in `front/` and it was not refused (`$ eslint . --fix`,
no change on a clean tree).

## Symptom

`bunx prettier --write` is refused in a phase, and
[prettier-rewraps-jsx-text-children](prettier-rewraps-jsx-text-children.md)
says `eslint --fix` is refused too.

## Why

The grant is on the `bun run lint` script, and bun passes trailing arguments
on to it, so `bun run lint --fix` runs `eslint . --fix`; eslint's
`prettier/prettier` rule then rewrites the files to prettier's format.

## Rule

Run `bun run lint --fix`, read `git diff` for what it rewrote, then
`bun run lint` again; hand-apply a reflow only if that call is refused.

## Evidence

T-105-S2 auditor, 2026-10-19, from `front/`: `bun run lint --fix` printed
`$ eslint . --fix` and exited clean; `git status --short` stayed empty.
