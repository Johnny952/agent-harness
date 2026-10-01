import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Crown } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  Gauge,
  HeartbeatDot,
  Mono,
  PageHeader,
} from "@/components/console/primitives";
import { accountsQuery } from "@/lib/api/queries";
import {
  COOLDOWN_S,
  PRIMARY_RESERVE,
  USAGE_THRESHOLD,
  type Account,
} from "@/lib/api/types";
import { agoSeconds, formatAge, isStale } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/pool")({
  head: () => ({
    meta: [
      { title: "Pool — harness operations console" },
      {
        name: "description",
        content:
          "Account pool: usage gauges against the 90% local threshold, provider cooldown countdowns and lock heartbeats.",
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

  const ordered = [...(accounts.data ?? [])].sort((a, b) => a.rank - b.rank);

  return (
    <AppShell>
      <PageHeader
        title="Pool"
        subtitle="Parked over the local threshold self-heals; refused by the provider does not."
        right={<RefreshedAt at={accounts.dataUpdatedAt} />}
      />

      {accounts.isError ? (
        <ErrorState
          title="The pool API did not answer"
          body="Account state could not be read. The containers may still be running — check the pool manager on the host before releasing anything."
        />
      ) : accounts.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading pool…</p>
      ) : ordered.length === 0 ? (
        <EmptyState
          title="No accounts registered"
          body="Register at least one agent container with the pool manager before running a task."
        />
      ) : (
        <div className="p-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {ordered.map((a) => (
              <AccountCard key={a.name} account={a} now={now} />
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
                    <span className="label-xs text-warning">primary · reserve {PRIMARY_RESERVE}%</span>
                  )}
                  <span className="ml-auto text-[10px] text-muted-foreground">{a.state}</span>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </AppShell>
  );
}

function AccountCard({ account, now }: { account: Account; now: number }) {
  const refusedAge = agoSeconds(account.rate_limited_at, now);
  const cooldownLeft = refusedAge === null ? null : Math.max(0, COOLDOWN_S - refusedAge);
  const refused = cooldownLeft !== null && cooldownLeft > 0;
  const parked = !refused && account.usage_pct >= USAGE_THRESHOLD;
  const lockLive = account.heartbeat !== null && !isStale(account.heartbeat, now);

  const releaseBlocked = lockLive || refused;
  const releaseReason = lockLive
    ? "The lock is live — this account is mid-phase and releasing it would orphan the worktree."
    : refused
      ? `The provider refused this account; ${formatAge(cooldownLeft)} of cooldown remain.`
      : "Release this account back to the pool";

  const stateTone: Record<Account["state"], string> = {
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
        <span
          className={cn(
            "ml-auto rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
            stateTone[account.state],
          )}
        >
          {account.state}
        </span>
      </div>
      <Mono className="text-[10px] text-muted-foreground">{account.container}</Mono>

      <div className="mt-3">
        <div className="flex items-baseline justify-between text-[10px] text-muted-foreground">
          <span>usage</span>
          <Mono>{account.usage_pct}%</Mono>
        </div>
        <div className="mt-1">
          <Gauge
            value={account.usage_pct}
            tone={parked ? "warning" : account.usage_pct > 75 ? "warning" : "default"}
            markers={[
              { at: USAGE_THRESHOLD, label: `local threshold ${USAGE_THRESHOLD}%` },
              ...(account.is_primary
                ? [{ at: PRIMARY_RESERVE, label: `primary reserve ${PRIMARY_RESERVE}%` }]
                : []),
            ]}
          />
        </div>
      </div>

      {parked && (
        <p className="mt-2 rounded-sm border border-warning/50 bg-warning/10 px-2 py-1 text-[11px] text-warning">
          Parked over the local {USAGE_THRESHOLD}% threshold. This self-heals — a re-probe runs
          every 60s, no action needed.
        </p>
      )}
      {refused && (
        <p className="mt-2 rounded-sm border border-destructive/60 bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
          Refused by the provider. Back in <Mono>{formatAge(cooldownLeft)}</Mono> (1800s cooldown).
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
          <HeartbeatDot heartbeat={account.heartbeat} now={now} withLabel />
        </div>
      </div>

      <button
        disabled={releaseBlocked}
        title={releaseReason}
        className="mt-3 w-full rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
      >
        Release
      </button>
    </article>
  );
}
