import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Banner, EmptyState, ErrorState, PageHeader, RoleBadge } from "@/components/console/primitives";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import * as api from "@/lib/api/client";
import { actionBackendQuery, modelOptionsQuery, roleModelsQuery } from "@/lib/api/queries";
import type { Role } from "@/lib/api/types";
import { ROLES } from "@/lib/api/types";
import { agoSeconds, formatAge } from "@/lib/format";

export const Route = createFileRoute("/models")({
  head: () => ({
    meta: [
      { title: "Role models — harness operations console" },
      { name: "description", content: "Choose which model each pipeline role runs on." },
      { property: "og:title", content: "Role models — harness operations console" },
      { property: "og:description", content: "Per-role model selection for the harness pipeline." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ModelsPage,
});

function ModelsPage() {
  const cfg = useQuery(roleModelsQuery);
  const opts = useQuery(modelOptionsQuery);
  const { data: backendUp } = useQuery(actionBackendQuery);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (v: { role: Role; model: string }) => api.setRoleModel(v.role, v.model),
    onSuccess: (r) => {
      toast.success(`${r.role} now runs on ${r.model}`);
      void qc.invalidateQueries({ queryKey: roleModelsQuery.queryKey });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  return (
    <AppShell>
      <PageHeader title="Role models" subtitle="Changes apply to the next phase each role starts; running phases keep their model." right={<RefreshedAt at={cfg.dataUpdatedAt} />} />
      {backendUp === false && <Banner tone="danger">The action backend is unreachable, so model changes cannot be saved right now.</Banner>}
      {cfg.isError || opts.isError ? (
        <ErrorState title="Model configuration could not be read" body="The harness API returned an error. Roles keep running on their last saved model." />
      ) : cfg.isLoading || opts.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading configuration…</p>
      ) : !cfg.data?.length ? (
        <EmptyState title="No role has a model configured" body="Pick a model for each role below the harness can start phases." />
      ) : (
        <div className="p-4">
          <table className="panel w-full text-xs">
            <thead className="label-xs text-left text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Model</th>
                <th className="px-3 py-2">Context</th>
                <th className="px-3 py-2">Last changed</th>
              </tr>
            </thead>
            <tbody>
              {ROLES.map((role) => {
                const row = cfg.data.find((c) => c.role === role);
                const opt = opts.data?.find((o) => o.id === row?.model);
                return (
                  <tr key={role} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">
                      <RoleBadge role={role} />
                      {role === "revisor" && <p className="mt-0.5 text-[10px] text-muted-foreground">read-only role</p>}
                    </td>
                    <td className="px-3 py-2">
                      <Select
                        value={row?.model ?? ""}
                        disabled={backendUp === false || save.isPending}
                        onValueChange={(model) => save.mutate({ role, model })}
                      >
                        <SelectTrigger className="mono h-7 w-56 text-[11px]">
                          <SelectValue placeholder="not configured" />
                        </SelectTrigger>
                        <SelectContent>
                          {(opts.data ?? []).map((o) => (
                            <SelectItem key={o.id} value={o.id} className="mono text-[11px]">{o.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="mono px-3 py-2 tabular-nums text-muted-foreground">{opt ? `${opt.context_k}k` : "—"}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {row?.updated_at ? `${formatAge(agoSeconds(row.updated_at))} ago` : "never"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
