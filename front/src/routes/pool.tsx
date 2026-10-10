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
import type { Account, AccountState, LastProbe, Task } from "@/lib/api/types";
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

  const byTaskId = new Map<string, Task>((tasks.data?.data ?? []).map((t) => [t.task_id, t]));
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
        Releasing an account back to the pool is a write and this console only reads, so the control
        is not here rather than here and inert. It waits on the write surface — tier 3 of{" "}
        <Mono>docs/plans/front.md</Mono>. Until then it is{" "}
        <Mono>dispatcher release-account --name &lt;account&gt;</Mono> on the host, which refuses
        while the lock is live or a refusal is still inside its cooldown.
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
                  {/* The tag only: the primary answers to two ceilings since ADR 48,
                      and one number here would have to pick a window to lie about.
                      Both are on its card, read from its last probe. */}
                  {a.is_primary && <span className="label-xs text-warning">primary</span>}
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

/**
 * How old a probe may be before its card says so, in seconds.
 *
 * `docs/ui.md` *Staleness is served, never computed* is about the lock, whose
 * expiry the api judges against its own config and answers as `lock_expired`.
 * A probe has no such judgement to serve: nothing in the harness expires one, and
 * ADR 49 leaves it to the consumer — "a consumer reads `probed_at` before it reads
 * the numbers" — because an account nothing dispatches is never re-probed and its
 * record simply ages. So the mark is the console's, and it marks rather than
 * hides: the numbers stay, beside their age.
 *
 * Thirty minutes is a reading, not a measurement. A parked account is re-probed
 * every 60s and a busy one was probed when its phase was dispatched, so a record
 * older than half an hour belongs to an account that is idle or refused, and a
 * five-hour session can have moved by more than any margin the ceilings keep.
 */
export const PROBE_STALE_S = 30 * 60;

/** A percentage to one decimal, without a trailing `.0` on a whole number. */
function pct(n: number): string {
  return `${Math.round(n * 10) / 10}%`;
}

/**
 * The windows this probe found at or over their ceilings, by the dispatcher's own
 * comparison — `>=`, in `dispatcher/dispatcher.py:_quota_decision` — read off the
 * served record rather than re-decided: both sides of each test are on it.
 */
function overWindows(probe: LastProbe): string[] {
  const over: string[] = [];
  if (probe.session_pct >= probe.session_ceiling_pct) {
    over.push(`the session's ${pct(probe.session_pct)} over its ${pct(probe.session_ceiling_pct)}`);
  }
  if (probe.week_pct >= probe.week_ceiling.pct) {
    over.push(`the week's ${pct(probe.week_pct)} over its ${pct(probe.week_ceiling.pct)}`);
  }
  return over;
}

/**
 * The last probe, each window against the ceiling it was held to, age first.
 *
 * Since ADR 48 the primary's two windows answer to two different numbers —
 * `reserve_pct` on the session, a paced ceiling on the week — so they are two
 * rows, never one ceiling. Both come from the record the gate wrote (ADR 49) and
 * not from configuration: the paced value is a property of that one probe and the
 * reset it read, and `reserve_pct` beside `is_primary` cannot say which applied.
 * A worker renders through the same rows, its one threshold on both.
 */
