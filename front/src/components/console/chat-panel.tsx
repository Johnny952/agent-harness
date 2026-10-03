import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Send, X, Wrench } from "lucide-react";
import { threadsQuery, tasksQuery } from "@/lib/api/queries";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/format";
import type { ChatMessage } from "@/lib/api/types";
import { Mono, Absent } from "./primitives";

export function ChatPanel({ onClose }: { onClose: () => void }) {
  const { data: threads, isError } = useQuery(threadsQuery);
  const { data: tasks } = useQuery(tasksQuery);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [extra, setExtra] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const thread = useMemo(
    () => threads?.find((t) => t.id === threadId) ?? threads?.[0] ?? null,
    [threads, threadId],
  );

  // `tasksQuery` resolves to an envelope now (ADR 16), and a task's status carries
  // no role (ADR 26) — so "a phase is running" is the `in_progress` status itself
  // rather than a role parsed out of it. Which role is running waits on
  // `/api/phases`, and nothing on this dock needs it: the chat surface is what
  // C-8 declined to decide, and this is a translation, not a design.
  const phaseRunning = (tasks?.data ?? []).some((t) => t.status === "in_progress");
  const messages = thread ? [...thread.messages, ...extra] : [];

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, streaming]);

  function send() {
    const text = draft.trim();
    if (!text || !thread) return;
    setDraft("");
    setExtra((e) => [
      ...e,
      {
        id: `u-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
        tool_calls: [],
      },
    ]);
    const reply =
      "Reading the harness state for that. This reply streams from a fixture until the real thread endpoint is wired in lib/api/client.ts.";
    let i = 0;
    setStreaming("");
    const timer = setInterval(() => {
      i += 3;
      setStreaming(reply.slice(0, i));
      if (i >= reply.length) {
        clearInterval(timer);
        setStreaming(null);
        setExtra((e) => [
          ...e,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: reply,
            created_at: new Date().toISOString(),
            tool_calls: [
              {
                id: `tc-${Date.now()}`,
                name: "get_harness_state",
                arguments: { scope: "all" },
                result_summary: "9 tasks, 6 accounts, 5 actions",
              },
            ],
          },
        ]);
      }
    }, 28);
  }

  return (
    <aside className="flex h-full w-[380px] shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <select
          value={thread?.id ?? ""}
          onChange={(e) => {
            setThreadId(e.target.value);
            setExtra([]);
          }}
          className="mono flex-1 rounded-sm border border-border bg-surface-2 px-2 py-1 text-[11px]"
        >
          {(threads ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.title}
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

      {thread && (
        <div className="border-b border-border px-3 py-2">
          <div className="flex items-center justify-between text-[10px] text-muted-foreground">
            <span>context</span>
            <span className="mono">
              {Math.round((thread.context_used / thread.context_limit) * 100)}% ·{" "}
              {thread.context_used.toLocaleString()} / {thread.context_limit.toLocaleString()}
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full bg-info"
              style={{ width: `${(thread.context_used / thread.context_limit) * 100}%` }}
            />
          </div>
          <button
            disabled={phaseRunning}
            title={
              phaseRunning
                ? "A phase is running — compacting now would cut the thread the phase is reading."
                : "Compact this thread"
            }
            className="mt-2 w-full rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            Compact thread
          </button>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {isError && (
          <p className="text-xs text-destructive">
            The chat backend did not answer. Read-only screens are unaffected.
          </p>
        )}
        {!isError && messages.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No messages in this thread yet. Ask about a task id, an account, or the queue.
          </p>
        )}
        {messages.map((m) => (
          <MessageRow key={m.id} message={m} />
        ))}
        {streaming !== null && (
          <div className="text-xs leading-relaxed">
            <p className="label-xs mb-1">assistant</p>
            <p>
              {streaming}
              <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-foreground align-middle" />
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-border p-2">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask the harness…"
            className="flex-1 resize-none rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs outline-none focus:border-ring"
          />
          <button
            onClick={send}
            className="rounded-sm border border-border bg-surface-2 p-2 text-muted-foreground hover:text-foreground"
            aria-label="Send"
          >
            <Send className="size-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  return (
    <div className="text-xs leading-relaxed">
      <div className="mb-1 flex items-center gap-2">
        <span className="label-xs">{message.role}</span>
        <Mono className="text-[10px] text-muted-foreground/70">
          {formatClock(message.created_at)}
        </Mono>
      </div>
      <p className={cn(message.role === "user" ? "text-foreground" : "text-foreground/90")}>
        {message.content || <Absent label="empty message" />}
      </p>
      {message.tool_calls.map((tc) => (
        <ToolCallCard key={tc.id} name={tc.name} args={tc.arguments} summary={tc.result_summary} />
      ))}
    </div>
  );
}

function ToolCallCard({
  name,
  args,
  summary,
}: {
  name: string;
  args: Record<string, unknown>;
  summary: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1.5 rounded-sm border border-border bg-surface-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px]"
      >
        <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
        <Wrench className="size-3 text-muted-foreground" />
        <Mono className="text-[11px]">{name}</Mono>
        <span className="ml-auto truncate text-[10px] text-muted-foreground">{summary}</span>
      </button>
      {open && (
        <pre className="mono overflow-x-auto border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
    </div>
  );
}
