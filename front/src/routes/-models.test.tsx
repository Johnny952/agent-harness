/**
 * The Settings screen (`docs/decisions.md` ADR 113, ADR 114): every state a read
 * of `GET /api/settings` can produce, and the controls that write through `PUT`
 * and `DELETE /api/settings/<key>`.
 *
 * The reads and writes go through `renderRoute`, so each test runs the real
 * client, `readEnvelope` and `settingsQuery` against a served payload in the
 * writer's shape — `{data: {base, overrides, revision, rows, roles,
 * recent_changes, error}, warnings}` — rather than a component fed a prop, and a
 * write's method and body are what `fetch` was handed. The rows are real keys of
 * `dispatcher/config.py:CONFIG_KEYS` with their real kinds. The unreachable state
 * is the forward's own 503, served with its status, because a 404 from an
 * unserved path would pass it for the wrong reason.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, SETTINGS_NOTE } from "./models";
import { formatDateClock } from "@/lib/format";
import { renderRoute, type Answer, type Sent } from "@/test/render-route";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const ROWS = [
  {
    key: "max_revision_rounds",
    class: "editable",
    kind: "positive_int",
    reason: "A cap that is already validated.",
    value: 3,
    source: "override",
  },
  {
    key: "state_dir",
    class: "read-only",
    kind: null,
    reason: "A path, the same as above.",
    value: "/state",
    source: "config.yaml",
  },
  {
    key: "default_effort",
    class: "editable",
    kind: "effort",
    reason: "It is one of EFFORT_LEVELS, checked.",
    value: null,
    source: "default",
  },
  {
    key: "role_model",
    class: "editable",
    kind: "mapping",
    reason: "A role's model.",
    value: { implementador: "opus", auditor: "sonnet", cartografo: "haiku" },
    source: "config.yaml",
  },
  {
    key: "git_credentials_path",
    class: "credential",
    kind: null,
    reason: "The git credential file.",
    value: "/run/secrets/git",
    source: "config.yaml",
  },
  {
    key: "accounts",
    class: "read-only",
    kind: null,
    reason: "The pool's accounts.",
    value: [{ name: "cuenta1" }, { name: "cuenta2" }],
    source: "config.yaml",
  },
  {
    key: "primary_account",
    class: "editable",
    kind: "string",
    reason: "The account the pool spends first.",
    value: "cuenta1",
    source: "config.yaml",
  },
  {
    key: "gates_enabled",
    class: "editable",
    kind: "bool",
    reason: "Item 0 names it. It is a boolean.",
    value: true,
    source: "default",
  },
  {
    key: "fallback_roles",
    class: "editable",
    kind: "list",
    reason: "The roles that may fall back.",
    value: ["auditor"],
    source: "default",
  },
  {
    key: "default_model",
    class: "editable",
    kind: "string",
    reason: "The model a role with no entry runs on.",
    value: "opus",
    source: "config.yaml",
  },
];

const ROLES = ["arquitecto", "implementador", "auditor"];

const CHANGE = {
  at: "2026-10-16T21:04:05Z",
  actor: "operator",
  via: "console",
  key: "max_revision_rounds",
  op: "set",
  old: 2,
  new: 3,
  revision_before: "r1",
  revision_after: "r2",
};

function settings(over: Record<string, unknown> = {}) {
  return {
    base: { default_model: "opus", role_model: { implementador: "opus", auditor: "sonnet" } },
    overrides: { max_revision_rounds: 3 },
    revision: "r2",
    rows: ROWS,
    roles: { role_model: ROLES, role_effort: ROLES, fallback_roles: ROLES },
    recent_changes: [CHANGE],
    error: null,
    ...over,
  };
}

/** The rows with one key's value replaced, as the writer serves them after a save. */
function rowsWith(key: string, value: unknown) {
  return ROWS.map((r) => (r.key === key ? { ...r, value } : r));
}

async function open(data: unknown, warnings: string[] = []) {
  await renderRoute(Route, "/models", { "/api/settings": { data, warnings } });
  await screen.findByText(SETTINGS_NOTE);
}

