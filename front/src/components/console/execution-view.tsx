/**
 * The dock's execution thread (`docs/decisions.md` ADR NNN, step 7 of
 * `docs/plans/main-thread.md`): one started task's cycle in one scroll, read
 * only. Nothing here writes and nothing spends quota: every read is one the
 * console already makes (the task, its usage, the action queue, the threads)
 * plus the task's hook events (`GET /api/tasks/<id>/events`), and the shaping is
 * `lib/execution.ts`.
 *
 * The scroll is the start note, then each phase as a segment — its role,
 * account and model, its events under the thread's *Text* / *Text and commands*
 * switch, its handoff and its gates — oldest first. There is no composer:
 * talking about the task is a thread whose `task` is it (ADR 195), which the
 * view opens in the dock when one exists, and the task page has the rest.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Wrench } from "lucide-react";
import { actionQueueListQuery, taskQuery, threadsQuery, usageQuery } from "@/lib/api/queries";
import { ROLES } from "@/lib/api/types";
import type { Role, Task } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/format";
import {
  buildSegments,
  mapEvents,
  parseSections,
  startNote,
  type ExecutionItem,
  type Segment,
} from "@/lib/execution";
import type { ThreadView } from "@/lib/thread";
import { Absent, GateChip, Mono, RoleBadge, WarningBanner } from "./primitives";
import { Markdown } from "./markdown";
import { useTaskEvents } from "./use-task-events";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function isRole(v: string): v is Role {
  return (ROLES as readonly string[]).includes(v);
}

export function ExecutionView({
  task,
  view,
  onView,
  onOpenThread,
  pollMs,
}: {
  /** The row from `/api/tasks`; the body and the rest are read here. */
  task: Task;
  view: ThreadView;
  onView: (view: ThreadView) => void;
  /** Shows a thread in the dock, as `openThread` does from a screen. */
  onOpenThread: (threadId: string) => void;
  pollMs?: number | undefined;
}) {
  const id = task.task_id;
  const detail = useQuery(taskQuery(id));
  const usage = useQuery(usageQuery(id));
  const queue = useQuery(actionQueueListQuery);
  const threads = useQuery(threadsQuery);
  const events = useTaskEvents(id, pollMs);

  const body = detail.data?.data?.body;
  const sections = useMemo(() => parseSections(body), [body]);
  const groups = usage.data?.data;
  const segments = useMemo(
    () => buildSegments(sections, groups, events.rows, task),
    [sections, groups, events.rows, task],
  );
  const note = useMemo(() => startNote(queue.data?.data, id), [queue.data, id]);
  const talk = (Array.isArray(threads.data?.data) ? threads.data.data : []).find(
    (t) => t?.task === id && t.status !== "archived",
  );

  const reads: string[] = [];
  if (detail.isError) reads.push(`the task did not answer: ${message(detail.error)}`);
  if (usage.isError) reads.push(`its usage did not answer: ${message(usage.error)}`);
  if (events.error) reads.push(`its events did not answer: ${events.error}`);
  const loading = !detail.data && !detail.isError;
  const empty = !loading && segments.length === 0;

  return (
    <>
      <div className="space-y-2 border-b border-border px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-muted-foreground">
          <span className="label-xs">execution</span>
          <Link
            to="/tasks/$taskId"
            params={{ taskId: id }}
            className="mono underline hover:text-foreground"
          >
            {id}
          </Link>
          <Mono>
            {task.phase ? `running ${task.phase}` : task.status}
            {task.owner ? ` · ${task.owner}` : ""}
          </Mono>
          {talk && (
            <button
              onClick={() => onOpenThread(talk.id)}
              className="ml-auto rounded-sm border border-border px-2 py-0.5 text-[11px] hover:bg-surface-2"
            >
              Open its thread
            </button>
          )}
        </div>
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

      <WarningBanner
        warnings={[
          ...(detail.data?.warnings ?? []),
          ...(usage.data?.warnings ?? []),
          ...events.warnings,
        ]}
      />

      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3" data-testid="execution">
        {reads.map((r) => (
          <p key={r} className="text-xs text-destructive">
            {r[0]!.toUpperCase() + r.slice(1)}
          </p>
        ))}
        {loading && <p className="text-xs text-muted-foreground">Reading the task…</p>}

        <section data-kind="start-note" className="text-xs leading-relaxed">
          <div className="mb-1 flex items-center gap-2">
            <span className="label-xs">start note</span>
            {note?.actor && <span className="label-xs">{note.actor}</span>}
            {note?.at && (
              <Mono className="text-[10px] text-muted-foreground/70">{formatClock(note.at)}</Mono>
            )}
          </div>
          {note ? (
            <p className="whitespace-pre-wrap text-foreground">{note.text}</p>
          ) : (
            <p className="text-[11px]">
              <Absent label="none in the queue's latest records" />{" "}
              <span className="text-muted-foreground">
                — an older one is in the task's comments.
              </span>
            </p>
          )}
        </section>

        {empty && (
          <p className="text-xs text-muted-foreground">
            No phase of this task has run or written yet.
          </p>
        )}
        {segments.map((s) => (
          <SegmentBlock key={s.key} segment={s} view={view} />
        ))}
      </div>
    </>
  );
}

