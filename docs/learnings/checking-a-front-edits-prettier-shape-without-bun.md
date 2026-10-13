# Checking a `front/` edit's prettier shape when the card does not grant `bun`

**When it applies:** your card grants no `front/` script, you wrote or are
reviewing an edit under `front/src/`, and the dispatcher's lint gate will be
the first thing to run prettier over it.

**Status:** unconfirmed — both halves are T-034's revisor's, from reading
and not from a run. No phase of T-034 ran `bun run lint`; the dispatcher's
gate after the task is the first observation of either.

## Symptom

None was produced. Two questions cost the review time:

```
const subtitle = t ? (t.description ?? <Absent label="no description" />) : undefined;
```

Does prettier keep the parentheses around the `??`, or strip them and turn
the hand-written line into a `prettier/prettier` error? And a width check
of the edited files printed lines the diff never touched.

## Why

- `front/bun.lock` locks prettier at 3.9.6. The revisor's reading is that
  prettier from 3.4 on keeps, and adds, parentheses around a `??` that sits
  inside a ternary arm, so the line above is already in prettier's shape.
  The version the behaviour arrived in was not checked against a changelog.
- `cut -c101- <file>` prints every line past `printWidth` 100, and prettier
  never breaks a string literal
  (`docs/learnings/an-emptystate-body-is-an-attribute-string-prettier-leaves-alone.md`),
  so a `front/` file carries long lines that are correct and were there
  before the edit.

## Rule

Write `t ? (a ?? b) : c` with the parentheses, and judge a width check by
the lines the diff added, never by the count; say in the handoff that the
lint was not run.

## Evidence

T-034's revisor handoff, round 1: `cut -c101-` over the four edited `front/`
files showed no new line past 100. What a red lint costs and why a long line
is an error and not a warning:
`docs/learnings/fronts-lint-turns-a-long-line-into-an-error-not-a-warning.md`.