function ProbeReadout({ probe, now }: { probe: LastProbe; now: number }) {
  const age = Math.max(0, Math.round(now / 1000 - probe.probed_at));
  const stale = age > PROBE_STALE_S;
  const week = probe.week_ceiling;
  const probedAt = new Date(probe.probed_at * 1000).toISOString();
  return (
    <>
      <div className="mt-3 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>last probe</span>
        <Mono className={cn(stale && "text-warning")}>
          <span title={`Probed at ${probedAt}`}>
            {stale ? `stale · ${formatAge(age)} ago` : `${formatAge(age)} ago`}
          </span>
        </Mono>
      </div>
      {stale && (
        <p className="mt-1 text-[10px] italic text-warning/80">
          Older than {PROBE_STALE_S / 60} minutes: an account nothing dispatches is not re-probed,
          so the numbers below may have moved.
        </p>
      )}
      <div className="mt-1 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>session</span>
        <Mono className={cn(probe.session_pct >= probe.session_ceiling_pct && "text-warning")}>
          {pct(probe.session_pct)} of {pct(probe.session_ceiling_pct)}
        </Mono>
      </div>
      <div className="mt-1 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>week</span>
        <Mono className={cn(probe.week_pct >= week.pct && "text-warning")}>
          {pct(probe.week_pct)} of {pct(week.pct)}
        </Mono>
      </div>
      {/* A paced week that fell back keeps `paced: true` (ADR 49), so the reason is
          the test, not the flag: with one, the ceiling above is the reserve, and the
          reset clause that would not parse is the whole explanation. */}
      {week.fallback_reason !== null ? (
        <p
          className="mt-1 text-[10px] italic text-muted-foreground/80"
          title={`week line's reset clause: ${probe.week_reset ?? "none"}`}
        >
          The week fell back to the reserve, because {week.fallback_reason}.
        </p>
      ) : week.paced && week.days_left !== null ? (
        <p
          className="mt-1 text-right text-[10px] text-muted-foreground/80"
          title={week.reset ? `paced against the reset at ${week.reset}` : undefined}
        >
          paced week · {week.days_left.toFixed(1)} days to the reset
        </p>
      ) : null}
    </>
  );
}

/**
 * No record to read: the account was never probed, or its state file would not
 * parse (ADR 49). What is left is configuration, and it is worded as that — the
 * ceilings this account is configured with, not what it is held to. For the
 * primary that is one number for the session only: its week's ceiling is set
 * per probe (ADR 48), and `reserve_pct` is what it falls back to, not what it is.
 */
function ConfiguredCeilings({ account }: { account: Account }) {
  return (
    <>
      <div className="mt-3 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>last probe</span>
        <Absent label="none recorded" />
      </div>
      <div className="mt-1 flex items-baseline justify-between text-[10px] text-muted-foreground">
        <span>configured</span>
        <Mono>
          {account.is_primary
            ? `session ${account.reserve_pct}% reserve`
            : `${account.quota_threshold_pct}% threshold, both windows`}
        </Mono>
      </div>
      {account.is_primary && (
        <p className="mt-1 text-[10px] italic text-muted-foreground/80">
          The week&apos;s ceiling is set at each probe, paced to its reset, and falls back to the
          reserve only when the reset will not parse.
        </p>
      )}
    </>
  );
}

export function AccountCard({
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
  // `PRE_COOLDOWN` is the harness's own word for parked over the account's own
  // ceiling: `dispatcher/dispatcher.py:_quota_decision` decides it, against
  // `reserve_pct` on the primary's session and a paced ceiling on its week since
  // ADR 48, so neither `_threshold_for` nor any one configured number is the
  // ceiling the primary parked on. The console reads the state rather than
  // re-deciding it; `last_probe` (ADR 49) is only what it can say about why.
  const parked = account.state === "PRE_COOLDOWN";
  const probe = account.last_probe;
  const over = probe ? overWindows(probe) : [];

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

      {/* The numbers are the last probe's and not a live gauge: the harness learns
          an account's usage at dispatch time, and ADR 49 keeps that one probe, with
          its time, in the account's state file. ADR 18 held the gauge back until a
          number had a stamp to be read with; it has one now, so the age is drawn
          first and the numbers under it. */}
      {probe ? <ProbeReadout probe={probe} now={now} /> : <ConfiguredCeilings account={account} />}

      {parked && (
        <p className="mt-2 rounded-sm border border-warning/50 bg-warning/10 px-2 py-1 text-[11px] text-warning">
          {over.length > 0
            ? `Parked: ${over.join(" and ")}.`
            : probe
              ? "Parked over its own ceiling; the last probe above was under both."
              : account.is_primary
                ? `Parked over its session's ${account.reserve_pct}% reserve or its week's paced ceiling — no probe recorded to say which.`
                : `Parked over the local ${account.quota_threshold_pct}% threshold.`}{" "}
          This self-heals — a re-probe runs every 60s, no action needed.
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
