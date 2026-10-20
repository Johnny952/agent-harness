/**
 * What the `/p/$slug/` routes wrap a screen in: the project's scope, a plain
 * not-found for a slug no task names, and the link to a task that stays inside
 * the project. The screens themselves are the harness's own, reused — they read
 * the scope from `ProjectScopeContext` and narrow their rows by it. ADR 208.
 */
import { useMemo, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { taskQuery, tasksQuery } from "@/lib/api/queries";
import { ProjectScopeContext, deriveProjects, useProjectScope } from "@/lib/projects";
import { AppShell } from "./app-shell";

/** The plain not-found a project route draws for what it does not know. */
export function ProjectNotFound({ title, body }: { title: string; body: string }) {
  return (
    <AppShell>
      <div data-testid="project-not-found" className="panel m-4 px-4 py-8 text-center">
        <p className="text-sm font-medium">{title}</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">{body}</p>
        <Link to="/" className="mt-3 inline-block text-xs text-muted-foreground hover:underline">
          Back to the harness board
        </Link>
      </div>
    </AppShell>
  );
}

/**
 * Draw `children` in `slug`'s scope, or the not-found when the tasks have been
 * read and none names `slug`. While the read is in flight, or when it failed,
 * the screen is drawn: it has its own loading and error states, and a failed
 * read is not evidence that the project does not exist.
 */
export function ProjectGate({ slug, children }: { slug: string; children: ReactNode }) {
  const q = useQuery(tasksQuery);
  const known = useMemo(() => deriveProjects(q.data?.data ?? []), [q.data]);
  if (q.data && !q.isError && !known.includes(slug)) {
    return (
      <ProjectNotFound
        title={`No project named ${slug}`}
        body="No task on the board names this project. A project shows in the sidebar once one of its tasks is on the board or in the backlog."
      />
    );
  }
  return <ProjectScopeContext.Provider value={slug}>{children}</ProjectScopeContext.Provider>;
}

/**
 * A link to a task's page: the harness's `/tasks/$taskId`, or the project's
 * `/p/$slug/tasks/$taskId` when the screen is drawn in a project's scope.
 */
export function TaskLink({
  taskId,
  className,
  children,
}: {
  taskId: string;
  className?: string;
  children: ReactNode;
}) {
  const project = useProjectScope();
  if (project === null) {
    return (
      <Link to="/tasks/$taskId" params={{ taskId }} className={className}>
        {children}
      </Link>
    );
  }
  return (
    <Link to="/p/$slug/tasks/$taskId" params={{ slug: project, taskId }} className={className}>
      {children}
    </Link>
  );
}

/**
 * Draw `children` (a task's page) only when the task belongs to `slug`. A task
 * of another project, or of the harness, is not-found under this project: its
 * page lives at its own project's path, or at `/tasks/$taskId`. While the read
 * is in flight, or when it failed, the page is drawn with its own states.
 */
export function ProjectTaskGate({
  slug,
  taskId,
  children,
}: {
  slug: string;
  taskId: string;
  children: ReactNode;
}) {
  const q = useQuery(taskQuery(taskId));
  const task = q.data?.data;
  if (task && !q.isError && task.project !== slug) {
    return (
      <ProjectNotFound
        title={`${taskId} is not a task of ${slug}`}
        body="This task belongs to another project, or to the harness itself. Open it from its own project in the sidebar."
      />
    );
  }
  return <>{children}</>;
}
