import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  Banner,
  BrokenBanner,
  EmptyState,
  ErrorState,
  Malformed,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { accountsQuery, credentialsQuery } from "@/lib/api/queries";
import type { Account } from "@/lib/api/types";
import { accountStateTone, agoSeconds, formatAge, formatDateClock } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/tokens")({
  head: () => ({
    meta: [
      { title: "Tokens — harness operations console" },
      {
        name: "description",
        content:
          "Whether each credential the harness uses exists: the git token, the deploy keys and the Claude logins.",
      },
      { property: "og:title", content: "Tokens — harness operations console" },
      {
        property: "og:description",
        content: "Which credentials the harness has, and which it lacks.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: TokensPage,
});

const NO_SNAPSHOT_NOTE =
  "The worker writes this snapshot when it starts and every 5 minutes; until it has, there is nothing to show. Check that the worker is running on the Queue screen.";

const GIT_TOKEN_SCOPE =
  "ADR 36 asks for a fine-grained token with `contents: read`; a project that pushes on approve needs `contents: write` (ADR 78).";

const GIT_TOKEN_HINT: Record<string, string> = {
  empty: "the file exists and is empty",
  absent: "no file at this path",
  directory:
    "Docker made a directory here because the host file `.data/credentials/git-credentials` was missing; create it and recreate the worker",
  not_mounted: "the worker has no such path: this compose mounts no token",
};

const PILL = "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide";

/** `present` is success; a missing credential is a warning, never a stop (ADR 489 decision 7). */
function fileTone(state: string): string {
  if (state === "present") return "border-success/50 bg-success/10 text-success";
  if (["empty", "absent", "directory", "not_mounted", "unreachable"].includes(state))
    return "border-warning/50 bg-warning/10 text-warning";
  return "border-border-strong text-muted-foreground";
}

/** Every key a row may carry, as served and so unchecked: each is read through a guard. */
interface Served {
  state?: unknown;
  path?: unknown;
  project?: unknown;
  fingerprint?: unknown;
  modified_at?: unknown;
  account?: unknown;
  container?: unknown;
  logged_in_at?: unknown;
}

function isRecord(v: unknown): v is Served {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function TokensPage() {
  const credentials = useQuery(credentialsQuery);
  const accounts = useQuery(accountsQuery);
  const now = useNow(2000);

  let body: ReactNode;
  if (credentials.isError) {
    body = (
      <ErrorState
        title="Credential status could not be read"
        body="The harness API returned an error. Containers may still be working; retrying automatically."
      />
    );
  } else if (credentials.isLoading || accounts.isLoading) {
    body = <p className="px-4 py-6 text-xs text-muted-foreground">Reading credentials…</p>;
  } else {
    const served = credentials.data;
    const snapshot = served?.data ?? null;
    const credentialWarnings = served?.warnings ?? [];
    const accountWarnings = accounts.data?.warnings ?? [];
    if (snapshot === null) {
      body = (
        <>
          <Banner tone="warning">
            {credentialWarnings.map((w, i) => (
              <p key={i} className="mono text-[10px] leading-relaxed">
                {w}
              </p>
            ))}
            <p className="mt-1 text-[11px]">{NO_SNAPSHOT_NOTE}</p>
          </Banner>
          <WarningBanner warnings={accountWarnings} />
        </>
      );
    } else {
      const pool = accounts.isError ? null : (accounts.data?.data ?? []);
      body = (
        <>
          <BrokenBanner
            reads={
              accounts.isError
                ? ["/api/accounts did not answer: the Claude logins' pool state is not shown"]
                : []
            }
          />
          <WarningBanner warnings={[...credentialWarnings, ...accountWarnings]} />
          {snapshot.stale === true && <StaleBanner snapshot={snapshot} now={now} />}
          <div className="grid gap-3 p-4">
            <GitTokenRegion value={snapshot.git_token} now={now} />
            <DeployKeysRegion value={snapshot.deploy_keys} now={now} />
            <LoginsRegion value={snapshot.accounts} pool={pool} now={now} />
          </div>
        </>
      );
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="Tokens"
        subtitle="Whether each credential the harness uses exists, from the worker's snapshot. Whether a Claude login still works is its account's pool state."
        right={<RefreshedAt at={credentials.dataUpdatedAt} />}
      />
      {body}
    </AppShell>
  );
}

function StaleBanner({
  snapshot,
  now,
}: {
  snapshot: { written_at: unknown; stale_after_seconds: unknown };
  now: number;
}) {
  const written = typeof snapshot.written_at === "string" ? snapshot.written_at : null;
  const age = agoSeconds(written, now);
  const minutes =
    typeof snapshot.stale_after_seconds === "number" ? snapshot.stale_after_seconds / 60 : "?";
  return (
    <Banner tone="warning">
      {age === null
        ? "The credentials snapshot is of unknown age"
        : `The credentials snapshot is ${formatAge(age)} old, written ${formatDateClock(written)}`}
      {`; the api calls it stale past ${minutes} minutes, so the worker may not be running.`}
    </Banner>
  );
}

function Region({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel space-y-2 p-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function StatePill({ state }: { state: unknown }) {
  if (typeof state !== "string") return <Malformed label="state" got={state} want="a string" />;
  return <span className={cn(PILL, fileTone(state))}>{state}</span>;
}

/** A served time: its clock and, ticking, its age. A string `Date` cannot parse is shown as it came. */
function When({
  at,
  now,
  none,
  prefix = "",
}: {
  at: unknown;
  now: number;
  none: string;
  prefix?: string;
}) {
  if (at === null) return <Absent label={none} />;
  if (typeof at !== "string") return <Malformed got={at} want="a string or null" />;
  const age = agoSeconds(at, now);
  if (age === null)
    return (
      <span className="mono text-[11px]">
        {prefix}
        {at}
      </span>
    );
  return (
    <span className="mono text-[11px] tabular-nums">
      {prefix}
      {formatDateClock(at)} · {formatAge(age)} ago
    </span>
  );
}

function GitTokenRegion({ value, now }: { value: unknown; now: number }) {
  const title = "GitHub token";
  if (!isRecord(value))
    return (
      <Region title={title}>
        <Malformed label="git_token" got={value} want="an object" />
      </Region>
    );
  const hint = typeof value.state === "string" ? GIT_TOKEN_HINT[value.state] : undefined;
  return (
    <Region title={title}>
      <div className="flex flex-wrap items-center gap-2">
        <StatePill state={value.state} />
        {typeof value.path === "string" ? (
          <Mono className="text-[11px]">{value.path}</Mono>
        ) : (
          <Malformed label="path" got={value.path} want="a string" />
        )}
        <When at={value.modified_at} now={now} none="never written" />
      </div>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
      <p className="text-[11px] text-muted-foreground">{GIT_TOKEN_SCOPE}</p>
    </Region>
  );
}

function DeployKeysRegion({ value, now }: { value: unknown; now: number }) {
  const title = "Deploy keys";
  if (!Array.isArray(value))
    return (
      <Region title={title}>
        <Malformed label="deploy_keys" got={value} want="a list" />
      </Region>
    );
  if (value.length === 0)
    return (
      <Region title={title}>
        <EmptyState
          title="No project has a record or a key directory"
          body="A project's deploy key appears here once it has a record or a key directory."
        />
      </Region>
    );
  return (
    <Region title={title}>
      {value.map((row: unknown, i) =>
        !isRecord(row) || typeof row.project !== "string" ? (
          <div key={i} className="py-1">
            <Malformed label={`deploy_keys[${i}]`} got={row} want="an object with a project" />
          </div>
        ) : (
          <div key={`${row.project}:${i}`} className="flex flex-wrap items-center gap-2 py-1">
            <Mono className="text-xs font-semibold">{row.project}</Mono>
            <StatePill state={row.state} />
            {row.fingerprint === null ? (
              <Absent label="no fingerprint" />
            ) : typeof row.fingerprint === "string" ? (
              <Mono className="text-[11px]">{row.fingerprint}</Mono>
            ) : (
              <Malformed label="fingerprint" got={row.fingerprint} want="a string or null" />
            )}
            <When at={row.modified_at} now={now} none="never written" />
          </div>
        ),
      )}
    </Region>
  );
}

function PoolState({ pool, name }: { pool: Account[] | null; name: string }) {
  if (pool === null) return null;
  const row = pool.find((a) => a.name === name);
  if (!row) return <Absent label="not in the pool" />;
  if (row.state === null) return <Absent label="state unreadable" />;
  return (
    <span
      className={cn(
        PILL,
        accountStateTone[row.state] ?? "border-border-strong text-muted-foreground",
      )}
    >
      {row.state}
    </span>
  );
}

function LoginsRegion({
  value,
  pool,
  now,
}: {
  value: unknown;
  pool: Account[] | null;
  now: number;
}) {
  const title = "Claude logins";
  if (!Array.isArray(value))
    return (
      <Region title={title}>
        <Malformed label="accounts" got={value} want="a list" />
      </Region>
    );
  if (value.length === 0)
    return (
      <Region title={title}>
        <EmptyState
          title="No account is configured"
          body="Accounts come from the harness configuration."
        />
      </Region>
    );
  return (
    <Region title={title}>
      {value.map((row: unknown, i) =>
        !isRecord(row) || typeof row.account !== "string" ? (
          <div key={i} className="py-1">
            <Malformed label={`accounts[${i}]`} got={row} want="an object with an account" />
          </div>
        ) : (
          <div key={`${row.account}:${i}`} className="flex flex-wrap items-center gap-2 py-1">
            <Mono className="text-xs font-semibold">{row.account}</Mono>
            {typeof row.container === "string" ? (
              <Mono className="text-[10px] text-muted-foreground">{row.container}</Mono>
            ) : (
              <Malformed label="container" got={row.container} want="a string" />
            )}
            <PoolState pool={pool} name={row.account} />
            <StatePill state={row.state} />
            <When at={row.modified_at} now={now} none="never written" />
            <When
              at={row.logged_in_at}
              now={now}
              none="never from the console"
              prefix="signed in from the console "
            />
          </div>
        ),
      )}
    </Region>
  );
}