/** Open the screen with `writes` served beside the read; the read may be a sequence. */
async function openWith(read: Answer | Answer[], writes: Record<string, Answer> = {}) {
  const r = await renderRoute(Route, "/models", { "/api/settings": read, ...writes });
  await screen.findByText(SETTINGS_NOTE);
  return r;
}

function main(): HTMLElement {
  return document.querySelector("main") as HTMLElement;
}

function line(key: string): HTMLElement {
  return main().querySelector(`tr[data-key="${key}"]`) as HTMLElement;
}

function writes(sent: Sent[]): Sent[] {
  return sent.filter((s) => s.method !== "GET");
}

/**
 * The screen's reads: the sidebar's `/api/projects` and `/api/tasks` (its project
 * list and the list's fallback, ADR 208, ADR 209) and `/api/actions` (its waiting
 * counts, ADR NNN) are not among them.
 */
const SIDEBAR_READS = new Set(["/api/projects", "/api/tasks", "/api/actions"]);
function reads(sent: Sent[]): number {
  return sent.filter((s) => s.method === "GET" && !SIDEBAR_READS.has(s.path)).length;
}

function brokenBanner(): HTMLElement {
  return screen.getByText(/The config does not load/).parentElement as HTMLElement;
}

describe("a successful read", () => {
  it("draws every row with its key, value, source, class and reason", async () => {
    await open(settings());
    const table = main().querySelector("table") as HTMLElement;
    const lines = within(table).getAllByRole("row");
    expect(lines).toHaveLength(ROWS.length + 1);
    const first = line("max_revision_rounds");
    expect(first.textContent).toContain("max_revision_rounds");
    expect(first.textContent).toContain("3");
    expect(first.textContent).toContain("override");
    expect(first.textContent).toContain("editable");
    expect(first.textContent).toContain("A cap that is already validated.");
    expect(line("state_dir").textContent).toContain("config.yaml");
    expect(line("state_dir").textContent).toContain("read-only");
    expect(line("default_effort").textContent).toContain("default");
  });

  it("titles the screen Settings", async () => {
    await open(settings());
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
  });

  it("draws no control on a read-only or a credential row, not even a disabled one", async () => {
    await open(settings());
    for (const key of ["state_dir", "accounts", "git_credentials_path"]) {
      expect(line(key).querySelectorAll("input, select, textarea, button")).toHaveLength(0);
      for (const role of ["button", "textbox", "switch", "combobox", "checkbox", "spinbutton"]) {
        expect(within(line(key)).queryAllByRole(role)).toHaveLength(0);
      }
    }
  });

  it("renders a value by its type", async () => {
    await open(settings());
    expect(line("default_effort").textContent).toContain("not set");
    expect(line("role_model").textContent).toContain(
      '{"implementador":"opus","auditor":"sonnet","cartografo":"haiku"}',
    );
    expect(line("state_dir").textContent).toContain("/state");
  });

  it("sends a credential to the Tokens screen", async () => {
    await open(settings());
    expect(line("git_credentials_path").textContent).toContain(
      "Renewing it belongs to the Tokens screen.",
    );
    expect(line("max_revision_rounds").textContent).not.toContain("Tokens screen");
  });

  it("shows the note, verbatim", async () => {
    await open(settings());
    expect(main().textContent).toContain(SETTINGS_NOTE);
    expect(SETTINGS_NOTE).toBe(
      "A change applies to the next dispatcher run. A task already running keeps the settings it started with. The Pool screen's thresholds follow at the api's next restart.",
    );
  });

  it("shows the served warnings", async () => {
    await open(settings(), ["role_model names an unknown model"]);
    expect(main().textContent).toContain("role_model names an unknown model");
  });

  it("draws a change line with its date, clock, actor, key, op, old and new", async () => {
    await open(settings());
    const changes = main().querySelectorAll("table")[1] as HTMLElement;
    const change = within(changes).getAllByRole("row")[1]!;
    const cells = change.querySelectorAll("td");
    expect(cells[0]!.textContent).toBe("2026-10-16 21:04:05");
    expect(cells[0]!.getAttribute("title")).toBe("2026-10-16T21:04:05Z");
    expect(change.textContent).toContain("operator");
    expect(change.textContent).toContain("max_revision_rounds");
    expect(change.textContent).toContain("set");
    expect(cells[4]!.textContent).toBe("2");
    expect(cells[5]!.textContent).toBe("3");
  });

  it("says no change has been saved when the log is empty", async () => {
    await open(settings({ recent_changes: [] }));
    expect(main().textContent).toContain("No change has been saved from the console");
    expect(main().querySelectorAll("table")).toHaveLength(1);
  });
});

