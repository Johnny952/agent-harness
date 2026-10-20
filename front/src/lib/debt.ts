/**
 * The Debt screen's pairing of an index entry with its backlog item, apart from
 * the markup so it is tested as plain functions (`docs/plans/board-service.md`
 * section 8, the Debt row; ADR 141).
 *
 * The pairing is the dispatcher's own: `dispatcher/tasks.py:find_debt_item`
 * matches on `debt_id` alone, under any status, so this does too — the
 * `source` field is not consulted. Where the dispatcher takes the first id of
 * two items that share a `debt_id`, this keeps both: two is a fault in the hive
 * an operator has to see, and picking one would hide the other.
 *
 * Below the pairing: the filter box's match, and the surface overlap an open
 * entry is pointed out by (section 5, *Debt on a surface already in play*).
 */
import type { DebtEntry, Task } from "@/lib/api/types";
import { BACKLOG_STATUSES, compareIds } from "@/lib/backlog";
import { parseDescription } from "@/lib/description";

/**
 * What became of one entry's item.
 *
 * - `not_imported`: no task carries the entry's id.
 * - `backlog`: still `proposed`, `deferred` or `dropped` — not accepted.
 * - `accepted`: on the board under any other status the dispatcher writes, or
 *   merged; `status` says which, and `merged` wins over the status word.
 * - `unknown`: a status this console does not know, shown verbatim rather
 *   than counted as accepted or not.
 */
export type ItemState =
  | { kind: "not_imported" }
  | { kind: "backlog"; status: string }
  | { kind: "accepted"; status: string }
  | { kind: "unknown"; status: string };

/** The board statuses an accepted item can hold (`TaskStatus` less the backlog's). */
export const ACCEPTED_STATUSES: readonly string[] = [
  "pending",
  "in_progress",
  "blocked",
  "conflict",
  "done",
  "epic",
];

export function itemState(task: Task | undefined): ItemState {
  if (!task) return { kind: "not_imported" };
  if (task.merged) return { kind: "accepted", status: "merged" };
  if ((BACKLOG_STATUSES as readonly string[]).includes(task.status))
    return { kind: "backlog", status: task.status };
  if (ACCEPTED_STATUSES.includes(task.status)) return { kind: "accepted", status: task.status };
  return { kind: "unknown", status: String(task.status) };
}

/**
 * Every task that names a debt id, under that id, in id number order. A task
 * with no `debt_id` is left out; an id held by two tasks keeps both.
 */
export function itemsByDebtId(tasks: Task[]): Map<string, Task[]> {
  const out = new Map<string, Task[]>();
  for (const t of tasks) {
    if (!t.debt_id) continue;
    out.set(t.debt_id, [...(out.get(t.debt_id) ?? []), t]);
  }
  for (const list of out.values()) list.sort((a, b) => compareIds(a.task_id, b.task_id));
  return out;
}

/** One entry's items, each with its state, and whether there is more than one. */
export interface Pairing {
  items: { task: Task; state: ItemState }[];
  duplicate: boolean;
}

export function pairEntry(debtId: string, byId: Map<string, Task[]>): Pairing {
  const tasks = byId.get(debtId) ?? [];
  return {
    items: tasks.map((task) => ({ task, state: itemState(task) })),
    duplicate: tasks.length > 1,
  };
}

/** The words a state renders as. */
export function stateLabel(state: ItemState): string {
  switch (state.kind) {
    case "not_imported":
      return "not imported";
    case "backlog":
      return `${state.status} — not accepted`;
    case "accepted":
      return `accepted — ${state.status}`;
    case "unknown":
      return `status ${state.status} — not one the dispatcher writes`;
  }
}

/** The warning one entry's duplicate items raise, naming each of them. */
export function duplicateWarning(debtId: string, pairing: Pairing): string | null {
  if (!pairing.duplicate) return null;
  const ids = pairing.items.map((i) => i.task.task_id).join(", ");
  return `${debtId} is the debt_id of ${pairing.items.length} tasks (${ids}); an entry is filed once, so one of them is a fault in the hive.`;
}

