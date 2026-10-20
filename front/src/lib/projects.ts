/**
 * The projects the console knows, and the scope a project's screens read in.
 *
 * There is no read route for the project records: the board-service holds the
 * writes (`POST /api/projects`, `PATCH /api/projects/<slug>`, ADR 203) and the
 * api serves no list of them. So the list is derived from what the console
 * already reads — the `project` field on each task of `/api/tasks` (ADR 204) —
 * and a project is a slug, with no title, until a read route serves the records.
 * A task with no project (`null`, the harness's one pre-existing project before
 * projects were recorded, ADR 199) belongs to no project here: it shows on the
 * harness's own screens only.
 */
import { createContext, useContext } from "react";
import type { Task } from "@/lib/api/types";

/** The projects named on `tasks`, each once, in slug order. A non-string or empty project names none. */
export function deriveProjects(tasks: readonly Task[]): string[] {
  const slugs = new Set<string>();
  for (const t of tasks) {
    const p: unknown = t.project;
    if (typeof p === "string" && p !== "") slugs.add(p);
  }
  return [...slugs].sort();
}

/** The tasks of one project, by slug, in the order they came. */
export function inProject(tasks: readonly Task[], slug: string): Task[] {
  return tasks.filter((t) => t.project === slug);
}

/**
 * Narrow `tasks` to the scope: all of them on the harness's screens (`null`),
 * the project's own under `/p/$slug/`.
 */
export function scoped(tasks: Task[], project: string | null): Task[] {
  return project === null ? tasks : inProject(tasks, project);
}

/**
 * The project a screen is drawn for, or `null` on the harness's own screens.
 * Set by the `/p/$slug/` routes, read by the links a screen draws to a task so
 * they stay inside the project.
 */
export const ProjectScopeContext = createContext<string | null>(null);

export function useProjectScope(): string | null {
  return useContext(ProjectScopeContext);
}
