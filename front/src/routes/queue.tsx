import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  Banner,
  EmptyState,
  ErrorState,
  Malformed,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { actionQueueListQuery, cancelActionMutation } from "@/lib/api/queries";
import type { ActionQueue, ActionRecord } from "@/lib/api/types";
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
          "The action queue: pending actions in the order the worker takes them, the running ones, and the newest finished, with Cancel on a queued or running start.",
      },
      { property: "og:title", content: "Queue — harness operations console" },
      { property: "og:description", content: "Watch and cancel the worker's queued actions." },
    ],
  }),
  component: QueuePage,
});

type Stage = "pending" | "claimed" | "done";

const statusTone: Record<string, string> = {
  done: "text-success border-success/50 bg-success/10",
  failed: "text-destructive border-destructive/60 bg-destructive/10",
  interrupted: "text-warning border-warning/50 bg-warning/10",
};
const mutedTone = "text-muted-foreground border-border-strong bg-surface-2";

/** The Board's wording for a held-back pending record (ADR 160 part 3). */
function waitingText(waiting: string): string {
  if (waiting === "account") return "waiting for an account";
  if (waiting === "project") return "waiting for the project";
  return `waiting: ${waiting}`;
}

function QueuePage() {
  const now = useNow();
  const queue = useQuery(actionQueueListQuery);
  const qc = useQueryClient();
  const cancel = useMutation(cancelActionMutation(qc));
  const [target, setTarget] = useState<ActionRecord | null>(null);

  const q: ActionQueue | undefined = queue.data?.data;
  const done = q && Array.isArray(q.done) ? q.done : null;
  const empty =
    q !== undefined &&
    Array.isArray(q.pending) &&
    q.pending.length === 0 &&
    Array.isArray(q.claimed) &&
    q.claimed.length === 0 &&
    done !== null &&
    done.length === 0 &&
    q.done_total === 0;

  const confirm = () => {
    if (!target) return;
    const id = target.id;
    cancel.mutate(id, {
      onSuccess: () => toast.success(`Cancel of ${id} queued`),
      onError: (e) => toast.error(e instanceof Error ? e.message : `${id} was not cancelled`),
      onSettled: () => setTarget(null),
    });
  };

  return (
    <AppShell>
      <PageHeader
        title="Queue"
        subtitle="What the worker will take, what it is running, and what it finished."
        right={<RefreshedAt at={queue.dataUpdatedAt} />}
      />

      <WarningBanner warnings={queue.data?.warnings ?? []} />
      {q && <WorkerLine worker={q.worker} now={now} />}

      {queue.isError ? (
        <ErrorState
          title="The action queue could not be read"
          body="GET /api/actions did not answer. Actions may still be running on the host."
        />
      ) : queue.isLoading || !q ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading queue…</p>
      ) : empty ? (
        <EmptyState
          title="Nothing has been queued"
          body="A Ready card's Start development on the Board queues the first one."
        />
      ) : (
        <div className="space-y-4 p-4">
          <Group title={`Pending · ${Array.isArray(q.pending) ? q.pending.length : 0}`}>
            <Rows
              label="pending"
              rows={q.pending}
              stage="pending"
              now={now}
              onCancel={setTarget}
              none="Nothing is waiting."
            />
          </Group>
          <Group title={`Running · ${Array.isArray(q.claimed) ? q.claimed.length : 0}`}>
            <Rows
              label="claimed"
              rows={q.claimed}
              stage="claimed"
              now={now}
              onCancel={setTarget}
              none="Nothing is running."
            />
          </Group>
          <Group
            title={
              done === null
                ? "Finished"
                : q.done_total > done.length
                  ? `Finished · ${done.length} of ${q.done_total}`
                  : `Finished · ${done.length}`
            }
          >
            <Rows
              label="done"
              rows={q.done}
              stage="done"
              now={now}
              onCancel={setTarget}
              none="Nothing has finished."
            />
          </Group>
        </div>
      )}

      <Dialog open={target !== null} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel {target?.task_id ?? target?.id}?</DialogTitle>
            <DialogDescription>
              Action <span className="mono">{target?.id}</span>
              {target?.task_id ? ` for ${target.task_id}` : ""}. Confirming queues a cancel; the
              worker carries it out.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => setTarget(null)}>
              Keep it
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={cancel.isPending}
              onClick={confirm}
            >
              Cancel action
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

