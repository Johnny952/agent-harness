/**
 * The Settings screen: the config writer's table, and a control on every key it
 * lets the console change.
 *
 * The route is still `/models` and the chord still `m` (`docs/decisions.md` ADR
 * 113 part 3) because the Role models screen this replaces is where an operator
 * already goes to change what a role runs on; the title and the nav label say
 * *Settings* because the table is every key the dispatcher reads, roles' models
 * among them. It reads `GET /settings` and writes `PUT` and `DELETE
 * /settings/<key>` through the console's settings forward (ADR 112), never the
 * api, and never a mock.
 *
 * **A control is drawn by the row's `class` and `kind`, and only there.** An
 * `editable` row gets its kind's control and a *Save*; one whose source is
 * `override` also gets a *Reset*. A `read-only` or `credential` row gets none —
 * not a disabled one, because a control that looks wired and is not breaks tier
 * 3's floor (ADR 113 part 1). The broken-config banner offers a *Reset* for every
 * key the overrides file holds, editable or not, because that is how an operator
 * takes back an override that made the config stop loading (ADR 110).
 *
 * **One write at a time, and every write carries the last read's `revision`.**
 * While one is in flight every control on the page is disabled, so two saves can
 * never race each other to the writer. A 200 refetches the table; a 409 says the
 * file changed under the page, refetches and drops the edit; any other refusal is
 * the writer's message under its row with the edit kept (ADR 114).
 *
 * The states are ADR 113 part 4's, in `docs/ui.md`'s terms. A failed read takes
 * the page, because without the table there is nothing true left to draw — and
 * no control, because a save needs a revision; a config that does not load is a
 * banner *above* the table, because the table is still the writer's and *Reset*
 * on it is how the operator repairs it.
 */
import { useState, type ReactNode } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import * as api from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import { settingsQuery } from "@/lib/api/queries";
import { EFFORT_LEVELS } from "@/lib/api/types";
import type { SettingChange, SettingRow, Settings } from "@/lib/api/types";
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

/** The string keys whose value is a model ID, and so take the model suggestions. */
const MODEL_KEYS = new Set(["default_model", "mapping_model"]);

/** One write: a save with its value, or a reset. `origin` is where a refusal is shown. */
type Change =
  | { op: "set"; key: string; value: unknown; origin: string }
  | { op: "reset"; key: string; origin: string };
type Write = Change & { revision: string | null };

/** Where the broken-config banner's own refusals are kept, beside the rows' keys. */
const BANNER = "\u0000banner";

function SettingsPage() {
  const settings = useQuery(settingsQuery);
  const data = settings.data?.data;
  const qc = useQueryClient();
  // A refusal's message, by the row (or the banner) that made the write.
  const [refusals, setRefusals] = useState<Record<string, string>>({});
  const [stale, setStale] = useState(false);
  // Bumped on a 409, so every row remounts and drops its edit even if the
  // refetch happened to serve the same revision.
  const [epoch, setEpoch] = useState(0);

  const refetch = () => void qc.invalidateQueries({ queryKey: settingsQuery.queryKey });

  const write = useMutation({
    mutationFn: (w: Write) =>
      w.op === "set"
        ? api.putSetting(w.key, w.value, w.revision)
        : api.resetSetting(w.key, w.revision),
    onMutate: (w) => {
      setStale(false);
      setRefusals((all) => without(all, w.origin));
    },
    onSuccess: refetch,
    onError: (e, w) => {
      if (e instanceof ApiError && e.status === 409) {
        setStale(true);
        setEpoch((n) => n + 1);
        refetch();
        return;
      }
      // 422, 400, 500 and the forward's 503 and 504: the writer's own message,
      // verbatim, where the operator made the change. The edit stays.
      setRefusals((all) => ({ ...all, [w.origin]: e instanceof Error ? e.message : String(e) }));
    },
  });

  // A write in flight, or the refetch after one: either way the revision a new
  // save would send is about to be wrong, so nothing can be sent.
  const busy = write.isPending || settings.isFetching;
  const revision = data?.revision ?? null;
  const send = (c: Change) => write.mutate({ ...c, revision });

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
            <BrokenConfig
              error={String(data.error)}
              overrides={data.overrides}
              busy={busy}
              refusal={refusals[BANNER]}
              onReset={(key) => send({ op: "reset", key, origin: BANNER })}
            />
          )}
          {stale && (
            <Banner tone="warning">
              The settings changed since this page loaded, so that change was not saved. The table
              below is the current one; make the change again on it.
            </Banner>
          )}
          <WarningBanner warnings={settings.data?.warnings ?? []} />
          <Banner tone="info">{SETTINGS_NOTE}</Banner>
          <SettingsTable
            data={data}
            busy={busy}
            refusals={refusals}
            epoch={epoch}
            onSave={(key, value) => send({ op: "set", key, value, origin: key })}
            onReset={(key) => send({ op: "reset", key, origin: key })}
          />
          <RecentChanges changes={data.recent_changes} />
        </>
      )}
    </AppShell>
  );
}

