import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Bot, User } from "lucide-react";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Absent, EmptyState, ErrorState, Mono, PageHeader } from "@/components/console/primitives";
import { actionBackendQuery, actionsQuery } from "@/lib/api/queries";
import { commandLine, enqueueAction } from "@/lib/api/client";
import type { ActionVerb, QueuedAction } from "@/lib/api/types";
import { agoSeconds, formatAge } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/queue")({
  head: () => ({
    meta: [
      { title: "Queue — harness operations console" },
      {
        name: "description",
        content:
          "The action queue: running, waiting and failed commands, who enqueued them, and the live output tail.",
      },
      { property: "og:title", content: "Queue — harness operations console" },
      { property: "og:description", content: "Start and watch harness commands." },
    ],
  }),
  component: QueuePage,
});

const VERBS: ActionVerb[] = [
  "run-task",
  "run-phase",
  "merge-task",
  "cleanup-task",
  "release-account",
  "bootstrap-project",
];

const stateTone: Record<QueuedAction["state"], string> = {
  running: "text-info border-info/50 bg-info/10",
  queued: "text-muted-foreground border-border-strong bg-surface-2",
  succeeded: "text-success border-success/50 bg-success/10",
  failed: "text-destructive border-destructive/60 bg-destructive/10",
};

function QueuePage() {
  const now = useNow();
  const actions = useQuery(actionsQuery);
  const { data: backendUp } = useQuery(actionBackendQuery);
  const [pending, setPending] = useState<{ verb: ActionVerb; args: string[] } | null>(null);
  const [argText, setArgText] = useState("");
  const [confirmText, setConfirmText] = useState("");

  const rows = actions.data ?? [];
  const running = rows.filter((a) => a.state === "running");
  const waiting = rows.filter((a) => a.state === "queued");
  const finished = rows.filter((a) => a.state === "succeeded" || a.state === "failed");

  const destructive = pending?.verb === "cleanup-task";
  const confirmTarget = pending?.args[0] ?? "";
  const canConfirm = !destructive || confirmText === confirmTarget;

  async function run() {
    if (!pending) return;
    try {
      await enqueueAction(pending.verb, pending.args);
      toast.success(`Enqueued ${pending.verb} ${pending.args.join(" ")}`);
      void actions.refetch();
    } catch {
      toast.error("The action backend refused the command. Nothing was started.");
    }
    setPending(null);
    setConfirmText("");
    setArgText("");
  }

  return (
    <AppShell>
      <PageHeader
        title="Queue"
        subtitle="Every action is confirmed with the exact command that will run."
        right={<RefreshedAt at={actions.dataUpdatedAt} />}
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <input
          value={argText}
          onChange={(e) => setArgText(e.target.value)}
          placeholder="arguments, e.g. T-013"
          className="mono w-48 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
        {VERBS.map((v) => (
          <button
            key={v}
            disabled={backendUp === false}
            onClick={() =>
              setPending({ verb: v, args: argText.trim().split(/\s+/).filter(Boolean) })
            }
            className={cn(
              "mono rounded-sm border px-2 py-1 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              v === "cleanup-task"
                ? "border-destructive/50 text-destructive hover:bg-destructive/10"
                : "border-border text-muted-foreground hover:bg-surface-2 hover:text-foreground",
            )}
          >
            {v}
          </button>
        ))}
      </div>

      {actions.isError ? (
        <ErrorState
          title="The action queue could not be read"
          body="Commands may still be running on the host. Do not re-enqueue until this screen loads again."
        />
      ) : actions.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading queue…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="The queue is empty"
          body="Nothing is running or waiting. Pick a verb above to enqueue the first command."
        />
      ) : (
        <div className="space-y-4 p-4">
          <Group title={`Running · ${running.length}`}>
            {running.map((a) => (
              <ActionRow key={a.id} action={a} now={now} showTail />
            ))}
            {running.length === 0 && <Idle text="Nothing is running." />}
          </Group>
          <Group title={`Waiting · ${waiting.length}`}>
            {waiting.map((a) => (
              <ActionRow key={a.id} action={a} now={now} />
            ))}
            {waiting.length === 0 && <Idle text="Nothing is waiting." />}
          </Group>
          <Group title={`Finished · ${finished.length}`}>
            {finished.map((a) => (
              <ActionRow key={a.id} action={a} now={now} showTail={a.state === "failed"} />
            ))}
          </Group>
        </div>
      )}

      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4">
          <div className="panel w-full max-w-lg p-4">
            <h2 className="text-sm font-semibold">
              {destructive ? "Destructive action" : "Confirm action"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              This runs on the harness host immediately:
            </p>
            <pre className="mono mt-2 overflow-x-auto rounded-sm border border-border bg-surface-2 px-2 py-2 text-[11px]">
              {commandLine(pending.verb, pending.args)}
            </pre>
            {destructive && (
              <div className="mt-3">
                <p className="text-[11px] text-destructive">
                  cleanup-task removes the worktrees and locks for{" "}
                  <Mono>{confirmTarget || "—"}</Mono>. Type the task id to confirm.
                </p>
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder={confirmTarget || "T-000"}
                  className="mono mt-1.5 w-full rounded-sm border border-destructive/50 bg-surface px-2 py-1 text-[11px] outline-none"
                />
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setPending(null);
                  setConfirmText("");
                }}
                className="rounded-sm border border-border px-3 py-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                Cancel
              </button>
              <button
                disabled={!canConfirm}
                onClick={run}
                className={cn(
                  "rounded-sm px-3 py-1 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-40",
                  destructive
                    ? "bg-destructive text-destructive-foreground"
                    : "bg-primary text-primary-foreground",
                )}
              >
                Run
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h2 className="border-b border-border px-3 py-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      <div className="divide-y divide-border/60">{children}</div>
    </section>
  );
}

function Idle({ text }: { text: string }) {
  return <p className="px-3 py-2 text-[11px] text-muted-foreground">{text}</p>;
}

function ActionRow({
  action,
  now,
  showTail = false,
}: {
  action: QueuedAction;
  now: number;
  showTail?: boolean;
}) {
  const Icon = action.enqueued_by === "operator" ? User : Bot;
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Mono className="text-muted-foreground">{action.id}</Mono>
        <span
          className={cn(
            "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
            stateTone[action.state],
          )}
        >
          {action.state}
        </span>
        <Mono>{commandLine(action.verb, action.args)}</Mono>
        <span
          className="ml-auto inline-flex items-center gap-1 text-[10px] text-muted-foreground"
          title={
            action.enqueued_by === "operator" ? "Enqueued by the operator" : "Enqueued by an agent"
          }
        >
          <Icon className="size-3" />
          {action.enqueued_by} · {formatAge(agoSeconds(action.enqueued_at, now))} ago
        </span>
      </div>
      {showTail &&
        (action.output_tail ? (
          <pre className="mono mt-1.5 max-h-40 overflow-auto rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-[10px] text-muted-foreground">
            {action.output_tail}
          </pre>
        ) : (
          <p className="mt-1.5">
            <Absent label="no output yet" />
          </p>
        ))}
    </div>
  );
}
