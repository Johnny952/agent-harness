import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  Banner,
  EmptyState,
  ErrorState,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { InPhaseTable, LearningStatus } from "@/components/console/learnings";
import { learningsQuery } from "@/lib/api/queries";
import { useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/learnings")({
  head: () => ({
    meta: [
      { title: "Learnings inbox — harness operations console" },
      {
        name: "description",
        content:
          "Confirmed, unconfirmed and refuted learnings, showing which rows still reach a running phase.",
      },
      { property: "og:title", content: "Learnings inbox — harness operations console" },
      { property: "og:description", content: "What the harness believes, and how sure it is." },
    ],
  }),
  component: LearningsPage,
});

function LearningsPage() {
  const learnings = useQuery(learningsQuery);
  const searchRef = useSearchHotkey();
  const [q, setQ] = useState("");

  // No sort. `/api/learnings` answers in the order a phase's own table is in —
  // confirmed before unconfirmed, fresh before stale, `ref` as the tie-break —
  // and that order is this screen's subject, so the filter is all that stands
  // between the served rows and the table (`docs/decisions.md` ADR 42). The
  // dependency is the envelope object and not the unwrapped `?? []`, which would
  // be a fresh array on every render
  // (`docs/learnings/a-query-default-feeding-a-usememo-dep-depends-on-the-data-object.md`).
  const rows = useMemo(
    () =>
      (learnings.data?.data ?? []).filter((l) =>
        q ? `${l.ref} ${l.task} ${l.when} ${l.rule}`.toLowerCase().includes(q.toLowerCase()) : true,
      ),
    [learnings.data, q],
  );

  // The rows a phase does not get, counted off the served judgement rather than
  // off a position in any list here: a refuted entry is out for its own reason
  // and this banner is about the cap. The cap itself is read off a row, because
  // the number is `dispatcher/learnings.py:MAX_ROWS` and not the console's.
  const served = learnings.data?.data ?? [];
  const overCap = served.filter((l) => l.status !== "refuted" && !l.in_phase_table);
  const cap = served[0]?.phase_table_cap;

  return (
    <AppShell>
      <PageHeader
        title="Learnings inbox"
        subtitle={
          cap === undefined
            ? "Every trap this project's phases can be handed, in the order a phase sees them."
            : `In the order a phase sees them. Only the first ${cap} are handed to one.`
        }
        right={<RefreshedAt at={learnings.dataUpdatedAt} />}
      />

      {overCap.length > 0 && cap !== undefined && (
        <Banner tone="warning">
          {overCap.length} {overCap.length === 1 ? "entry is" : "entries are"} past the {cap}-row
          cap and reach no phase. The order is the dispatcher&apos;s — confirmed before unconfirmed,
          fresh before stale — so confirming or retiring an entry is what changes which ones get
          there.
        </Banner>
      )}

      <div className="border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter learnings…  /"
          className="mono w-64 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
      </div>

      <WarningBanner warnings={learnings.data?.warnings ?? []} />

      {learnings.isError ? (
        <ErrorState
          title="The learnings tree could not be read"
          body="/api/learnings did not answer, so which traps this project's phases are being handed is unknown rather than empty. Phases still get their table — the dispatcher reads the same directory directly — and the entries themselves are files under the hive's learnings tree."
        />
      ) : learnings.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading learnings…</p>
      ) : rows.length === 0 ? (
        // Two facts and two sentences: the route is wired, so an empty served
        // collection is about the harness, while a filter that removed every row
        // is about the query in the box above. `docs/ui.md` *Absent, empty and
        // broken are three different things* — Empty and No match, in that order.
        <LearningsEmpty served={served.length} query={q} />
      ) : (
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-background">
            <tr className="border-b border-border text-left">
              {["entry", "task", "scope", "status", "when it applies", "rule", "in table"].map(
                (h) => (
                  <th key={h} className="label-xs px-4 py-2 font-normal">
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((l) => (
              <tr
                key={l.ref}
                className={cn(
                  "border-b border-border/50 hover:bg-surface-2/50",
                  // Dimmed for the same reason the status pill is destructive:
                  // the entry is on disk and was written on purpose, and the
                  // point of retiring one is to stop it costing turns rather
                  // than to hide it.
                  l.status === "refuted" && "opacity-45",
                )}
              >
                {/* The `ref` and not an id: `inbox/<slug>.md` is the pointer
                    every prompt and every `refutes:` line already cites, and it
                    is where an operator who wants the Symptom goes — ADR 41
                    serves `rule` and not the body. Text, not a link: the file is
                    in the hive, outside every worktree, and the console has no
                    route to it. */}
                <td className="mono px-4 py-1.5 text-muted-foreground">{l.ref}</td>
                <td className="mono px-4 py-1.5 text-muted-foreground">
                  {l.task || <Absent label="no task" />}
                </td>
                <td className="px-4 py-1.5">
                  <span className="rounded-sm border border-border-strong px-1 text-[10px] uppercase text-muted-foreground">
                    {l.scope}
                  </span>
                </td>
                <td className="px-4 py-1.5">
                  <LearningStatus entry={l} />
                </td>
                {/* An empty string is the harness having recorded nothing there,
                    which is a fact and not a blank cell. ADR 42. */}
                <td className="px-4 py-1.5 text-muted-foreground">
                  {l.when || <Absent label="no trigger line" />}
                </td>
                <td className="px-4 py-1.5">{l.rule || <Absent label="no rule" />}</td>
                <td className="px-4 py-1.5">
                  <InPhaseTable entry={l} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}

/**
 * Which of the two bare states this screen is in, over the served collection and
 * not over the filtered list.
 *
 * `served` is the count `/api/learnings` answered with, so the tests are in the
 * order `docs/ui.md` fixes: a collection that really holds nothing keeps Empty's
 * sentence even with a query still in the box, because the filter is not why
 * there is nothing there. Exported, and taking the two numbers rather than the
 * query result, so the branch can be rendered without a router or a query client
 * (`docs/decisions.md` ADR 43).
 */
export function LearningsEmpty({ served, query }: { served: number; query: string }) {
  if (served === 0)
    return (
      <EmptyState
        title="No phase has written a learning entry"
        body="A phase files one when a trap costs it time, and the auditor of the task that carries it promotes it into the project's own index. Nothing has been filed against this project yet."
      />
    );
  // The query verbatim and the control that brings the rows back, and nothing
  // about the harness: the operator typed the reason this table is bare.
  return (
    <EmptyState
      title={`No learning entry matches “${query}”`}
      body={`Clear the filter box to see ${served === 1 ? "the one entry" : `all ${served} entries`} again. The filter reads the entry, the task, the trigger line and the rule.`}
    />
  );
}
