import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Banner,
  BrokenBanner,
  EmptyState,
  ErrorState,
  HeartbeatDot,
  Malformed,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { accountsQuery, debtQuery, tasksQuery } from "@/lib/api/queries";
import { debtTaskId } from "@/lib/api/client";
import type { Task, TaskStatus } from "@/lib/api/types";
import { useNow, useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Board — harness operations console" },
      {
        name: "description",
        content:
          "Kanban of harness tasks over the four statuses a task file carries, with the api's lock-expiry judgement and declared debt per card.",
      },
      { property: "og:title", content: "Board — harness operations console" },
      {
        property: "og:description",
        content: "Live task board for a multi-agent development harness.",
      },
    ],
  }),
  component: BoardPage,
});

/** The four columns, in the order a task moves through them. ADR 26. */
const COLUMNS: { status: TaskStatus; title: string }[] = [
  { status: "pending", title: "Pending" },
  { status: "in_progress", title: "In progress" },
  { status: "blocked", title: "Blocked" },
  { status: "done", title: "Done" },
];

function BoardPage() {
  const now = useNow();
  const searchRef = useSearchHotkey();
  const tasks = useQuery(tasksQuery);
  const accounts = useQuery(accountsQuery);
  // The debt chip is a filter over /api/debt by the task prefix of a debt id, not
  // a field /api/tasks serves. `docs/decisions.md` ADR 17.
  const debt = useQuery(debtQuery);

  const [q, setQ] = useState("");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [dense, setDense] = useState(false);

  // `tasks.data?.data ?? []` is a fresh array on every render while the read has
  // not answered, and `useNow` re-renders this screen every two seconds, so the
  // filter below recomputed on every tick. The memo is here for the array's
  // identity, not for the cost of `??`: deleting it un-memoises `filtered`.
  // `docs/decisions.md` ADR 40.
  const rows = useMemo(() => tasks.data?.data ?? [], [tasks.data]);

  const debtByTask = useMemo(() => {
    const map = new Map<string, number>();
    for (const d of debt.data?.data ?? []) {
      const taskId = debtTaskId(d.id);
      if (taskId) map.set(taskId, (map.get(taskId) ?? 0) + 1);
    }
    return map;
  }, [debt.data]);

  const filtered = useMemo(() => {
    return rows.filter((t) => {
      if (accountFilter !== "all" && t.owner !== accountFilter) return false;
      if (q && !`${t.task_id} ${t.description}`.toLowerCase().includes(q.toLowerCase()))
        return false;
      return true;
    });
  }, [rows, accountFilter, q]);

  const byStatus = (status: TaskStatus) => filtered.filter((t) => t.status === status);

  // A status outside the four appears in no column, so it is named rather than
  // dropped: the api serves what the task file says and nothing validates it, and
  // filtering on four literals loses a row silently. ADR 26.
  const unplaced = rows.filter((t) => !COLUMNS.some((c) => c.status === t.status));

  // A failed read carries no `warnings`, so the banner above stays silent for it:
  // broken and partial are different states and they are reported separately.
  // `docs/ui.md` *Absent, empty and broken are three different things*. Neither
  // of these two takes the screen — the board is the task index, and it is fine.
  const brokenReads = [
    accounts.isError
      ? "/api/accounts did not answer, so the account filter has no options to offer. " +
        "The rows below are the task index and are unaffected."
      : null,
    debt.isError
      ? "/api/debt did not answer, so no card carries a debt chip. That is the read " +
        "failing, not a board on which nothing has declared debt."
      : null,
  ].filter((r): r is string => r !== null);

  const warnings = [
    ...(tasks.data?.warnings ?? []),
    ...(accounts.data?.warnings ?? []),
    ...(debt.data?.warnings ?? []),
    ...unplaced.map(
      (t) =>
        `${t.task_id}: status ${JSON.stringify(t.status)} is none of pending, in_progress, blocked or done, ` +
        `so this task is in no column. The api serves what the task file says.`,
    ),
  ];

  return (
    <AppShell>
      <PageHeader
        title="Board"
        subtitle="The four statuses a task file carries. Lock expiry is the api's judgement, not this screen's."
        right={
          <>
            <RefreshedAt at={tasks.dataUpdatedAt} />
            <button
              onClick={() => setDense((d) => !d)}
              className="rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              {dense ? "comfortable" : "compact"}
            </button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter tasks…  /"
          className="mono w-56 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
        <Select
          value={accountFilter}
          onChange={setAccountFilter}
          options={["all", ...(accounts.data?.data ?? []).map((a) => a.name)]}
          label="account"
        />
        <span className="ml-auto text-[10px] text-muted-foreground">
          {filtered.length} of {rows.length} tasks
        </span>
      </div>

      {/* The role lanes are not waiting on a route any more: they are waiting on a
          *record*, and this names it rather than sending an operator to look for a
          bug in an api that answers correctly. `docs/ui.md` *A region with no route
          says which route, and when*, third case; `docs/decisions.md` ADR 28. */}
      <Banner tone="info">
        In progress is one column, and the five role lanes are not coming from a route. Which role
        is <em>running</em> is written down nowhere: the dispatcher knows it while the phase runs
        and persists only a heartbeat, and <Mono>/api/phases</Mono> answers phases that have{" "}
        <em>ended</em>. The role filter and the gate chips go with the lanes for the same reason.
        What a card carries instead is <Mono>owner</Mono> — the account holding the task — and the
        api&apos;s own <Mono>lock_expired</Mono> judgement over that heartbeat. The last role that
        finished is on the task detail&apos;s timeline, and the gate notes are prose in that
        task&apos;s body, on the same screen.
      </Banner>

      <BrokenBanner reads={brokenReads} />

      <WarningBanner warnings={warnings} />

      {tasks.isError ? (
        <ErrorState
          title="The task API did not answer"
          body="The board could not read /api/tasks. Check that observability/api is up and that this console was started with API_TOKEN set, then reload — nothing was changed."
        />
      ) : tasks.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading tasks…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No task has been filed"
          body="The harness holds no task cards. Create one with run-task and it will appear in Pending."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No task matches these filters"
          body="Clear the search box or set the account filter back to all to see the rest of the board."
        />
      ) : (
        <div className="flex min-h-0 gap-3 overflow-x-auto p-3">
          {COLUMNS.map(({ status, title }) => (
            <Column key={status} title={title} count={byStatus(status).length}>
              {byStatus(status).map((t) => (
                <TaskCard
                  key={t.task_id}
                  task={t}
                  now={now}
                  dense={dense}
                  debtCount={debtByTask.get(t.task_id) ?? 0}
                />
              ))}
            </Column>
          ))}
        </div>
      )}
    </AppShell>
  );
}

