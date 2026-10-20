import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  ProjectMark,
  StatusPill,
  WarningBanner,
} from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import { acceptTaskMutation, tasksQuery } from "@/lib/api/queries";
import type { AcceptTaskBody, Task, TaskPriority } from "@/lib/api/types";
import {
  BACKLOG_STATUSES,
  NO_SOURCE,
  PRIORITIES,
  bucketOf,
  filterGroups,
  groupBacklog,
  isBacklog,
  isEpic,
  isReorderable,
  reorderWrites,
  splitInto,
  type BacklogFilter,
  type RankWrite,
} from "@/lib/backlog";
import { cn } from "@/lib/utils";
import { scoped, useProjectScope } from "@/lib/projects";
import { TaskLink } from "@/components/console/project-scope";

export const Route = createFileRoute("/backlog")({
  head: () => ({
    meta: [
      { title: "Backlog — harness operations console" },
      {
        name: "description",
        content:
          "Proposed, deferred and dropped tasks and epics in the board's priority order, to accept, defer, drop or reorder.",
      },
      { property: "og:title", content: "Backlog — harness operations console" },
      {
        property: "og:description",
        content: "What waits to be accepted onto the board, in priority order.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: BacklogPage,
});

const ANY = "all";
/** Dropped is final and hidden until asked for (`docs/plans/board-service.md` section 8). */
const DEFAULT_STATUSES = ["proposed", "deferred"];

type Move = "up" | "down";
type Verdict = NonNullable<AcceptTaskBody["to"]>;

const VERDICT_DONE: Record<Verdict, string> = {
  pending: "accepted onto the board",
  deferred: "deferred",
  dropped: "dropped",
};

/**
 * The backlog. On the harness's own screen it holds every task; under
 * `/p/$slug/backlog` (ADR 208) the same screen holds that project's only.
 */
export function BacklogPage() {
  const project = useProjectScope();
  const q = useQuery(tasksQuery);
  const qc = useQueryClient();
  const [statuses, setStatuses] = useState<string[]>(DEFAULT_STATUSES);
  const [tag, setTag] = useState<string>(ANY);
  const [source, setSource] = useState<string>(ANY);

  const tasks = useMemo(() => scoped(q.data?.data ?? [], project), [q.data, project]);
  const backlog = useMemo(() => tasks.filter(isBacklog), [tasks]);
  const groups = useMemo(() => groupBacklog(tasks), [tasks]);
  const filter: BacklogFilter = useMemo(
    () => ({
      statuses,
      tag: tag === ANY ? null : tag,
      source: source === ANY ? null : source,
    }),
    [statuses, tag, source],
  );
  const shown = useMemo(() => filterGroups(groups, filter), [groups, filter]);

  // The options are what the backlog holds, so a filter never offers a value
  // that matches nothing; `debt` is a tag like any other (section 5d).
  const tags = useMemo(() => [...new Set(backlog.flatMap((t) => t.tags))].sort(), [backlog]);
  const sources = useMemo(
    () => [...new Set(backlog.map((t) => t.source ?? NO_SOURCE))].sort(),
    [backlog],
  );

  const accept = useMutation(acceptTaskMutation(qc));
  const reorder = useMutation({
    mutationKey: ["board", "reorder"],
    // One write after another: a renumber is several, and a refusal stops the
    // rest rather than leaving the order half one thing and half the other.
    mutationFn: async (writes: RankWrite[]) => {
      for (const w of writes) await api.editTask(w.taskId, { rank: w.rank });
    },
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: ["tasks"] }),
        qc.invalidateQueries({ queryKey: ["ready"] }),
      ]),
  });
  const busy = accept.isPending || reorder.isPending;

  const decide = (taskId: string, to: Verdict, priority?: TaskPriority) => {
    const body: AcceptTaskBody = priority ? { to, priority } : { to };
    accept.mutate(
      { taskId, body },
      {
        onSuccess: () => toast.success(`${taskId} ${VERDICT_DONE[to]}`),
        onError: (e) => toast.error(e instanceof Error ? e.message : `${taskId} was not changed`),
      },
    );
  };

  // The neighbour is the next row up or down that the operator can see and
  // move, in the same bucket: a reorder never moves an item across buckets —
  // that is a priority edit — and the rank is computed over the whole bucket,
  // hidden rows included (`reorderWrites`).
  const visibleMovable = shown.filter(isReorderable).map((g) => g.head as Task);
  const neighborOf = (task: Task, dir: Move): Task | undefined => {
    const peers = visibleMovable.filter((t) => bucketOf(t) === bucketOf(task));
    const i = peers.findIndex((t) => t.task_id === task.task_id);
    return dir === "up" ? peers[i - 1] : peers[i + 1];
  };
  const move = (task: Task, dir: Move) => {
    const neighbor = neighborOf(task, dir);
    if (!neighbor) return;
    const bucket = groups
      .filter(isReorderable)
      .map((g) => g.head as Task)
      .filter((t) => bucketOf(t) === bucketOf(task));
    const writes = reorderWrites(bucket, task.task_id, neighbor.task_id, dir);
    if (writes.length === 0) return;
    reorder.mutate(writes, {
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : `${task.task_id} could not be moved`),
    });
  };

  const counts = BACKLOG_STATUSES.map(
    (s) => `${backlog.filter((t) => t.status === s).length} ${s}`,
  ).join(" · ");

  return (
    <AppShell>
      <div className="flex h-full flex-col">
        <PageHeader
          title={project === null ? "Backlog" : `Backlog · ${project}`}
          subtitle={`${counts} · in the board's order: priority, then rank, then id`}
          right={<RefreshedAt at={q.dataUpdatedAt} />}
        />

        <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2 text-[11px]">
          <span className="label-xs">status</span>
          {BACKLOG_STATUSES.map((s) => (
            <label key={s} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={statuses.includes(s)}
                onChange={(e) =>
                  setStatuses((cur) =>
                    e.target.checked ? [...cur, s] : cur.filter((x) => x !== s),
                  )
                }
              />
              {s}
            </label>
          ))}
          <FilterSelect label="tag" value={tag} onChange={setTag} options={tags} />
          <FilterSelect label="source" value={source} onChange={setSource} options={sources} />
        </div>

        <WarningBanner warnings={q.data?.warnings ?? []} />

        {q.isError ? (
          <ErrorState
            title="The backlog could not be read"
            body="The harness API returned an error for /api/tasks. Nothing on this screen is current until it loads."
          />
        ) : q.isLoading ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">Reading the backlog…</p>
        ) : backlog.length === 0 ? (
          <EmptyState
            title="The backlog is empty"
            body="Nothing is proposed, deferred or dropped. A proposal from the operator, the arquitecto or the debt index shows here until someone accepts it."
          />
        ) : shown.length === 0 ? (
          <EmptyState
            title="No backlog item matches the filters"
            body={`The backlog holds ${backlog.length === 1 ? "one item" : `${backlog.length} items`}. Tick another status or set tag and source back to all to see them.`}
          />
        ) : (
          <div className="flex-1 overflow-auto p-4">
            <table className="panel w-full text-[11px]">
              <thead>
                <tr className="border-b border-border text-left">
                  {["", "id", "title", "priority", "tags", "source", "status", ""].map((h, i) => (
                    <th key={i} className="label-xs px-3 py-1.5 font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((g) => {
                  const head = g.head;
                  const parentStatus = head?.status ?? null;
                  return [
                    g.headInBacklog && head ? (
                      <ItemRow
                        key={g.id}
                        task={head}
                        busy={busy}
                        muted={!g.headMatches}
                        up={isReorderable(g) && !!neighborOf(head, "up")}
                        down={isReorderable(g) && !!neighborOf(head, "down")}
                        onMove={(dir) => move(head, dir)}
                        onDecide={decide}
                      />
                    ) : (
                      <EpicHeader key={g.id} id={g.id} head={head} />
                    ),
                    ...g.children.map((c) => (
                      <ItemRow
                        key={c.task_id}
                        task={c}
                        busy={busy}
                        child
                        // A task of an epic goes to the board with its epic, or
                        // after it: the board-service refuses it alone before then.
                        acceptBlocked={parentStatus !== "epic"}
                        onDecide={decide}
                      />
                    )),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <label className="flex items-center gap-1">
      <span className="label-xs">{label}</span>
      <select
        aria-label={`${label} filter`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mono rounded-sm border border-border bg-surface px-1.5 py-1 text-[11px] text-foreground"
      >
        <option value={ANY}>all</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The row an epic's tasks hang from when the epic itself is not in the backlog. */
function EpicHeader({ id, head }: { id: string; head: Task | null }) {
  return (
    <tr className="border-b border-border/50 bg-surface-2/40">
      <td className="px-3 py-1.5" />
      <td className="px-3 py-1.5">
        <TaskLink taskId={id}>
          <Mono className="font-semibold hover:underline">{id}</Mono>
        </TaskLink>
      </td>
      <td className="px-3 py-1.5" colSpan={4}>
        <span className="mr-2 text-[10px] uppercase text-muted-foreground">epic</span>
        {head?.title ?? <Absent label="not served" />}
      </td>
      <td className="px-3 py-1.5">
        {head ? <StatusPill status={head.status} /> : <Absent label="not read" />}
      </td>
      <td className="px-3 py-1.5 text-[10px] text-muted-foreground">
        {head ? "on the board" : "not in the read"}
      </td>
    </tr>
  );
}

function ItemRow({
  task,
  busy,
  child = false,
  muted = false,
  acceptBlocked = false,
  up = false,
  down = false,
  onMove,
  onDecide,
}: {
  task: Task;
  busy: boolean;
  child?: boolean;
  muted?: boolean;
  acceptBlocked?: boolean;
  up?: boolean;
  down?: boolean;
  onMove?: (dir: Move) => void;
  onDecide: (taskId: string, to: Verdict, priority?: TaskPriority) => void;
}) {
  const epic = isEpic(task);
  const id = task.task_id;
  return (
    <tr
      data-testid={`row-${id}`}
      className={cn(
        "border-b border-border/50 last:border-0 hover:bg-surface-2/50",
        muted && "opacity-60",
      )}
    >
      <td className="whitespace-nowrap px-1 py-1.5">
        {onMove ? (
          <>
            <button
              type="button"
              aria-label={`move ${id} up`}
              disabled={busy || !up}
              onClick={() => onMove("up")}
              className="px-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
            >
              ↑
            </button>
            <button
              type="button"
              aria-label={`move ${id} down`}
              disabled={busy || !down}
              onClick={() => onMove("down")}
              className="px-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
            >
              ↓
            </button>
          </>
        ) : null}
      </td>
      <td className={cn("whitespace-nowrap px-3 py-1.5", child && "pl-8")}>
        <TaskLink taskId={id}>
          <Mono className="font-semibold hover:underline">{id}</Mono>
        </TaskLink>
      </td>
      <td className="px-3 py-1.5">
        {epic ? (
          <span className="mr-2 text-[10px] uppercase text-muted-foreground">
            epic · {splitInto(task).length} tasks
          </span>
        ) : null}
        {task.title ?? <Absent label="untitled" />}
        <span className="ml-2">
          <ProjectMark project={task.project} />
        </span>
      </td>
      <td className="mono px-3 py-1.5">{task.priority}</td>
      <td className="px-3 py-1.5">
        {task.tags.length === 0 ? (
          <Absent label="none" />
        ) : (
          <span className="flex flex-wrap gap-1">
            {task.tags.map((t) => (
              <span
                key={t}
                className={cn(
                  "mono rounded-sm border px-1 text-[10px]",
                  t === "debt"
                    ? "border-warning/50 text-warning"
                    : "border-border text-muted-foreground",
                )}
              >
                {t}
              </span>
            ))}
          </span>
        )}
      </td>
      <td className="mono px-3 py-1.5">
        {task.source ?? <Absent label="not recorded" />}
        {task.source === "debt" && task.debt_id ? (
          <>
            {" · "}
            <Link to="/debt" className="hover:underline">
              {task.debt_id}
            </Link>
          </>
        ) : null}
      </td>
      <td className="px-3 py-1.5">
        <StatusPill status={task.status} />
      </td>
      <td className="whitespace-nowrap px-3 py-1.5">
        {task.status === "dropped" ? (
          <span className="text-[10px] text-muted-foreground">final</span>
        ) : (
          <Actions
            task={task}
            busy={busy}
            epic={epic}
            acceptBlocked={acceptBlocked}
            onDecide={onDecide}
          />
        )}
      </td>
    </tr>
  );
}

function Actions({
  task,
  busy,
  epic,
  acceptBlocked,
  onDecide,
}: {
  task: Task;
  busy: boolean;
  epic: boolean;
  acceptBlocked: boolean;
  onDecide: (taskId: string, to: Verdict, priority?: TaskPriority) => void;
}) {
  const id = task.task_id;
  const [priority, setPriority] = useState<TaskPriority>(bucketOf(task));
  const [confirmDrop, setConfirmDrop] = useState(false);
  const btn =
    "rounded-sm border border-border px-1.5 py-0.5 text-[11px] hover:bg-surface-2 disabled:opacity-40";

  if (confirmDrop)
    return (
      <span className="flex items-center gap-1">
        <span className="text-[10px] text-destructive">
          drop {id}
          {epic ? " and its tasks" : ""}? It is final.
        </span>
        <button
          type="button"
          disabled={busy}
          className={cn(btn, "border-destructive/60 text-destructive")}
          onClick={() => {
            setConfirmDrop(false);
            onDecide(id, "dropped");
          }}
        >
          confirm drop
        </button>
        <button type="button" className={btn} onClick={() => setConfirmDrop(false)}>
          cancel
        </button>
      </span>
    );

  return (
    <span className="flex items-center gap-1">
      <select
        aria-label={`priority for ${id}`}
        value={priority}
        disabled={busy || acceptBlocked}
        onChange={(e) => setPriority(e.target.value as TaskPriority)}
        className="mono rounded-sm border border-border bg-surface px-1 py-0.5 text-[11px] text-foreground"
      >
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={busy || acceptBlocked}
        title={
          acceptBlocked
            ? "Accept the epic first: its tasks go to the board with it"
            : epic
              ? "Accepting an epic carries its tasks"
              : undefined
        }
        className={btn}
        onClick={() => onDecide(id, "pending", priority)}
      >
        accept
      </button>
      {task.status === "proposed" ? (
        <button
          type="button"
          disabled={busy}
          className={btn}
          onClick={() => onDecide(id, "deferred")}
        >
          defer
        </button>
      ) : null}
      <button type="button" disabled={busy} className={btn} onClick={() => setConfirmDrop(true)}>
        drop
      </button>
    </span>
  );
}
