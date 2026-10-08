import { useEffect, useMemo, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Pause, Play } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import type { HookEvent } from "@/lib/api/types";
import { formatClock } from "@/lib/format";
import { useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/tail")({
  head: () => ({
    meta: [
      { title: "Live tail — harness operations console" },
      {
        name: "description",
        content:
          "Append-only hook event stream with pause-on-scroll, source and type filters and per-minute rate.",
      },
      { property: "og:title", content: "Live tail — harness operations console" },
      { property: "og:description", content: "Streaming hook events from the harness." },
    ],
  }),
  component: TailPage,
});

const ROW_H = 24;

/**
 * What to show when a read failed.
 *
 * An `ApiError` carries the api's own sentence, and the forward's 502 names what
 * did not answer — `The api did not answer: …`, the service and not its address —
 * which is the difference between "the harness is quiet" and "the console cannot
 * reach it". Anything else keeps the generic line.
 */
function failureMessage(cause: unknown, fallback: string): string {
  return cause instanceof api.ApiError ? cause.message : fallback;
}

function TailPage() {
  const searchRef = useSearchHotkey();
  const [events, setEvents] = useState<HookEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Warnings are their own piece of state and not folded into `error`. A warning
  // is a successful read with something to say — rows plus a warning is a partial,
  // which shows both — and this screen used to collapse every failure *and* every
  // warning into one `error` string, which swallowed the warning outright.
  // `docs/ui.md` *A degraded backend is a banner, not a blank screen*.
  const [warnings, setWarnings] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [source, setSource] = useState("all");
  const [type, setType] = useState("all");
  const [taskQ, setTaskQ] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState(Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(600);

  useEffect(() => {
    let alive = true;
    api
      .listEvents()
      .then(({ data, warnings: w }) => {
        if (!alive) return;
        setEvents(data);
        setWarnings(w);
        setLastRefreshed(Date.now());
      })
      .catch(
        (cause: unknown) =>
          alive && setError(failureMessage(cause, "The event stream could not be opened.")),
      );
    return () => {
      alive = false;
    };
  }, []);

  // SSE-style polling on id > last_seen. Never faster than 2s.
  //
  // `events.at(-1)?.id` is the *newest* id this screen holds and the array stays
  // ascending, because `listEvents` reverses each batch out of the api's
  // `ORDER BY id DESC` before it gets here. Neither the cursor nor the append
  // changed for the wiring, and neither should: `docs/decisions.md` ADR 22 keeps
  // the knowledge of the api's order in `client.ts`, in one place.
  useEffect(() => {
    if (paused) return;
    const t = setInterval(async () => {
      try {
        const last = events.at(-1)?.id ?? 0;
        const { data: next, warnings: w } = await api.listEvents(last);
        setEvents((prev) => [...prev, ...next].slice(-2000));
        setWarnings(w);
        setLastRefreshed(Date.now());
        setError(null);
      } catch (cause) {
        setError(failureMessage(cause, "The event stream dropped. Retrying every 2.5s."));
      }
    }, 2500);
    return () => clearInterval(t);
  }, [paused, events]);

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        if (source !== "all" && e.source_app !== source) return false;
        if (type !== "all" && e.event_type !== type) return false;
        if (taskQ && !JSON.stringify(e.payload).toLowerCase().includes(taskQ.toLowerCase()))
          return false;
        return true;
      }),
    [events, source, type, taskQ],
  );

  useEffect(() => {
    if (paused) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [filtered.length, paused]);

  const sources = Array.from(new Set(events.map((e) => e.source_app)));
  const types = Array.from(new Set(events.map((e) => e.event_type)));

  const perMinute = useMemo(() => {
    const buckets = new Array(20).fill(0) as number[];
    const now = Date.now();
    for (const e of events) {
      const minsAgo = Math.floor((now - new Date(e.created_at).getTime()) / 60000);
      if (minsAgo >= 0 && minsAgo < 20) buckets[19 - minsAgo]! += 1;
    }
    return buckets;
  }, [events]);

  const total = filtered.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - 10);
  const end = Math.min(total, start + Math.ceil(viewportH / ROW_H) + 20);
  const slice = filtered.slice(start, end);

  return (
    <AppShell>
      <PageHeader
        title="Live tail"
        subtitle="Append-only hook stream, polled on id > last_seen every 2.5s."
        right={
          <>
            <RefreshedAt at={lastRefreshed} />
            <button
              onClick={() => setPaused((p) => !p)}
              className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              {paused ? <Play className="size-3" /> : <Pause className="size-3" />}
              {paused ? "resume" : "pause"}
            </button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={taskQ}
          onChange={(e) => setTaskQ(e.target.value)}
          placeholder="Filter payload / task…  /"
          className="mono w-56 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
        <Picker label="source" value={source} onChange={setSource} options={["all", ...sources]} />
        <Picker label="type" value={type} onChange={setType} options={["all", ...types]} />
        <div
          className="ml-auto flex items-end gap-[2px]"
          title="events per minute, last 20 minutes"
        >
          {perMinute.map((v, i) => (
            <span
              key={i}
              className="w-[3px] bg-info"
              style={{ height: `${Math.max(2, Math.min(24, v * 2))}px` }}
            />
          ))}
        </div>
      </div>

      {paused && (
        <div className="border-b border-warning/40 bg-warning/10 px-4 py-1 text-[11px] text-warning">
          Paused — new events are still arriving on the server. Resume to catch up.
        </div>
      )}
      {error && (
        <div className="border-b border-destructive/40 bg-destructive/10 px-4 py-1 text-[11px] text-destructive">
          {error}
        </div>
      )}
      <WarningBanner warnings={warnings} />

      {!error && events.length === 0 ? (
        <EmptyState
          title="No events yet"
          body="The api answered and the collector holds no events. Run a phase and rows will appear here — this console polls /api/events on id > last_seen and terminates no stream of its own."
        />
      ) : error && events.length === 0 ? (
        <ErrorState
          title="The event stream is unavailable"
          body="Nothing can be tailed right now. The other read-only screens still work."
        />
      ) : (
        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            setScrollTop(el.scrollTop);
            setViewportH(el.clientHeight);
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            if (!atBottom && !paused) setPaused(true);
          }}
          className="h-[calc(100vh-170px)] overflow-y-auto"
        >
          <div style={{ height: total * ROW_H, position: "relative" }}>
            <div style={{ transform: `translateY(${start * ROW_H}px)` }}>
              {slice.map((e) => (
                <div key={e.id} className="border-b border-border/60">
                  <button
                    onClick={() => setExpanded((x) => (x === e.id ? null : e.id))}
                    className={cn(
                      "flex w-full items-center gap-3 px-4 text-left text-[11px] hover:bg-surface-2/60",
                    )}
                    style={{ height: ROW_H }}
                  >
                    <Mono className="w-16 text-muted-foreground">{formatClock(e.created_at)}</Mono>
                    <Mono className="w-14 text-muted-foreground">{e.id}</Mono>
                    <span className="w-16 text-muted-foreground">{e.source_app}</span>
                    <Mono className="w-36">{e.event_type}</Mono>
                    <span className="flex-1 truncate text-muted-foreground">
                      {JSON.stringify(e.payload)}
                    </span>
                  </button>
                  {expanded === e.id && (
                    <pre className="mono overflow-x-auto bg-surface px-4 py-2 text-[10px] text-muted-foreground">
                      {JSON.stringify(e.payload, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Picker({
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
