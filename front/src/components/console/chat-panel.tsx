/**
 * The chat dock: the harness's threads (`docs/decisions.md` ADR 184, step 4 of
 * `docs/plans/main-thread.md`), with the composer and *stop* (ADR 190, step 5).
 *
 * The selector lists `GET /api/threads` with `main` first; the history is the
 * selected thread's log, every segment of it in one scroll, read by
 * `useThreadEvents` and mapped by `lib/thread.ts`. The switch picks the *Text* or
 * the *Text and commands* view and is remembered in this browser.
 *
 * The composer sends `POST /api/threads/<id>/turns`; a message sent mid-turn
 * queues. A queued turn is not in the log until the worker claims it, so the dock
 * draws it from the action queue (`["action-queue"]`, shared with the Board)
 * after the history. *Stop* on a running turn and *Cancel* on a queued one are
 * both `cancelAction`. An archived thread has no composer. The unsent text is
 * kept per thread in this component's state only.
 *
 * *New thread* sends `POST /api/threads` and selects the record it answers;
 * *Edit* sends `PATCH /api/threads/<id>` with the title, model and effort that
 * changed; *Archive* and *Reopen* send the status (ADR 195). `main` has no
 * *Archive*: the board service refuses it. The console holds no list of an
 * account's thread models, so a new thread takes the service's default model and
 * a change of model is typed, checked by the service. A refused write is shown in
 * its form and the form keeps what was typed. A screen opens the dock on a thread
 * with `openThread` (`hooks/use-console.ts`), which arrives here as `select`.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Plus, Wrench, X } from "lucide-react";
import {
  accountsQuery,
  actionQueueQuery,
  cancelActionMutation,
  createThreadMutation,
  sendTurnMutation,
  threadsQuery,
  updateThreadMutation,
} from "@/lib/api/queries";
import { EFFORT_LEVELS } from "@/lib/api/types";
import type { CreateThreadBody, Thread, UpdateThreadBody } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/format";
import {
  mapThread,
  orderThreads,
  queuedTurns,
  readStoredView,
  stoppingIds,
  storeView,
  threadMeter,
  type QueuedTurn,
  type ThreadItem,
  type ThreadMeter,
  type ThreadView,
} from "@/lib/thread";
import { Absent, Banner, Gauge, Malformed, Mono, WarningBanner } from "./primitives";
import { SlotsPanel } from "./slots";
import { useThreadEvents } from "./use-thread-events";

function optionLabel(t: Thread): string {
  const name = t.id === "main" ? "main" : `${t.title || t.id}${t.project ? ` · ${t.project}` : ""}`;
  return `${name} — ${t.account} · ${t.model}${t.status === "archived" ? " (archived)" : ""}`;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function ChatPanel({
  onClose,
  pollMs,
  select,
}: {
  onClose: () => void;
  pollMs?: number;
  /** A thread another screen asked for (`openThread`); `n` changes on every ask. */
  select?: { threadId: string; n: number } | null;
}) {
  const { data, isError, error } = useQuery(threadsQuery);
  const threads = useMemo(() => orderThreads(data?.data ?? []), [data]);
  const [chosen, setChosen] = useState<string | null>(select?.threadId ?? null);
  const [view, setView] = useState<ThreadView>(readStoredView);
  // Which thread form is open: none, *New thread*, or *Edit* on the shown thread.
  const [form, setForm] = useState<"new" | "edit" | null>(null);

  useEffect(() => {
    if (select) {
      setChosen(select.threadId);
      setForm(null);
    }
  }, [select]);

  const thread = threads.find((t) => t.id === chosen) ?? threads[0] ?? null;
  const events = useThreadEvents(thread?.id ?? null, pollMs);
  const items = useMemo(() => mapThread(events.rows, view), [events.rows, view]);
  const meter = useMemo(() => threadMeter(events.rows), [events.rows]);
  const hasMain = threads.some((t) => t.id === "main");

  const qc = useQueryClient();
  const queue = useQuery(actionQueueQuery);
  const queueData: unknown = queue.data?.data;
  const queued = useMemo(
    () => (thread ? queuedTurns(queueData, thread.id, events.rows) : []),
    [queueData, thread, events.rows],
  );
  const stopping = useMemo(() => stoppingIds(queueData), [queueData]);
  const workerStale =
    typeof queueData === "object" &&
    queueData !== null &&
    (queueData as { worker?: { stale?: unknown } }).worker?.stale === true;

  const send = useMutation(sendTurnMutation(qc));
  const cancel = useMutation(cancelActionMutation(qc));
  const update = useMutation(updateThreadMutation(qc));
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // The last refusal, with the thread it was for: shown until the next send or a
  // change of thread (ADR 190, 5).
  const [problem, setProblem] = useState<{ threadId: string; message: string } | null>(null);
  const draft = thread ? (drafts[thread.id] ?? "") : "";
  const shownProblem = problem && thread && problem.threadId === thread.id ? problem.message : null;
  const archived = thread?.status === "archived";

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight });
  }, [items.length, queued.length]);

  function choose(next: ThreadView) {
    setView(next);
    storeView(next);
  }

  function submit() {
    if (!thread || send.isPending || !draft.trim()) return;
    const id = thread.id;
    const text = draft;
    setProblem(null);
    send.mutate(
      { threadId: id, text },
      {
        // Cleared only if the person has not typed on since the send began.
        onSuccess: () => setDrafts((d) => (d[id] === text ? { ...d, [id]: "" } : d)),
        onError: (e) =>
          setProblem({ threadId: id, message: e instanceof Error ? e.message : String(e) }),
      },
    );
  }

  function stop(actionId: string) {
    if (!thread) return;
    const id = thread.id;
    cancel.mutate(actionId, {
      onError: (e) =>
        setProblem({ threadId: id, message: e instanceof Error ? e.message : String(e) }),
    });
  }

  function setStatus(status: "open" | "archived") {
    if (!thread || update.isPending) return;
    const id = thread.id;
    setProblem(null);
    update.mutate(
      { threadId: id, body: { status } },
      { onError: (e) => setProblem({ threadId: id, message: message(e) }) },
    );
  }

  return (
    <aside className="flex h-full w-[420px] shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <select
          aria-label="Thread"
          value={thread?.id ?? ""}
          disabled={threads.length === 0}
          onChange={(e) => {
            setChosen(e.target.value);
            setProblem(null);
          }}
          className="mono min-w-0 flex-1 rounded-sm border border-border bg-surface-2 px-2 py-1 text-[11px]"
        >
          {threads.map((t) => (
            <option key={t.id} value={t.id}>
              {optionLabel(t)}
            </option>
          ))}
        </select>
        <button
          onClick={() => setForm((f) => (f === "new" ? null : "new"))}
          aria-pressed={form === "new"}
          className="rounded-sm p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
          aria-label="New thread"
          title="New thread"
        >
          <Plus className="size-3.5" />
        </button>
        <button
          onClick={onClose}
          className="rounded-sm p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
          aria-label="Close chat"
        >
          <X className="size-3.5" />
        </button>
      </div>

      <SlotsPanel />

      {form === "new" && (
        <NewThreadForm
          hasMain={hasMain}
          onCancel={() => setForm(null)}
          onCreated={(record) => {
            setChosen(record.id);
            setProblem(null);
            setForm(null);
          }}
        />
      )}

      {thread && form === "edit" && (
        <EditThreadForm key={thread.id} thread={thread} onDone={() => setForm(null)} />
      )}

      {thread && (
        <Header
          thread={thread}
          meter={meter}
          view={view}
          onView={choose}
          editing={form === "edit"}
          onEdit={() => setForm((f) => (f === "edit" ? null : "edit"))}
          busy={update.isPending}
          onStatus={setStatus}
        />
      )}

      {data && !hasMain && (
        <Banner tone="info">
          No <Mono>main</Mono> thread yet. <em>New thread</em> opens it with{" "}
          <Mono>POST /api/threads</Mono> and <Mono>main: true</Mono>.
        </Banner>
      )}
      <WarningBanner warnings={[...(data?.warnings ?? []), ...events.warnings]} />

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {isError && (
          <p className="text-xs text-destructive">
            /api/threads did not answer: {error instanceof Error ? error.message : String(error)}
          </p>
        )}
        {events.error && (
          <p className="text-xs text-destructive">
            The thread's log did not answer: {events.error}
          </p>
        )}
        {data && threads.length === 0 && (
          <p className="text-xs text-muted-foreground">The harness holds no thread yet.</p>
        )}
        {thread && events.loaded && items.length === 0 && !events.error && (
          <p className="text-xs text-muted-foreground">No turn in this thread yet.</p>
        )}
        {items.map((item) => (
          <Item key={item.key} item={item} stopping={stopping} onStop={stop} />
        ))}
        {queued.map((turn) => (
          <QueuedItem key={turn.id} turn={turn} onCancel={stop} />
        ))}
      </div>

      <div className="space-y-1.5 border-t border-border px-3 py-2">
        {queue.isError && (
          <p className="text-[11px] text-destructive">
            The action queue did not answer, so queued messages are not shown.
          </p>
        )}
        {workerStale && (
          <p className="text-[11px] text-warning">
            The worker is not running: a message you send waits in the queue until it is.
          </p>
        )}
        {shownProblem && (
          <p role="alert" className="whitespace-pre-wrap text-[11px] text-destructive">
            {shownProblem}
          </p>
        )}
        {thread && archived && (
          <p className="text-[11px] text-muted-foreground">
            This thread is archived and takes no new turn.
          </p>
        )}
        {thread && !archived && (
          <div className="flex items-end gap-2">
            <textarea
              aria-label="Message"
              value={draft}
              rows={3}
              placeholder="Message this thread — Ctrl+Enter sends"
              onChange={(e) => {
                const next = e.target.value;
                setDrafts((d) => ({ ...d, [thread.id]: next }));
              }}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
              className="min-w-0 flex-1 resize-none rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs"
            />
            <button
              onClick={submit}
              disabled={!draft.trim() || send.isPending}
              className="rounded-sm border border-border px-3 py-1 text-xs hover:bg-surface-2 disabled:opacity-50"
            >
              {send.isPending ? "Sending…" : "Send"}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

const FIELD =
  "min-w-0 rounded-sm border border-border bg-surface-2 px-2 py-1 text-[11px] disabled:opacity-50";
const SMALL_BUTTON =
  "rounded-sm border border-border px-2 py-0.5 text-[11px] hover:bg-surface-2 disabled:opacity-50";

/**
 * `POST /api/threads` (ADR 195). The account is one the config names, or the
 * primary when left on the default; the effort is one of the five, or the
 * default; the model is always the service's default for that account. A
 * refusal stays in the form with what was typed.
 */
function NewThreadForm({
  hasMain,
  onCancel,
  onCreated,
}: {
  hasMain: boolean;
  onCancel: () => void;
  onCreated: (record: Thread) => void;
}) {
  const qc = useQueryClient();
  const create = useMutation(createThreadMutation(qc));
  const accounts = useQuery(accountsQuery);
  const names = (Array.isArray(accounts.data?.data) ? accounts.data.data : [])
    .map((a) => a?.name)
    .filter((n): n is string => typeof n === "string" && n !== "");
  const [title, setTitle] = useState("");
  const [account, setAccount] = useState("");
  const [effort, setEffort] = useState("");
  const [main, setMain] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  function submit() {
    if (create.isPending || (!main && !title.trim())) return;
    const body: CreateThreadBody = { title: title.trim() || "Main thread" };
    if (main) body.main = true;
    if (account) body.account = account;
    if (effort) body.effort = effort;
    setProblem(null);
    create.mutate(body, {
      onSuccess: (record) => onCreated(record),
      onError: (e) => setProblem(message(e)),
    });
  }

  return (
    <form
      aria-label="New thread"
      className="space-y-1.5 border-b border-border px-3 py-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        aria-label="Title"
        value={title}
        placeholder={main ? "Main thread" : "What this thread is about"}
        onChange={(e) => setTitle(e.target.value)}
        className={cn(FIELD, "w-full")}
      />
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Account"
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          className={FIELD}
        >
          <option value="">primary (default)</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select
          aria-label="Effort"
          value={effort}
          onChange={(e) => setEffort(e.target.value)}
          className={FIELD}
        >
          <option value="">effort (default)</option>
          {EFFORT_LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        {!hasMain && (
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <input type="checkbox" checked={main} onChange={(e) => setMain(e.target.checked)} />
            the main thread
          </label>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">
        The model is the account's default thread model; change it after with <em>Edit</em>.
      </p>
      {problem && (
        <p role="alert" className="whitespace-pre-wrap text-[11px] text-destructive">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={create.isPending || (!main && !title.trim())}
          className={SMALL_BUTTON}
        >
          {create.isPending ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={onCancel} className={SMALL_BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * `PATCH /api/threads/<id>` with the keys that changed (ADR 195). A new model or
 * effort opens a segment on the service's side, which the log draws as a change
 * line. A refusal stays in the form with what was typed.
 */
function EditThreadForm({ thread, onDone }: { thread: Thread; onDone: () => void }) {
  const qc = useQueryClient();
  const update = useMutation(updateThreadMutation(qc));
  const [title, setTitle] = useState(thread.title ?? "");
  const [model, setModel] = useState(thread.model ?? "");
  const [effort, setEffort] = useState(thread.effort ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const efforts: string[] = (EFFORT_LEVELS as readonly string[]).includes(effort)
    ? [...EFFORT_LEVELS]
    : [effort, ...EFFORT_LEVELS];

  function submit() {
    if (update.isPending) return;
    const body: UpdateThreadBody = {};
    if (title.trim() !== thread.title) body.title = title.trim();
    if (model.trim() !== thread.model) body.model = model.trim();
    if (effort !== thread.effort) body.effort = effort;
    if (Object.keys(body).length === 0) {
      onDone();
      return;
    }
    setProblem(null);
    update.mutate(
      { threadId: thread.id, body },
      { onSuccess: () => onDone(), onError: (e) => setProblem(message(e)) },
    );
  }

  return (
    <form
      aria-label="Edit thread"
      className="space-y-1.5 border-b border-border px-3 py-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        aria-label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className={cn(FIELD, "w-full")}
      />
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="Model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className={cn(FIELD, "mono flex-1")}
        />
        <select
          aria-label="Effort"
          value={effort}
          onChange={(e) => setEffort(e.target.value)}
          className={FIELD}
        >
          {efforts.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {problem && (
        <p role="alert" className="whitespace-pre-wrap text-[11px] text-destructive">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={update.isPending} className={SMALL_BUTTON}>
          {update.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onDone} className={SMALL_BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Header({
  thread,
  meter,
  view,
  onView,
  editing,
  onEdit,
  busy,
  onStatus,
}: {
  thread: Thread;
  meter: ThreadMeter;
  view: ThreadView;
  onView: (view: ThreadView) => void;
  editing: boolean;
  onEdit: () => void;
  busy: boolean;
  onStatus: (status: "open" | "archived") => void;
}) {
  const pct =
    meter.contextTokens !== null && meter.contextWindow
      ? Math.min(100, Math.round((meter.contextTokens / meter.contextWindow) * 100))
      : null;
  return (
    <div className="space-y-2 border-b border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-muted-foreground">
        <Mono>
          {thread.account} · {thread.model} · {thread.effort}
        </Mono>
        {thread.status === "archived" && <span className="label-xs">archived</span>}
        <span className="ml-auto mono" data-testid="thread-meter">
          context {meter.contextTokens === null ? "—" : meter.contextTokens.toLocaleString("en-US")}
          {meter.contextWindow ? ` / ${meter.contextWindow.toLocaleString("en-US")}` : ""}
          {pct !== null ? ` (${pct}%)` : ""} · cost{" "}
          {meter.costUsd === null ? "—" : `$${meter.costUsd.toFixed(2)}`}
        </span>
      </div>
      {pct !== null && (
        <Gauge value={pct} tone={pct >= 90 ? "danger" : pct >= 75 ? "warning" : "default"} />
      )}
      <div className="flex gap-1" role="group" aria-label="View">
        {(
          [
            ["text", "Text"],
            ["commands", "Text and commands"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            aria-pressed={view === value}
            onClick={() => onView(value)}
            className={cn(
              "rounded-sm border px-2 py-0.5 text-[11px]",
              view === value
                ? "border-ring bg-surface-2 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto flex gap-1">
          <button
            onClick={onEdit}
            aria-pressed={editing}
            className={SMALL_BUTTON}
            aria-label="Edit thread"
          >
            Edit
          </button>
          {/* The board service refuses to archive `main` (ADR 195), so it has no button. */}
          {thread.id !== "main" &&
            (thread.status === "archived" ? (
              <button onClick={() => onStatus("open")} disabled={busy} className={SMALL_BUTTON}>
                Reopen
              </button>
            ) : (
              <button onClick={() => onStatus("archived")} disabled={busy} className={SMALL_BUTTON}>
                Archive
              </button>
            ))}
        </span>
      </div>
    </div>
  );
}

const CHANGE_NAMES = { account: "account", model: "model", effort: "effort" } as const;

/** A message sent and not yet in the log: pending or claimed in the action queue (ADR 190, 3). */
function QueuedItem({ turn, onCancel }: { turn: QueuedTurn; onCancel: (id: string) => void }) {
  return (
    <div data-kind="queued" data-state={turn.state} className="text-xs leading-relaxed">
      <div className="mb-1 flex items-center gap-2">
        <span className="label-xs">operator</span>
        <span className="text-[10px] italic text-muted-foreground">{turn.state}</span>
        <button
          onClick={() => onCancel(turn.id)}
          disabled={turn.stopping}
          className="ml-auto rounded-sm border border-border px-1.5 py-0.5 text-[10px] hover:bg-surface-2 disabled:opacity-50"
        >
          {turn.stopping ? "stopping…" : "Cancel"}
        </button>
      </div>
      <p className="whitespace-pre-wrap text-foreground/80">{turn.text}</p>
    </div>
  );
}

function Item({
  item,
  stopping,
  onStop,
}: {
  item: ThreadItem;
  stopping: Set<string>;
  onStop: (actionId: string) => void;
}) {
  switch (item.kind) {
    case "session":
      return (
        <div
          role="separator"
          data-kind="session"
          className="border-t-2 border-border-strong pt-1.5"
        >
          <p className="text-[10px] font-medium text-foreground/80">
            New session — the model does not remember anything above this line.
          </p>
        </div>
      );
    case "change":
      return (
        <div role="separator" data-kind="change" className="flex items-center gap-2">
          <span className="h-px flex-1 bg-border" />
          <Mono className="text-[10px] text-muted-foreground">
            {item.changed
              .map((k) => `${CHANGE_NAMES[k]} ${item.from[k] || "—"} → ${item.to[k] || "—"}`)
              .join(" · ")}
          </Mono>
          <span className="h-px flex-1 bg-border" />
        </div>
      );
    case "user":
      return (
        <div className="text-xs leading-relaxed">
          <div className="mb-1 flex items-center gap-2">
            <span className="label-xs">{item.actor ?? "operator"}</span>
            <Mono className="text-[10px] text-muted-foreground/70">
              {formatClock(item.at ?? "")}
            </Mono>
          </div>
          <p className="whitespace-pre-wrap text-foreground">
            {item.text ? item.text : <Absent label="no text recorded" />}
          </p>
        </div>
      );
    case "assistant":
      return (
        <div className="text-xs leading-relaxed">
          <p className="label-xs mb-1">assistant</p>
          <p className="whitespace-pre-wrap text-foreground/90">{item.text}</p>
        </div>
      );
    case "tool":
      return <ToolCall item={item} />;
    case "turn":
      return <TurnEnd item={item} stopping={stopping} onStop={onStop} />;
    case "bad":
      return (
        <p data-kind="bad" className="text-[11px]">
          <Mono className="text-muted-foreground">line {item.n}</Mono>{" "}
          {item.line !== null ? (
            <span className="italic text-destructive">{item.reason}</span>
          ) : (
            <Malformed label={item.reason} got={item.got} want="an event" />
          )}
        </p>
      );
  }
}

const TURN_LABEL = {
  running: "running…",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
  unclosed: "ended without a turn_end",
} as const;

function TurnEnd({
  item,
  stopping,
  onStop,
}: {
  item: Extract<ThreadItem, { kind: "turn" }>;
  stopping: Set<string>;
  onStop: (actionId: string) => void;
}) {
  const stoppable = item.status === "running" ? item.action : null;
  const bad =
    item.status === "failed" || item.status === "interrupted" || item.status === "unclosed";
  return (
    <div
      data-kind="turn"
      data-status={item.status}
      className={cn(
        "text-[10px]",
        bad || item.status === "cancelled" ? "text-destructive" : "text-muted-foreground",
        item.status === "running" && "animate-pulse text-info",
      )}
    >
      <Mono>
        turn {TURN_LABEL[item.status]}
        {item.at ? ` · ${formatClock(item.at)}` : ""}
        {item.costUsd !== null ? ` · $${item.costUsd.toFixed(2)}` : ""}
      </Mono>
      {stoppable && (
        <button
          onClick={() => onStop(stoppable)}
          disabled={stopping.has(stoppable)}
          className="ml-2 rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-foreground hover:bg-surface-2 disabled:opacity-50"
        >
          {stopping.has(stoppable) ? "stopping…" : "Stop"}
        </button>
      )}
      {item.error && <p className="mt-0.5 whitespace-pre-wrap">{item.error}</p>}
    </div>
  );
}

function ToolCall({ item }: { item: Extract<ThreadItem, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div data-kind="tool" className="rounded-sm border border-border bg-surface-2">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px]"
      >
        <ChevronRight className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")} />
        <Wrench className="size-3 shrink-0 text-muted-foreground" />
        <Mono className="shrink-0 text-[11px]">{item.name}</Mono>
        <span className="min-w-0 truncate text-[10px] text-muted-foreground">{item.summary}</span>
        {item.denied && (
          <span className="ml-auto shrink-0 text-[10px] font-medium uppercase text-destructive">
            denied
          </span>
        )}
        {!item.denied && item.isError && (
          <span className="ml-auto shrink-0 text-[10px] uppercase text-destructive">error</span>
        )}
      </button>
      {item.dispatch && (
        <p className="flex flex-wrap items-center gap-x-2 border-t border-border px-2 py-1 text-[10px] text-muted-foreground">
          <span>queued</span>
          <Link
            to="/tasks/$taskId"
            params={{ taskId: item.dispatch.taskId }}
            className="mono underline hover:text-foreground"
          >
            {item.dispatch.taskId}
          </Link>
          <Link to="/queue" className="mono underline hover:text-foreground">
            {item.dispatch.actionId}
          </Link>
        </p>
      )}
      {open && (
        <pre className="mono max-h-64 overflow-auto whitespace-pre-wrap border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground">
          {item.result ?? "no result recorded"}
        </pre>
      )}
    </div>
  );
}
