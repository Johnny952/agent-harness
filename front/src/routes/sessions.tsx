import { useEffect, useMemo, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { EmptyState, ErrorState, Mono, PageHeader, RoleBadge } from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import { POLL_MS, sessionsQuery } from "@/lib/api/queries";
import type { SessionLogLine } from "@/lib/api/ops-types";
import { formatClock } from "@/lib/format";
import { useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/sessions")({
  head: () => ({
    meta: [
      { title: "Session logs — harness operations console" },
      { name: "description", content: "Real-time log output from every agent container session." },
      { property: "og:title", content: "Session logs — harness operations console" },
      { property: "og:description", content: "Live stdout of each running agent session." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SessionsPage,
});

const levelTone = {
  debug: "text-muted-foreground/70",
  info: "text-foreground",
  warn: "text-warning",
  error: "text-destructive",
} as const;

function SessionsPage() {
  const sessions = useQuery(sessionsQuery);
  const searchRef = useSearchHotkey();
  const [lines, setLines] = useState<SessionLogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [container, setContainer] = useState("all");
  const [level, setLevel] = useState("all");
  const [q, setQ] = useState("");
  const [paused, setPaused] = useState(false);
  const [refreshed, setRefreshed] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastSeq = useRef<number | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await api.listSessionLines(lastSeq.current);
        if (!alive) return;
        if (next.length) lastSeq.current = next[next.length - 1]!.seq;
        setLines((prev) => [...prev, ...next].slice(-2000));
        setError(null);
        setRefreshed(Date.now());
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "unknown error");
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const shown = useMemo(
    () =>
      lines.filter(
        (l) =>
          (container === "all" || l.container === container) &&
          (level === "all" || l.level === level) &&
          (!q || `${l.text} ${l.task_id ?? ""}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [lines, container, level, q],
  );

  useEffect(() => {
    if (!paused && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [shown, paused]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (!atBottom && !paused) setPaused(true);
  };

  const stale = refreshed > 0 && Date.now() - refreshed > POLL_MS * 4;

  return (
    <AppShell>
      <div className="flex h-full flex-col">
        <PageHeader
          title="Session logs"
          subtitle="Raw output of each agent container, streamed by seq > last_seen."
          right={<RefreshedAt at={refreshed} />}
        />
        <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">
          <button
            onClick={() => setContainer("all")}
            className={cn("rounded-sm border px-2 py-0.5 text-[11px]", container === "all" ? "border-ring bg-surface-2" : "border-border text-muted-foreground")}
          >
            all sessions
          </button>
          {(sessions.data ?? []).map((s) => (
            <button
              key={s.container}
              onClick={() => setContainer(s.container)}
              className={cn(
                "flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-[11px]",
                container === s.container ? "border-ring bg-surface-2" : "border-border text-muted-foreground",
              )}
            >
              <span className={cn("size-1.5 rounded-full", s.live ? "bg-success" : "bg-muted-foreground/40")} />
              <Mono>{s.container}</Mono>
              {s.task_id ? <Mono className="text-muted-foreground">{s.task_id}</Mono> : <span className="italic">idle</span>}
              {s.role && <RoleBadge role={s.role} compact />}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          <select value={level} onChange={(e) => setLevel(e.target.value)} className="rounded-sm border border-border bg-surface px-2 py-1 text-[11px]">
            <option value="all">all levels</option>
            <option value="debug">debug</option>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="error">error</option>
          </select>
          <input
            ref={searchRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter text or task…  /"
            className="mono w-64 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
          />
          <span className="flex-1" />
          <span className="text-[11px] text-muted-foreground">{shown.length} lines</span>
          <button
            onClick={() => setPaused((p) => !p)}
            className="flex items-center gap-1 rounded-sm border border-border px-2 py-1 text-[11px] hover:bg-surface-2"
          >
            {paused ? <Play className="size-3" /> : <Pause className="size-3" />}
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
        {error && (
          <ErrorState title="The session stream stopped answering" body={`Last error: ${error}. Lines already shown are kept; the stream retries every ${POLL_MS / 1000}s.`} />
        )}
        {stale && !error && (
          <p className="border-b border-warning/40 bg-warning/10 px-4 py-1 text-[11px] text-warning">
            No new lines for over {(POLL_MS * 4) / 1000}s — the stream may be stuck. Check the container is still running on the Pool screen.
          </p>
        )}
        {shown.length === 0 && !error ? (
          <EmptyState title="No log lines match" body="Either no session is running or your filters hide everything. Clear the filters or start a task from the Queue." />
        ) : (
          <div ref={scrollRef} onScroll={onScroll} className="mono min-h-0 flex-1 overflow-y-auto bg-background px-4 py-2 text-[11px] leading-5">
            {shown.map((l) => (
              <div key={l.seq} className="flex gap-2 whitespace-pre-wrap">
                <span className="shrink-0 text-muted-foreground/60">{formatClock(l.ts)}</span>
                <span className="w-28 shrink-0 truncate text-muted-foreground">{l.container}</span>
                <span className={cn("w-10 shrink-0 uppercase", levelTone[l.level])}>{l.level}</span>
                <span className={levelTone[l.level]}>{l.text}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
