import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitBranch, X } from "lucide-react";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { EmptyState, ErrorState, GateChip, Mono, PageHeader, Banner } from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import { actionBackendQuery, approvalsQuery } from "@/lib/api/queries";
import type { Approval } from "@/lib/api/ops-types";
import { agoSeconds, formatAge } from "@/lib/format";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/approvals")({
  head: () => ({
    meta: [
      { title: "Approvals — harness operations console" },
      { name: "description", content: "Every pending operator approval in one place: pushes with diffs, merges, tool permissions and budget overrides." },
      { property: "og:title", content: "Approvals — harness operations console" },
      { property: "og:description", content: "One inbox for everything waiting on the operator." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ApprovalsPage,
});

function ApprovalsPage() {
  const q = useQuery(approvalsQuery);
  const { data: backendUp } = useQuery(actionBackendQuery);
  const qc = useQueryClient();
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const [selected, setSelected] = useState<string | null>(null);
  const decide = useMutation({
    mutationFn: (v: { id: string; d: "approved" | "rejected" }) => api.decideApproval(v.id, v.d),
    onSuccess: (a) => {
      toast.success(`${a.id} ${a.state}`);
      void qc.invalidateQueries({ queryKey: approvalsQuery.queryKey });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Decision failed"),
  });

  const rows = useMemo(
    () => (q.data ?? []).filter((a) => (tab === "pending" ? a.state === "pending" : a.state !== "pending")),
    [q.data, tab],
  );
  const current = rows.find((a) => a.id === selected) ?? rows[0] ?? null;
  const pendingCount = (q.data ?? []).filter((a) => a.state === "pending").length;

  return (
    <AppShell>
      <div className="flex h-full flex-col">
        <PageHeader title="Approvals" subtitle="Everything the harness is waiting on you for." right={<RefreshedAt at={q.dataUpdatedAt} />} />
        {backendUp === false && <Banner tone="danger">Decisions are disabled while the action backend is unreachable. You can still review diffs.</Banner>}
        <div className="flex gap-1 border-b border-border px-4 py-2">
          {(["pending", "decided"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn("rounded-sm px-2 py-1 text-[11px]", tab === t ? "bg-surface-2 text-foreground" : "text-muted-foreground")}
            >
              {t === "pending" ? `Pending · ${pendingCount}` : "Decided"}
            </button>
          ))}
        </div>
        {q.isError ? (
          <ErrorState title="Approvals could not be read" body="The harness API returned an error. Agents waiting on approval stay paused; retrying automatically." />
        ) : q.isLoading ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">Reading approvals…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            title={tab === "pending" ? "Nothing is waiting on you" : "No decisions recorded yet"}
            body={tab === "pending" ? "When an agent asks to push, merge, use a tool or exceed a budget it will appear here." : "Approved and rejected requests will be listed here."}
          />
        ) : (
          <div className="flex min-h-0 flex-1">
            <ul className="w-[340px] shrink-0 overflow-y-auto border-r border-border">
              {rows.map((a) => (
                <li key={a.id}>
                  <button
                    onClick={() => setSelected(a.id)}
                    className={cn("w-full border-b border-border px-3 py-2 text-left hover:bg-surface-2/60", current?.id === a.id && "bg-surface-2")}
                  >
                    <div className="flex items-center gap-2">
                      <Mono className="text-[11px] font-semibold">{a.id}</Mono>
                      <KindBadge kind={a.kind} />
                      {a.task_id && <Mono className="text-[11px] text-muted-foreground">{a.task_id}</Mono>}
                      <span className="flex-1" />
                      <span className="text-[10px] text-muted-foreground">{formatAge(agoSeconds(a.requested_at))} ago</span>
                    </div>
                    <p className="mt-0.5 truncate text-xs">{a.summary}</p>
                    <p className="mono text-[10px] text-muted-foreground">{a.requested_by}</p>
                  </button>
                </li>
              ))}
            </ul>
            {current && (
              <Detail
                a={current}
                disabled={backendUp === false || decide.isPending}
                onDecide={(d) => decide.mutate({ id: current.id, d })}
              />
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}

function KindBadge({ kind }: { kind: Approval["kind"] }) {
  const cls = kind === "push" ? "text-info border-info/50" : kind === "cleanup-task" ? "text-destructive border-destructive/50" : "text-muted-foreground border-border-strong";
  return <span className={cn("rounded-sm border px-1 text-[10px] uppercase tracking-wider", cls)}>{kind}</span>;
}

function Detail({ a, disabled, onDecide }: { a: Approval; disabled: boolean; onDecide: (d: "approved" | "rejected") => void }) {
  return (
    <div className="min-w-0 flex-1 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="text-sm font-semibold">{a.id}</Mono>
        <KindBadge kind={a.kind} />
        {a.task_id && (
          <Link to="/tasks/$taskId" params={{ taskId: a.task_id }} className="mono text-xs underline">{a.task_id}</Link>
        )}
        <span className="flex-1" />
        {a.state === "pending" ? (
          <>
            <button disabled={disabled} onClick={() => onDecide("rejected")} className="flex items-center gap-1 rounded-sm border border-destructive/60 px-2 py-1 text-[11px] text-destructive hover:bg-destructive/10 disabled:opacity-50">
              <X className="size-3" /> Reject
            </button>
            <button
              disabled={disabled || a.push?.gate_worst === "blocking"}
              title={a.push?.gate_worst === "blocking" ? "A blocking gate finding prevents this push" : undefined}
              onClick={() => onDecide("approved")}
              className="flex items-center gap-1 rounded-sm border border-success/60 bg-success/15 px-2 py-1 text-[11px] text-success hover:bg-success/25 disabled:opacity-50"
            >
              <Check className="size-3" /> {a.kind === "push" ? "Approve push" : "Approve"}
            </button>
          </>
        ) : (
          <span className={cn("text-[11px] font-semibold uppercase", a.state === "approved" ? "text-success" : "text-destructive")}>
            {a.state} {a.decided_at && `· ${formatAge(agoSeconds(a.decided_at))} ago`}
          </span>
        )}
      </div>
      <p className="mt-2 text-sm">{a.summary}</p>
      <p className="mono text-[11px] text-muted-foreground">requested by {a.requested_by}</p>

      {a.push && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center gap-2 text-xs">
            <GitBranch className="size-3.5" />
            <Mono>{a.push.branch}</Mono> <span className="text-muted-foreground">→</span> <Mono>{a.push.base}</Mono>
            {a.push.gate_worst && <GateChip level={a.push.gate_worst} />}
          </div>
          <ul className="panel divide-y divide-border">
            {a.push.commits.map((c) => (
              <li key={c.sha} className="flex gap-2 px-3 py-1.5 text-[11px]">
                <Mono className="text-muted-foreground">{c.sha}</Mono>
                <span>{c.message}</span>
              </li>
            ))}
          </ul>
          <DiffView diff={a.push.diff} />
        </div>
      )}
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <EmptyState title="No diff recorded" body="The push request arrived without a diff. Reject it and ask the agent to re-request." />;
  const files = diff.split(/^(?=diff --git )/m).filter(Boolean);
  const stats = files.map((f) => {
    const ls = f.split("\n");
    return {
      name: /^\+\+\+ b\/(.*)$/m.exec(f)?.[1] ?? "unknown",
      add: ls.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length,
      del: ls.filter((l) => l.startsWith("-") && !l.startsWith("---")).length,
      lines: ls,
    };
  });
  return (
    <div className="space-y-3">
      {stats.map((f) => (
        <div key={f.name} className="panel overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5 text-[11px]">
            <Mono className="flex-1">{f.name}</Mono>
            <span className="mono text-success">+{f.add}</span>
            <span className="mono text-destructive">−{f.del}</span>
          </div>
          <pre className="mono overflow-x-auto text-[11px] leading-5">
            {f.lines
              .filter((l) => !/^(diff --git|index |--- |\+\+\+ )/.test(l))
              .map((l, i) => (
                <div
                  key={i}
                  className={cn(
                    "px-3",
                    l.startsWith("+") && "bg-success/10 text-success",
                    l.startsWith("-") && "bg-destructive/10 text-destructive",
                    l.startsWith("@@") && "bg-info/10 text-info",
                  )}
                >
                  {l || " "}
                </div>
              ))}
          </pre>
        </div>
      ))}
    </div>
  );
}