describe("the controls, by key and kind", () => {
  it("gives an editable row its kind's control, a Save, and a Reset only when overridden", async () => {
    await open(settings());
    expect(within(line("max_revision_rounds")).getByRole("spinbutton")).toBeTruthy();
    expect(within(line("gates_enabled")).getByRole("switch")).toBeTruthy();
    expect(within(line("default_model")).getByRole("combobox")).toBeTruthy();
    expect(within(line("fallback_roles")).getAllByRole("checkbox")).toHaveLength(ROLES.length);
    expect(within(line("role_model")).getAllByRole("combobox")).toHaveLength(ROLES.length);
    expect(screen.getByRole("button", { name: "Save default_model" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reset max_revision_rounds" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reset default_model" })).toBeNull();
  });

  it("offers the five effort levels, and the account names for primary_account", async () => {
    await open(settings());
    const effort = within(line("default_effort")).getByRole("combobox") as HTMLSelectElement;
    expect([...effort.options].map((o) => o.value)).toEqual([
      "",
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
    ]);
    const account = within(line("primary_account")).getByRole("combobox") as HTMLSelectElement;
    expect([...account.options].map((o) => o.value)).toEqual(["cuenta1", "cuenta2"]);
    expect(account.value).toBe("cuenta1");
  });

  it("suggests only the model IDs the config already names", async () => {
    await open(settings({ overrides: { role_model: { arquitecto: "opus-4" } } }));
    const list = main().querySelector("datalist#settings-models") as HTMLElement;
    const ids = [...list.querySelectorAll("option")].map((o) => o.getAttribute("value"));
    expect(ids.sort()).toEqual(["opus", "opus-4", "sonnet"]);
  });

  it("sends a switch and the ticked roles in the writer's order", async () => {
    const { sent } = await openWith(
      { data: settings() },
      {
        "PUT /api/settings/gates_enabled": { data: settings() },
        "PUT /api/settings/fallback_roles": { data: settings() },
      },
    );
    fireEvent.click(within(line("gates_enabled")).getByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: "Save gates_enabled" }));
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]!.body).toEqual({ value: false, revision: "r2" });
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Save fallback_roles" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "fallback_roles implementador" }));
    fireEvent.click(screen.getByRole("button", { name: "Save fallback_roles" }));
    await waitFor(() => expect(writes(sent)).toHaveLength(2));
    expect(writes(sent)[1]!.body).toEqual({ value: ["implementador", "auditor"], revision: "r2" });
  });
});

