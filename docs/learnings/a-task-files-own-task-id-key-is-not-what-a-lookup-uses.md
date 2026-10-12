# A task file's own `task_id` key is not what a lookup uses, and one check still reads it

**When it applies:** you are writing or changing a check in
`dispatcher/tasks.py` that compares a task's id with a list in another file
(`split_into`, `depends_on`), or setting up a hand-written epic in a test.

**Status:** unconfirmed — proposed by T-030's implementador as "identify a
parent by the id its child names, as `close_epic` and `epic_problems` do".
The auditor checked it against both functions and it holds for the parent
only: the revisor's risk about the child is right, and is the second
paragraph below.

## What is true

Every lookup goes by file name: `context_transfer.task_file_path(hive, id)`.
`read_task_file` fills `TaskFile.task_id` from the frontmatter's `task_id`
key and nothing compares that with the name of the file it came from. In a
file the dispatcher wrote the two agree. In a hand-written one they can
differ, and the epic format is exactly what an operator may write by hand.

`close_epic` and `epic_problems` both name the parent by the child's `epic`
value, the file they read, and never by the parent's own key. For the child
they disagree. `close_epic(hive_dir, task_id)` tests its `task_id` argument
against `split_into`. `epic_problems(hive_dir, task)` tests `task.task_id`,
the frontmatter key. A child file named after one id and carrying another in
its key is checked by the gate under the key and by the merge under the
file name.

No run has produced it: `file_split` writes both the same.

## What to do

Take the id from what was looked up, an argument or the value another file
names, not from `TaskFile.task_id`. If you change `epic_problems`, pass it
the id `open_cycle` was called with. In a test hive, keep the key and the
file name equal unless the mismatch is what the test is about.

## Evidence

T-030: `close_epic`, `epic_problems` and `_read_for_check` in
`dispatcher/tasks.py`; `read_task_file` and `task_file_path` in
`dispatcher/context_transfer.py`; the gate in `open_cycle` in
`dispatcher/dispatcher.py`. Read off the code; no test has a file whose key
and name differ.
