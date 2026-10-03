import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { agoSeconds, formatAge, roleColorVar, roleGlyph } from "@/lib/format";
import type { GateFinding, Role, TaskStatus } from "@/lib/api/types";

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

/**
 * The four states the harness writes, and a fifth rendering for anything else.
 *
 * `pending` is `muted` because nothing has happened to the task yet, which is not
 * the same news as something that finished. A status outside the four is possible
 * — the api serves what the task file says and `read_task_file` validates none of
 * it — and renders muted with the string verbatim rather than as an unstyled pill
 * nobody designed. `docs/ui.md` *The tone of a state*, `docs/decisions.md` ADR 26.
 */
export function StatusPill({ status }: { status: TaskStatus }) {
  const muted = "text-muted-foreground border-border-strong bg-surface-2";
  const tone: Record<string, string> = {
    pending: muted,
    in_progress: "text-info border-info/50 bg-info/10",
    blocked: "text-destructive border-destructive/60 bg-destructive/10",
    done: "text-success border-success/50 bg-success/10",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-[1px] text-[10px] font-medium uppercase tracking-wide",
        tone[status] ?? muted,
      )}
    >
      {status}
    </span>
  );
}

/**
 * Whether a lock is stale is the api's judgement, not this component's.
 *
 * `lockExpired` is `lock_expired` on a task row, computed against the expiry
 * window the api holds in its own config; the age beside it is the console's own
 * subtraction and ticks. The three values are three renderings and the `null` is
 * two facts, exactly as `docs/ui.md` *Staleness is served, never computed* writes
 * them. This component compared an age against its own `120` until T-012 —
 * `docs/decisions.md` ADR 18 deleted that number rather than serving it.
 *
 * An account has no heartbeat of its own: the lock an account holds is the lock on
 * the task it is running, so callers on the Pool screen pass the joined task's two
 * fields. ADR 17.
 *
 * `withLabel` suppresses the *ordinary* words — the age, and "no lock" — on the
 * dense surfaces that only have room for the dot. It does not suppress the two
 * pieces of news: a stale lock always says so, and an unparseable heartbeat always
 * shows its string.
 */
export function HeartbeatDot({
  heartbeat,
  lockExpired,
  now,
  withLabel = false,
}: {
  heartbeat: string | null;
  lockExpired: boolean | null;
  now: number;
  withLabel?: boolean;
}) {
  const age = agoSeconds(heartbeat, now);

  if (lockExpired === null) {
    // Nothing to judge. With no heartbeat, a task nobody holds; with a heartbeat
    // that is not a timestamp, a hand-edited card — and the string travels
    // verbatim, because an unparseable heartbeat is the harness's news to report
    // and not the console's to hide (ADR 10).
    if (heartbeat === null) {
      return (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <span className="inline-block size-[7px] rounded-full border border-border-strong" />
          {withLabel && <Absent label="no lock" />}
        </span>
      );
    }
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
        title="The harness recorded a heartbeat this console cannot read as a timestamp."
      >
        <span className="inline-block size-[7px] rounded-full border border-border-strong" />
        <Mono className="text-[10px]">{heartbeat}</Mono>
      </span>
    );
  }

  if (lockExpired) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-medium text-destructive"
        title={`Heartbeat stale — last beat ${heartbeat}`}
      >
        <span className="inline-block size-[7px] animate-pulse rounded-full bg-destructive ring-2 ring-destructive/30" />
        stale {formatAge(age)}
      </span>
    );
  }

  // A live lock is not good news, it is ordinary news: a success dot in muted type.
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
      title={`Heartbeat ${formatAge(age)} ago — last beat ${heartbeat}`}
    >
      <span className="inline-block size-[7px] rounded-full bg-success" />
      {withLabel ? formatAge(age) : null}
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

/**
 * Every warning a screen was handed, in one banner above the content.
 *
 * `docs/ui.md` *A degraded backend is a banner, not a blank screen* is the rule
 * and this is the shape of it. A screen that makes several reads concatenates
 * their warnings into this one banner, because three banners stacked is three
 * queries' implementation detail on an operator's screen; the rows still render
 * beside it, because a warning is a partial and never an error state.
 *
 * Nothing is truncated. `observability/api/app.py:_capped` already holds the list
 * at a hundred and spends its last slot tallying the rest, so a console that
 * truncated a second time would be dropping something nothing will mention again —
 * the region scrolls instead.
 */
export function WarningBanner({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <Banner tone="warning">
      <div className="max-h-32 space-y-0.5 overflow-y-auto">
        {warnings.map((w, i) => (
          <p key={i} className="mono text-[10px] leading-relaxed">
            {w}
          </p>
        ))}
      </div>
    </Banner>
  );
}

export function Absent({ label = "not recorded" }: { label?: string }) {
  return <span className="text-[11px] italic text-muted-foreground/70">{label}</span>;
}
