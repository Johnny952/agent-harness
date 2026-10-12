# In the root `README.md`, "card" mostly meant the task file

**When it applies:** you are grepping the root `README.md`, or any prose
written before T-028, for `card` or "the board" to find what describes a
board, or you are judging whether such a grep came back clean.

**Status:** unconfirmed — proposed by T-032's implementador in both rounds;
the auditor re-ran the grep.

## What is true

Before T-032 most `card` hits in the root `README.md` were not board cards.
They named the task file under the hive tasks dir: the learnings, `status`
and `release-account` passages said "card" for the file a phase writes its
handoff to. T-032 reworded those to "task" or "task file".

After T-032, `grep -n -i -E "kanban|local_board|card|the board" README.md`
prints 17 lines of three kinds: sentences saying the board was removed,
citing `docs/decisions.md` ADR 59; the debt index's `card` column, which the
auditor writes as `none`; and `discarded` or `discards`, the word inside
another word.

The dispatcher's prompts still call the task file a card and the hive the
board on purpose
([removing-a-feature-greps-the-prose-the-prompts-hand-to-agents](removing-a-feature-greps-the-prose-the-prompts-hand-to-agents.md)),
and `dispatcher/cli.py`'s help text says "task cards" and "on the board".

## What to do

Read each hit and sort it before editing: a board card, the task file, the
index column, or a substring. Do not require the grep to come back empty.
Keep the table in `docs/implementations/T-032.md` *What a grep of
`README.md` still finds* as the base to compare a later grep against.

## Evidence

T-032: the grep above on the branch, 17 lines;
`docs/implementations/T-032.md` *The `card` hits* and *What a grep of
`README.md` still finds*.
