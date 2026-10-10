# A docker image tag in backticks reads as a path to the `pointers` gate — name the image outside them

**When it applies:** you are writing a docker image reference into a doc under
`docs/` and reach for backticks around it — any namespace/image:tag whose tag
has a dot in it, the agent image's bun base at 1.3.12 being the one that bit.

**Status:** unconfirmed — one task. T-019's revisor read the mechanism off the
gate after the dispatcher reported it against the implementador's round 1.

## Symptom

The dispatcher's `pointers` gate reports the image as a broken citation, in a
note of this shape (the image is written here without its backticks, which is
the whole rule):

```
pointers (note) — docs/decisions.md points at oven/bun:1.3.12 — those paths
are not there. Fix the pointer or the path.
```

## Why

`dispatcher/gates.py:pointer_token` rejects far more than it accepts, but an
image tag survives every rejection. It has a slash, no whitespace and none of
the characters the gate treats as prose, no `://`, and no leading `-`, `/` or
`~`. The last test is `_HAS_EXTENSION` against the final path segment, and a
segment ending `:1.3.12` ends in `.12` — a dotted tag reads as a file
extension, so the token is treated as a path and resolves nowhere. An image
whose tag has no dot, like Node's `24-bookworm-slim`, passes only by luck.

The match is a regex over whole lines, so a fenced block does not hide a
backticked tag; only taking the backticks off does. And in a record —
`docs/decisions.md`, an implementation note, an entry in this directory — the
gate reads only the lines a task added, so the note fires on the task that
wrote it and never again: fix it in that round.

## What to do

Name the image outside the backticks, as `docs/decisions.md` **ADR 39**
("copies `bun` 1.3.12 from the official image") and `docs/debt/T-013-D1.md`
*How it was resolved* ("ships `bun` 1.3.12") already do; ADR 46's first
*Context* bullet, "the official `bun` image at 1.3.12", is the example T-019
corrected to. A quotation of the whole Dockerfile
line in backticks is safe, because the spaces in it make `pointer_token`
return `None` — `docs/implementations/T-019.md`'s verification table keeps
one — but the bare reference is the one to avoid.

## Evidence

Reported by the dispatcher against T-019's implementador round 1 (`629caed`),
in that phase's *Dispatcher gates* note on the task card; fixed in round 2 by
rewording ADR 46's bullet. Inbox entry
`T-019-a-docker-image-tag-reads-as-a-path-to-the-pointers-gate.md`, which
carries the gate's note verbatim.
