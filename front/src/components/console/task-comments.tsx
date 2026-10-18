/**
 * A task's comment thread and the form that adds to it (ADR 135, ADR 139).
 *
 * The thread is `GET /api/tasks/<id>/comments`, oldest first, as served. The
 * form posts `{text}` through the board-service (`commentTaskMutation`), which
 * names the author itself; the length is checked here first, counted as the
 * dispatcher counts it (`lib/comments.ts`), and a refusal is a toast, as on
 * the Backlog screen.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Absent, ErrorState, Mono } from "@/components/console/primitives";
import { Textarea } from "@/components/ui/textarea";
import { commentTaskMutation, commentsQuery } from "@/lib/api/queries";
import type { TaskComment } from "@/lib/api/types";
import { MAX_COMMENT_CHARS, commentAnchor, commentLength, commentProblem } from "@/lib/comments";
import { formatDateClock } from "@/lib/format";
import { cn } from "@/lib/utils";

export function CommentThread({ taskId }: { taskId: string }) {
  const q = useQuery(commentsQuery(taskId));
  const comments: unknown = q.data?.data?.comments;

  return (
    <div className="space-y-3">
      {q.isLoading ? (
        <p className="text-xs text-muted-foreground">Reading comments…</p>
      ) : q.isError ? (
        <ErrorState
          title="The comments did not answer"
          body={`/api/tasks/${taskId}/comments could not be read, so what was said on this task is unknown rather than absent. Everything above comes from the task card and is unaffected.`}
        />
      ) : q.data?.data === null ? (
        <p className="text-xs italic text-muted-foreground">
          The comments file is there and could not be read; the reason is in the warning above.
        </p>
      ) : !Array.isArray(comments) || comments.length === 0 ? (
        <p className="text-xs text-muted-foreground">No comments on this task yet.</p>
      ) : (
        <ol className="space-y-2">
          {comments.map((c, i) => (
            <CommentRow key={i} comment={c as TaskComment} />
          ))}
        </ol>
      )}
      <CommentForm taskId={taskId} />
    </div>
  );
}

function CommentRow({ comment }: { comment: TaskComment }) {
  const anchor = commentAnchor(comment);
  return (
    <li className="rounded-sm border border-border px-2 py-1.5" data-testid="comment">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        {typeof comment.author === "string" && comment.author ? (
          <Mono className="text-foreground">{comment.author}</Mono>
        ) : (
          <Absent label="no author" />
        )}
        <span title={typeof comment.at === "string" ? comment.at : undefined}>
          {formatDateClock(typeof comment.at === "string" ? comment.at : null)}
        </span>
        {anchor && (
          <Mono className="rounded-sm border border-border-strong px-1 text-[10px]">{anchor}</Mono>
        )}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-xs">
        {typeof comment.text === "string" ? comment.text : <Absent label="no text" />}
      </p>
    </li>
  );
}

function CommentForm({ taskId }: { taskId: string }) {
  const qc = useQueryClient();
  const post = useMutation(commentTaskMutation(qc));
  const [text, setText] = useState("");
  const problem = commentProblem(text);
  const over = commentLength(text) > MAX_COMMENT_CHARS;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (problem || post.isPending) return;
    post.mutate(
      { taskId, body: { text } },
      {
        onSuccess: () => {
          setText("");
          toast.success(`comment added to ${taskId}`);
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : `the comment on ${taskId} was not added`,
          ),
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-1.5">
      <Textarea
        aria-label="new comment"
        placeholder="Add a comment"
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="min-h-[72px] text-xs"
      />
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className={cn("text-muted-foreground", over && "text-destructive")}>
          {over && problem
            ? problem
            : `${commentLength(text).toLocaleString("en-US")} / ${MAX_COMMENT_CHARS.toLocaleString("en-US")}`}
        </span>
        <button
          type="submit"
          disabled={problem !== null || post.isPending}
          className="rounded-sm border border-border-strong px-2 py-0.5 hover:text-foreground disabled:opacity-40"
        >
          {post.isPending ? "posting…" : "post comment"}
        </button>
      </div>
    </form>
  );
}
