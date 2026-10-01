import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Absent, EmptyState, ErrorState, Mono, PageHeader } from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import { actionBackendQuery, tokensQuery } from "@/lib/api/queries";
import type { ContainerToken, TokenState } from "@/lib/api/ops-types";
import { agoSeconds, formatAge } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/tokens")({
  head: () => ({
    meta: [
      { title: "Session tokens — harness operations console" },
      { name: "description", content: "Provider session token status per agent container, with one-click re-authentication." },
      { property: "og:title", content: "Session tokens — harness operations console" },
      { property: "og:description", content: "Which containers can still talk to their provider." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: TokensPage,
});

const tone: Record<TokenState, string> = {
  valid: "border-success/50 bg-success/10 text-success",
  expiring: "border-warning/50 bg-warning/10 text-warning",
  expired: "border-destructive/60 bg-destructive/15 text-destructive",
  revoked: "border-destructive/60 bg-destructive/15 text-destructive",
  reauthenticating: "border-info/50 bg-info/10 text-info",
};

function TokensPage() {
  const tokens = useQuery(tokensQuery);
  const { data: backendUp } = useQuery(actionBackendQuery);
  const qc = useQueryClient();
  const reauth = useMutation({
    mutationFn: (c: string) => api.reauthContainer(c),
    onSuccess: (t) => {
      toast.success(`Device login started for ${t.container}`);
      void qc.invalidateQueries({ queryKey: tokensQuery.queryKey });
    },
    onError: (e) => toast.error(`Re-auth failed: ${e instanceof Error ? e.message : "unknown"}`),
  });

  return (
    <AppShell>
      <PageHeader
        title="Session tokens"
        subtitle="Provider login of each container. Expired or revoked tokens make the account unusable until re-authenticated."
        right={<RefreshedAt at={tokens.dataUpdatedAt} />}
      />
      {tokens.isError ? (
        <ErrorState title="Token status could not be read" body="The harness API returned an error. Containers may still be working; retrying automatically." />
      ) : tokens.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading tokens…</p>
      ) : !tokens.data?.length ? (
        <EmptyState title="No containers reported a token" body="Bootstrap a project from the Queue to create agent containers." />
      ) : (
        <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
          {tokens.data.map((t) => (
            <TokenCard
              key={t.container}
              t={t}
              busy={reauth.isPending && reauth.variables === t.container}
              disabled={backendUp === false}
              onReauth={() => reauth.mutate(t.container)}
            />
          ))}
        </div>
      )}
    </AppShell>
  );
}

function TokenCard({ t, busy, disabled, onReauth }: { t: ContainerToken; busy: boolean; disabled: boolean; onReauth: () => void }) {
  const now = useNow(2000);
  const expIn = t.expires_at ? Math.round((Date.parse(t.expires_at) - now) / 1000) : null;
  return (
    <div className="panel space-y-2 p-3">
      <div className="flex items-center justify-between">
        <div>
          <Mono className="text-xs font-semibold">{t.container}</Mono>
          <p className="text-[11px] text-muted-foreground">{t.account}</p>
        </div>
        <span className={cn("rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", tone[t.state])}>{t.state}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
        <dt className="text-muted-foreground">Expires</dt>
        <dd className="mono tabular-nums">
          {expIn === null ? <Absent label="no expiry recorded" /> : expIn > 0 ? `in ${formatAge(expIn)}` : `${formatAge(-expIn)} ago`}
        </dd>
        <dt className="text-muted-foreground">Last refresh</dt>
        <dd className="mono tabular-nums">
          {t.last_refreshed_at ? `${formatAge(agoSeconds(t.last_refreshed_at, now))} ago` : <Absent label="never" />}
        </dd>
      </dl>
      {t.last_error && <p className="mono rounded-sm bg-destructive/10 px-2 py-1 text-[11px] text-destructive">{t.last_error}</p>}
      {t.device_code && (
        <div className="rounded-sm border border-info/40 bg-info/10 p-2 text-[11px]">
          <p>Open <a className="underline" href={t.device_code.verification_url} target="_blank" rel="noreferrer">{t.device_code.verification_url}</a> and enter:</p>
          <p className="mono mt-1 text-base font-semibold tracking-widest">{t.device_code.user_code}</p>
          <p className="mt-1 text-muted-foreground">Waiting for the provider to confirm — this card updates on its own.</p>
        </div>
      )}
      <button
        onClick={onReauth}
        disabled={busy || disabled || t.state === "reauthenticating"}
        title={disabled ? "Action backend is down" : undefined}
        className="w-full rounded-sm border border-border-strong bg-surface-2 px-2 py-1 text-[11px] font-medium hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? "Starting…" : t.state === "reauthenticating" ? "Login pending" : "Re-authenticate"}
      </button>
    </div>
  );
}
