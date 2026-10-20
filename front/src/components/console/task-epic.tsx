/**
 * An epic's page body: its children in the order it was split, each with its
 * title, status and merged state, what it waits on, and the epic's progress
 * (`lib/epic.ts`, ADR 139). The rows are one `/api/tasks` read, not one detail
 * read per child; an id that read does not hold is named as missing. Above them,
 * *Start epic* and *Stop epic* (`epic-actions.tsx`, ADR NNN).
 */
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { EpicActions } from "@/components/console/epic-actions";
import { Absent, ErrorState, Mono, StatusPill } from "@/components/console/primitives";
import { tasksQuery } from "@/lib/api/queries";
import type { Task } from "@/lib/api/types";
import { splitInto } from "@/lib/backlog";
import { childDependsOn, epicChildren, epicProgress } from "@/lib/epic";
import { cn } from "@/lib/utils";

export function EpicView({ epic }: { epic: Task }) {
  const q = useQuery(tasksQuery);
  const ids = splitInto(epic);

  if (q.isError) {
    return (
      <ErrorState
        title="/api/tasks did not answer"
        body={`The children's rows could not be read, so how far ${epic.task_id} is stands unknown. Its split is ${ids.join(", ") || "empty"}.`}
      />
    );
  }
  if (q.isLoading) return <p className="text-xs text-muted-foreground">Reading the children…</p>;
  if (ids.length === 0) return <Absent label="split into nothing" />;

  const children = epicChildren(ids, q.data?.data ?? []);
  const p = epicProgress(children);

  return (
    <div className="space-y-2">
      <EpicActions epic={epic} ids={ids} />
      <p className="text-xs" data-testid="epic-progress">
        <span className="font-medium">
          {p.merged} of {p.total} merged
        </span>
        {p.dropped > 0 && <span className="text-muted-foreground"> · {p.dropped} dropped</span>}
        {p.missing > 0 && <span className="text-destructive"> · {p.missing} missing</span>}
        {p.settled && <span className="text-muted-foreground"> · every child settled</span>}
      </p>
      <ol className="space-y-1.5">
        {children.map(({ id, task }) => {
          const after = childDependsOn(task);
          return (
            <li
              key={id}
              data-testid={`child-${id}`}
              className="flex flex-wrap items-center gap-2 rounded-sm border border-border px-2 py-1 text-[11px]"
            >
              <Link to="/tasks/$taskId" params={{ taskId: id }}>
                <Mono className="hover:text-foreground">{id}</Mono>
              </Link>
              {task ? (
                <>
                  <span>{task.title ?? <Absent label="no title" />}</span>
                  <StatusPill status={task.status} />
                  <span
                    className={cn(
                      "rounded-sm border px-1 text-[10px] uppercase",
                      task.merged
                        ? "text-success border-success/50 bg-success/10"
                        : "border-border-strong text-muted-foreground",
                    )}
                  >
                    {task.merged ? "merged" : "not merged"}
                  </span>
                  {after.length > 0 && (
                    <span className="text-muted-foreground">
                      after{" "}
                      {after.map((d, i) => (
                        <span key={d}>
                          {i > 0 && ", "}
                          <Link to="/tasks/$taskId" params={{ taskId: d }}>
                            <Mono className="hover:text-foreground">{d}</Mono>
                          </Link>
                        </span>
                      ))}
                    </span>
                  )}
                </>
              ) : (
                <span className="italic text-destructive">missing: not in the task list</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
