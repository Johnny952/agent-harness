/**
 * Approvals: the command requests phases filed (ADR 123), and below them the
 * review of a finished task's branch (`docs/plans/review.md`, ADR 219).
 *
 * The review reads three routes and writes one. `/api/tasks` gives the two
 * lists: *Awaiting review* is an `awaiting_review` task, or an older `done`
 * one, whose work is not on main (ADR 240, ADR 249), and *Conflict* is
 * the `conflict` status a failed merge leaves. `/api/tasks/<id>/diff` is the
 * branch against its merge base, drawn by hand (review.md step 7 leaves the
 * diff library to its own ask). `/api/tasks/<id>/comments` gives the comments,
 * and one pinned to a line is drawn under that line while its commit is the
 * branch's tip, and listed as outdated once the branch has moved past it.
 *
 * It writes two things, each at the tip the diff was taken at. A new-side
 * line number opens a form that pins a comment to that line, `{text, file,
 * line, commit}` through the board-service's `task_comment`
 * (`commentTaskMutation`). And the *comment* verdict (review.md step 3, ADR
 * 222) posts `{kind: "comment", text, commit}` to `review_verdict`
 * (`verdictTaskMutation`); it is a comment line too, and the verdicts given
 * are listed above the diff, marked when the branch has moved since.
 *
 * The verdict form also holds *approve* (ADR 270): `{project, commit, text}` to
 * `POST /api/tasks/<id>/approve` (`approveTaskMutation`), at the tip the diff
 * was read at, `Approved.` when nothing was typed. It is off on the conflict
 * lane, on a merged task, and once queued at this tip; the dispatcher's
 * `approve at <commit>:` comments are listed as outcomes. *Request changes*
 * (ADR 276) posts the same body to `/request-changes`
 * (`requestChangesMutation`) with the typed words required and no default; it
 * is on only for an `awaiting_review` task not on main, and its outcome is the
 * task leaving the list and returning at a new tip. There is no resolve route
 * yet, so the verdict panel names the CLI.
 */