describe("a save", () => {
  it("sends the value and the revision, null as null, and refetches the table", async () => {
    const after = settings({ revision: "r3", rows: rowsWith("max_revision_rounds", 5) });
    const { sent } = await openWith([{ data: settings({ revision: null }) }, { data: after }], {
      "PUT /api/settings/max_revision_rounds": { data: after },
    });
    fireEvent.change(within(line("max_revision_rounds")).getByRole("spinbutton"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save max_revision_rounds" }));
    await waitFor(() => expect(reads(sent)).toBe(2));
    expect(writes(sent)).toEqual([
      {
        method: "PUT",
        path: "/api/settings/max_revision_rounds",
        body: { value: 5, revision: null },
      },
    ]);
    await waitFor(() => expect(line("max_revision_rounds").textContent).toContain("5"));
  });

  it("sends the whole role map, with a role left on (default) taken out of it", async () => {
    const { sent } = await openWith(
      { data: settings() },
      { "PUT /api/settings/role_model": { data: settings() } },
    );
    const auditor = screen.getByRole("combobox", {
      name: "role_model auditor",
    }) as HTMLInputElement;
    expect(auditor.value).toBe("sonnet");
    fireEvent.change(auditor, { target: { value: "" } });
    fireEvent.change(screen.getByRole("combobox", { name: "role_model arquitecto" }), {
      target: { value: "opus" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save role_model" }));
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    // `cartografo` is in the map but not in the writer's role list: it is kept.
    expect(writes(sent)[0]!.body).toEqual({
      value: { cartografo: "haiku", arquitecto: "opus", implementador: "opus" },
      revision: "r2",
    });
  });

  it("disables every control on the page while it is in flight", async () => {
    const { sent, fetchMock } = await openWith({ data: settings() });
    let answer: (r: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save default_model" }));
    const table = main().querySelector("table") as HTMLElement;
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Save default_model" }) as HTMLButtonElement).disabled,
      ).toBe(true),
    );
    const controls = [...table.querySelectorAll("input, select, button")];
    expect(controls.length).toBeGreaterThan(10);
    for (const c of controls) expect((c as HTMLButtonElement).disabled).toBe(true);
    // A second click on another row sends nothing: there is one write at a time.
    fireEvent.click(screen.getByRole("button", { name: "Reset max_revision_rounds" }));
    expect(writes(sent)).toHaveLength(0);
    answer(
      new Response(JSON.stringify({ data: settings(), warnings: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Save default_model" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
  });

  it("sends no number that is empty or not whole, and says why", async () => {
    const { sent } = await openWith({ data: settings() });
    const field = within(line("max_revision_rounds")).getByRole("spinbutton");
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save max_revision_rounds" }));
    expect(line("max_revision_rounds").textContent).toContain("Empty, so nothing was sent");
    fireEvent.change(field, { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save max_revision_rounds" }));
    expect(line("max_revision_rounds").textContent).toContain(
      "1.5 is not a whole number, so nothing was sent.",
    );
    expect(writes(sent)).toHaveLength(0);
  });
});

describe("a refused write", () => {
  it("shows a 422 under its row and keeps the value in the control", async () => {
    const { sent } = await openWith(
      { data: settings() },
      {
        "PUT /api/settings/max_revision_rounds": {
          status: 422,
          error: "max_revision_rounds must be a positive integer",
        },
      },
    );
    const field = within(line("max_revision_rounds")).getByRole("spinbutton") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save max_revision_rounds" }));
    const message = await screen.findByText(/max_revision_rounds must be a positive integer/);
    const under = main().querySelector('tr[data-refusal="max_revision_rounds"]') as HTMLElement;
    expect(under.contains(message)).toBe(true);
    expect(line("max_revision_rounds").nextElementSibling).toBe(under);
    expect(field.value).toBe("0");
    expect(reads(sent)).toBe(1);
  });

  it("quotes a 503 from the forward verbatim under its row", async () => {
    await openWith(
      { data: settings() },
      {
        "PUT /api/settings/default_model": {
          status: 503,
          error: "the config-writer service did not answer",
        },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save default_model" }));
    const message = await screen.findByText(/the config-writer service did not answer/);
    expect(
      (main().querySelector('tr[data-refusal="default_model"]') as HTMLElement).contains(message),
    ).toBe(true);
  });

  it("says the settings changed on a 409, refetches, and drops the edit", async () => {
    const { sent } = await openWith(
      [{ data: settings() }, { data: settings({ revision: "r3" }) }],
      {
        "PUT /api/settings/max_revision_rounds": {
          status: 409,
          error: "the overrides changed since this revision; reload and try again",
        },
      },
    );
    fireEvent.change(within(line("max_revision_rounds")).getByRole("spinbutton"), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save max_revision_rounds" }));
    await screen.findByText(/The settings changed since this page loaded/);
    await waitFor(() => expect(reads(sent)).toBe(2));
    await waitFor(() =>
      expect(
        (within(line("max_revision_rounds")).getByRole("spinbutton") as HTMLInputElement).value,
      ).toBe("3"),
    );
    expect(main().querySelector('tr[data-refusal="max_revision_rounds"]')).toBeNull();
  });
});

describe("a reset", () => {
  it("sends DELETE with the revision and refetches the table", async () => {
    const after = settings({ revision: "r3", overrides: {} });
    const { sent } = await openWith([{ data: settings() }, { data: after }], {
      "DELETE /api/settings/max_revision_rounds": { data: after },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reset max_revision_rounds" }));
    await waitFor(() => expect(reads(sent)).toBe(2));
    expect(writes(sent)).toEqual([
      { method: "DELETE", path: "/api/settings/max_revision_rounds", body: { revision: "r2" } },
    ]);
  });
});

describe("a config that does not load", () => {
  it("quotes the error above a table that is still drawn", async () => {
    await open(settings({ error: "config.yaml: line 3: bad indent" }));
    expect(main().textContent).toContain("config.yaml: line 3: bad indent");
    expect(main().textContent).toContain("the dispatcher refuses every verb until it is");
    expect(main().querySelector("table")).not.toBeNull();
  });

  it("draws no such banner when the config loads", async () => {
    await open(settings());
    expect(main().textContent).not.toContain("refuses every verb");
  });

  it("offers a Reset for every key the overrides hold, editable or not", async () => {
    await open(
      settings({ error: "bad", overrides: { max_revision_rounds: 3, state_dir: "/elsewhere" } }),
    );
    const banner = brokenBanner();
    expect(within(banner).getByRole("button", { name: "Reset max_revision_rounds" })).toBeTruthy();
    expect(within(banner).getByRole("button", { name: "Reset state_dir" })).toBeTruthy();
  });

  it("shows a 422 from the banner's Reset in the banner", async () => {
    const { sent } = await openWith(
      { data: settings({ error: "bad", overrides: { state_dir: "/elsewhere" } }) },
      {
        "DELETE /api/settings/state_dir": {
          status: 422,
          error: "the config still does not load: accounts is empty",
        },
      },
    );
    fireEvent.click(within(brokenBanner()).getByRole("button", { name: "Reset state_dir" }));
    const message = await screen.findByText(/accounts is empty/);
    expect(brokenBanner().contains(message)).toBe(true);
    expect(writes(sent)[0]).toEqual({
      method: "DELETE",
      path: "/api/settings/state_dir",
      body: { revision: "r2" },
    });
  });

  it("says where an overrides file that does not parse is fixed, and offers no Reset", async () => {
    await open(settings({ error: "bad", overrides: null }));
    const banner = brokenBanner();
    expect(banner.textContent).toContain("The overrides file does not parse either");
    expect(within(banner).queryAllByRole("button")).toHaveLength(0);
  });
});

describe("a read that failed", () => {
  it("names the service and both variables and quotes the served message", async () => {
    await renderRoute(Route, "/models", {
      "/api/settings": {
        status: 503,
        error: "CONFIG_WRITER_TOKEN is not set, so the console cannot authenticate.",
      },
    });
    await screen.findByText("The settings could not be read");
    const text = main().textContent ?? "";
    expect(text).toContain("config-writer service");
    expect(text).toContain("CONFIG_WRITER_URL");
    expect(text).toContain("CONFIG_WRITER_TOKEN is not set, so the console cannot authenticate.");
    expect(main().querySelector("table")).toBeNull();
    expect(text).not.toContain(SETTINGS_NOTE);
    expect(main().querySelectorAll("input, select, textarea, button")).toHaveLength(0);
  });
});

describe("a payload of the wrong shape", () => {
  it("names rows that are not a list", async () => {
    await renderRoute(Route, "/models", {
      "/api/settings": { data: settings({ rows: { max_revision_rounds: 3 } }) },
    });
    await screen.findByText(/rows — an object, not a list/);
    // The rows are named, and the change log beside them is still drawn.
    expect(main().textContent).toContain("2026-10-16 21:04:05");
  });

  it("names recent changes that are not a list", async () => {
    await open(settings({ recent_changes: "none" }));
    expect(main().textContent).toMatch(/recent changes — a string, not a list/);
  });

  it("names a row and a change that are not objects, without dropping them", async () => {
    await open(settings({ rows: [...ROWS, 7], recent_changes: [CHANGE, "x"] }));
    expect(main().textContent).toMatch(/row — a number, not an object/);
    expect(main().textContent).toMatch(/change — a string, not an object/);
  });
});

describe("formatDateClock", () => {
  it("is the UTC date and the clock", () => {
    expect(formatDateClock("2026-10-16T21:04:05Z")).toBe("2026-10-16 21:04:05");
  });

  it("is a dash for a stamp that does not parse, or none", () => {
    expect(formatDateClock("yesterday")).toBe("—");
    expect(formatDateClock(null)).toBe("—");
  });
});
