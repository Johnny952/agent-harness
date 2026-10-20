import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { commentProblem } from "@/lib/comments";
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
  ProjectMark,
  WarningBanner,
} from "@/components/console/primitives";
import {
  accountsQuery,
  actionQueueQuery,
  debtQuery,
  readyQuery,
  startTaskMutation,
  tasksQuery,
} from "@/lib/api/queries";
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
import { deriveSlots, standingLine } from "@/lib/slots";
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
  // What the worker holds, to hide *Start development* on a card already started.
  const actionQueue = useQuery(actionQueueQuery);

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

  // A start is known by kind and task id; a claimed one wins over a pending one.
  // ADR 160 part 2.
  const startByTask = useMemo(() => {
    const map = new Map<string, StartState>();
    const queue = actionQueue.data?.data;
    for (const [state, records] of [
      ["pending", queue?.pending],
      ["claimed", queue?.claimed],
    ] as const) {
      for (const r of records ?? []) {
        if (r.kind !== "start" || typeof r.task_id !== "string") continue;
        if (state === "pending" && map.get(r.task_id)?.state === "claimed") continue;
        map.set(r.task_id, { state, waiting: r.waiting });
      }
    }
    return map;
  }, [actionQueue.data]);

  // Where a new start would stand, from the same derivation as the dock's slots,
  // derived once and worded per card: a card's project names the starts of that
  // project it would queue behind (ADR NNN).
  const slotsView = useMemo(
    () =>
      accounts.isError
        ? ("accounts-failed" as const)
        : accounts.data
          ? deriveSlots(
              accounts.data.data,
              tasks.isError ? null : (tasks.data?.data ?? null),
              actionQueue.isError ? null : actionQueue.data?.data,
            )
          : ("loading" as const),
    [
      accounts.isError,
      accounts.data,
      tasks.isError,
      tasks.data,
      actionQueue.isError,
      actionQueue.data,
    ],
  );

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
    actionQueue.isError
      ? "/api/actions did not answer, so a Ready card cannot show whether its start is " +
        "already queued or running. Start development is still offered; the board-service " +
        "refuses a second start of the same task."
      : null,
  ].filter((r): r is string => r !== null);

  const warnings = [
    ...(tasks.data?.warnings ?? []),
    ...(ready.data?.warnings ?? []),
    ...(accounts.data?.warnings ?? []),
    ...(debt.data?.warnings ?? []),
    ...(actionQueue.data?.warnings ?? []),
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
                        footer={
                          c.key === "ready" ? (
                            <StartControl
                              task={t}
                              queued={startByTask.get(t.task_id)}
                              loading={actionQueue.isLoading}
                              standing={standingLine(
                                slotsView,
                                typeof t.project === "string" ? t.project : null,
                              )}
                            />
                          ) : undefined
                        }
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
  footer,
}: {
  task: Task;
  now: number;
  dense: boolean;
  debtCount: number;
  /** Outside the card's link: a button inside an anchor would also navigate. ADR 160. */
  footer?: React.ReactNode;
}) {
  const note = phaseNote(task);
  // Ready or Waiting does not change the chip, so any set will do here.
  const placedAs = columnOf(task, null);
  return (
    <div
      className={cn(
        "rounded-sm border border-border bg-card transition-colors hover:border-border-strong",
        dense ? "px-2 py-1.5" : "px-2 py-2",
      )}
    >
      <Link to="/tasks/$taskId" params={{ taskId: task.task_id }} className="block">
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
          <ProjectMark project={task.project} />
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
      {footer && <div className="mt-1.5">{footer}</div>}
    </div>
  );
}

type StartState = { state: "pending" | "claimed"; waiting: string | null };

function waitingText(waiting: string | null): string {
  if (waiting === null) return "";
  if (waiting === "account") return ", waiting for an account";
  if (waiting === "project") return ", waiting for the project";
  return `, waiting: ${waiting}`;
}

/**
 * The Ready card's footer: the button, or the state of a start already in the
 * queue. Nothing while the queue read is loading, so a queued start never
 * flashes a button. ADR 160 part 3.
 */
function StartControl({
  task,
  queued,
  loading,
  standing,
}: {
  task: Task;
  queued: StartState | undefined;
  loading: boolean;
  standing: string;
}) {
  const qc = useQueryClient();
  const start = useMutation(startTaskMutation(qc));
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  // A note is optional, so blank is not the comment rule's "needs some text".
  const problem = note.trim() ? commentProblem(note) : null;
  const chip = "mono rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground";

  if (loading) return null;
  if (queued?.state === "claimed") return <span className={chip}>Running</span>;
  if (queued) return <span className={chip}>Queued{waitingText(queued.waiting)}</span>;

  const taskId = task.task_id;
  const confirm = () =>
    start.mutate(
      { taskId, note: note.trim() ? note : null },
      {
        onSuccess: () => {
          toast.success(`${taskId} queued to start`);
          setOpen(false);
          setNote("");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : `${taskId} was not started`),
      },
    );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
      >
        Start development
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start development of {taskId}</DialogTitle>
            <DialogDescription>
              {task.title ?? task.description ?? "no title"}. Confirming queues this task's cycle
              for the worker, which runs it when an account and the project are free.
            </DialogDescription>
          </DialogHeader>
          <p className="text-xs text-muted-foreground" data-testid="standing-line">
            {standing}
          </p>
          <label className="space-y-1 text-xs">
            Note (optional)
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {problem && <p className="text-xs text-destructive">{problem}</p>}
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="button" disabled={problem !== null || start.isPending} onClick={confirm}>
              Start
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
