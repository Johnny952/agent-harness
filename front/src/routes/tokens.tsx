import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import * as api from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import {
  accountsQuery,
  actionQuery,
  actionQueueQuery,
  cancelActionMutation,
  credentialsQuery,
} from "@/lib/api/queries";
import type { Account, ActionRecord } from "@/lib/api/types";
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
  const queue = useQuery(actionQueueQuery);
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
      const reads: string[] = [];
      if (accounts.isError)
        reads.push("/api/accounts did not answer: the Claude logins' pool state is not shown");
      if (queue.isError)
        reads.push("/api/actions did not answer: a sign-in started elsewhere is not shown");
      const queued = queue.data?.data;
      const open = [...(queued?.pending ?? []), ...(queued?.claimed ?? [])];
      body = (
        <>
          <BrokenBanner reads={reads} />
          <WarningBanner warnings={[...credentialWarnings, ...accountWarnings]} />
          {snapshot.stale === true && <StaleBanner snapshot={snapshot} now={now} />}
          <div className="grid gap-3 p-4">
            <GitTokenRegion value={snapshot.git_token} now={now} />
            <DeployKeysRegion value={snapshot.deploy_keys} now={now} />
            <LoginsRegion value={snapshot.accounts} pool={pool} now={now} open={open} />
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
      <RenewToken now={now} />
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
          <div key={`${row.project}:${i}`} className="py-1">
            <div className="flex flex-wrap items-center gap-2">
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
            <KeyControls project={row.project} />
          </div>
        ),
      )}
    </Region>
  );
}

type Rec = Record<string, unknown>;

const OPEN = ["pending", "running"];
const ENDED = ["done", "failed", "cancelled", "interrupted"];

/** The record's `result`, when it is an object. */
function resultOf(a: ActionRecord): Rec {
  return typeof a.result === "object" && a.result !== null && !Array.isArray(a.result)
    ? (a.result as Rec)
    : {};
}

/** What the screen shows of a write the service refused: its message, never a value sent. */
function Refusal({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null;
  const message = error instanceof Error ? error.message : "the request failed";
  return (
    <Banner tone="danger">
      {error instanceof ApiError
        ? error.status === 0
          ? `Not sent: ${message}`
          : `Refused (${error.status}): ${message}`
        : message}
    </Banner>
  );
}

/** Once a write settles, what it changed is read again (ADR 492 decision 8). */
function useSettle() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      [["credentials"], ["action-queue"]].map((queryKey) => qc.invalidateQueries({ queryKey })),
    );
}

interface Followed {
  id: string | null;
  record: ActionRecord | null;
  /** The sentence for a read that gave no record; null while it is simply in flight. */
  problem: string | null;
}

/** An action record by its id, read until it ends; the snapshot is read again once it has. */
function useFollowed(id: string | null): Followed {
  const qc = useQueryClient();
  const q = useQuery({ ...actionQuery(id ?? ""), enabled: id !== null });
  const record = q.data?.data ?? null;
  let problem: string | null = null;
  if (id !== null) {
    if (q.isError)
      problem =
        q.error instanceof ApiError && q.error.status === 404
          ? `the queue has no record of ${id}`
          : `GET /api/actions/${id} did not answer`;
    else if (q.data && record === null)
      problem = `its record is unreadable: ${q.data.warnings.join("; ")}`;
  }
  const status = record?.status;
  useEffect(() => {
    if (id !== null && status !== undefined && ENDED.includes(status))
      void qc.invalidateQueries({ queryKey: ["credentials"] });
  }, [id, status, qc]);
  return { id, record, problem };
}

function isOpen(f: Followed): boolean {
  if (f.id === null) return false;
  return f.record ? OPEN.includes(f.record.status) : f.problem === null;
}

/** The line every kind shares: a read that failed, or a record that ended without a result. */
function Ending({ label, f }: { label: string; f: Followed }): ReactNode {
  const a = f.record;
  if (f.problem !== null) return <p className="text-[11px] text-muted-foreground">{f.problem}</p>;
  if (a === null) return <p className="text-[11px] text-muted-foreground">reading {f.id}…</p>;
  if (a.status === "failed" || a.status === "interrupted")
    return (
      <Banner tone="danger">
        {label} {a.status}: {a.error ?? "no error was recorded"}
      </Banner>
    );
  if (a.status === "cancelled") return <p className="text-[11px]">cancelled</p>;
  return null;
}

function Progress({
  label,
  f,
  done,
}: {
  label: string;
  f: Followed;
  done: (a: ActionRecord) => ReactNode;
}) {
  if (f.id === null) return null;
  const a = f.record;
  if (a?.status === "pending")
    return <p className="text-[11px]">queued as {a.id}, waiting for the worker</p>;
  if (a?.status === "running") return <p className="text-[11px]">running</p>;
  if (a?.status === "done") return <>{done(a)}</>;
  return <Ending label={label} f={f} />;
}

