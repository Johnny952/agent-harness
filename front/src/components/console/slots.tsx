import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Absent, Mono } from "@/components/console/primitives";
import { accountsQuery, actionQueueQuery, tasksQuery } from "@/lib/api/queries";
import { deriveSlots, resetText, waitingText, type Slot } from "@/lib/slots";

function SlotLine({ slot, tasksKnown }: { slot: Slot; tasksKnown: boolean }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
      <Mono>{slot.name}</Mono>
      {slot.primary && (
        <span
          className="label-xs text-warning"
          title="A phase here spends the conversation's quota"
        >
          primary · {slot.reserve}% reserve
        </span>
      )}
      {!slot.open ? (
        <span className="text-destructive">
          closed — {slot.closedBy}
          {slot.reset ? (
            <>
              , resets <Mono>{resetText(slot.reset)}</Mono>
            </>
          ) : (
            <>
              , <Absent label="no reset recorded" />
            </>
          )}
        </span>
      ) : slot.empty ? (
        <span className="text-muted-foreground">empty</span>
      ) : (
        <span className="flex flex-wrap items-baseline gap-x-2">
          {slot.taskId ? <Mono>{slot.taskId}</Mono> : <Absent label="task not recorded" />}
          {slot.phase ? (
            <Mono>{slot.phase}</Mono>
          ) : (
            <Absent label={tasksKnown ? "phase not served" : "phase not known"} />
          )}
          {slot.project ? <Mono>{slot.project}</Mono> : <Absent label="project not recorded" />}
        </span>
      )}
    </li>
  );
}

/**
 * The slots under the dock's selector: one line per account, then the started
 * tasks that are not in a phase, each with its reason. ADR 198, 5.
 */
export function SlotsPanel() {
  const accounts = useQuery(accountsQuery);
  const tasks = useQuery(tasksQuery);
  const queue = useQuery(actionQueueQuery);
  const accountList = accounts.data?.data;
  const taskList = tasks.isError ? null : (tasks.data?.data ?? null);
  const queueData: unknown = queue.isError ? null : queue.data?.data;
  const view = useMemo(
    () => (accountList ? deriveSlots(accountList, taskList, queueData) : null),
    [accountList, taskList, queueData],
  );

  let body;
  if (accounts.isError) {
    body = (
      <p className="text-[11px] italic text-destructive/80">
        /api/accounts did not answer, so the slots are not known
      </p>
    );
  } else if (!view) {
    body = <p className="text-[11px] text-muted-foreground">Reading the slots…</p>;
  } else if (view.slots.length === 0) {
    body = <p className="text-[11px] text-muted-foreground">No account is configured</p>;
  } else {
    body = (
      <>
        <ul className="space-y-1">
          {view.slots.map((s) => (
            <SlotLine key={s.name} slot={s} tasksKnown={view.tasksKnown} />
          ))}
        </ul>
        {view.waiting === null ? (
          <p className="mt-2 text-[11px] italic text-destructive/80">
            {queue.isError || queue.data
              ? "/api/actions did not answer, so what waits is not known"
              : "Reading what waits…"}
          </p>
        ) : (
          view.waiting.length > 0 && (
            <ul className="mt-2 space-y-1 border-t border-border pt-2">
              {view.waiting.map((w) => (
                <li key={w.taskId} className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
                  <Mono>{w.taskId}</Mono>
                  <span className="text-muted-foreground">{waitingText(w)}</span>
                </li>
              ))}
            </ul>
          )
        )}
      </>
    );
  }

  return (
    <section aria-label="Slots" className="border-b border-border px-3 py-2">
      <h2 className="label-xs mb-1">
        slots{view ? ` — ${view.free} free of ${view.slots.length}` : ""}
      </h2>
      {body}
    </section>
  );
}
