/**
 * The projects the console knows, and the scope a project's screens read in.
 *
 * The list is the project records (`.hive/projects/<slug>.json`, ADR 199), read
 * from the api's `GET /api/projects` (ADR NNN): a project has a title, shows
 * before any of its tasks exists, and an archived one is known but not listed.
 * When that read fails (an api older than the route, say) the list falls back
 * to what the console already reads, the `project` field on each task of
 * `/api/tasks` (ADR 204, ADR 208), by slug. A task with no project (`null`, the
 * harness's one pre-existing project before projects were recorded, ADR 199)
 * belongs to no project here: it shows on the harness's own screens only.
 */
import { createContext, useContext, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { projectsQuery, tasksQuery } from "@/lib/api/queries";
import type { ProjectRecord, Task } from "@/lib/api/types";

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

/** One project as the sidebar draws it: its slug, the title to show, its status. */
export interface ProjectEntry {
  slug: string;
  title: string;
  status: ProjectRecord["status"];
}

/** The records as entries, in slug order. An empty title shows as the slug. */
export function projectEntries(records: readonly ProjectRecord[]): ProjectEntry[] {
  return records
    .map((r) => ({ slug: r.slug, title: r.title.trim() || r.slug, status: r.status }))
    .sort((a, b) => (a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0));
}

/** What the sidebar lists: the active projects. An archived one stays reachable by its URL. */
export function listedProjects(entries: readonly ProjectEntry[]): ProjectEntry[] {
  return entries.filter((e) => e.status === "active");
}

/**
 * The project list and where it came from. `entries` is `null` while nothing
 * has answered, and when neither read did (`failed`). `source` is `"tasks"` only
 * when the records read failed and the list was derived from the task list's
 * `project` fields instead: slugs as titles, every one active.
 */
export interface ProjectList {
  entries: ProjectEntry[] | null;
  source: "records" | "tasks";
  failed: boolean;
}

/** The fields of a query's state the two hooks below read. */
interface Read<T> {
  data: { data: T } | undefined;
  isError: boolean;
  isPending: boolean;
  errorUpdateCount: number;
}

/** What a read answered, or `null`. A refetch that failed over old data is no answer. */
function answer<T>(q: Read<T>): T | null {
  return q.data && !q.isError ? q.data.data : null;
}

/**
 * Whether a read has failed with nothing to show. A query with no data that
 * refetches goes back to `pending` (TanStack Query v5), and every observer that
 * mounts refetches it; counting the errors it has had keeps a failed read failed
 * meanwhile, so the gate does not swap the screen in and out on each remount.
 */
function failed<T>(q: Read<T>): boolean {
  return answer(q) === null && (q.isError || q.errorUpdateCount > 0);
}

export function useProjectList(): ProjectList {
  const records = useQuery(projectsQuery);
  const tasks = useQuery(tasksQuery);
  const recordsRead = answer(records);
  const recordsFailed = failed(records);
  const tasksRead = answer(tasks);
  const tasksFailed = failed(tasks);
  return useMemo((): ProjectList => {
    if (recordsRead) {
      return { entries: projectEntries(recordsRead), source: "records", failed: false };
    }
    if (!recordsFailed) return { entries: null, source: "records", failed: false };
    if (tasksRead) {
      const entries = deriveProjects(tasksRead).map((slug): ProjectEntry => ({
        slug,
        title: slug,
        status: "active",
      }));
      return { entries, source: "tasks", failed: false };
    }
    return { entries: null, source: "tasks", failed: tasksFailed };
  }, [recordsRead, recordsFailed, tasksRead, tasksFailed]);
}

/**
 * Whether `slug` names a project the console knows: a record, archived ones
 * among them, or a slug a task names. `"unknown"` while a read that has not
 * failed is still in flight; `"absent"` only when every read has settled, one
 * at least answered, and none knows the slug. A failed read is not evidence
 * either way, so with both failed this stays `"unknown"`.
 */
export function useProjectKnown(slug: string): "known" | "absent" | "unknown" {
  const records = useQuery(projectsQuery);
  const tasks = useQuery(tasksQuery);
  const recordsRead = answer(records);
  const tasksRead = answer(tasks);
  if (recordsRead?.some((r) => r.slug === slug)) return "known";
  if (tasksRead && deriveProjects(tasksRead).includes(slug)) return "known";
  const settled = (q: Read<unknown>) => answer(q) !== null || failed(q);
  if (!settled(records) || !settled(tasks)) return "unknown";
  return recordsRead || tasksRead ? "absent" : "unknown";
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
