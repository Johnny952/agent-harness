/**
 * The Settings screen's read-only half (`docs/decisions.md` ADR 113): every state
 * a read of `GET /api/settings` can produce, and no control in any of them.
 *
 * The reads go through `renderRoute`, so each test runs the real client,
 * `readEnvelope` and `settingsQuery` against a served payload in the writer's
 * shape — `{data: {base, overrides, revision, rows, roles, recent_changes,
 * error}, warnings}` — rather than a component fed a prop. The unreachable state
 * is the forward's own 503, served with its status, because a 404 from an
 * unserved path would pass it for the wrong reason.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { Route, SETTINGS_NOTE } from "./models";
import { formatDateClock } from "@/lib/format";
import { renderRoute } from "@/test/render-route";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const ROWS = [
  {
    key: "max_rounds",
    class: "editable",
    kind: "int",
    reason: "How many implementador rounds a task gets.",
    value: 3,
    source: "override",
  },
  {
    key: "base_branch",
    class: "read-only",
    kind: null,
    reason: "Changing it moves every clone.",
    value: "main",
    source: "config.yaml",
  },
  {
    key: "budget_usd",
    class: "editable",
    kind: "float",
    reason: "A ceiling nothing sets by default.",
    value: null,
    source: "default",
  },
  {
    key: "roles.auditor.models",
    class: "editable",
    kind: "list",
    reason: "The auditor's model order.",
    value: ["opus", "sonnet"],
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
];

const CHANGE = {
  at: "2026-10-16T21:04:05Z",
  actor: "operator",
  via: "console",
  key: "max_rounds",
  op: "set",
  old: 2,
  new: 3,
  revision_before: "r1",
  revision_after: "r2",
};

function settings(over: Record<string, unknown> = {}) {
  return {
    base: {},
    overrides: { max_rounds: 3 },
    revision: "r2",
    rows: ROWS,
    roles: { auditor: ["opus", "sonnet"] },
    recent_changes: [CHANGE],
    error: null,
    ...over,
  };
}

async function open(data: unknown, warnings: string[] = []) {
  await renderRoute(Route, "/models", { "/api/settings": { data, warnings } });
  await screen.findByText(SETTINGS_NOTE);
}

function main(): HTMLElement {
  return document.querySelector("main") as HTMLElement;
}

describe("a successful read", () => {
  it("draws every row with its key, value, source, class and reason", async () => {
    await open(settings());
    const table = main().querySelector("table") as HTMLElement;
    const lines = within(table).getAllByRole("row");
    expect(lines).toHaveLength(ROWS.length + 1);
    expect(lines[1]!.textContent).toContain("max_rounds");
    expect(lines[1]!.textContent).toContain("3");
    expect(lines[1]!.textContent).toContain("override");
    expect(lines[1]!.textContent).toContain("editable");
    expect(lines[1]!.textContent).toContain("How many implementador rounds a task gets.");
    expect(lines[2]!.textContent).toContain("config.yaml");
    expect(lines[2]!.textContent).toContain("read-only");
    expect(lines[3]!.textContent).toContain("default");
  });

  it("titles the screen Settings", async () => {
    await open(settings());
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
  });

  it("draws no control, not even a disabled one", async () => {
    await open(settings({ error: "config.yaml: line 3: bad indent" }));
    for (const role of ["button", "textbox", "switch", "combobox", "checkbox", "spinbutton"]) {
      expect(within(main()).queryAllByRole(role)).toHaveLength(0);
    }
    expect(main().querySelectorAll("input, select, textarea, button")).toHaveLength(0);
  });

  it("renders a value by its type", async () => {
    await open(settings());
    const lines = within(main().querySelector("table") as HTMLElement).getAllByRole("row");
    expect(lines[3]!.textContent).toContain("not set");
    expect(lines[4]!.textContent).toContain('["opus","sonnet"]');
    expect(lines[2]!.textContent).toContain("main");
  });

  it("sends a credential to the Tokens screen", async () => {
    await open(settings());
    const lines = within(main().querySelector("table") as HTMLElement).getAllByRole("row");
    expect(lines[5]!.textContent).toContain("Renewing it belongs to the Tokens screen.");
    expect(lines[1]!.textContent).not.toContain("Tokens screen");
  });

  it("shows the note, verbatim", async () => {
    await open(settings());
    expect(main().textContent).toContain(SETTINGS_NOTE);
    expect(SETTINGS_NOTE).toBe(
      "A change applies to the next dispatcher run. A task already running keeps the settings it started with. The Pool screen's thresholds follow at the api's next restart.",
    );
  });

  it("shows the served warnings", async () => {
    await open(settings(), ["roles.auditor.models names an unknown model"]);
    expect(main().textContent).toContain("roles.auditor.models names an unknown model");
  });

  it("draws a change line with its date, clock, actor, key, op, old and new", async () => {
    await open(settings());
    const changes = main().querySelectorAll("table")[1] as HTMLElement;
    const line = within(changes).getAllByRole("row")[1]!;
    const cells = line.querySelectorAll("td");
    expect(cells[0]!.textContent).toBe("2026-10-16 21:04:05");
    expect(cells[0]!.getAttribute("title")).toBe("2026-10-16T21:04:05Z");
    expect(line.textContent).toContain("operator");
    expect(line.textContent).toContain("max_rounds");
    expect(line.textContent).toContain("set");
    expect(cells[4]!.textContent).toBe("2");
    expect(cells[5]!.textContent).toBe("3");
  });

  it("says no change has been saved when the log is empty", async () => {
    await open(settings({ recent_changes: [] }));
    expect(main().textContent).toContain("No change has been saved from the console");
    expect(main().querySelectorAll("table")).toHaveLength(1);
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
  });
});

describe("a payload of the wrong shape", () => {
  it("names rows that are not a list", async () => {
    await renderRoute(Route, "/models", {
      "/api/settings": { data: settings({ rows: { max_rounds: 3 } }) },
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
