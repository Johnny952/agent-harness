/**
 * The two cells of a learning row whose whole content is a served judgement.
 *
 * Here and not in `routes/learnings.tsx`, the one screen that draws them, for
 * ADR 30's reason — a test could not reach a component under `src/routes/`.
 * That reason was wrong: `routeFileIgnorePrefix` defaults to `-`, so a test may
 * sit beside its route. `docs/decisions.md` ADR 45 narrows ADR 30 and its last
 * bullet is why these two do not move back.
 *
 * Both of them render `/api/learnings` fields verbatim and derive nothing:
 * `stale` and `in_phase_table` are the api's judgements against the config it
 * holds and the dispatcher's own `MAX_ROWS`, so a console that re-ranked rows to
 * work either of them out would be computing, on stale input, a value it was
 * handed (`docs/ui.md` *Staleness is served, never computed*,
 * `docs/decisions.md` ADR 41 and ADR 42).
 *
 * **Components only in this file.** A non-component export here costs a tenth
 * `react-refresh/only-export-components` warning and this console's bar is the
 * nine it already carries — `docs/debt/T-015-D1.md`.
 */
import { Mono } from "./primitives";
import type { LearningEntry } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The confirmation status, with `stale` as a qualifier beside it.
 *
 * `status` is `str()`-derived on the api side, so a word outside the harness's
 * three is still the file's own answer and renders verbatim and muted, the way
 * `StatusPill` treats a fifth task status and `PhasePill` an unknown verdict
 * (`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`).
 *
 * The qualifier reads `(stale)` rather than recolouring the pill, because it is
 * a different axis: an entry a second task has confirmed is still confirmed
 * under a permission surface that has since changed, and the phase table a
 * dispatch renders says exactly this — `confirmed (stale)`.
 */
export function LearningStatus({ entry }: { entry: LearningEntry }) {
  const muted = "text-muted-foreground border-border-strong bg-surface-2";
  const tone: Record<string, string> = {
    confirmed: "text-success border-success/50 bg-success/10",
    unconfirmed: "text-warning border-warning/50 bg-warning/10",
    refuted: "text-destructive border-destructive/50 bg-destructive/10",
  };
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={cn(
          "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase",
          tone[entry.status] ?? muted,
        )}
      >
        {entry.status}
      </span>
      {entry.stale && (
        <span
          className="text-[10px] italic text-muted-foreground"
          title="Written under a permission surface this harness no longer has. Shown, and not counted as evidence."
        >
          (stale)
        </span>
      )}
    </span>
  );
}

/**
 * Whether this row reaches a running phase, and which of the two reasons it does
 * not.
 *
 * `in_phase_table` is `handed(eligible(…))` on the api's side and never an index
 * into any order this screen holds. The two falses are two different facts and
 * are told apart: a refuted entry is dropped before the cap is applied at all,
 * and a row the cap cut is one a confirmation or a retirement elsewhere would
 * let through. `phase_table_cap` is read off the row rather than from a constant
 * here, because the number is `dispatcher/learnings.py:MAX_ROWS` and the console
 * cannot see it change (ADR 42).
 */
export function InPhaseTable({ entry }: { entry: LearningEntry }) {
  if (entry.status === "refuted") {
    return (
      <span
        className="text-[10px] italic text-muted-foreground"
        title="A refuted entry is dropped before the cap is applied, so it is never handed to a phase."
      >
        refuted — not handed to phases
      </span>
    );
  }
  if (entry.in_phase_table) {
    return <Mono className="text-[10px] text-success">in table</Mono>;
  }
  return <span className="text-[10px] text-warning">over the {entry.phase_table_cap} cap</span>;
}
