# Removing a feature greps the prose the prompts hand to agents, not only the module's name

**When it applies:** you are removing or renaming something the dispatcher
tells its roles about (a board, a card, a verb, a file a phase is told to
write), and your "nothing is left" check is a grep for the module or symbol
name; or you are writing a test that holds such wording out of a prompt.

**Status:** unconfirmed — it cost T-028 one revision round (findings F1 to
F4); the auditor re-read the three strings and the tests.

## What is true

Two modules hold English that every phase reads and no import reaches:

- `dispatcher/project_docs.py`, the role duty texts (`_AUDITOR`,
  `_IMPLEMENTADOR` and their siblings);
- `dispatcher/handoff.py`, the `description` strings of the handoff schema.

T-028 deleted the board seam and grepped `kanban` and `local_board` over
`dispatcher/` and `observability/`. The grep came back clean while three live
strings still described debt cards: the auditor was told filed debt's "cards
are already on the board", the implementador that `resolved_debt` closes "the
entry, and its card", and the `debt_rulings` schema that accepted debt is
"filed and put on the board". None of them names the module. The auditor's
sentence contradicted `debt.filing_note` inside the same prompt.

The obvious test does not work either. These prompts call the task file a
"card" and the hive "the board" on purpose, so a test asserting the bare word
is absent fails on correct text.

## What to do

After the symbol grep, grep those two modules for the feature's *nouns and
verbs in prose* ("on the board", "its card", "mirrors"), and read every hit.
Hold the result with a test that asserts **phrases**, never the word:
`tests/dispatcher/test_project_docs.py`
`test_no_role_is_told_that_debt_has_a_card_on_a_board` and
`tests/dispatcher/test_handoff.py`
`test_no_schema_description_puts_debt_on_a_board` are the shape.

A phase launched before the merge still reads the old text: the prompt is
built from the dispatcher's installed code, not from the task branch.

## Evidence

T-028: `docs/implementations/T-028.md` *Round 2: the prompt text the first
round missed*; the two tests named above.
