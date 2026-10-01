import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { agoSeconds, formatAge, roleColorVar, roleGlyph } from "@/lib/format";
import type { GateFinding, Role, TaskStatus } from "@/lib/api/types";
import { splitStatus } from "@/lib/format";

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("mono", className)}>{children}</span>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

export function RoleBadge({ role, compact = false }: { role: Role; compact?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-sm border px-1.5 py-[1px] text-[10px] font-medium uppercase tracking-wide"
      style={{
        color: roleColorVar[role],
        borderColor: roleColorVar[role],
        backgroundColor: `color-mix(in oklch, ${roleColorVar[role]} 14%, transparent)`,
      }}
      title={role}
    >
      <span aria-hidden>{roleGlyph[role]}</span>
      {!compact && role}
    </span>
  );
}

export function StatusPill({ status }: { status: TaskStatus }) {
  const { lane, role } = splitStatus(status);
  if (role) return <RoleBadge role={role} />;
  const tone: Record<string, string> = {
    queued: "text-muted-foreground border-border-strong bg-surface-2",
    blocked: "text-destructive border-destructive/60 bg-destructive/10",
    done: "text-success border-success/50 bg-success/10",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-[1px] text-[10px] font-medium uppercase tracking-wide",
        tone[lane],
      )}
    >
      {lane}
    </span>
  );
}

export function HeartbeatDot({
  heartbeat,
  now,
  withLabel = false,
}: {
  heartbeat: string | null;
  now: number;
  withLabel?: boolean;
}) {
  const age = agoSeconds(heartbeat, now);
  if (age === null) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
        <span className="inline-block size-[7px] rounded-full border border-border-strong" />
        {withLabel && "no lock"}
      </span>
    );
  }
  const stale = age > 120;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[10px]",
        stale ? "font-medium text-destructive" : "text-muted-foreground",
      )}
      title={stale ? `Heartbeat stale — last beat ${formatAge(age)} ago` : `Heartbeat ${formatAge(age)} ago`}
    >
      <span
        className={cn(
          "inline-block size-[7px] rounded-full",
          stale ? "animate-pulse bg-destructive ring-2 ring-destructive/30" : "bg-success",
        )}
      />
      {stale ? `stale ${formatAge(age)}` : withLabel ? formatAge(age) : null}
    </span>
  );
}

export const gateTone = {
  note: "text-muted-foreground border-border-strong bg-surface-2",
  warning: "text-warning border-warning/50 bg-warning/10",
  blocking: "text-destructive border-destructive/60 bg-destructive/15",
} as const;

export function worstGate(findings: GateFinding[]): GateFinding["level"] | null {
  if (findings.some((f) => f.level === "blocking")) return "blocking";
  if (findings.some((f) => f.level === "warning")) return "warning";
  if (findings.length) return "note";
  return null;
}

export function GateChip({
  level,
  count,
  label,
}: {
  level: GateFinding["level"];
  count?: number;
  label?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
        gateTone[level],
      )}
    >
      {label ?? level}
      {count !== undefined && <span className="mono">{count}</span>}
    </span>
  );
}

export function Gauge({
  value,
  markers = [],
  tone = "default",
}: {
  value: number;
  markers?: { at: number; label: string }[];
  tone?: "default" | "warning" | "danger";
}) {
  const color =
    tone === "danger" ? "var(--destructive)" : tone === "warning" ? "var(--warning)" : "var(--info)";
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${Math.min(100, value)}%`, backgroundColor: color }}
      />
      {markers.map((m) => (
        <span
          key={m.label}
          title={m.label}
          className="absolute top-0 h-full w-px bg-foreground/70"
          style={{ left: `${m.at}%` }}
        />
      ))}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string | undefined;
  right?: ReactNode | undefined;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
      <div>
        <h1 className="text-sm font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">{right}</div>
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="panel m-4 px-4 py-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">{body}</p>
    </div>
  );
}

export function ErrorState({ title, body }: { title: string; body: string }) {
  return (
    <div className="m-4 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-6 text-center">
      <p className="text-sm font-medium text-destructive">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">{body}</p>
    </div>
  );
}

export function Banner({
  tone = "warning",
  children,
}: {
  tone?: "warning" | "danger" | "info";
  children: ReactNode;
}) {
  const cls = {
    warning: "border-warning/50 bg-warning/10 text-warning",
    danger: "border-destructive/50 bg-destructive/10 text-destructive",
    info: "border-border-strong bg-surface-2 text-muted-foreground",
  }[tone];
  return (
    <div className={cn("mx-4 mt-3 rounded-md border px-3 py-2 text-xs", cls)}>{children}</div>
  );
}

export function Absent({ label = "not recorded" }: { label?: string }) {
  return <span className="text-[11px] italic text-muted-foreground/70">{label}</span>;
}
