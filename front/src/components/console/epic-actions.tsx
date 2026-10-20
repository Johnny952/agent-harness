/**
 * *Start epic* and *Stop epic* on an epic's page (`docs/plans/board-service.md`
 * *Per epic*; ADR 207, ADR 211). Two buttons over the board service's two epic
 * writes, each behind a confirmation that says what the press would do from what
 * the api serves: the epic's children in Ready (`/api/ready`, the dispatcher's own
 * order) and the epic's starts in the queue (`/api/actions`, `params.epic`).
 *
 * *Start epic* queues one start per child in Ready, with the note, the way
 * *Start development* queues one; the children not in Ready are named, not
 * queued. Where the first start would stand is `lib/slots.ts`'s line, the same
 * the single start shows (ADR 198, ADR 204), and never a time.
 *
 * The press starts the epic's run (ADR NNN): each merge then queues the epic's
 * next tasks as their dependencies land, until the spending limit, in measured
 * tokens, is reached. The run is `/api/actions`' `epics`; at its limit the epic
 * shows *Paused: limit reached* and the button is *Resume epic*, which asks a
 * new limit. A limit prefilled from past cycles is not built: the field is blank.
 *
 * *Stop epic* drops the epic's starts still waiting and leaves a running one
 * alone; its answer names both, and the toast counts them.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Mono } from "@/components/console/primitives";
import {
  accountsQuery,
  actionQueueQuery,
  readyQuery,
  startEpicMutation,
  stopEpicMutation,
  tasksQuery,
} from "@/lib/api/queries";
import type { ActionRecord, EpicRun, Task } from "@/lib/api/types";
import { commentProblem } from "@/lib/comments";
import { deriveSlots, standingLine } from "@/lib/slots";

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** The queue's starts *Start epic* queued for `epicId`: `params.epic` names it. */
function epicStarts(list: unknown, epicId: string): ActionRecord[] {
  if (!Array.isArray(list)) return [];
  return (list as ActionRecord[]).filter(
    (r) =>
      typeof r === "object" &&
      r !== null &&
      r.kind === "start" &&
      typeof r.params === "object" &&
      r.params !== null &&
      r.params["epic"] === epicId,
  );
}

/** The epic's run still going (not stopped), from `/api/actions`' `epics`. */
function goingRun(list: unknown, epicId: string): EpicRun | null {
  if (!Array.isArray(list)) return null;
  const run = (list as EpicRun[]).find(
    (r) => typeof r === "object" && r !== null && r.epic === epicId && r.stopped_at === null,
  );
  return run ?? null;
}

/** The limit field's text as the route takes it: blank is null, else a whole number of at least 1. */
function readLimit(
  text: string,
  spent: number | null,
): { ok: true; value: number | null } | { ok: false; why: string } {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^\d+$/.test(t) || Number(t) < 1) {
    return { ok: false, why: `${t} is not a whole number of at least 1.` };
  }
  const value = Number(t);
  if (spent !== null && value <= spent) {
    return { ok: false, why: `The run has already spent ${spent} measured tokens.` };
  }
  return { ok: true, value };
}

function IdList({ ids }: { ids: string[] }) {
  return (
    <>
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Mono>{id}</Mono>
        </span>
      ))}
    </>
  );
}

