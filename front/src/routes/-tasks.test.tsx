/**
 * The task page's header, read off the mounted route. `TaskDetailPage` builds
 * the subtitle itself and hands it to no component of its own, so there is
 * nothing to export and render alone: the page is mounted with `renderRoute`
 * and the header is read where the operator reads it. Three arms, one test
 * each: a task with no `description.md` says *no description*, a task with a
 * description shows it, and a card the api could not read claims neither.
 * `docs/debt/T-034-D1.md`.
 *
 * The page asks for five paths and all five are served, because one left out
 * answers 404 and the page draws that region's error state:
 * `docs/implementations/T-035.md` *Every read the page makes*.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { Route } from "./tasks.$taskId";
import { renderRoute } from "@/test/render-route";
import type { TaskDetail } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const DESCRIPTION = "the pool screen says which account holds a lock";

/**
 * A task nobody holds, with nothing below the header that could repeat the
 * description: no dependencies, an empty body.
 */
const TASK: TaskDetail = {
  task_id: "T-101",
  status: "pending",
  owner: null,
  depends_on: [],
  heartbeat: null,
  description: DESCRIPTION,
  resolved_debt: [],
  lock_expired: null,
  phase: null,
  priority: "normal",
  source: null,
  tags: [],
  rank: null,
  debt_id: null,
  body: "",
};

/** The page at `/tasks/T-101`, with `data` as what the api answered for it. */
async function taskPage(
  data: TaskDetail | null,
  warnings: string[] = [],
  usage: { data: unknown; warnings?: string[] } = { data: [] },
) {
  await renderRoute(
    Route,
    "/tasks/$taskId",
    {
      "/api/tasks/T-101": { data, warnings },
      "/api/tasks/T-101/usage": usage,
      "/api/debt": { data: [] },
      "/api/phases": { data: [] },
      "/api/learnings": { data: [] },
    },
    "/tasks/T-101",
  );
}

/** One served usage line; the figures are `null` unless a test sets them. */
function line(over: Record<string, unknown> = {}) {
  return {
    task_id: "T-101",
    role: "implementador",
    round: null,
    account: "cuenta1",
    call: "phase",
    session_id: "s-1",
    recorded_at: "2026-10-14T10:00:00Z",
    measured: true,
    raw_keys: [],
    is_error: false,
    num_turns: null,
    duration_ms: null,
    total_cost_usd: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_input_tokens: null,
    cache_creation_input_tokens: null,
    ...over,
  };
}

/** One served attempt around `calls`; the first is its phase line unless told. */
function group(calls: ReturnType<typeof line>[], over: Record<string, unknown> = {}) {
  return {
    task_id: "T-101",
    role: "implementador",
    round: null,
    account: "cuenta1",
    calls,
    call_count: calls.length,
    call_kinds: calls.map((c) => c.call),
    measured_calls: calls.filter((c) => c.measured).length,
    unmeasured_calls: calls.filter((c) => !c.measured).length,
    phase_call: calls[0] ?? null,
    ...over,
  };
}

/** The block the page's title sits in: the task id and the subtitle under it. */
function header(): HTMLElement {
  return screen.getByRole("heading", { level: 1, name: "T-101" }).parentElement as HTMLElement;
}

describe("the task page's header for a task with no description", () => {
  it("says so under the task id", async () => {
    await taskPage({ ...TASK, description: null });
    // The title is drawn before the read lands; this is drawn after it.
    await screen.findByText("task body");
    expect(header().textContent).toContain("no description");
    expect(header().textContent).not.toContain("null");
  });
});

describe("the task page's header for a task with a description", () => {
  it("shows the description and does not call it absent", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    expect(header().textContent).toContain(DESCRIPTION);
    expect(screen.queryByText("no description")).toBeNull();
  });
});

describe("the task page's header for a card the api could not read", () => {
  it("claims nothing about a description", async () => {
    await taskPage(null, ["T-101: the task file could not be parsed"]);
    await screen.findByText("T-101 exists and could not be read");
    expect(header().textContent).toBe("T-101");
    expect(screen.queryByText("no description")).toBeNull();
  });
});

const REGION = "what this task's calls cost";