function SegmentBlock({ segment, view }: { segment: Segment; view: ThreadView }) {
  const items = useMemo(() => mapEvents(segment.events, view), [segment.events, view]);
  const section = segment.section;
  return (
    <section
      data-kind="segment"
      data-role={segment.role}
      className="space-y-2 border-t-2 border-border-strong pt-1.5"
    >
      <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-muted-foreground">
        {isRole(segment.role) ? (
          <RoleBadge role={segment.role} />
        ) : (
          <span className="label-xs">{segment.role}</span>
        )}
        {segment.round !== null && <Mono>round {segment.round}</Mono>}
        {segment.phase && (
          <Mono>
            {segment.account ?? "—"} · {segment.model ?? "—"}
          </Mono>
        )}
        {segment.running && <span className="animate-pulse text-info">running…</span>}
      </div>
      {!segment.phase && (
        <p className="text-[10px] text-muted-foreground">
          Events under the task's worktree that no phase's session or time window holds.
        </p>
      )}
      {items.map((item) => (
        <EventItem key={item.key} item={item} />
      ))}
      {segment.phase && items.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          {view === "text" ? "No text event recorded." : "No event recorded."}
        </p>
      )}
      {section && (
        <div data-kind="handoff" className="space-y-1">
          <p className="label-xs">handoff</p>
          {section.handoff ? (
            <div className="text-xs">
              <Markdown source={section.handoff} />
            </div>
          ) : (
            <Absent label="empty handoff" />
          )}
        </div>
      )}
      {segment.phase && !segment.running && !section && (
        <p className="text-[11px]">
          <Absent label="no handoff in the task body" />
        </p>
      )}
      {section && section.gates.length > 0 && (
        <ul data-kind="gates" className="space-y-1">
          {section.gates.map((g, i) => (
            <li key={i} className="flex items-start gap-2 text-[11px]">
              <GateChip level={g.level} label={g.gate} />
              <span className="text-muted-foreground">{g.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function EventItem({ item }: { item: ExecutionItem }) {
  switch (item.kind) {
    case "prompt":
      return (
        <div data-kind="prompt" className="text-xs leading-relaxed">
          <div className="mb-1 flex items-center gap-2">
            <span className="label-xs">prompt</span>
            <Mono className="text-[10px] text-muted-foreground/70">
              {formatClock(item.at ?? "")}
            </Mono>
          </div>
          <p className="max-h-48 overflow-y-auto whitespace-pre-wrap text-foreground/80">
            {item.text}
          </p>
        </div>
      );
    case "assistant":
      return (
        <div data-kind="assistant" className="text-xs leading-relaxed">
          <p className="label-xs mb-1">{item.subagent ? "subagent" : "assistant"}</p>
          <p className="whitespace-pre-wrap text-foreground/90">{item.text}</p>
        </div>
      );
    case "notice":
      return (
        <p data-kind="notice" className="text-[11px] italic text-muted-foreground">
          {item.text}
        </p>
      );
    case "marker":
      return (
        <p data-kind="marker" className="text-[10px] text-muted-foreground">
          <Mono>
            {item.label}
            {item.at ? ` · ${formatClock(item.at)}` : ""}
          </Mono>
        </p>
      );
    case "tool":
      return <ToolCall item={item} />;
  }
}

function ToolCall({ item }: { item: Extract<ExecutionItem, { kind: "tool" }> }) {
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
      </button>
      {open && (
        <pre className="mono max-h-64 overflow-auto whitespace-pre-wrap border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground">
          {item.result ?? "no result recorded"}
        </pre>
      )}
    </div>
  );
}
