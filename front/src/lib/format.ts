import { HEARTBEAT_STALE_S, type Role, type TaskStatus } from "./api/types";

export function agoSeconds(iso: string | null, now: number = Date.now()): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "running";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function formatClock(iso: string): string {
  const d = new Date(iso);
  return d.toISOString().slice(11, 19);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function isStale(iso: string | null, now: number = Date.now()): boolean {
  const age = agoSeconds(iso, now);
  return age !== null && age > HEARTBEAT_STALE_S;
}

export function splitStatus(status: TaskStatus): { lane: string; role: Role | null } {
  if (status.startsWith("in_progress:")) {
    return { lane: "in_progress", role: status.split(":")[1] as Role };
  }
  return { lane: status, role: null };
}

export const roleColorVar: Record<Role, string> = {
  cartografo: "var(--role-cartografo)",
  arquitecto: "var(--role-arquitecto)",
  implementador: "var(--role-implementador)",
  revisor: "var(--role-revisor)",
  auditor: "var(--role-auditor)",
};

export const roleGlyph: Record<Role, string> = {
  cartografo: "◰",
  arquitecto: "◇",
  implementador: "▶",
  revisor: "◉",
  auditor: "✓",
};