function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
  label: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mono rounded-sm border border-border bg-surface px-1.5 py-1 text-[11px] normal-case text-foreground"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

function Column({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="panel flex w-[210px] shrink-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-2 py-1.5">
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
          {title}
        </span>
        <span className="mono text-[10px] text-muted-foreground">{count}</span>
      </div>
      <div className="flex-1 space-y-2 p-2">
        {count === 0 && <p className="px-1 py-2 text-[10px] text-muted-foreground/70">empty</p>}
        {children}
      </div>
    </section>
  );
}

/**
 * Exported only so a test can mount it: the `depends_on` guard below is the
 * one malformed payload on this screen that renders instead of throwing, and
 * `-index.test.tsx` is where that difference is written down.
 */
export function TaskCard({
  task,
  now,
  dense,
  debtCount,
}: {
  task: Task;
  now: number;
  dense: boolean;
  debtCount: number;
}) {
  return (
    <Link
      to="/tasks/$taskId"
      params={{ taskId: task.task_id }}
      className={cn(
        "block rounded-sm border border-border bg-card transition-colors hover:border-border-strong",
        dense ? "px-2 py-1.5" : "px-2 py-2",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <Mono className="text-[11px] font-semibold">{task.task_id}</Mono>
        <HeartbeatDot heartbeat={task.heartbeat} lockExpired={task.lock_expired} now={now} />
      </div>
      {!dense && (
        <p className="mt-1 line-clamp-2 text-[11px] text-foreground/90">{task.description}</p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {task.owner ? (
          <Mono className="text-[10px] text-muted-foreground">{task.owner}</Mono>
        ) : (
          <span className="text-[10px] italic text-muted-foreground/60">unassigned</span>
        )}
        {/* `.length` over a string is a number, so a `depends_on: T-001` written
            without a dash would put a confident `7` on this card — the one failure
            of this kind that does not throw, and the worse one for it. ADR 29. */}
        {!Array.isArray(task.depends_on) ? (
          <Malformed label="depends on" got={task.depends_on} want="a list" />
        ) : task.depends_on.length > 0 ? (
          <span className="inline-flex items-center gap-0.5 rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            <GitBranch className="size-2.5" />
            {task.depends_on.length}
          </span>
        ) : null}
        {debtCount > 0 && (
          <span className="mono rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            debt {debtCount}
          </span>
        )}
      </div>
    </Link>
  );
}