/**
 * Whether the Debt screen's filter box keeps an entry. It reads the four index
 * cells and, for an open entry whose pairing is in, each item's id and the
 * words its state is drawn in (`stateLabel`), "not imported" included: what the
 * row shows is what the box finds. `pairing` is `null` for a resolved entry,
 * which draws no item, and while the tasks read is loading or broken, when the
 * row draws no item either.
 */
export function matchesQuery(entry: DebtEntry, query: string, pairing: Pairing | null): boolean {
  if (!query) return true;
  const words = [entry.id, entry.what, entry.where, entry.card];
  if (pairing) {
    if (pairing.items.length === 0) words.push(stateLabel({ kind: "not_imported" }));
    for (const { task, state } of pairing.items) words.push(task.task_id, stateLabel(state));
  }
  return words.join(" ").toLowerCase().includes(query.toLowerCase());
}

/*
 * Debt on a surface already in play (`docs/plans/board-service.md` section 5):
 * an open entry whose *where* falls on the *Surface* of a task on the board and
 * not merged is pointed out beside that task. Pointed out only — nothing here
 * folds the debt into the task, and the screen still writes nothing.
 *
 * The path rules are `dispatcher/surface.py`'s, mirrored: backticked tokens that
 * look like repo paths, a literal entry holding itself and everything under it by
 * whole segments, `*` and `?` inside one segment and `**` across them, and two
 * entries overlapping when one can name a path the other does. Two departures,
 * both toward saying less, since a pointer that fires on every row is noise:
 *
 * - A surface that names no path is the whole repo to the dispatcher, which errs
 *   toward overlap because a missed one costs a merge conflict. Here it points at
 *   nothing: "this entry is in that task's context" cannot be checked.
 * - The append-only logs are set aside on both sides, as `surface.overlaps` sets
 *   them aside: every task appends to them, so naming one is no shared context.
 */

/** `dispatcher/surface.py:APPEND_ONLY`. */
export const APPEND_ONLY: readonly string[] = [
  "docs/decisions.md",
  "docs/ROADMAP.md",
  "docs/implementations/",
  "docs/learnings/",
];

const BACKTICKED = /`([^`\n]+)`/g;
const NOT_A_PATH = new Set(" \t()=,;:'\"<>|$&{}");
const EXTENSION = /\.[A-Za-z0-9]+$/;

/** `surface._looks_like_path`: a repo path or glob rather than code or a command. */
export function looksLikePath(token: string): boolean {
  if (!token || [...token].some((c) => NOT_A_PATH.has(c))) return false;
  if ("-/~".includes(token[0]!) || token.split("/").includes("..")) return false;
  if (token.includes("/") || token.includes("*") || token.includes("?")) return true;
  return EXTENSION.test(token);
}

function normalize(path: string): string {
  while (path.startsWith("./")) path = path.slice(2);
  return path;
}

function backticked(text: string): string[] {
  return [...text.matchAll(BACKTICKED)].map((m) => m[1]!.trim());
}

/** `surface.parse`: the path entries of a *Surface* section, `[]` for none. */
export function surfacePaths(text: string | null | undefined): string[] {
  if (!text) return [];
  return [...new Set(backticked(text).map(normalize).filter(looksLikePath))];
}

/**
 * The repo paths an entry's *where* names. The index writes them backticked
 * inside prose, often with the symbol after a colon (`dispatcher/debt.py:_rows`,
 * `tests/x.py:test_y`), so the colon and what follows are cut before the path
 * test. A cell that was one code span reaches the api unwrapped
 * (`dispatcher/debt.py:_unwrap_code`), so a cell with no backtick is read as one
 * token. Bare prose words are not read: `and/or` would be a path.
 */
export function wherePaths(where: string): string[] {
  const tokens = where.includes("`") ? backticked(where) : [where.trim()];
  const paths = tokens.map((t) => normalize(t.split(":")[0]!.trim())).filter(looksLikePath);
  return [...new Set(paths)];
}

