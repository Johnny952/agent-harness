# `docs/decisions.md` is a megabyte: read an ADR by offset

**When it applies:** you need one ADR out of `docs/decisions.md` and are about
to Read the whole file.

**Status:** unconfirmed — proposed by T-164-S2's arquitecto; the auditor
measured the file at 1,020,756 bytes on 2026-10-23.

## What is true

The file holds every ADR since the project began and only grows (its preamble
forbids editing an entry). A whole-file Read is over the tool's size limit and,
where it is allowed, spends a turn's context on hundreds of ADRs you do not
need.

## What to do

`grep -n "^## ADR 396" docs/decisions.md` (the ADR's number as written in its
heading), then Read with `offset` at that line and a `limit` sized to the
entry, about 80-120 lines.

## Evidence

`ls -la docs/decisions.md` on the T-164-S2 branch: 1020756 bytes.
