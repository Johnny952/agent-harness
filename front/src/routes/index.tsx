import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  BrokenBanner,
  EmptyState,
  ErrorState,
  HeartbeatDot,
  Malformed,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { accountsQuery, debtQuery, readyQuery, tasksQuery } from "@/lib/api/queries";
import { debtTaskId } from "@/lib/api/client";
import type { Task } from "@/lib/api/types";
import {
  KNOWN_STATUSES,
  buildBoard,
  columnOf,
  phaseNote,
  type BoardColumn,
  type Lane,
} from "@/lib/board";
import { splitInto } from "@/lib/backlog";
import { epicChildren, epicProgress } from "@/lib/epic";
import { useNow, useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Board — harness operations console" },
      {
        name: "description",
        content:
          "Kanban of accepted harness tasks: Ready in the dispatcher's order, Waiting, one lane per role's live phase, the stops and Merged, in one swimlane per epic.",
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

/**
 * The ready read says it failed in two ways: an error, or the envelope
 * `observability/api/app.py` answers when `ready_order` itself raised — an
 * empty list beside a "no ready order" warning. Both mean the same to this
 * board: there is no order, so pending cannot be split.
 */
const NO_READY_ORDER = ": no ready order: ";

function BoardPage() {
  const now = useNow();
  const searchRef = useSearchHotkey();
  const tasks = useQuery(tasksQuery);
  // One read for the Ready column and its order, the dispatcher's own
  // (`ready_order`, ADR 133): the board never recomputes it. ADR 140.
  const ready = useQuery(readyQuery);
  const accounts = useQuery(accountsQuery);
  // The debt chip is a filter over /api/debt by the task prefix of a debt id, not
  // a field /api/tasks serves. `docs/decisions.md` ADR 17.
  const debt = useQuery(debtQuery);

  const [q, setQ] = useState("");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [columnFilter, setColumnFilter] = useState<string>("all");
  const [dense, setDense] = useState(false);

  // `tasks.data?.data ?? []` is a fresh array on every render while the read has
  // not answered, and `useNow` re-renders this screen every two seconds, so the
  // board below recomputed on every tick. The memo is here for the array's
  // identity, not for the cost of `??`. `docs/decisions.md` ADR 40.
  const rows = useMemo(() => tasks.data?.data ?? [], [tasks.data]);

  const readyFailed =
    ready.isError || (ready.data?.warnings ?? []).some((w) => w.includes(NO_READY_ORDER));
  const readyOrder = useMemo(
    () => (readyFailed ? null : (ready.data?.data ?? [])),
    [readyFailed, ready.data],
  );

  const debtByTask = useMemo(() => {
    const map = new Map<string, number>();
    for (const d of debt.data?.data ?? []) {
      const taskId = debtTaskId(d.id);
      if (taskId) map.set(taskId, (map.get(taskId) ?? 0) + 1);
    }
    return map;
  }, [debt.data]);

  const filtering = q !== "" || accountFilter !== "all" || columnFilter !== "all";
  const board = useMemo(
    () =>
      buildBoard(
        rows,
        readyOrder,
        (t, column) => {
          if (columnFilter !== "all" && column !== columnFilter) return false;
          if (accountFilter !== "all" && t.owner !== accountFilter) return false;
          // `description` and `title` are `null` when absent, and a template
          // literal would spell that as the text `null` and match it.
          const hay = `${t.task_id} ${t.title ?? ""} ${t.description ?? ""}`.toLowerCase();
          return !q || hay.includes(q.toLowerCase());
        },
        filtering,
      ),
    [rows, readyOrder, columnFilter, accountFilter, q, filtering],
  );

  // A status the dispatcher does not write is still a card — in Other — and is
  // also named here: the api serves what the task file says and nothing
  // validates it. ADR 26, ADR 140.
  const unknown = rows.filter((t) => !KNOWN_STATUSES.includes(t.status));

  // A failed read carries no `warnings`, so the banner above stays silent for it:
  // broken and partial are different states and they are reported separately.
  // `docs/ui.md` *Absent, empty and broken are three different things*.
  const brokenReads = [
    readyFailed
      ? "/api/ready gave no ready order, so Ready and Waiting cannot be told apart: every " +
        "pending task is in one Pending column, in the dispatcher's order but not split by " +
        "whether its dependencies are merged."
      : null,
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
    ...(ready.data?.warnings ?? []),
    ...(accounts.data?.warnings ?? []),
    ...(debt.data?.warnings ?? []),
    ...unknown.map(
      (t) =>
        `${t.task_id}: status ${JSON.stringify(t.status)} is not one the dispatcher writes, ` +
        `so this task is in Other. The api serves what the task file says.`,
    ),
  ];

  return (
    <AppShell>
      <PageHeader
        title="Board"
        subtitle="Accepted tasks by where they are: ready, waiting, in a role's phase, stopped, merged. One lane per epic."
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
          value={columnFilter}
          onChange={setColumnFilter}
          options={[
            { value: "all", label: "all" },
            ...board.columns.map((c) => ({ value: c.key, label: c.title })),
          ]}
          label="column"
        />
        <Select
          value={accountFilter}
          onChange={setAccountFilter}
          options={["all", ...(accounts.data?.data ?? []).map((a) => a.name)].map((n) => ({
            value: n,
            label: n,
          }))}
          label="account"
        />
        <span className="ml-auto text-[10px] text-muted-foreground">
          {board.shown} of {board.cards} cards
        </span>
      </div>

      <BrokenBanner reads={brokenReads} />

      <WarningBanner warnings={warnings} />

      {tasks.isError ? (
        <ErrorState
          title="The task API did not answer"
          body="The board could not read /api/tasks. Check that observability/api is up and that this console was started with API_TOKEN set, then reload — nothing was changed."
        />
      ) : tasks.isLoading || ready.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading tasks…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No task has been filed"
          body="The harness holds no task cards. Propose one on the Backlog screen and accept it to put it here."
        />
      ) : board.cards === 0 && board.lanes.length === 0 ? (
        <EmptyState
          title="Nothing is on the board"
          body="Every task is in the backlog — proposed, deferred or dropped. Accepting one on the Backlog screen puts it here."
        />
      ) : filtering && board.shown === 0 ? (
        <EmptyState
          title="No task matches these filters"
          body="Clear the search box or set the column and account filters back to all to see the rest of the board."
        />
      ) : (
        <div className="min-h-0 space-y-4 overflow-x-auto p-3">
          {board.lanes.map((lane) => (
            <Swimlane key={lane.id ?? "no-epic"} lane={lane} rows={rows}>
              {board.columns.map((c) => {
                const cards = lane.cells.get(c.key) ?? [];
                return (
                  <Column key={c.key} column={c} count={cards.length}>
                    {cards.map((t) => (
                      <TaskCard
                        key={t.task_id}
                        task={t}
                        now={now}
                        dense={dense}
                        debtCount={debtByTask.get(t.task_id) ?? 0}
                      />
                    ))}
                  </Column>
                );
              })}
            </Swimlane>
          ))}
        </div>
      )}
    </AppShell>
  );
}

/**
 * One epic's row of columns, headed by a link to the epic and its progress by
 * the epic view's own rule (ADR 139), or the no-epic lane.
 */
function Swimlane({
  lane,
  rows,
  children,
}: {
  lane: Lane;
  rows: Task[];
  children: React.ReactNode;
}) {
  const progress =
    lane.head && lane.id ? epicProgress(epicChildren(splitInto(lane.head), rows)) : null;
  return (
    <div data-lane={lane.id ?? "none"} className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-2 px-1">
        {lane.id === null ? (
          <span className="text-[11px] font-semibold text-muted-foreground">No epic</span>
        ) : (
          <Link
            to="/tasks/$taskId"
            params={{ taskId: lane.id }}
            className="flex items-baseline gap-2 hover:underline"
          >
            <Mono className="text-[11px] font-semibold">{lane.id}</Mono>
            <span className="text-[11px]">
              {lane.head ? (
                (lane.head.title ?? <Absent label="no title" />)
              ) : (
                <Absent label="epic not served by /api/tasks" />
              )}
            </span>
          </Link>
        )}
        {progress && (
          <span className="mono text-[10px] text-muted-foreground">
            {progress.merged} of {progress.total} merged
            {progress.dropped > 0 && `, ${progress.dropped} dropped`}
            {progress.missing > 0 && `, ${progress.missing} missing`}
          </span>
        )}
        <span className="mono ml-auto text-[10px] text-muted-foreground">{lane.count}</span>
      </div>
      <div className="flex gap-3">{children}</div>
    </div>
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
  options: readonly { value: string; label: string }[];
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
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Column({
  column,
  count,
  children,
}: {
  column: BoardColumn;
  count: number;
  children: React.ReactNode;
}) {
  // Awaiting review has no status behind it yet (`docs/plans/review.md`), so its
  // empty is "not built", not "nothing waiting". ADR 140.
  const empty = column.key === "review" ? "not built: no status says this yet" : "empty";
  return (
    <section data-column={column.key} className="panel flex w-[200px] shrink-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-2 py-1.5">
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
          {column.title}
        </span>
        <span className="mono text-[10px] text-muted-foreground">{count}</span>
      </div>
      <div className="flex-1 space-y-2 p-2">
        {count === 0 && <p className="px-1 py-2 text-[10px] text-muted-foreground/70">{empty}</p>}
        {children}
      </div>
    </section>
  );
}

/**
 * Exported only so a test can mount it: the `depends_on` guard below is the
 * one malformed payload on this screen that renders instead of throwing, and
 * `-index.test.tsx` is where that difference is written down. The absent arm
 * of the description is tested through the same export.
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
  const note = phaseNote(task);
  // Ready or Waiting does not change the chip, so any set will do here.
  const placedAs = columnOf(task, null);
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
        <p className="mt-1 line-clamp-2 text-[11px] text-foreground/90">
          {task.title ?? task.description ?? <Absent label="no description" />}
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {note && (
          <span className="mono rounded-sm border border-warning/50 px-1 text-[10px] text-warning">
            {note}
          </span>
        )}
        {placedAs === "other" && (
          <span className="mono rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            {task.status}
          </span>
        )}
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
