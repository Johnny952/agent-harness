import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Crown } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  Banner,
  BrokenBanner,
  EmptyState,
  ErrorState,
  HeartbeatDot,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { accountsQuery, tasksQuery } from "@/lib/api/queries";
import type { Account, AccountState, Task } from "@/lib/api/types";
import { agoSeconds, formatAge } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/pool")({
  head: () => ({
    meta: [
      { title: "Pool — harness operations console" },
      {
        name: "description",
        content:
          "Account pool: the four states the harness records, each account's own quota thresholds, provider cooldown countdowns and the lock on the task it is running.",
      },
      { property: "og:title", content: "Pool — harness operations console" },
      {
        property: "og:description",
        content: "Which agent accounts are available, parked or refused by the provider.",
      },
    ],
  }),
  component: PoolPage,
});

function PoolPage() {
  const now = useNow(1000);
  const accounts = useQuery(accountsQuery);
  // An account has no heartbeat of its own: the lock it holds is the lock on the
  // task it is running, which is a join across two routes the console already
  // calls. `docs/decisions.md` ADR 17, `docs/ui.md` *Staleness is served*.
  const tasks = useQuery(tasksQuery);

  // `is_primary` as the sort key, which is what it is — workers first, the primary
  // last, then by name so the order is stable. `rank` is gone: nothing in
  // `dispatcher/config.py` ranks accounts, the pool is ordered rather than
  // partitioned, and the primary is simply the one the picker reaches last. ADR 17.
  const ordered = [...(accounts.data?.data ?? [])].sort(
    (a, b) => Number(a.is_primary) - Number(b.is_primary) || a.name.localeCompare(b.name),
  );

  const byTaskId = new Map<string, Task>(
    (tasks.data?.data ?? []).map((t) => [t.task_id, t]),
  );
  // A failed read carries no `warnings`, so the banner below stays silent for it:
  // broken and partial are different states and they are reported separately.
  // The join failing does not take the screen — the pool itself still answered.
  // `docs/ui.md` *Absent, empty and broken are three different things*.
  const brokenReads = tasks.isError
    ? [
        "/api/tasks did not answer, so no card can show the heartbeat of the lock it " +
          "holds: that join is the task index. The states, thresholds and cooldowns " +
          "below come from /api/accounts and are unaffected.",
      ]
    : [];
  const warnings = [...(accounts.data?.warnings ?? []), ...(tasks.data?.warnings ?? [])];

  return (
    <AppShell>
      <PageHeader
        title="Pool"
        subtitle="Parked over the account's own threshold self-heals; refused by the provider does not."
        right={<RefreshedAt at={accounts.dataUpdatedAt} />}
      />

      {/* Releasing an account is a write, and the control goes rather than being
          rendered disabled over a route nothing serves: `docs/ui.md` *A region with
          no route says which route, and when*. The capability is not lost, only the
          button: `release-account` already refuses on exactly the two conditions the
          tooltip described, so nothing an operator could do here goes away with it. */}
      <Banner tone="info">
        Releasing an account back to the pool is a write and this console only reads, so the
        control is not here rather than here and inert. It waits on the write surface — tier 3
        of <Mono>docs/plans/front.md</Mono>. Until then it is{" "}
        <Mono>dispatcher release-account --name &lt;account&gt;</Mono> on the host, which
        refuses while the lock is live or a refusal is still inside its cooldown.
      </Banner>

      <BrokenBanner reads={brokenReads} />

      <WarningBanner warnings={warnings} />

      {accounts.isError ? (
        <ErrorState
          title="The pool API did not answer"
          body="/api/accounts could not be read. The containers may still be running — check observability/api, and this console's API_TOKEN, before releasing anything."
        />
      ) : accounts.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading pool…</p>
      ) : ordered.length === 0 ? (
        <EmptyState
          title="No accounts configured"
          body="The api read the pool and it is empty: config.yaml declares no accounts. Add at least one to its accounts block before running a task."
        />
      ) : (
        <div className="p-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {ordered.map((a) => (
              <AccountCard
                key={a.name}
                account={a}
                lockedTask={a.current_task_id ? (byTaskId.get(a.current_task_id) ?? null) : null}
                lockJoinBroken={tasks.isError}
                now={now}
              />
            ))}
          </div>

          <section className="panel mt-4 px-3 py-3">
            <h2 className="label-xs mb-2">pool priority — workers first, primary last</h2>
            <ol className="space-y-1">
              {ordered.map((a, i) => (
                <li key={a.name} className="flex items-center gap-2 text-[11px]">
                  <Mono className="w-5 text-muted-foreground">{i + 1}</Mono>
                  <Mono>{a.name}</Mono>
                  {a.is_primary && (
                    <span className="label-xs text-warning">
                      primary · reserve {a.reserve_pct}%
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {a.state ?? <Absent label="state unreadable" />}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </AppShell>
  );
}

function AccountCard({
  account,
  lockedTask,
  lockJoinBroken,
  now,
}: {
  account: Account;
  lockedTask: Task | null;
  lockJoinBroken: boolean;
  now: number;
}) {
  const refusedAge = agoSeconds(account.rate_limited_at, now);
  const cooldownLeft =
    refusedAge === null ? null : Math.max(0, account.quota_cooldown_seconds - refusedAge);
  const refused = cooldownLeft !== null && cooldownLeft > 0;
  // `PRE_COOLDOWN` is the harness's own word for parked over the local threshold:
  // `dispatcher/dispatcher.py` sets it when a `/usage` probe crosses
  // `_threshold_for`. The console reads the state rather than re-deriving it from
  // a usage number it is not served — ADR 18.
  const parked = account.state === "PRE_COOLDOWN";

  const stateTone: Record<AccountState, string> = {
    IDLE: "text-muted-foreground border-border-strong",
    BUSY: "text-info border-info/50 bg-info/10",
    PRE_COOLDOWN: "text-warning border-warning/50 bg-warning/10",
    COOLING_DOWN: "text-destructive border-destructive/60 bg-destructive/10",
  };

  return (
    <article className="panel px-3 py-3">
      <div className="flex items-center gap-2">
        <Mono className="text-[12px] font-semibold">{account.name}</Mono>
        {account.is_primary && (
          <span className="inline-flex items-center gap-1 rounded-sm border border-warning/50 bg-warning/10 px-1 text-[10px] uppercase text-warning">
            <Crown className="size-2.5" /> primary
          </span>
        )}
        {/* An unreadable state file nulls what the state file says and keeps the
            row, with a warning already in the banner above. Absent, not an
            unstyled pill: `docs/ui.md` *Absent, empty and broken*. */}
        <span className="ml-auto">
          {account.state === null ? (
            <Absent label="state unreadable" />
          ) : (
            <span
              className={cn(
                "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
                stateTone[account.state],
              )}
            >
              {account.state}
            </span>
          )}
        </span>
      </div>
      <Mono className="text-[10px] text-muted-foreground">{account.container}</Mono>

      {/* The gauge is gone with its number, and is not repaired with a fixture.
          Nothing persists `usage_pct`: the harness learns an account's usage from a
          `/usage` probe at dispatch time and `state_machine` keeps the outcome — a
          state, a current task, a stamp — never the measurement. ADR 18 calls this
          a deliberate visible regression, because that gauge was reading a mock and
          the same gauge reading nothing is the same picture with none of the
          meaning. The thresholds below are this account's own, served per row
          (ADR 20), and they are what the number would have been read against. */}
      <div className="mt-3 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>usage</span>
        <Absent label="not recorded — nothing persists the probe" />
      </div>
      <div className="mt-1 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>its ceiling</span>
        <Mono>
          {account.is_primary
            ? `${account.reserve_pct}% reserve`
            : `${account.quota_threshold_pct}% threshold`}
        </Mono>
      </div>

      {parked && (
        <p className="mt-2 rounded-sm border border-warning/50 bg-warning/10 px-2 py-1 text-[11px] text-warning">
          Parked over the local {account.is_primary ? account.reserve_pct : account.quota_threshold_pct}%
          threshold. This self-heals — a re-probe runs every 60s, no action needed.
        </p>
      )}
      {refused && (
        <p className="mt-2 rounded-sm border border-destructive/60 bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
          Refused by the provider. Back in <Mono>{formatAge(cooldownLeft)}</Mono> (
          {account.quota_cooldown_seconds}s cooldown).
        </p>
      )}

      <div className="mt-3 flex items-center justify-between text-[11px]">
        <div>
          <p className="label-xs">current task</p>
          {account.current_task_id ? (
            <Mono>{account.current_task_id}</Mono>
          ) : (
            <Absent label="idle" />
          )}
        </div>
        <div>
          <p className="label-xs">lock heartbeat</p>
          {lockedTask ? (
            <HeartbeatDot
              heartbeat={lockedTask.heartbeat}
              lockExpired={lockedTask.lock_expired}
              now={now}
              withLabel
            />
          ) : lockJoinBroken ? (
            // Not `Absent`: the task index refused, so whether this account holds a
            // live lock is unknown, and "task not in the index" would be a fact the
            // console does not have. The banner above names the read.
            <span className="text-[11px] italic text-destructive/80">read failed</span>
          ) : account.current_task_id ? (
            <Absent label="task not in the index" />
          ) : (
            <Absent label="no current task" />
          )}
        </div>
      </div>
    </article>
  );
}
