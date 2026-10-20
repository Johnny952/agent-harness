/**
 * The chat dock: the harness's threads, read-only (`docs/decisions.md` ADR 184,
 * step 4 of `docs/plans/main-thread.md`).
 *
 * The selector lists `GET /api/threads` with `main` first; the history is the
 * selected thread's log, every segment of it in one scroll, read by
 * `useThreadEvents` and mapped by `lib/thread.ts`. The switch picks the *Text* or
 * the *Text and commands* view and is remembered in this browser.
 *
 * There is no composer and no stop button on purpose: sending a turn and
 * stopping one are step 5's, through the board service, and the dock says so
 * rather than drawing a box that sends nowhere (`docs/ui.md` *A region with no
 * route says which route, and when*).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Wrench, X } from "lucide-react";
import { threadsQuery } from "@/lib/api/queries";
import type { Thread } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/format";
import {
  mapThread,
  orderThreads,
  readStoredView,
  storeView,
  threadMeter,
  type ThreadItem,
  type ThreadMeter,
  type ThreadView,
} from "@/lib/thread";
import { Absent, Banner, Gauge, Malformed, Mono, WarningBanner } from "./primitives";
import { useThreadEvents } from "./use-thread-events";

function optionLabel(t: Thread): string {
  const name = t.id === "main" ? "main" : `${t.title || t.id}${t.project ? ` · ${t.project}` : ""}`;
  return `${name} — ${t.account} · ${t.model}${t.status === "archived" ? " (archived)" : ""}`;
}

export function ChatPanel({ onClose, pollMs }: { onClose: () => void; pollMs?: number }) {
  const { data, isError, error } = useQuery(threadsQuery);
  const threads = useMemo(() => orderThreads(data?.data ?? []), [data]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [view, setView] = useState<ThreadView>(readStoredView);

  const thread = threads.find((t) => t.id === chosen) ?? threads[0] ?? null;
  const events = useThreadEvents(thread?.id ?? null, pollMs);
  const items = useMemo(() => mapThread(events.rows, view), [events.rows, view]);
  const meter = useMemo(() => threadMeter(events.rows), [events.rows]);
  const hasMain = threads.some((t) => t.id === "main");

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight });
  }, [items.length]);

  function choose(next: ThreadView) {
    setView(next);
    storeView(next);
  }

  return (
    <aside className="flex h-full w-[420px] shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <select
          aria-label="Thread"
          value={thread?.id ?? ""}
          disabled={threads.length === 0}
          onChange={(e) => setChosen(e.target.value)}
          className="mono min-w-0 flex-1 rounded-sm border border-border bg-surface-2 px-2 py-1 text-[11px]"
        >
          {threads.map((t) => (
            <option key={t.id} value={t.id}>
              {optionLabel(t)}
            </option>
          ))}
        </select>
        <button
          onClick={onClose}
          className="rounded-sm p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
          aria-label="Close chat"
        >
          <X className="size-3.5" />
        </button>
      </div>

      {thread && <Header thread={thread} meter={meter} view={view} onView={choose} />}

      <Banner tone="info">
        Read-only. Sending a turn and stopping one land with step 5 of{" "}
        <Mono>docs/plans/main-thread.md</Mono>, through the board service; this dock reads{" "}
        <Mono>/api/threads</Mono> and the thread's log.
      </Banner>
      {data && !hasMain && (
        <Banner tone="info">
          No <Mono>main</Mono> thread yet. The board service opens it on the first request for it,
          which this dock cannot make until step 5.
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
          <Item key={item.key} item={item} />
        ))}
      </div>
    </aside>
  );
}

function Header({
  thread,
  meter,
  view,
  onView,
}: {
  thread: Thread;
  meter: ThreadMeter;
  view: ThreadView;
  onView: (view: ThreadView) => void;
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
      </div>
    </div>
  );
}

const CHANGE_NAMES = { account: "account", model: "model", effort: "effort" } as const;

function Item({ item }: { item: ThreadItem }) {
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
      return <TurnEnd item={item} />;
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

function TurnEnd({ item }: { item: Extract<ThreadItem, { kind: "turn" }> }) {
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
      {open && (
        <pre className="mono max-h-64 overflow-auto whitespace-pre-wrap border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground">
          {item.result ?? "no result recorded"}
        </pre>
      )}
    </div>
  );
}
