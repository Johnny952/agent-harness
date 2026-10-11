# A substring prompt test cannot tell the whole description from its Goal and Done when

**When it applies:** you are writing or reading a test of `_role_prompt` (or
any prompt builder) that asserts the task's description is in the prompt.

**Status:** unconfirmed — reported by T-027's implementador.

## What is true

Since T-027 `_role_prompt` embeds only `## Goal` and `## Done when`
(re-joined through `_ask`), not the whole stored description. For a
description with only those two sections, the re-joined text and the stored
text are the same, so `description in prompt` passes whichever the prompt
embeds. The test only discriminates when the description carries an optional
section.

## What to do

Give the test's description an optional section, and assert both that Goal
and Done when are in the prompt and that the optional section's body is not.

## Evidence

T-027: the `_role_prompt` change and its tests.
