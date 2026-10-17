/**
 * The Settings screen: the config writer's table, read-only.
 *
 * The route is still `/models` and the chord still `m` (`docs/decisions.md` ADR
 * 113 part 3) because the Role models screen this replaces is where an operator
 * already goes to change what a role runs on; the title and the nav label say
 * *Settings* because the table is every key the dispatcher reads, roles' models
 * among them. It reads `GET /settings` through the console's settings forward
 * (ADR 112), never the api, and never a mock.
 *
 * **It draws no control** — not one, not a disabled one. ADR 113 part 1 splits
 * `docs/plans/console-config.md` task 3 in two: this half is the forward and
 * every state a read can produce; *Save*, *Reset* (the broken-config banner's
 * included), the 409 banner, the 422 message under a row and the refetch after a
 * write are the follow-up's. A disabled control would be tier 3's floor broken
 * the other way: something on the screen that looks wired and is not.
 *
 * The states are ADR 113 part 4's, in `docs/ui.md`'s terms. A failed read takes
 * the page, because without the table there is nothing true left to draw; a
 * config that does not load is a banner *above* the table, because the table is
 * still the writer's and the follow-up's *Reset* on it is how the operator
 * repairs it.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  Banner,
  EmptyState,
  ErrorState,
  Malformed,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { settingsQuery } from "@/lib/api/queries";
import type { SettingChange, SettingRow } from "@/lib/api/types";
import { formatDateClock } from "@/lib/format";

export const Route = createFileRoute("/models")({
  head: () => ({
    meta: [
      { title: "Settings — harness operations console" },
      {
        name: "description",
        content: "Every setting the dispatcher reads, its effective value and where it comes from.",
      },
      { property: "og:title", content: "Settings — harness operations console" },
      {
        property: "og:description",
        content: "The harness configuration as the config writer serves it.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SettingsPage,
});

/**
 * The plan's three sentences, verbatim (`docs/plans/console-config.md` task 3
 * *The note*). Shown whenever the read succeeded, including with no override,
 * because the question it answers — when does a change take effect — is the
 * one an operator has before the first save and not after.
 */
export const SETTINGS_NOTE =
  "A change applies to the next dispatcher run. A task already running keeps the settings it started with. The Pool screen's thresholds follow at the api's next restart.";

function SettingsPage() {
  const settings = useQuery(settingsQuery);
  const data = settings.data?.data;

  return (
    <AppShell>
      <PageHeader
        title="Settings"
        subtitle="Every key the dispatcher reads, its effective value and the layer it comes from."
        right={<RefreshedAt at={settings.dataUpdatedAt} />}
      />
      {settings.isError ? (
        // ADR 113 part 4, *Unreachable*: the forward's 503 or 504, the writer's
        // 401, a non-JSON answer. All of them are one state because all of them are
        // fixed in the same two places, so both variables are named and the served
        // message — which says which of them it was — is quoted, not paraphrased.
        <ErrorState
          title="The settings could not be read"
          body={
            "The console reads them from the config-writer service at CONFIG_WRITER_URL, " +
            "authenticated with CONFIG_WRITER_TOKEN. It answered: " +
            (settings.error instanceof Error ? settings.error.message : String(settings.error))
          }
        />
      ) : settings.isLoading || !data ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading settings…</p>
      ) : (
        <>
          {data.error !== null && data.error !== undefined && (
            <Banner tone="danger">
              <p>
                The config does not load, and the dispatcher refuses every verb until it is fixed:
              </p>
              <p className="mono mt-1 whitespace-pre-wrap text-[11px]">{String(data.error)}</p>
            </Banner>
          )}
          <WarningBanner warnings={settings.data?.warnings ?? []} />
          <Banner tone="info">{SETTINGS_NOTE}</Banner>
          <SettingsTable rows={data.rows} />
          <RecentChanges changes={data.recent_changes} />
        </>
      )}
    </AppShell>
  );
}