describe("the usage region's group", () => {
  it("prints the first call, the retry count and the closed calls", async () => {
    const first = line({ input_tokens: 12345, total_cost_usd: 0.0421 });
    const retry = line({ call: "gate-retry", total_cost_usd: 0.05 });
    await taskPage(TASK, [], { data: [group([first, retry])] });
    await screen.findByText(REGION);
    await screen.findByText("first call; 1 retry not included");
    expect(screen.getByText("12,345")).toBeTruthy();
    expect(screen.getByText("$0.0421")).toBeTruthy();
    expect(screen.getByText("gate-retry")).toBeTruthy();
    expect(screen.queryByText("session cost so far")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /calls \(2\)/ }));
    expect(screen.getAllByText("session cost so far")).toHaveLength(2);
    // Nothing is summed across the two lines.
    expect(screen.queryByText("$0.0921")).toBeNull();
  });
});

describe("the usage region's unmeasured line", () => {
  it("says no result and does not draw a row of absents", async () => {
    const lost = line({ call: "gate-retry", measured: false, is_error: null });
    await taskPage(TASK, [], { data: [group([line(), lost])] });
    await screen.findByText("1 unmeasured");
    fireEvent.click(screen.getByRole("button", { name: /calls \(2\)/ }));
    expect(screen.getByText("no result: crashed or timed out")).toBeTruthy();
    // Only the measured first line's null figures are absent, never the lost one's.
    expect(screen.getAllByText("not recorded").length).toBeGreaterThan(0);
    expect(screen.getAllByText("session cost so far")).toHaveLength(1);
  });
});

describe("the usage region's null phase_call", () => {
  it("names the missing phase line and claims no first call", async () => {
    const retry = line({ call: "gate-retry" });
    await taskPage(TASK, [], { data: [group([retry], { phase_call: null })] });
    await screen.findByText("no phase line in this attempt");
    expect(screen.queryByText(/first call/)).toBeNull();
  });
});

describe("the usage region's models", () => {
  const OLD = "written before models were logged";

  /** One slot's text; with the disclosure closed only the first-call block is drawn. */
  function slot(label: string): string {
    return screen.getByText(label, { exact: true }).parentElement!.textContent ?? "";
  }

  async function open(over: Record<string, unknown>) {
    await taskPage(TASK, [], { data: [group([line(over)])] });
    await screen.findByText("first call");
  }

  it("prints an explicit model and a models_used list on the line and in the first call", async () => {
    await open({ model: "claude-opus-5-5", models_used: ["claude-haiku-5-5", "claude-opus-5-5"] });
    expect(slot("model")).toContain("claude-opus-5-5");
    expect(slot("models used")).toContain("claude-haiku-5-5");
    expect(screen.getAllByText("claude-haiku-5-5")).toHaveLength(1);
    expect(screen.getAllByText("claude-opus-5-5")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /calls \(1\)/ }));
    expect(screen.getAllByText("claude-haiku-5-5")).toHaveLength(2);
    expect(screen.getAllByText("claude-opus-5-5")).toHaveLength(4);
  });

  it("reads model null as the CLI default", async () => {
    await open({ model: null, models_used: null });
    expect(slot("model")).toContain("CLI default");
    expect(slot("model")).not.toContain("not recorded");
    expect(slot("model")).not.toContain(OLD);
  });

  it("reads models_used null as absent", async () => {
    await open({ model: "m", models_used: null });
    expect(slot("models used")).toContain("not recorded");
  });

  it("reads models_used [] as an empty list", async () => {
    await open({ model: "m", models_used: [] });
    expect(slot("models used")).toContain("empty list");
    expect(slot("models used")).not.toContain("not recorded");
  });

  it("reads missing keys as written before models were logged", async () => {
    await open({});
    for (const label of ["model", "models used"]) {
      expect(slot(label)).toContain(OLD);
      expect(slot(label)).not.toContain("CLI default");
      expect(slot(label)).not.toContain("not recorded");
    }
  });

  it("names a value of the wrong shape", async () => {
    await open({ model: 42, models_used: "x" });
    expect(slot("model")).toMatch(/not a string or null/);
    expect(slot("models used")).toMatch(/not a list or null/);
  });

  it("keeps the names of a list holding a non-string", async () => {
    await open({ model: "m", models_used: ["claude-opus-5-5", 7] });
    expect(screen.getByText("claude-opus-5-5", { selector: "*" })).toBeTruthy();
    expect(slot("models used")).toMatch(/not a model name/);
  });

  it("shows the model of an unmeasured line", async () => {
    await open({ measured: false, is_error: null, model: "m-unmeasured" });
    expect(screen.getByText("m-unmeasured")).toBeTruthy();
  });
});