function RenewToken({ now }: { now: number }) {
  const settle = useSettle();
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);
  const [id, setId] = useState<string | null>(null);
  const followed = useFollowed(id);

  // Not `useMutation`: its cache keeps the variables, and these hold the token (ADR 492 decision 4).
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setRefusal(null);
    try {
      const queued = await api.renewGitToken(
        token,
        username.trim() === "" ? null : username.trim(),
      );
      setId(queued.id);
    } catch (err) {
      setRefusal(err);
    } finally {
      setToken("");
      setBusy(false);
      void settle();
    }
  }

  return (
    <div className="space-y-1.5">
      <form aria-label="Renew the GitHub token" onSubmit={submit} className="flex flex-wrap gap-2">
        <Input
          type="password"
          autoComplete="off"
          aria-label="Token"
          placeholder="New token"
          className="h-8 w-64"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <Input
          aria-label="Username"
          placeholder="x-access-token"
          className="h-8 w-44"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={busy || token === ""}>
          Renew
        </Button>
      </form>
      <Refusal error={refusal} />
      <Progress
        label="Renewal"
        f={followed}
        done={(a) => (
          <p className="text-[11px]">
            <When
              at={a.finished_at}
              now={now}
              none="written, time not recorded"
              prefix="written "
            />
          </p>
        )}
      />
    </div>
  );
}

function CheckResult({ a }: { a: ActionRecord }) {
  const r = resultOf(a);
  const detail = typeof r["detail"] === "string" && r["detail"] !== "" ? r["detail"] : null;
  const tail = detail ? ` ${detail}` : "";
  if (r["reachable"] !== true)
    return (
      <Banner tone="warning">
        The remote does not answer this key yet{detail ? `: ${detail}` : "."}
      </Banner>
    );
  if (r["writable"] === true)
    return (
      <Banner tone="info">The remote answers this key and would take a push from it.{tail}</Banner>
    );
  if (r["writable"] === false)
    return (
      <Banner tone="warning">
        The remote answers this key but refuses a push from it. If the harness's push_task_branch is
        on, tick “Allow write access” on the deploy key, then check again.{tail}
      </Banner>
    );
  return (
    <Banner tone="info">The remote answers this key; write access was not tested.{tail}</Banner>
  );
}