function SettingsTable({ rows }: { rows: unknown }) {
  if (!Array.isArray(rows)) {
    return (
      <div className="p-4">
        <Malformed label="rows" got={rows} want="a list" />
      </div>
    );
  }
  return (
    <div className="p-4">
      <table className="panel w-full text-xs">
        <thead className="label-xs text-left text-muted-foreground">
          <tr className="border-b border-border">
            <th className="px-3 py-2">Key</th>
            <th className="px-3 py-2">Value</th>
            <th className="px-3 py-2">Source</th>
            <th className="px-3 py-2">Class</th>
            <th className="px-3 py-2">Reason</th>
          </tr>
        </thead>
        <tbody>
          {(rows as unknown[]).map((row, i) => (
            <SettingLine key={isRecord(row) ? String(row["key"]) : `#${i}`} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One key. The table's order is the writer's, so nothing here sorts. A row that
 * is not a mapping is named in its own line rather than dropped, for the reason
 * `Malformed` gives: the operator has to see that something arrived.
 */
function SettingLine({ row }: { row: unknown }) {
  if (!isRecord(row)) {
    return (
      <tr className="border-b border-border last:border-0">
        <td className="px-3 py-2" colSpan={5}>
          <Malformed label="row" got={row} want="an object" />
        </td>
      </tr>
    );
  }
  const r = row as Partial<SettingRow>;
  return (
    <tr className="border-b border-border align-top last:border-0">
      <td className="px-3 py-2">
        <Mono>{String(r.key)}</Mono>
      </td>
      <td className="max-w-md break-all px-3 py-2">
        <SettingValue value={r.value} />
      </td>
      <td className="px-3 py-2 text-muted-foreground">{String(r.source)}</td>
      <td className="px-3 py-2 text-muted-foreground">{String(r.class)}</td>
      <td className="px-3 py-2 text-muted-foreground">
        {typeof r.reason === "string" ? r.reason : <Absent />}
        {r.class === "credential" && (
          // `docs/plans/console-config.md` task 3: a credential has no control
          // here in either half, because renewing one is a login, not a value.
          <p className="mt-0.5 text-[10px]">Renewing it belongs to the Tokens screen.</p>
        )}
      </td>
    </tr>
  );
}

/**
 * A value of any JSON type, rendered by type (ADR 113 part 5). `null` is a key
 * nothing sets, which `Absent` says in words; a list or a mapping is compact JSON
 * because that is what an operator would type into the follow-up's control, and
 * a scalar is itself.
 */
function SettingValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <Absent label="not set" />;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <Mono>{String(value)}</Mono>;
  }
  return <Mono>{JSON.stringify(value)}</Mono>;
}

/**
 * The writer's change log, newest first as served. Empty is its own
 * `EmptyState` and not the page's: a config with no console change is the normal
 * starting point, and the table above it is full (ADR 113 part 4, *No overrides*).
 */
function RecentChanges({ changes }: { changes: unknown }) {
  return (
    <section className="pb-4">
      <h2 className="label-xs px-4 pt-2 text-muted-foreground">Recent changes</h2>
      {!Array.isArray(changes) ? (
        <div className="p-4">
          <Malformed label="recent changes" got={changes} want="a list" />
        </div>
      ) : changes.length === 0 ? (
        <EmptyState
          title="No change has been saved from the console"
          body="Every row above comes from config.yaml or the dispatcher's default. A save or a reset made here is logged in this table."
        />
      ) : (
        <div className="px-4 pt-2">
          <table className="panel w-full text-xs">
            <thead className="label-xs text-left text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-3 py-2">Time (UTC)</th>
                <th className="px-3 py-2">Actor</th>
                <th className="px-3 py-2">Key</th>
                <th className="px-3 py-2">Operation</th>
                <th className="px-3 py-2">Old</th>
                <th className="px-3 py-2">New</th>
              </tr>
            </thead>
            <tbody>
              {(changes as unknown[]).map((change, i) => (
                <ChangeLine key={i} change={change} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ChangeLine({ change }: { change: unknown }) {
  if (!isRecord(change)) {
    return (
      <tr className="border-b border-border last:border-0">
        <td className="px-3 py-2" colSpan={6}>
          <Malformed label="change" got={change} want="an object" />
        </td>
      </tr>
    );
  }
  const c = change as Partial<SettingChange>;
  const at = typeof c.at === "string" ? c.at : null;
  return (
    <tr className="border-b border-border align-top last:border-0">
      <td className="mono px-3 py-2 tabular-nums" title={at ?? undefined}>
        {formatDateClock(at)}
      </td>
      <td className="px-3 py-2">{String(c.actor)}</td>
      <td className="px-3 py-2">
        <Mono>{String(c.key)}</Mono>
      </td>
      <td className="px-3 py-2 text-muted-foreground">{String(c.op)}</td>
      <td className="max-w-xs break-all px-3 py-2">
        <SettingValue value={c.old} />
      </td>
      <td className="max-w-xs break-all px-3 py-2">
        <SettingValue value={c.new} />
      </td>
    </tr>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
