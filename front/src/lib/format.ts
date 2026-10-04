import { type Role } from "./api/types";

/**
 * How long ago, in whole seconds, or `null` when there is nothing to subtract
 * from.
 *
 * A string that is not a timestamp answers `null` rather than `NaN`: a
 * hand-edited heartbeat is a real case (`docs/decisions.md` ADR 10, and ADR 8 for
 * the board already handling it) and the console would otherwise render the word
 * `NaN` at the operator. What to show instead is `HeartbeatDot`'s decision, not
 * this function's — `docs/ui.md` *Staleness is served, never computed* renders the
 * unparseable string verbatim.
 */
export function agoSeconds(iso: string | null, now: number = Date.now()): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.round((now - then) / 1000));
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/**
 * Kept with no caller, deliberately, and the reason is the same one `splitStatus`
 * has below. Its only caller was the phase timeline's `started … · duration` line,
 * and T-013 removed it: the harness records when a phase **ended** and nothing
 * else, so there is no duration to format and differencing two end stamps is not
 * one either (`docs/decisions.md` ADR 27). It comes back unchanged for the first
 * duration the harness persists, which ADR 27 names as a dispatcher task.
 */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "running";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/**
 * The UTC clock time of a stamp, or `—` when there is nothing readable to show.
 *
 * `agoSeconds`'s shape, for `agoSeconds`'s reason. The api serves a phase's
 * `saved_at` verbatim out of a handoff file and validates neither its presence
 * nor its type (`observability/api/app.py:_phase`, and `docs/decisions.md` ADR
 * 27 for why it is served that way), so a hand-edited or truncated file reaches
 * here as `null` or as a string `Date` cannot parse, and `toISOString` answers
 * that with a thrown `RangeError`. The only `errorComponent` in the console is
 * on the root route, so one bad file would take the whole screen where
 * `docs/ui.md` *Absent, empty and broken are three different things* gives it
 * the region. The sentinel is `formatAge`'s, so both halves of a timestamp line
 * degrade the same way.
 */
export function formatClock(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().slice(11, 19);
}

/**
 * Kept with no caller for `formatDuration`'s reason. It formatted both ends of the
 * phase timeline's byte-budget bar, and T-013 removed the bar: `bytes_used` is the
 * same word for two different numbers in the harness and neither is persisted, so
 * `bytes_budget` — which `dispatcher/handoff.py:budget_for` would answer in one
 * line — is a denominator with no numerator (`docs/decisions.md` ADR 27).
 */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

// `isStale` is deleted, not moved. Staleness is served: the api applies
// `heartbeat_ttl_seconds` against its own config and answers `lock_expired`, and a
// console that re-derives it is computing, on stale input, a value it was handed.
// `docs/decisions.md` ADR 18, `docs/ui.md` *Staleness is served, never computed*.
// Its only caller was `routes/pool.tsx`, which now joins the task's own field.

/**
 * Kept with no caller, deliberately. Nothing `/api/tasks` serves has a role in it
 * — `docs/decisions.md` ADR 26 — but `in_progress:<role>` is the shape a *card's*
 * status has, which is the alternative that ADR records for the Board's role
 * lanes, so the parser costs nothing and is here if that route is taken. Widened
 * to `string` for the same reason: a card's status is not a `TaskStatus`.
 */
export function splitStatus(status: string): { lane: string; role: Role | null } {
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
