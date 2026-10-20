/**
 * A start's spending limit, as the start dialogs take it: in measured tokens,
 * for *Start epic* the run's (ADR 218) and for *Start development* the task's
 * own (ADR 230). Blank is null, no limit; anything else is a whole number of at
 * least 1, refused in the form before it is sent, as
 * `dispatcher/actions.py:check_limit` would refuse it. A paused epic run passes
 * what it has `spent`, and a new limit must be over it.
 */

export type LimitReading = { ok: true; value: number | null } | { ok: false; why: string };

/** The limit field's text as the routes take it: blank is null, else a whole number of at least 1. */
export function readLimit(text: string, spent: number | null = null): LimitReading {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^\d+$/.test(t) || Number(t) < 1) {
    return { ok: false, why: `${t} is not a whole number of at least 1.` };
  }
  const value = Number(t);
  if (spent !== null && value <= spent) {
    return { ok: false, why: `The run has already spent ${spent} measured tokens.` };
  }
  return { ok: true, value };
}