function isGlob(entry: string): boolean {
  return /[*?[]/.test(entry);
}

/** `surface._glob_regex`. */
function globRegex(pattern: string): RegExp {
  pattern = pattern.replace(/\/+$/, "");
  let out = "";
  let i = 0;
  while (i < pattern.length) {
    if (pattern.startsWith("**/", i)) {
      out += "(?:.*/)?";
      i += 3;
    } else if (pattern.startsWith("**", i)) {
      out += ".*";
      i += 2;
    } else if (pattern[i] === "*") {
      out += "[^/]*";
      i += 1;
    } else if (pattern[i] === "?") {
      out += "[^/]";
      i += 1;
    } else if (pattern[i] === "[" && pattern.indexOf("]", i + 2) !== -1) {
      const end = pattern.indexOf("]", i + 2);
      let body = pattern.slice(i + 1, end);
      if (body.startsWith("!")) body = "^" + body.slice(1);
      out += "[" + body.replace(/\\/g, "\\\\") + "]";
      i = end + 1;
    } else {
      out += pattern[i]!.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
      i += 1;
    }
  }
  return new RegExp("^" + out + "$");
}

function staticPrefix(glob: string): string {
  const segments: string[] = [];
  for (const segment of glob.split("/")) {
    if (isGlob(segment)) break;
    segments.push(segment);
  }
  return segments.join("/");
}

/** `surface._under`: `path` is the entry or under it, by whole segments. */
function under(path: string, entry: string): boolean {
  const root = entry.replace(/\/+$/, "");
  if (!root) return true;
  path = path.replace(/\/+$/, "");
  return path === root || path.startsWith(root + "/");
}

function entryCovers(entry: string, path: string): boolean {
  return isGlob(entry) ? globRegex(entry).test(path.replace(/\/+$/, "")) : under(path, entry);
}

/** `surface._entries_overlap`, erring toward yes between globs. */
function entriesOverlap(a: string, b: string): boolean {
  if (!isGlob(a) && !isGlob(b)) return under(a, b) || under(b, a);
  const [x, y] = isGlob(a) && !isGlob(b) ? [b, a] : [a, b];
  // Now y is a glob.
  if (!isGlob(x) && entryCovers(y, x)) return true;
  const xRoot = isGlob(x) ? staticPrefix(x) : x;
  const yRoot = staticPrefix(y);
  return under(xRoot, yRoot) || under(yRoot, xRoot);
}

function isAppendOnly(path: string): boolean {
  return APPEND_ONLY.some((shared) => under(normalize(path), shared));
}

/**
 * Whether a *where*'s paths fall on a surface's: some pair of them overlaps,
 * once the append-only logs are set aside. Either side naming no path is `false`
 * here, not the dispatcher's whole repo (the note above).
 */
export function fallsOn(where: string[], surface: string[]): boolean {
  const w = where.filter((p) => !isAppendOnly(p));
  const s = surface.filter((p) => !isAppendOnly(p));
  return w.some((x) => s.some((y) => entriesOverlap(x, y)));
}

/** A task on the board and not merged, with the paths its *Surface* names. */
export interface InPlay {
  task: Task;
  surface: string[];
}

/**
 * The tasks an entry can be pointed at: accepted (`itemState`) and not merged,
 * with a description that parses (`lib/description.ts`, the dispatcher's rules)
 * and a *Surface* naming at least one path. A description that does not parse
 * has no surface the harness would read, so it is left out rather than guessed
 * at. In id number order.
 */
export function tasksInPlay(tasks: Task[]): InPlay[] {
  const out: InPlay[] = [];
  for (const task of tasks) {
    const state = itemState(task);
    if (state.kind !== "accepted" || state.status === "merged" || !task.description) continue;
    const parsed = parseDescription(task.description);
    if (!parsed.ok) continue;
    const surface = surfacePaths(parsed.sections.find((s) => s.name === "Surface")?.body);
    if (surface.length) out.push({ task, surface });
  }
  return out.sort((a, b) => compareIds(a.task.task_id, b.task.task_id));
}

/**
 * The tasks in play an entry's *where* falls on. The entry's own item is left
 * out: its surface was written from this very entry, so it would always match
 * and point at what the item column already shows.
 */
export function surfaceOverlaps(entry: DebtEntry, inPlay: InPlay[]): Task[] {
  const where = wherePaths(entry.where);
  if (!where.length) return [];
  return inPlay
    .filter(({ task, surface }) => task.debt_id !== entry.id && fallsOn(where, surface))
    .map(({ task }) => task);
}
