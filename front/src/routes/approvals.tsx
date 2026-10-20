/**
 * Approvals: the command requests phases filed (ADR 123), and below them the
 * review of a finished task's branch (`docs/plans/review.md`, ADR 219).
 *
 * The review reads three routes and writes one. `/api/tasks` gives the two
 * lists: *Awaiting review* is a `done` task whose work is not on main — the
 * board has no *awaiting review* status yet (review.md step 1), and with
 * `merge_on_done` off that is where a finished task sits — and *Conflict* is
 * the `conflict` status a failed merge leaves. `/api/tasks/<id>/diff` is the
 * branch against its merge base, drawn by hand (review.md step 7 leaves the
 * diff library to its own ask). `/api/tasks/<id>/comments` gives the comments,
 * and one pinned to a line is drawn under that line while its commit is the
 * branch's tip, and listed as outdated once the branch has moved past it. The
 * one write is that pin: a new-side line number opens a form that posts
 * `{text, file, line, commit}` through the board-service's `task_comment`
 * (`commentTaskMutation`), the commit being the tip the diff was taken at.
 *
 * There is no approve, request-changes or resolve action in the api or the
 * action queue (`dispatcher/actions.py:KINDS`), so the verdict panel names the
 * CLI the operator runs instead of drawing a button that would do nothing.
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
import { commentTaskMutation, commentsQuery, taskDiffQuery, tasksQuery } from "@/lib/api/queries";
import type { Task, TaskComment, TaskDiff } from "@/lib/api/types";
import { MAX_COMMENT_CHARS, commentAnchor, commentLength, commentProblem } from "@/lib/comments";
import { parseDiff, reviewLane, type ReviewLane as Tab } from "@/lib/review";
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
            <OutdatedComments comments={comments} diff={diff} />
            <DiffView taskId={task.task_id} diff={diff} comments={comments} />
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The verdicts review.md plans have no route yet, so this says what does
 * exist: the CLI merge, and for a conflict the merge of the base into the
 * branch that comes first (never a rebase: a phase's history stays as it ran).
 */
function VerdictPanel({ task, lane }: { task: Task; lane: Tab }) {
  const project = task.project ? ` --project ${task.project}` : "";
  const merge = `dispatch merge-task ${task.task_id}${project}`;
  return (
    <div className="panel space-y-1.5 px-3 py-2 text-xs" data-testid="review-verdict">
      <p className="text-muted-foreground">
        {lane === "conflict"
          ? "The merge stopped on a conflict. Merge the base into the task branch, resolve it there, then merge again:"
          : "Approve and request changes are not in the console yet. To approve, merge from the CLI (add --push to push too):"}
      </p>
      <Mono className="block select-all text-[11px]">{merge}</Mono>
      <p className="text-[11px] text-muted-foreground">
        A line number in the diff pins a comment to that line at this tip; a comment on the whole
        task is on the{" "}
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

function CommentLine({ comment, anchor = false }: { comment: TaskComment; anchor?: boolean }) {
  return (
    <li className="text-[11px]" data-testid="review-comment">
      <span className="text-muted-foreground">
        {typeof comment.author === "string" && comment.author ? comment.author : "no author"} ·{" "}
        {formatDateClock(typeof comment.at === "string" ? comment.at : null)}
      </span>
      {anchor && <Mono className="ml-2 text-[10px]">{commentAnchor(comment)}</Mono>}
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
