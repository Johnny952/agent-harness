# `read_task_file` splits on the first two `---` anywhere in the file, not on delimiter lines

**When it applies:** you are adding a frontmatter key to a task file that
holds free text (an operator's or a model's), or reading a legacy
`description` key out of one.

**Status:** unconfirmed — read off the code by T-026's arquitecto and noted
again by its revisor in both rounds. No live task file has the shape, and no
test pins it either way.

## What is true

`dispatcher/context_transfer.py`'s `read_task_file` does
`text.split(_FRONTMATTER_DELIM, 2)` with the delimiter `---`. That is a
substring split, not a match on a line that is exactly three dashes. A
frontmatter *value* holding `---` (a markdown rule in a description, a table
separator row, a long dash run) ends the frontmatter there: the YAML is cut
mid-value and what follows becomes the body. The body itself is safe, since
the split stops after two.

`dispatcher/tasks.py` uses the same split in `_read_frontmatter` on purpose,
so `is_unmigrated` and `migrate` see what `read_task_file` sees and inherit
the cut for a legacy description that holds `---`.

## What to do

Keep free text out of the frontmatter: that is what moving the description to
its own `description.md` did, and it removed the one key where this was
likely. If a new key has to hold prose, fix the split first (match the
delimiter as a whole line) and add the case to
`tests/dispatcher/test_context_transfer.py`; do not rely on the YAML block
style to protect it.

An operator running `dispatch migrate` over a hive should read a refusal that
says `unreadable task file` with this in mind: the file may be a description
with a dash rule in it, to be moved by hand.

## Evidence

T-026: `dispatcher/context_transfer.py` `read_task_file` and
`_FRONTMATTER_DELIM`, `dispatcher/tasks.py` `_read_frontmatter`. The
arquitecto read all 23 live descriptions and found none with `---` in a line.