/**
 * The config's own error, and a *Reset* for every key the overrides file holds.
 *
 * Every key, not only the editable ones: a reset of a non-editable key the file
 * holds is the writer's way out of an override written by hand (ADR 110). An
 * overrides file that does not parse has no key to offer — every write is a 422
 * then — so the banner says where it is fixed instead.
 */
function BrokenConfig({
  error,
  overrides,
  busy,
  refusal,
  onReset,
}: {
  error: string;
  overrides: Settings["overrides"] | undefined;
  busy: boolean;
  refusal: string | undefined;
  onReset: (key: string) => void;
}) {
  const held = isRecord(overrides) ? Object.keys(overrides) : null;
  return (
    <Banner tone="danger">
      <p>The config does not load, and the dispatcher refuses every verb until it is fixed:</p>
      <p className="mono mt-1 whitespace-pre-wrap text-[11px]">{error}</p>
      {held === null ? (
        <p className="mt-2">
          The overrides file does not parse either, so no reset can be made from here; it is fixed
          on the host.
        </p>
      ) : held.length > 0 ? (
        <div className="mt-2">
          <p>Taking an override back may fix it:</p>
          <ul className="mt-1 flex flex-wrap gap-2">
            {held.map((key) => (
              <li key={key} className="flex items-center gap-1">
                <Mono>{key}</Mono>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  aria-label={`Reset ${key}`}
                  onClick={() => onReset(key)}
                >
                  Reset
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {refusal !== undefined && (
        <p role="alert" className="mono mt-2 whitespace-pre-wrap text-[11px]">
          {refusal}
        </p>
      )}
    </Banner>
  );
}

function SettingsTable({
  data,
  busy,
  refusals,
  epoch,
  onSave,
  onReset,
}: {
  data: Settings;
  busy: boolean;
  refusals: Record<string, string>;
  epoch: number;
  onSave: (key: string, value: unknown) => void;
  onReset: (key: string) => void;
}) {
  const rows = data.rows;
  if (!Array.isArray(rows)) {
    return (
      <div className="p-4">
        <Malformed label="rows" got={rows} want="a list" />
      </div>
    );
  }
  const context = {
    roles: isRecord(data.roles) ? data.roles : {},
    models: modelSuggestions(data),
    accounts: accountNames(rows),
  };
  return (
    <div className="p-4">
      <datalist id="settings-models">
        {context.models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
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
            <SettingLine
              // The revision and the epoch in the key remount a row's control on
              // every new table, so an edit never outlives the value it was made on.
              key={`${isRecord(row) ? String(row["key"]) : `#${i}`}@${data.revision}@${epoch}`}
              row={row}
              context={context}
              busy={busy}
              refusal={isRecord(row) ? refusals[String(row["key"])] : undefined}
              onSave={onSave}
              onReset={onReset}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What a row's control needs from beyond the row. */
interface Context {
  roles: Record<string, unknown>;
  models: string[];
  accounts: string[];
}

/**
 * One key. The table's order is the writer's, so nothing here sorts. A row that
 * is not a mapping is named in its own line rather than dropped, for the reason
 * `Malformed` gives: the operator has to see that something arrived. A refusal
 * for the row is a second line under it.
 */
function SettingLine({
  row,
  context,
  busy,
  refusal,
  onSave,
  onReset,
}: {
  row: unknown;
  context: Context;
  busy: boolean;
  refusal: string | undefined;
  onSave: (key: string, value: unknown) => void;
  onReset: (key: string) => void;
}) {
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
  const key = String(r.key);
  return (
    <>
      <tr className="border-b border-border align-top last:border-0" data-key={key}>
        <td className="px-3 py-2">
          <Mono>{key}</Mono>
        </td>
        <td className="max-w-md break-all px-3 py-2">
          <SettingValue value={r.value} />
          {r.class === "editable" && (
            <SettingControl
              row={r}
              context={context}
              busy={busy}
              onSave={(value) => onSave(key, value)}
              onReset={r.source === "override" ? () => onReset(key) : undefined}
            />
          )}
        </td>
        <td className="px-3 py-2 text-muted-foreground">{String(r.source)}</td>
        <td className="px-3 py-2 text-muted-foreground">{String(r.class)}</td>
        <td className="px-3 py-2 text-muted-foreground">
          {typeof r.reason === "string" ? r.reason : <Absent />}
          {r.class === "credential" && (
            // `docs/plans/console-config.md` task 3: a credential has no control
            // here, because renewing one is a login, not a value.
            <p className="mt-0.5 text-[10px]">Renewing it belongs to the Tokens screen.</p>
          )}
        </td>
      </tr>
      {refusal !== undefined && (
        <tr className="border-b border-border last:border-0" data-refusal={key}>
          <td className="px-3 pb-2" colSpan={5}>
            <p role="alert" className="mono whitespace-pre-wrap text-[11px] text-destructive">
              {key}: {refusal}
            </p>
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * An editable row's control, by its key and then its kind, with *Save* and, on
 * an overridden row, *Reset*. The edit lives here and nowhere else, so a refusal
 * other than a 409 leaves it as the operator typed it.
 *
 * A number field that is empty or not a whole number is not sent: the row says
 * why and no request is made. Its range is the writer's to judge — a 0 for a
 * positive key is its 422, under the row — because a second copy of the rule
 * here would drift from `dispatcher/config.py`. A kind with no control of its
 * own (a generic list or mapping, or one this console does not know) says so and
 * keeps *Reset*, which is still a true action on it.
 */
function SettingControl({
  row,
  context,
  busy,
  onSave,
  onReset,
}: {
  row: Partial<SettingRow>;
  context: Context;
  busy: boolean;
  onSave: (value: unknown) => void;
  onReset: (() => void) | undefined;
}) {
  const key = String(row.key);
  const value = row.value;
  const [draft, setDraft] = useState<unknown>(() =>
    initialDraft(key, row.kind ?? null, value, context),
  );
  const [invalid, setInvalid] = useState<string | null>(null);

  let control: ReactNode;
  let toSend: () => { ok: true; value: unknown } | { ok: false; why: string };

  if (key === "role_model" || key === "role_effort") {
    const roles = roleList(context.roles[key]);
    const lines = draft as Record<string, string>;
    control = (
      <div className="mt-1 grid gap-1">
        {roles.map((role) => (
          <label key={role} className="flex items-center gap-2">
            <span className="mono w-28 shrink-0 text-[11px] text-muted-foreground">{role}</span>
            {key === "role_model" ? (
              <Input
                aria-label={`${key} ${role}`}
                className="h-7 text-xs"
                list="settings-models"
                placeholder="(default)"
                disabled={busy}
                value={lines[role] ?? ""}
                onChange={(e) => setDraft({ ...lines, [role]: e.target.value })}
              />
            ) : (
              <NativeSelect
                label={`${key} ${role}`}
                disabled={busy}
                value={lines[role] ?? ""}
                options={["", ...EFFORT_LEVELS]}
                onChange={(v) => setDraft({ ...lines, [role]: v })}
              />
            )}
          </label>
        ))}
        <p className="text-[10px] text-muted-foreground">
          {key === "role_model"
            ? "(default) — an empty field — takes the role out of the map, and it runs on default_model."
            : "(default) takes the role out of the map, and it runs on default_effort."}
        </p>
      </div>
    );
    toSend = () => {
      // The whole map is sent, as the writer replaces it whole: the current
      // entries for roles this list does not name are kept, and a role left on
      // *(default)* is not in it.
      const out: Record<string, string> = {};
      const current = isRecord(value) ? value : {};
      for (const [role, v] of Object.entries(current)) {
        if (!roles.includes(role) && typeof v === "string") out[role] = v;
      }
      for (const role of roles) {
        const v = (lines[role] ?? "").trim();
        if (v !== "") out[role] = v;
      }
      return { ok: true, value: out };
    };
  } else if (key === "fallback_roles") {
    const roles = roleList(context.roles[key]);
    const picked = draft as string[];
    control = (
      <div className="mt-1 flex flex-wrap gap-3">
        {roles.map((role) => (
          <label key={role} className="flex items-center gap-1.5">
            <Checkbox
              aria-label={`${key} ${role}`}
              disabled={busy}
              checked={picked.includes(role)}
              onCheckedChange={(on) =>
                setDraft(on === true ? [...picked, role] : picked.filter((p) => p !== role))
              }
            />
            <span className="mono text-[11px]">{role}</span>
          </label>
        ))}
      </div>
    );
    // In the writer's role order, whatever order the boxes were ticked in.
    toSend = () => ({ ok: true, value: roles.filter((r) => picked.includes(r)) });
  } else if (key === "primary_account") {
    const names = context.accounts;
    const current = draft as string;
    control = (
      <div className="mt-1">
        <NativeSelect
          label={key}
          disabled={busy}
          value={current}
          options={names.includes(current) ? names : [current, ...names]}
          onChange={setDraft}
        />
      </div>
    );
    toSend = () => ({ ok: true, value: current });
  } else if (row.kind === "bool") {
    control = (
      <div className="mt-1">
        <Switch
          aria-label={key}
          disabled={busy}
          checked={draft === true}
          onCheckedChange={(on) => setDraft(on)}
        />
      </div>
    );
    toSend = () => ({ ok: true, value: draft === true });
  } else if (row.kind === "positive_int" || row.kind === "non_negative_int") {
    const text = draft as string;
    control = (
      <Input
        aria-label={key}
        type="number"
        step={1}
        className="mt-1 h-7 w-32 text-xs"
        disabled={busy}
        value={text}
        onChange={(e) => setDraft(e.target.value)}
      />
    );
    toSend = () => {
      const t = text.trim();
      if (t === "") return { ok: false, why: "Empty, so nothing was sent: enter a whole number." };
      if (!/^-?\d+$/.test(t)) {
        return { ok: false, why: `${t} is not a whole number, so nothing was sent.` };
      }
      return { ok: true, value: Number(t) };
    };
  } else if (row.kind === "effort") {
    const current = draft as string;
    control = (
      <div className="mt-1">
        <NativeSelect
          label={key}
          disabled={busy}
          value={current}
          options={
            (EFFORT_LEVELS as readonly string[]).includes(current)
              ? [...EFFORT_LEVELS]
              : [current, ...EFFORT_LEVELS]
          }
          onChange={setDraft}
        />
      </div>
    );
    toSend = () => ({ ok: true, value: current });
  } else if (row.kind === "string") {
    const text = draft as string;
    control = (
      <Input
        aria-label={key}
        className="mt-1 h-7 text-xs"
        list={MODEL_KEYS.has(key) ? "settings-models" : undefined}
        disabled={busy}
        value={text}
        onChange={(e) => setDraft(e.target.value)}
      />
    );
    toSend = () => ({ ok: true, value: text });
  } else {
    return (
      <div className="mt-1">
        <p className="text-[10px] text-muted-foreground">
          This console has no control for a{" "}
          {row.kind === null || row.kind === undefined
            ? "key with no kind"
            : `${String(row.kind)} key`}
          .
        </p>
        {onReset && <RowButtons busy={busy} name={key} onReset={onReset} />}
      </div>
    );
  }

  return (
    <div className="mt-1">
      {control}
      <RowButtons
        busy={busy}
        name={key}
        onSave={() => {
          const out = toSend();
          if (!out.ok) {
            setInvalid(out.why);
            return;
          }
          setInvalid(null);
          onSave(out.value);
        }}
        onReset={onReset}
      />
      {invalid !== null && (
        <p role="alert" className="mt-1 text-[11px] text-destructive">
          {invalid}
        </p>
      )}
    </div>
  );
}

function RowButtons({
  busy,
  name,
  onSave,
  onReset,
}: {
  busy: boolean;
  name: string;
  onSave?: () => void;
  onReset: (() => void) | undefined;
}) {
  return (
    <div className="mt-1 flex gap-2">
      {onSave && (
        <Button size="sm" disabled={busy} aria-label={`Save ${name}`} onClick={onSave}>
          Save
        </Button>
      )}
      {onReset && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          aria-label={`Reset ${name}`}
          onClick={onReset}
        >
          Reset
        </Button>
      )}
    </div>
  );
}

/**
 * A native `<select>`, the console's own idiom (`tail.tsx`, `index.tsx`) rather
 * than the Radix one in `ui/select.tsx`: the options are a handful of words, and
 * a native control is one a keyboard and a test both drive as-is. `""` is shown
 * as *(default)*.
 */
function NativeSelect({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className="mono rounded-sm border border-border bg-surface px-1.5 py-1 text-[11px] text-foreground disabled:cursor-not-allowed disabled:opacity-50"
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o === "" ? "(default)" : o}
        </option>
      ))}
    </select>
  );
}

/** A control's starting edit: the row's value, in the shape its control holds. */
function initialDraft(key: string, kind: string | null, value: unknown, context: Context): unknown {
  if (key === "role_model" || key === "role_effort") {
    const map = isRecord(value) ? value : {};
    const lines: Record<string, string> = {};
    for (const role of roleList(context.roles[key])) {
      const v = map[role];
      lines[role] = typeof v === "string" ? v : "";
    }
    return lines;
  }
  if (key === "fallback_roles") {
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  }
  if (kind === "bool") return value === true;
  if (value === null || value === undefined) return "";
  return String(value);
}

/**
 * The model IDs the config already names, for the model fields' suggestions:
 * `default_model`, `mapping_model` and every `role_model` value, in the base
 * file and the overrides. Nothing else — the console invents no list of models.
 */
function modelSuggestions(data: Settings): string[] {
  const found = new Set<string>();
  for (const layer of [data.base, data.overrides]) {
    if (!isRecord(layer)) continue;
    for (const k of ["default_model", "mapping_model"]) {
      const v = layer[k];
      if (typeof v === "string" && v !== "") found.add(v);
    }
    const map = layer["role_model"];
    if (isRecord(map)) {
      for (const v of Object.values(map)) if (typeof v === "string" && v !== "") found.add(v);
    }
  }
  return [...found];
}

/** The account names `primary_account` can take: the `accounts` row's `name`s. */
function accountNames(rows: unknown[]): string[] {
  const row = rows.find((r) => isRecord(r) && r["key"] === "accounts");
  const value = isRecord(row) ? row["value"] : null;
  if (!Array.isArray(value)) return [];
  return value.flatMap((a) => (isRecord(a) && typeof a["name"] === "string" ? [a["name"]] : []));
}

function roleList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function without(all: Record<string, string>, key: string): Record<string, string> {
  const rest = { ...all };
  delete rest[key];
  return rest;
}

/**
 * A value of any JSON type, rendered by type (ADR 113 part 5). `null` is a key
 * nothing sets, which `Absent` says in words; a list or a mapping is compact JSON
 * because that is how the writer holds it, and a scalar is itself.
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