/** The worker's heartbeat as the api judged it; never recomputed here. */
function WorkerLine({ worker, now }: { worker: ActionQueue["worker"]; now: number }) {
  if (!worker) return null;
  if (worker.heartbeat === null) {
    return (
      <Banner tone="warning">
        No worker has written a heartbeat, so nothing is being taken from the queue.
      </Banner>
    );
  }
  if (!worker.stale) return null;
  const beat = worker.heartbeat["beat_at"];
  const age = typeof beat === "string" ? agoSeconds(beat, now) : null;
  return (
    <Banner tone="warning">
      The worker's heartbeat is older than {worker.stale_after_seconds} seconds
      {age !== null ? ` (last beat ${formatAge(age)} ago)` : ""}, so nothing is being taken from the
      queue.
    </Banner>
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

function Rows({
  label,
  rows,
  stage,
  now,
  onCancel,
  none,
}: {
  label: string;
  rows: unknown;
  stage: Stage;
  now: number;
  onCancel: (a: ActionRecord) => void;
  none: string;
}) {
  if (!Array.isArray(rows)) {
    return (
      <p className="px-3 py-2">
        <Malformed label={label} got={rows} want="a list" />
      </p>
    );
  }
  if (rows.length === 0) {
    return <p className="px-3 py-2 text-[11px] text-muted-foreground">{none}</p>;
  }
  return (
    <>
      {(rows as ActionRecord[]).map((a) => (
        <ActionRow key={a.id} action={a} stage={stage} now={now} onCancel={onCancel} />
      ))}
    </>
  );
}

function ActionRow({
  action,
  stage,
  now,
  onCancel,
}: {
  action: ActionRecord;
  stage: Stage;
  now: number;
  onCancel: (a: ActionRecord) => void;
}) {
  const [since, stamp] =
    stage === "pending"
      ? [action.created_at, "queued"]
      : stage === "claimed"
        ? [action.claimed_at, "claimed"]
        : [action.finished_at, "finished"];
  const age = agoSeconds(since, now);
  const param = action.params?.["target"];
  const target = typeof param === "string" ? param : null;
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Mono className="font-medium">{action.kind}</Mono>
        {action.task_id ? <Mono>{action.task_id}</Mono> : <Absent label="no task" />}
        {action.project ? (
          <Mono className="text-muted-foreground">{action.project}</Mono>
        ) : (
          <Absent label="no project" />
        )}
        {target && <Mono className="text-muted-foreground">→ {target}</Mono>}
        {stage === "done" && (
          <span
            className={cn(
              "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
              statusTone[action.status] ?? mutedTone,
            )}
          >
            {action.status}
          </span>
        )}
        {stage === "pending" && action.waiting && (
          <span className="text-[10px] text-warning">{waitingText(action.waiting)}</span>
        )}
        <span className="text-muted-foreground">{action.actor}</span>
        {action.note ? (
          <span className="text-muted-foreground">{action.note}</span>
        ) : (
          <Absent label="no note" />
        )}
        <span className="ml-auto text-[10px] text-muted-foreground">
          {age === null ? <Absent /> : `${stamp} ${formatAge(age)} ago`}
        </span>
        {stage !== "done" && action.kind === "start" && (
          <Button
            variant="outline"
            size="sm"
            type="button"
            aria-label={`Cancel ${action.id}`}
            onClick={() => onCancel(action)}
          >
            Cancel
          </Button>
        )}
      </div>
      {stage === "done" && action.error && (
        <p className="mt-1.5 text-[11px] text-destructive">{action.error}</p>
      )}
    </div>
  );
}
