/**
 * A start's pool floor, as the start dialogs take it (ADR 226, ADR 227): the
 * primary account's free share, in percent, under which the worker holds the
 * start as `waiting: "pool"`. Blank is null, the worker's default; anything
 * else is a whole number from 0 to 100, refused in the form before it is sent,
 * as `dispatcher/actions.py:check_floor` would refuse it.
 */

/** `dispatcher/actions.py:DEFAULT_POOL_FLOOR_PCT`, named in the field's help. */
export const DEFAULT_POOL_FLOOR_PCT = 5;

/** The floor field's text as the routes take it: blank is null, else a whole 0..100. */
export function readFloor(
  text: string,
): { ok: true; value: number | null } | { ok: false; why: string } {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^\d+$/.test(t) || Number(t) > 100) {
    return { ok: false, why: `${t} is not a whole percent from 0 to 100.` };
  }
  return { ok: true, value: Number(t) };
}