/** `ids` is the epic's split, in its order. */
export function EpicActions({ epic, ids }: { epic: Task; ids: string[] }) {
  const qc = useQueryClient();
  const ready = useQuery(readyQuery);
  const accounts = useQuery(accountsQuery);
  const tasks = useQuery(tasksQuery);
  const actionQueue = useQuery(actionQueueQuery);
  const start = useMutation(startEpicMutation(qc));
  const stop = useMutation(stopEpicMutation(qc));
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [note, setNote] = useState("");
  const [limitText, setLimitText] = useState("");
  // A note is optional, so blank is not the comment rule's "needs some text".
  const problem = note.trim() ? commentProblem(note) : null;
  const epicId = epic.task_id;

  // The children in Ready, in the dispatcher's order, as the route queues them.
  const order = ready.isError ? null : (ready.data?.data ?? null);
  const inReady = order === null ? null : order.filter((id) => ids.includes(id));
  const notReady = inReady === null ? null : ids.filter((id) => !inReady.includes(id));

  const queue = actionQueue.isError ? null : (actionQueue.data?.data ?? null);
  const waiting = queue === null ? null : epicStarts(queue.pending, epicId);
  const running = queue === null ? null : epicStarts(queue.claimed, epicId);
  const run = queue === null ? null : goingRun(queue.epics, epicId);
  const paused = run?.limit_reached === true;
  const limit = readLimit(limitText, paused && run ? run.spent.tokens : null);

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
  const standing = standingLine(slotsView, typeof epic.project === "string" ? epic.project : null);

  const confirmStart = () =>
    start.mutate(
      { epicId, note: note.trim() ? note : null, limit: limit.ok ? limit.value : null },
      {
        onSuccess: (out) => {
          const left = out.not_ready.length;
          toast.success(
            `${plural(out.queued.length, "start")} of ${epicId} queued` +
              (left > 0 ? `; ${plural(left, "task")} not in Ready left out` : "") +
              (typeof out.limit === "number" ? `; limit ${out.limit} measured tokens` : ""),
          );
          setStarting(false);
          setNote("");
          setLimitText("");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : `${epicId} was not started`),
      },
    );

  const confirmStop = () =>
    stop.mutate(epicId, {
      onSuccess: (out) => {
        toast.success(
          `${plural(out.dropped.length, "waiting start")} of ${epicId} dropped; ` +
            `${plural(out.left.length, "running start")} left alone`,
        );
        setStopping(false);
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : `${epicId} was not stopped`),
    });

  // Stop is offered once the queue holds a start of this epic; with the queue
  // unread it is offered too, and the route answers what there was.
  // A run still going is stoppable with nothing queued: between two tasks.
  const stoppable =
    queue === null || run !== null || (waiting?.length ?? 0) + (running?.length ?? 0) > 0;

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="epic-actions">
      <Button size="sm" variant="outline" type="button" onClick={() => setStarting(true)}>
        {paused ? "Resume epic" : "Start epic"}
      </Button>
      {stoppable && (
        <Button size="sm" variant="outline" type="button" onClick={() => setStopping(true)}>
          Stop epic
        </Button>
      )}
      {waiting !== null && running !== null && waiting.length + running.length > 0 && (
        <span className="text-[11px] text-muted-foreground" data-testid="epic-queued">
          {plural(waiting.length, "start")} waiting, {running.length} running
        </span>
      )}
      {run !== null && (paused || run.limit !== null) && (
        <span
          className={`text-[11px] ${paused ? "text-destructive" : "text-muted-foreground"}`}
          data-testid="epic-run"
        >
          {paused ? "Paused: limit reached · " : ""}
          {run.spent.tokens} of {run.limit ?? "no limit"} measured tokens spent
          {run.spent.unmeasured > 0 ? ` (${plural(run.spent.unmeasured, "phase")} unmeasured)` : ""}
        </span>
      )}

      <Dialog open={starting} onOpenChange={setStarting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {paused ? "Resume epic" : "Start epic"} {epicId}
            </DialogTitle>
            <DialogDescription>
              Confirming queues one start per task of the epic in Ready, each for the worker to run
              when an account and the project are free.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1 text-xs" data-testid="epic-start-summary">
            {inReady === null || notReady === null ? (
              <p className="text-muted-foreground">
                {ready.isError
                  ? "/api/ready did not answer, so which tasks would be queued is not known"
                  : "Reading the ready order…"}
              </p>
            ) : (
              <>
                <p>
                  {inReady.length === 0 ? (
                    "No task of this epic is in Ready; the board service would refuse the start."
                  ) : (
                    <>
                      {plural(inReady.length, "start")} would be queued: <IdList ids={inReady} />
                    </>
                  )}
                </p>
                {notReady.length > 0 && (
                  <p className="text-muted-foreground">
                    Not in Ready, not queued: <IdList ids={notReady} />
                  </p>
                )}
              </>
            )}
          </div>
          <p className="text-xs text-muted-foreground" data-testid="standing-line">
            For the first of them: {standing}
          </p>
          <label className="space-y-1 text-xs">
            Note (optional, copied to every start)
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {problem && <p className="text-xs text-destructive">{problem}</p>}
          <label className="space-y-1 text-xs" data-testid="epic-limit">
            {paused && run
              ? `New spending limit (measured tokens; ${run.spent.tokens} spent of ${run.limit})`
              : "Spending limit (measured tokens, optional)"}
            <Input
              aria-label="Spending limit"
              type="number"
              step={1}
              min={1}
              className="h-7 w-40 text-xs"
              value={limitText}
              onChange={(e) => setLimitText(e.target.value)}
            />
          </label>
          {!limit.ok && <p className="text-xs text-destructive">{limit.why}</p>}
          {paused && limit.ok && limit.value === null && (
            <p className="text-xs text-muted-foreground">
              No limit given: the run resumes with none.
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => setStarting(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={
                problem !== null ||
                !limit.ok ||
                start.isPending ||
                (!paused && inReady?.length === 0)
              }
              onClick={confirmStart}
            >
              {paused ? "Resume" : "Start"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={stopping} onOpenChange={setStopping}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Stop epic {epicId}</DialogTitle>
            <DialogDescription>
              Confirming drops the epic's starts still waiting. A start the worker already took is a
              running phase and is left alone.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1 text-xs" data-testid="epic-stop-summary">
            {waiting === null || running === null ? (
              <p className="text-muted-foreground">
                {actionQueue.isError
                  ? "/api/actions did not answer, so which starts would be dropped is not known"
                  : "Reading the queue…"}
              </p>
            ) : (
              <>
                <p>
                  {waiting.length === 0 ? (
                    "No start of this epic is waiting."
                  ) : (
                    <>
                      {plural(waiting.length, "waiting start")} would be dropped:{" "}
                      <IdList ids={waiting.map((r) => r.task_id ?? r.id)} />
                    </>
                  )}
                </p>
                {running.length > 0 && (
                  <p className="text-muted-foreground">
                    Running, left alone: <IdList ids={running.map((r) => r.task_id ?? r.id)} />
                  </p>
                )}
              </>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => setStopping(false)}>
              Cancel
            </Button>
            <Button type="button" disabled={stop.isPending} onClick={confirmStop}>
              Stop
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
