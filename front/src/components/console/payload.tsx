/**
 * The two renderers for a handoff payload — the one api value whose shape
 * nothing checks. `dispatcher/handoff.py` writes back what the role returned
 * and `observability/api/app.py:_phase` passes it through verbatim, so every
 * key of it arrives at a render as `unknown` and is guarded here. ADR 29.
 *
 * Here and not in `routes/tasks.$taskId.tsx`, the one screen that draws them.
 * ADR 30 moved them for a reason that was false — a test file beside that route
 * is allowed, as `-<name>.test.tsx`, because `routeFileIgnorePrefix` defaults
 * to `-`. ADR 45 keeps them here for the reason that holds: `asPathLine` is not
 * a component, and exporting it beside a route's `Route` would cost a tenth
 * `react-refresh/only-export-components` warning (`docs/debt/T-015-D1.md`).
 */
import { Malformed } from "./primitives";
import { cn } from "@/lib/utils";

/**
 * The tone of a phase's own status, which is a second vocabulary and not the
 * task's four: `complete` finished, `partial` finished and left something,
 * `blocked` is the one word both vocabularies share. A revisor's `verdict` reads
 * the same way, and `CHANGES_REQUESTED` is `warning` and not `destructive` —
 * sending a round back is the cycle working. `docs/ui.md` *The tone of a state*.
 *
 * Not in `primitives.tsx` beside `StatusPill`, where the task's four live: one
 * screen shows this one, and the move into this file bought a test, not a
 * second screen. The second screen that shows it is what promotes it.
 */
const phaseTone: Record<string, string> = {
  complete: "text-success border-success/50 bg-success/10",
  partial: "text-warning border-warning/50 bg-warning/10",
  blocked: "text-destructive border-destructive/60 bg-destructive/10",
  APPROVED: "text-success border-success/50 bg-success/10",
  CHANGES_REQUESTED: "text-warning border-warning/50 bg-warning/10",
};

export function PhasePill({ field, value }: { field: string; value: unknown }) {
  // The key set belongs to the role, so a payload without this key is not news
  // and an empty one reads as the role leaving it blank. A key holding
  // something that is not a word is news, and `{value}` would otherwise throw
  // it as a React child. ADR 29.
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string") return <Malformed label={field} got={value} want="text" />;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-[1px] text-[10px] font-medium uppercase tracking-wide",
        // Anything the harness has not said is muted with the string verbatim,
        // the same way `StatusPill` treats a status outside its four.
        phaseTone[value] ?? "text-muted-foreground border-border-strong bg-surface-2",
      )}
    >
      {value}
    </span>
  );
}

/**
 * One labelled sub-list of a handoff payload, taking the value `unknown`.
 *
 * Not fixed by validating in the route: `docs/decisions.md` ADR 10 and ADR 27
 * both rule that a value this harness cannot judge is news to report rather
 * than something the api hides, so the guard belongs where the render is.
 * Three failures are told apart because they are three different facts — the
 * key is not on this payload (nothing), the key holds the wrong kind of thing
 * (`Malformed` naming the key), one item of a good list is the wrong kind
 * (`Malformed` in that item's place, the other items still listed). ADR 29.
 */
export function PhaseList({
  label,
  value,
  line = asLine,
  itemWant = "text",
}: {
  label: string;
  value: unknown;
  line?: (item: unknown) => string | null;
  itemWant?: string;
}) {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value)) {
    return (
      <div>
        <Malformed label={label} got={value} want="a list" />
      </div>
    );
  }
  if (value.length === 0) return null;
  return (
    <div>
      <p className="label-xs">{label}</p>
      <ul className="mt-0.5 space-y-0.5">
        {value.map((item, i) => {
          const text = line(item);
          return (
            <li key={i} className="text-[11px] text-muted-foreground">
              {text === null ? <Malformed got={item} want={itemWant} /> : text}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** A plain list item. The payload says a line of text; anything else is not. */
function asLine(item: unknown): string | null {
  return typeof item === "string" ? item : null;
}

/**
 * A `paths` item, which is the one key of the six whose items are objects.
 * `dispatcher/handoff.py:_pairs` drops a non-dict item and a dict missing
 * either key; this says so instead, because the dispatcher is feeding a prompt
 * and the console is answering an operator asking what the role returned.
 */
export function asPathLine(item: unknown): string | null {
  if (item === null || typeof item !== "object") return null;
  const { path, holds } = item as { path?: unknown; holds?: unknown };
  if (typeof path !== "string" || typeof holds !== "string") return null;
  return `${path} — ${holds}`;
}