describe("the usage region's denials", () => {
  const BASH = {
    tool_name: "Bash",
    tool_use_id: "toolu_01",
    tool_input: { command: "npm i left-pad", description: "Install a package" },
  };

  /** The denied row of the one call line; the calls disclosure is opened first. */
  async function denied(over: Record<string, unknown>, calls = [line(over)]): Promise<string> {
    await taskPage(TASK, [], { data: [group(calls)] });
    await screen.findByText("first call");
    fireEvent.click(screen.getByRole("button", { name: /calls \(\d+\)/ }));
    return screen.getAllByText("denied", { exact: true })[0]!.parentElement!.textContent ?? "";
  }

  it("reads a missing key as written before denials were logged", async () => {
    const row = await denied({});
    expect(row).toContain("written before denials were logged");
    expect(row).not.toContain("not recorded");
    expect(row).not.toContain("none");
  });

  it("reads null as absent", async () => {
    const row = await denied({ permission_denials: null });
    expect(row).toContain("not recorded");
    expect(row).not.toContain("written before denials were logged");
  });

  it("reads [] as none", async () => {
    const row = await denied({ permission_denials: [] });
    expect(row).toContain("none");
    expect(row).not.toContain("not recorded");
    expect(screen.queryByText(/^\d+ denied$/)).toBeNull();
  });

  it("lists each denial with its tool and its input on one line, whole in the title", async () => {
    const long = { ...BASH, tool_input: { command: "x".repeat(200) } };
    const row = await denied({ permission_denials: [BASH, long] });
    expect(row).toContain("Bash");
    expect(row).toContain('"command":"npm i left-pad"');
    const short = screen.getByText(/^\{"command":"x+…$/);
    expect(short.textContent!.length).toBe(80);
    expect(short.parentElement!.getAttribute("title")).toContain("x".repeat(200));
  });

  it("names a malformed entry, the dispatcher's marker or a wrong type", async () => {
    const row = await denied({
      permission_denials: [BASH, { malformed: "str" }, 7, { tool_use_id: "toolu_02" }],
    });
    expect(row).toContain("Bash");
    expect(row).toMatch(/denial — a string, not a refused tool call/);
    expect(row).toMatch(/denial — a number, not a refused tool call/);
    expect(row).toMatch(/denial — an object, not a refused tool call/);
  });

  it("names a permission_denials that is not a list", async () => {
    const row = await denied({ permission_denials: "Bash" });
    expect(row).toMatch(/not a list or null/);
  });

  it("says how many more were counted and not kept", async () => {
    const row = await denied({ permission_denials: [BASH], permission_denials_dropped: 12 });
    expect(row).toContain("+12 more not recorded");
  });

  it("counts an attempt's denials on its header", async () => {
    await taskPage(TASK, [], {
      data: [
        group(
          [
            line({ permission_denials: [BASH, BASH] }),
            line({ call: "gate-retry", permission_denials: [BASH], permission_denials_dropped: 2 }),
          ],
          { call_count: 2, call_kinds: ["phase", "gate-retry"] },
        ),
      ],
    });
    expect(await screen.findByText("5 denied")).toBeTruthy();
  });

  it("puts no badge on an attempt with no denial", async () => {
    await taskPage(TASK, [], { data: [group([line({ permission_denials: [] })])] });
    await screen.findByText("first call");
    expect(screen.queryByText(/^\d+ denied$/)).toBeNull();
  });
});

describe("the usage region's states", () => {
  it("is an error, not an empty state, for a log it could not read", async () => {
    await taskPage(TASK, [], { data: null, warnings: ["T-101: unreadable usage log: boom"] });
    await screen.findByText("The usage log could not be read");
    expect(screen.getAllByText(/unreadable usage log: boom/).length).toBeGreaterThan(0);
    expect(screen.queryByText("No claude call of this task has been recorded")).toBeNull();
  });

  it("says a task with no call has none", async () => {
    await taskPage(TASK);
    await screen.findByText("No claude call of this task has been recorded");
  });

  it("labels a failover pair and names the account it came from", async () => {
    const a = group([line({ is_error: true })], { account: "cuenta1" });
    const b = group([line({ account: "cuenta2" })], { account: "cuenta2" });
    await taskPage(TASK, [], { data: [a, b] });
    await screen.findByText("attempt 1 of 2");
    expect(screen.getByText("attempt 2 of 2")).toBeTruthy();
    expect(screen.getByText("failover from cuenta1")).toBeTruthy();
  });
});