function KeyControls({ project }: { project: string }) {
  const settle = useSettle();
  const [confirming, setConfirming] = useState(false);
  const [keygenId, setKeygenId] = useState<string | null>(null);
  const [checkId, setCheckId] = useState<string | null>(null);
  const keygenRead = useFollowed(keygenId);
  const checkRead = useFollowed(checkId);
  const keygen = useMutation({
    mutationFn: () => api.keygenProject(project, true),
    onSuccess: (a) => setKeygenId(a.id),
    onSettled: settle,
  });
  const check = useMutation({
    mutationFn: () => api.checkProject(project),
    onSuccess: (a) => setCheckId(a.id),
    onSettled: settle,
  });
  const keygenBusy = keygen.isPending || isOpen(keygenRead);
  const checkBusy = check.isPending || isOpen(checkRead);

  return (
    <div className="space-y-1.5 py-1">
      <div className="flex flex-wrap items-center gap-2">
        {!confirming && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={keygenBusy}
            onClick={() => {
              keygen.reset();
              setConfirming(true);
            }}
          >
            Replace key
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={checkBusy}
          onClick={() => {
            check.reset();
            check.mutate();
          }}
        >
          Check
        </Button>
      </div>
      {confirming && (
        <div className="space-y-1.5 rounded-md border border-warning/50 bg-warning/10 p-2 text-xs text-warning">
          <p>
            Replacing {project}'s key makes its fetch and push fail until the new public half is
            added on GitHub as a deploy key.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={keygenBusy}
              onClick={() => {
                setConfirming(false);
                keygen.mutate();
              }}
            >
              Replace the key
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
        </div>
      )}
      <Refusal error={keygen.error} />
      <Progress
        label="Keygen"
        f={keygenRead}
        done={(a) => {
          const r = resultOf(a);
          const pub = typeof r["public"] === "string" && r["public"] !== "" ? r["public"] : null;
          if (pub === null)
            return <Banner tone="warning">the finished keygen reported no public key</Banner>;
          return (
            <div className="space-y-1">
              <pre
                data-testid="public-key"
                className="mono whitespace-pre-wrap break-all text-[11px]"
              >
                {pub}
              </pre>
              {typeof r["fingerprint"] === "string" && (
                <Mono className="text-[11px]">{r["fingerprint"]}</Mono>
              )}
              <p className="text-[11px] text-muted-foreground">
                Add it on GitHub: the repository's Settings → Deploy keys → Add deploy key. The
                replaced key stays there until it is deleted. Then press Check.
              </p>
            </div>
          );
        }}
      />
      <Refusal error={check.error} />
      <Progress label="Check" f={checkRead} done={(a) => <CheckResult a={a} />} />
    </div>
  );
}

/** An `https:` URL, or null: the screen links nothing else (ADR 492 decision 5). */
function httpsUrl(v: unknown): string | null {
  if (typeof v !== "string") return null;
  try {
    return new URL(v).protocol === "https:" ? v : null;
  } catch {
    return null;
  }
}

function LoginControls({
  account,
  now,
  adopted,
}: {
  account: string;
  now: number;
  adopted: ActionRecord | undefined;
}) {
  const settle = useSettle();
  const qc = useQueryClient();
  const [id, setId] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);
  const followed = useFollowed(id);
  const adoptedId = adopted?.id ?? null;

  // A sign-in started before a reload, or elsewhere, is resumed (ADR 492 decision 2).
  useEffect(() => {
    if (id === null && adoptedId !== null) setId(adoptedId);
  }, [id, adoptedId]);

  const cancel = useMutation(cancelActionMutation(qc));
  const login = useMutation({
    mutationFn: () => api.startAccountLogin(account),
    onSuccess: (a) => {
      cancel.reset();
      setId(a.id);
    },
    onSettled: settle,
  });
  const a = followed.record;
  const live = a !== null && OPEN.includes(a.status);

  // Not `useMutation`, for the same reason as the token: the code is a secret.
  async function send(e: FormEvent) {
    e.preventDefault();
    if (id === null) return;
    setBusy(true);
    setRefusal(null);
    try {
      await api.sendLoginCode(id, code);
    } catch (err) {
      setRefusal(err);
    } finally {
      setCode("");
      setBusy(false);
      void settle();
    }
  }

  let stage: ReactNode = null;
  if (id !== null) {
    if (a === null || !OPEN.includes(a.status)) {
      stage =
        a?.status === "done" ? (
          <p className="text-[11px]">
            signed in{" "}
            {typeof resultOf(a)["logged_in_at"] === "string" && (
              <When at={resultOf(a)["logged_in_at"]} now={now} none="" />
            )}
            {resultOf(a)["released"] === true ? "; the account was released from AUTH_EXPIRED" : ""}
          </p>
        ) : (
          <Ending label="Sign-in" f={followed} />
        );
    } else {
      const r = resultOf(a);
      if (a.status === "running" && r["awaiting"] === "code") {
        const url = httpsUrl(r["url"]);
        stage =
          url === null ? (
            <Malformed label="url" got={r["url"]} want="an https URL" />
          ) : (
            <div className="space-y-1.5">
              <p className="text-[11px]">
                Open{" "}
                <a href={url} target="_blank" rel="noopener noreferrer" className="underline">
                  {url}
                </a>
                , sign in, and paste the code it shows.
              </p>
              <form aria-label="Send the sign-in code" onSubmit={send} className="flex gap-2">
                <Input
                  type="password"
                  autoComplete="off"
                  aria-label="Code"
                  placeholder="Code"
                  className="h-8 w-64"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
                <Button type="submit" size="sm" disabled={busy || code === ""}>
                  Send code
                </Button>
              </form>
              <Refusal error={refusal} />
            </div>
          );
      } else if (a.status === "running" && (r["url"] != null || r["code_at"] != null)) {
        stage = <p className="text-[11px]">signing in</p>;
      } else {
        stage = <p className="text-[11px]">waiting for the sign-in URL</p>;
      }
    }
  }

  return (
    <div className="space-y-1.5 py-1">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={login.isPending || isOpen(followed)}
          onClick={() => login.mutate()}
        >
          Sign in again
        </Button>
        {live && id !== null && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(id, { onSettled: settle })}
          >
            Cancel
          </Button>
        )}
      </div>
      <Refusal error={login.error} />
      {stage}
      <Refusal error={cancel.error} />
      {cancel.data && <p className="text-[11px]">cancel queued as {cancel.data.id}</p>}
    </div>
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
  open,
}: {
  value: unknown;
  pool: Account[] | null;
  now: number;
  open: ActionRecord[];
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
          <div key={`${row.account}:${i}`} className="py-1">
            <div className="flex flex-wrap items-center gap-2">
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
            <LoginControls
              account={row.account}
              now={now}
              adopted={open.find(
                (a) => a.kind === "account_login" && a.params?.["account"] === row.account,
              )}
            />
          </div>
        ),
      )}
    </Region>
  );
}