import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { CommandRequestsSection } from "@/components/console/command-requests";
import {
  Absent,
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  ProjectMark,
  WarningBanner,
} from "@/components/console/primitives";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import {
  approveTaskMutation,
  commentTaskMutation,
  commentsQuery,
  requestChangesMutation,
  taskDiffQuery,
  tasksQuery,
  verdictTaskMutation,
} from "@/lib/api/queries";
import type { Task, TaskComment, TaskDiff } from "@/lib/api/types";
import { MAX_COMMENT_CHARS, commentAnchor, commentLength, commentProblem } from "@/lib/comments";
import {
  approveOutcomes,
  parseDiff,
  requestChangesRefusal,
  reviewLane,
  reviewVerdicts,
  type ReviewLane as Tab,
} from "@/lib/review";
import { formatDateClock } from "@/lib/format";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/approvals")({
  head: () => ({
    meta: [
      { title: "Approvals — harness operations console" },
      {
        name: "description",
        content:
          "What waits on the operator: command requests, and finished task branches to review before they merge.",
      },
      { property: "og:title", content: "Approvals — harness operations console" },
      { property: "og:description", content: "One inbox for everything waiting on the operator." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ApprovalsPage,
});

function ApprovalsPage() {
  const q = useQuery(tasksQuery);
  const [tab, setTab] = useState<Tab>("review");
  const [selected, setSelected] = useState<string | null>(null);

  const tasks = useMemo(() => (Array.isArray(q.data?.data) ? q.data.data : []), [q.data]);
  const lanes = useMemo(() => {
    const out: Record<Tab, Task[]> = { review: [], conflict: [] };
    for (const t of tasks) {
      const lane = reviewLane(t);
      if (lane) out[lane].push(t);
    }
    return out;
  }, [tasks]);
  const rows = lanes[tab];
  const current = rows.find((t) => t.task_id === selected) ?? rows[0] ?? null;

  return (
    <AppShell>
      <div className="flex h-full flex-col">
        <PageHeader
          title="Approvals"
          subtitle="Everything the harness is waiting on you for."
          right={<RefreshedAt at={q.dataUpdatedAt} />}
        />
        {/* Real, and read-only: the command requests phases filed (ADR 123). */}
        <CommandRequestsSection />
        <section aria-label="Review" className="flex min-h-0 flex-1 flex-col">
          <div className="flex gap-1 border-b border-border px-4 py-2">
            {(["review", "conflict"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "rounded-sm px-2 py-1 text-[11px]",
                  tab === t ? "bg-surface-2 text-foreground" : "text-muted-foreground",
                )}
              >
                {t === "review" ? "Awaiting review" : "Conflict"} · {lanes[t].length}
              </button>
            ))}
          </div>
          <WarningBanner warnings={q.data?.warnings ?? []} />
          {q.isError ? (
            <ErrorState
              title="The board could not be read"
              body={`/api/tasks did not answer${
                q.error instanceof Error && q.error.message ? `: ${q.error.message}` : ""
              }. What is waiting for review is unknown rather than empty.`}
            />
          ) : q.isLoading ? (
            <p className="px-4 py-6 text-xs text-muted-foreground">Reading the board…</p>
          ) : rows.length === 0 ? (
            <EmptyState
              title={tab === "review" ? "Nothing is awaiting review" : "No task is in conflict"}
              body={
                tab === "review"
                  ? "A task that finished and is not yet on main is listed here, with its branch's diff."
                  : "A task whose merge stopped on a conflict is listed here until the branch merges cleanly."
              }
            />
          ) : (
            <div className="flex min-h-0 flex-1">
              <ul className="w-[300px] shrink-0 overflow-y-auto border-r border-border">
                {rows.map((t) => (
                  <li key={t.task_id}>
                    <button
                      onClick={() => setSelected(t.task_id)}
                      data-testid="review-row"
                      className={cn(
                        "w-full border-b border-border px-3 py-2 text-left hover:bg-surface-2/60",
                        current?.task_id === t.task_id && "bg-surface-2",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <Mono className="text-[11px] font-semibold">{t.task_id}</Mono>
                        <span className="flex-1" />
                        <ProjectMark project={t.project} />
                      </div>
                      <p className="mt-0.5 truncate text-xs">
                        {t.title ?? <Absent label="no title" />}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
              {current && <Review key={current.task_id} task={current} lane={tab} />}
            </div>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function Review({ task, lane }: { task: Task; lane: Tab }) {
  const diffQ = useQuery(taskDiffQuery(task.task_id, task.project));
  const commentsQ = useQuery(commentsQuery(task.task_id));
  const diff = diffQ.data?.data ?? null;
  const served: unknown = commentsQ.data?.data?.comments;
  const comments = Array.isArray(served) ? (served as TaskComment[]) : [];
  const warnings = [...(diffQ.data?.warnings ?? []), ...(commentsQ.data?.warnings ?? [])];

  return (
    <div className="min-w-0 flex-1 overflow-y-auto pb-4">
      <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
        <Link
          to="/tasks/$taskId"
          params={{ taskId: task.task_id }}
          className="mono text-sm font-semibold underline"
        >
          {task.task_id}
        </Link>
        <span className="text-sm">{task.title ?? <Absent label="no title" />}</span>
      </div>
      <WarningBanner warnings={warnings} />
      {commentsQ.isError && (
        <ErrorState
          title="The comments did not answer"
          body={`/api/tasks/${task.task_id}/comments could not be read, so what was said on this branch is unknown rather than absent.`}
        />
      )}
      <div className="space-y-3 px-4 pt-3">
        <VerdictPanel task={task} lane={lane} />
        {diffQ.isError ? (
          <ErrorState
            title="The diff could not be read"
            body={`/api/tasks/${task.task_id}/diff did not answer${
              diffQ.error instanceof Error && diffQ.error.message ? `: ${diffQ.error.message}` : ""
            }.`}
          />
        ) : diffQ.isLoading ? (
          <p className="text-xs text-muted-foreground">Reading the diff…</p>
        ) : diff === null ? (
          <p className="text-xs italic text-muted-foreground">
            There is no diff to show; the reason is in the warning above.
          </p>
        ) : (
          <>
            <BranchLine diff={diff} />
            <Verdicts task={task} lane={lane} diff={diff} comments={comments} />
            <OutdatedComments comments={comments} diff={diff} />
            <DiffView taskId={task.task_id} diff={diff} comments={comments} />
          </>
        )}
      </div>
    </div>
  );
}

/**
 * This names the CLI merge, for a push an approve could not land, and for a
 * conflict the merge of the base into the branch that comes first (never a
 * rebase: a phase's history stays as it ran). The *comment* verdict, the
 * *approve* and *request changes* are built, in the form under the branch line.
 */
function VerdictPanel({ task, lane }: { task: Task; lane: Tab }) {
  const project = task.project ? ` --project ${task.project}` : "";
  const merge = `dispatch merge-task ${task.task_id}${project}`;
  return (
    <div className="panel space-y-1.5 px-3 py-2 text-xs" data-testid="review-verdict">
      <p className="text-muted-foreground">
        {lane === "conflict"
          ? "The merge stopped on a conflict. Merge the base into the task branch, resolve it there, then merge again:"
          : "If the push an approve queued was refused, merge from the CLI (add --push to push too):"}
      </p>
      <Mono className="block select-all text-[11px]">{merge}</Mono>
      <p className="text-[11px] text-muted-foreground">
        A line number in the diff pins a comment to that line at this tip, and the comment verdict
        below is on the change as a whole at this tip; a plain comment on the task is on the{" "}
        <Link to="/tasks/$taskId" params={{ taskId: task.task_id }} className="underline">
          task's page
        </Link>
        .
      </p>
    </div>
  );
}

function BranchLine({ diff }: { diff: TaskDiff }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="review-branch">
      <GitBranch className="size-3.5" />
      <Mono>{diff.branch}</Mono> <span className="text-muted-foreground">→</span>{" "}
      <Mono>{diff.base}</Mono>
      <span className="text-muted-foreground">at</span>
      <Mono className="text-muted-foreground">{diff.commit.slice(0, 12)}</Mono>
      <span className="text-muted-foreground">from</span>
      <Mono className="text-muted-foreground">{diff.merge_base.slice(0, 12)}</Mono>
    </div>
  );
}

/**
 * The *comment* verdict (review.md step 3, ADR 222): the ones given, then a
 * form for a new one at the tip. A verdict is a comment line, so it refetches
 * with the comments and shows from what was stored. One given at another
 * commit is marked: it was on a change the branch has moved past. It starts
 * nothing; no dispatcher reads it yet.
 */
function Verdicts({
  task,
  lane,
  diff,
  comments,
}: {
  task: Task;
  lane: Tab;
  diff: TaskDiff;
  comments: TaskComment[];
}) {
  const taskId = task.task_id;
  const qc = useQueryClient();
  const post = useMutation(verdictTaskMutation(qc));
  const approve = useMutation(approveTaskMutation(qc));
  const requestChanges = useMutation(requestChangesMutation(qc));
  const [text, setText] = useState("");
  // The tip and the kind of the review-ending verdict queued from this screen; both
  // buttons wait for the tip to move (ADR 276).
  const [queuedAt, setQueuedAt] = useState<{
    commit: string;
    kind: "approve" | "request_changes";
  } | null>(null);
  const given = reviewVerdicts(comments);
  const outcomes = approveOutcomes(comments);
  const problem = commentProblem(text);
  const over = commentLength(text) > MAX_COMMENT_CHARS;
  const queued = queuedAt?.commit === diff.commit ? queuedAt.kind : null;
  const anyPending = post.isPending || approve.isPending || requestChanges.isPending;
  const approveOff =
    lane === "conflict" || task.merged !== false || over || anyPending || queued !== null;
  const refusal = requestChangesRefusal(task);
  const requestOff = refusal !== null || problem !== null || anyPending || queued !== null;

  // A refusal carrying `verdict` was recorded and queued nothing; any other is its words.
  const refused = (what: string, err: unknown) => {
    const recorded =
      err instanceof ApiError &&
      typeof err.body === "object" &&
      err.body !== null &&
      "verdict" in err.body;
    toast.error(
      recorded
        ? `the ${what} verdict was recorded on ${taskId}, but nothing was queued: ${(err as ApiError).message}`
        : err instanceof Error
          ? err.message
          : `the ${what} on ${taskId} was not sent`,
    );
  };

  const sendApprove = () => {
    if (approveOff) return;
    approve.mutate(
      {
        taskId,
        body: {
          commit: diff.commit,
          text: text.trim() ? text : "Approved.",
          ...(typeof task.project === "string" ? { project: task.project } : {}),
        },
      },
      {
        onSuccess: (res) => {
          setText("");
          setQueuedAt({ commit: diff.commit, kind: "approve" });
          toast.success(`approve queued as ${res.action.id}; the outcome comes as a comment`);
        },
        onError: (err) => refused("approve", err),
      },
    );
  };

  const sendRequestChanges = () => {
    if (requestOff) return;
    requestChanges.mutate(
      {
        taskId,
        body: {
          commit: diff.commit,
          text,
          ...(typeof task.project === "string" ? { project: task.project } : {}),
        },
      },
      {
        onSuccess: (res) => {
          setText("");
          setQueuedAt({ commit: diff.commit, kind: "request_changes" });
          toast.success(
            `changes requested, queued as ${res.action.id}; ${taskId} leaves the list while its cycle reruns`,
          );
        },
        onError: (err) => refused("request for changes", err),
      },
    );
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (problem || post.isPending) return;
    post.mutate(
      { taskId, body: { kind: "comment", text, commit: diff.commit } },
      {
        onSuccess: () => {
          setText("");
          toast.success(`comment verdict recorded on ${taskId}`);
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : `the verdict on ${taskId} was not recorded`,
          ),
      },
    );
  };

  return (
    <div className="panel space-y-2 px-3 py-2" data-testid="review-verdicts">
      <p className="label-xs">verdicts · {given.length}</p>
      {given.length > 0 && (
        <ul className="space-y-1.5">
          {given.map((c, i) => (
            <CommentLine
              key={i}
              comment={c}
              note={`${c.verdict?.kind} at ${c.verdict?.commit.slice(0, 12)}${
                c.verdict?.commit === diff.commit ? "" : " · at an earlier tip"
              }`}
            />
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="space-y-1.5" data-testid="verdict-form">
        <Textarea
          aria-label={`comment verdict on ${taskId}`}
          placeholder="What you make of the change as a whole, at this tip"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-h-[56px] text-xs"
        />
        <div className="flex items-center justify-between gap-2 text-[11px]">
          <span className={cn("text-muted-foreground", over && "text-destructive")}>
            {over && problem
              ? problem
              : `${commentLength(text).toLocaleString("en-US")} / ${MAX_COMMENT_CHARS.toLocaleString("en-US")}`}
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              onClick={sendApprove}
              disabled={approveOff}
              title={
                lane === "conflict"
                  ? "The conflict waits on resolve conflict; approve once the branch merges cleanly"
                  : task.merged !== false
                    ? "This task's work is already on main"
                    : "Approve the change at this tip and queue its merge"
              }
              className="rounded-sm border border-border-strong px-2 py-0.5 hover:text-foreground disabled:opacity-40"
            >
              {approve.isPending
                ? "approving…"
                : queued === "approve"
                  ? "approve queued"
                  : "approve"}
            </button>
            <button
              type="button"
              onClick={sendRequestChanges}
              disabled={requestOff}
              title={
                refusal ??
                (problem
                  ? "Write what to change first"
                  : "Send the change back to the arquitecto at this tip")
              }
              className="rounded-sm border border-border-strong px-2 py-0.5 hover:text-foreground disabled:opacity-40"
            >
              {requestChanges.isPending
                ? "requesting…"
                : queued === "request_changes"
                  ? "changes requested"
                  : "request changes"}
            </button>
            <button
              type="submit"
              disabled={problem !== null || anyPending}
              className="rounded-sm border border-border-strong px-2 py-0.5 hover:text-foreground disabled:opacity-40"
            >
              {post.isPending ? "posting…" : "comment verdict"}
            </button>
          </span>
        </div>
      </form>
      {outcomes.length > 0 && (
        <div data-testid="review-outcomes">
          <p className="label-xs mb-1">outcomes · {outcomes.length}</p>
          <ul className="space-y-1.5">
            {outcomes.map((c, i) => (
              <CommentLine key={i} comment={c} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** A pinned comment taken at another commit than the tip: listed, not placed. */
function OutdatedComments({ comments, diff }: { comments: TaskComment[]; diff: TaskDiff }) {
  const outdated = comments.filter((c) => commentAnchor(c) && c.commit !== diff.commit);
  if (outdated.length === 0) return null;
  return (
    <div className="panel px-3 py-2" data-testid="outdated-comments">
      <p className="label-xs mb-1">outdated · the branch has moved since</p>
      <ul className="space-y-1.5">
        {outdated.map((c, i) => (
          <CommentLine key={i} comment={c} anchor />
        ))}
      </ul>
    </div>
  );
}

function CommentLine({
  comment,
  anchor = false,
  note,
}: {
  comment: TaskComment;
  anchor?: boolean;
  /** What the line is past its author and time: a verdict's kind and tip. */
  note?: string;
}) {
  return (
    <li className="text-[11px]" data-testid="review-comment">
      <span className="text-muted-foreground">
        {typeof comment.author === "string" && comment.author ? comment.author : "no author"} ·{" "}
        {formatDateClock(typeof comment.at === "string" ? comment.at : null)}
      </span>
      {anchor && <Mono className="ml-2 text-[10px]">{commentAnchor(comment)}</Mono>}
      {note && <Mono className="ml-2 text-[10px]">{note}</Mono>}
      <p className="whitespace-pre-wrap break-words">
        {typeof comment.text === "string" ? comment.text : <Absent label="no text" />}
      </p>
    </li>
  );
}

function DiffView({
  taskId,
  diff,
  comments,
}: {
  taskId: string;
  diff: TaskDiff;
  comments: TaskComment[];
}) {
  // The one line whose form is open, as `file:line`; a second click closes it.
  const [open, setOpen] = useState<string | null>(null);
  if (!diff.diff.trim())
    return (
      <EmptyState
        title="No change of its own"
        body={`${diff.branch} holds nothing ${diff.base} does not already have from their merge base.`}
      />
    );
  const placed = comments.filter((c) => commentAnchor(c) && c.commit === diff.commit);
  return (
    <div className="space-y-3">
      {parseDiff(diff.diff).map((f) => (
        <div key={f.name} className="panel overflow-hidden" data-testid="diff-file">
          <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5 text-[11px]">
            <Mono className="flex-1">{f.name}</Mono>
            <span className="mono text-success">+{f.add}</span>
            <span className="mono text-destructive">−{f.del}</span>
          </div>
          <pre className="mono overflow-x-auto text-[11px] leading-5">
            {f.lines.map((l, i) => {
              const here =
                l.newLine === null
                  ? []
                  : placed.filter((c) => c.file === f.name && c.line === l.newLine);
              return (
                <div key={i}>
                  <div
                    className={cn(
                      "flex px-3",
                      l.text.startsWith("+") && "bg-success/10 text-success",
                      l.text.startsWith("-") && "bg-destructive/10 text-destructive",
                      l.text.startsWith("@@") && "bg-info/10 text-info",
                    )}
                  >
                    {l.newLine === null ? (
                      <span className="w-10 shrink-0 select-none pr-2" />
                    ) : (
                      <button
                        type="button"
                        aria-label={`comment on ${f.name} line ${l.newLine}`}
                        onClick={() => {
                          const key = `${f.name}:${l.newLine}`;
                          setOpen((o) => (o === key ? null : key));
                        }}
                        className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground hover:text-foreground hover:underline"
                      >
                        {l.newLine}
                      </button>
                    )}
                    <span>{l.text || " "}</span>
                  </div>
                  {here.length > 0 && (
                    <ul className="space-y-1 border-y border-border bg-surface-2 px-3 py-1.5 font-sans">
                      {here.map((c, j) => (
                        <CommentLine key={j} comment={c} />
                      ))}
                    </ul>
                  )}
                  {l.newLine !== null && open === `${f.name}:${l.newLine}` && (
                    <LineCommentForm
                      taskId={taskId}
                      file={f.name}
                      line={l.newLine}
                      commit={diff.commit}
                      onDone={() => setOpen(null)}
                    />
                  )}
                </div>
              );
            })}
          </pre>
        </div>
      ))}
      {diff.truncated && (
        <p className="text-[11px] text-muted-foreground">
          The diff is cut here; the rest is past what the api serves.
        </p>
      )}
    </div>
  );
}

/**
 * A comment pinned to one new-side line at the tip, through `task_comment`
 * (ADR 219). Checked for length as the task page's form is; the board-service
 * names the author, and the comments refetch on settle, so the comment shows
 * under its line from what was stored, not from what was typed.
 */
function LineCommentForm({
  taskId,
  file,
  line,
  commit,
  onDone,
}: {
  taskId: string;
  file: string;
  line: number;
  commit: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const post = useMutation(commentTaskMutation(qc));
  const [text, setText] = useState("");
  const problem = commentProblem(text);
  const over = commentLength(text) > MAX_COMMENT_CHARS;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (problem || post.isPending) return;
    post.mutate(
      { taskId, body: { text, file, line, commit } },
      {
        onSuccess: () => {
          setText("");
          toast.success(`comment added to ${file}:${line}`);
          onDone();
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : `the comment on ${file}:${line} was not added`,
          ),
      },
    );
  };

  return (
    <form
      onSubmit={submit}
      className="space-y-1.5 border-y border-border bg-surface-2 px-3 py-1.5 font-sans"
      data-testid="line-comment-form"
    >
      <Textarea
        aria-label={`comment on ${file} line ${line}`}
        placeholder={`Comment on ${file}:${line}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="min-h-[56px] text-xs"
        autoFocus
      />
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className={cn("text-muted-foreground", over && "text-destructive")}>
          {over && problem
            ? problem
            : `${commentLength(text).toLocaleString("en-US")} / ${MAX_COMMENT_CHARS.toLocaleString("en-US")}`}
        </span>
        <span className="flex gap-2">
          <button
            type="button"
            onClick={onDone}
            className="rounded-sm border border-border px-2 py-0.5 hover:text-foreground"
          >
            cancel
          </button>
          <button
            type="submit"
            disabled={problem !== null || post.isPending}
            className="rounded-sm border border-border-strong px-2 py-0.5 hover:text-foreground disabled:opacity-40"
          >
            {post.isPending ? "posting…" : "pin comment"}
          </button>
        </span>
      </div>
    </form>
  );
}
