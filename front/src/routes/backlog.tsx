import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Banner, EmptyState, ErrorState, Mono, PageHeader } from "@/components/console/primitives";
import * as api from "@/lib/api/client";
import { actionBackendQuery, backlogQuery } from "@/lib/api/queries";
import type { BacklogCategory, BacklogItem } from "@/lib/api/ops-types";
import { formatAge, agoSeconds } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/backlog")({
  head: () => ({
    meta: [
      { title: "Backlog — harness operations console" },
      {
        name: "description",
        content:
          "Prioritized backlog of tasks and epics: priority order, creation date, completion state and category.",
      },
      { property: "og:title", content: "Backlog — harness operations console" },
      { property: "og:description", content: "Every pending task and epic, ordered by priority." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: BacklogPage,
});

const CATEGORIES: BacklogCategory[] = [
  "harness",
  "pool",
  "gates",
  "learnings",
  "debt",
  "docs",
  "infra",
];

function BacklogPage() {
  const q = useQuery(backlogQuery);
  const { data: backendUp } = useQuery(actionBackendQuery);
  const qc = useQueryClient();
  const [category, setCategory] = useState<string>("all");
  const [showCompleted, setShowCompleted] = useState(true);

  const toggle = useMutation({
    mutationFn: (v: { id: string; completed: boolean }) =>
      api.setBacklogCompleted(v.id, v.completed),
    onSuccess: (item) => {
      toast.success(`${item.id} ${item.completed ? "completado" : "reabierto"}`);
      void qc.invalidateQueries({ queryKey: backlogQuery.queryKey });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo actualizar"),
  });

  const rows = useMemo(() => {
    const all = [...(q.data ?? [])].sort((a, b) => a.priority - b.priority);
    return all.filter(
      (b) => (category === "all" || b.category === category) && (showCompleted || !b.completed),
    );
  }, [q.data, category, showCompleted]);

  const openCount = (q.data ?? []).filter((b) => !b.completed).length;

  return (
    <AppShell>
      <div className="flex h-full flex-col">
        <PageHeader
          title="Backlog"
          subtitle={`${openCount} pendientes · ordenado por prioridad`}
          right={<RefreshedAt at={q.dataUpdatedAt} />}
        />
        {backendUp === false && (
          <Banner tone="danger">
            El backend de acciones no responde: puedes consultar el backlog pero no marcar elementos
            como completados.
          </Banner>
        )}
        <div className="flex items-center gap-3 border-b border-border px-4 py-2">
          <span className="label-xs">Categoría</span>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="h-7 w-[150px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">todas</SelectItem>
              {CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <input
              type="checkbox"
              checked={showCompleted}
              onChange={(e) => setShowCompleted(e.target.checked)}
              className="accent-current"
            />
            mostrar completados
          </label>
        </div>
        {q.isError ? (
          <ErrorState
            title="No se pudo leer el backlog"
            body="La API del harness devolvió un error. Reintentando automáticamente; la priorización no se pierde."
          />
        ) : q.isLoading ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">Leyendo backlog…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No hay elementos que mostrar"
            body={
              category !== "all" || !showCompleted
                ? "Ningún elemento coincide con los filtros actuales. Amplía la categoría o muestra los completados."
                : "El backlog está vacío. Las nuevas tareas y épicas aparecerán aquí ordenadas por prioridad."
            }
          />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border text-left">
                  <th className="label-xs px-4 py-2 font-normal">Pri</th>
                  <th className="label-xs px-2 py-2 font-normal">ID</th>
                  <th className="label-xs px-2 py-2 font-normal">Tipo</th>
                  <th className="label-xs px-2 py-2 font-normal">Título</th>
                  <th className="label-xs px-2 py-2 font-normal">Categoría</th>
                  <th className="label-xs px-2 py-2 font-normal">Creado</th>
                  <th className="label-xs px-2 py-2 font-normal">Tarea</th>
                  <th className="label-xs px-4 py-2 font-normal text-right">Hecho</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <Row
                    key={b.id}
                    item={b}
                    disabled={backendUp === false || toggle.isPending}
                    onToggle={(completed) => toggle.mutate({ id: b.id, completed })}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function Row({
  item: b,
  disabled,
  onToggle,
}: {
  item: BacklogItem;
  disabled: boolean;
  onToggle: (completed: boolean) => void;
}) {
  return (
    <tr className={cn("border-b border-border/60 align-top", b.completed && "opacity-50")}>
      <td className="px-4 py-2">
        <Mono
          className={cn(
            "text-[11px] font-semibold",
            b.priority <= 3 ? "text-warning" : "text-muted-foreground",
          )}
        >
          P{b.priority}
        </Mono>
      </td>
      <td className="px-2 py-2">
        <Mono className="text-[11px]">{b.id}</Mono>
      </td>
      <td className="px-2 py-2">
        <span
          className={cn(
            "rounded-sm border px-1 text-[10px] uppercase tracking-wider",
            b.kind === "epic"
              ? "border-info/50 text-info"
              : "border-border-strong text-muted-foreground",
          )}
        >
          {b.kind === "epic" ? "épica" : "tarea"}
        </span>
      </td>
      <td className="max-w-[380px] px-2 py-2">
        <p className={cn("text-xs", b.completed && "line-through")}>{b.title}</p>
        {b.notes && <p className="mt-0.5 text-[10px] text-muted-foreground">{b.notes}</p>}
      </td>
      <td className="px-2 py-2">
        <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[10px]">{b.category}</span>
      </td>
      <td className="px-2 py-2 text-[11px] text-muted-foreground">
        {formatAge(agoSeconds(b.created_at))}
        {b.completed && b.completed_at && (
          <span className="block text-[10px]">
            hecho hace {formatAge(agoSeconds(b.completed_at))}
          </span>
        )}
      </td>
      <td className="px-2 py-2">
        {b.task_id ? (
          <Link
            to="/tasks/$taskId"
            params={{ taskId: b.task_id }}
            className="mono text-[11px] underline"
          >
            {b.task_id}
          </Link>
        ) : (
          <span className="text-[11px] text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-right">
        <input
          type="checkbox"
          checked={b.completed}
          disabled={disabled}
          onChange={(e) => onToggle(e.target.checked)}
          className="accent-current"
          title={
            disabled
              ? "El backend de acciones no responde"
              : b.completed
                ? "Reabrir"
                : "Marcar como completado"
          }
        />
      </td>
    </tr>
  );
}
