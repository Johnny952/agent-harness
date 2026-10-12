# Widening a console type to include `null` is only partly visible to the type checker

**When it applies:** you are adding `| null` to a field of an interface in
`front/src/lib/api/types.ts`, and plan to find the reads you must guard by
running the typecheck and fixing what it reports.

**Status:** unconfirmed — reasoned by T-034's arquitecto from the three
reads of `Task.description` and agreed by its implementador and revisor by
reading. No phase of T-034 ran `bun run typecheck`: the card granted
`python3 -m pytest` only.

## Symptom

There is none, which is the trap. `Task.description` went from `string` to
`string | null` and two of its three reads still compile unguarded:

```
`${t.task_id} ${t.description}`      // template literal: any type is accepted
<p>{task.description}</p>             // JSX child: null is a valid ReactNode
subtitle={t?.description}             // prop typed string: the one tsc reports
```

The first spells `null` as the text `null`, so the Board's search matched it.
The second renders nothing where `docs/ui.md` asks for `Absent`.

## Why

TypeScript accepts any value in a template literal, and `null` is a valid
JSX child. Only a read that passes the value to something typed `string` (a
prop, a parameter) or calls a string method on it is an error. So the
typecheck finds the reads that would throw and misses the reads that are
wrong on screen.

## Rule

List the reads by following the type's bindings (every variable, prop and
parameter typed with the interface, then every access of the field on them),
not by waiting for the typecheck; then guard each at the render.

## Evidence

`docs/implementations/T-034.md` *Every read of the field* is the table made
this way: three reads, of which the type checker's view gives one.
`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md` is
why the guard goes at the render, and
`docs/learnings/narrowing-a-served-type-is-also-a-fixture-edit.md` is the
fixture half of the same edit.
