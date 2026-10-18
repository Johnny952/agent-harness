/**
 * The task page's header, read off the mounted route. `TaskDetailPage` builds
 * the subtitle itself and hands it to no component of its own, so there is
 * nothing to export and render alone: the page is mounted with `renderRoute`
 * and the header is read where the operator reads it. A card the api could not
 * read claims nothing under its id (`docs/debt/T-034-D1.md`); since ADR 139 the
 * header carries the title and the epic, and the description has its region.
 *
 * Then ADR 139's regions: the description's sections, the comment thread and
 * its form, the running phase, and the epic view over one `/api/tasks` read.
 *
 * The page asks for seven paths and all are served, because one left out
 * answers 404 and the page draws that region's error state:
 * `docs/implementations/T-035.md` *Every read the page makes*.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { Route } from "./tasks.$taskId";
import { renderRoute, type Served } from "@/test/render-route";
import type { Task, TaskComment, TaskDetail } from "@/lib/api/types";

// `renderRoute` draws no Toaster, so the toast is asserted at its call.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
  epic: null,
  split_into: [],
  merged: false,
  title: null,
  body: "",
};

/** The page at `/tasks/T-101`, with `data` as what the api answered for it. */
async function taskPage(
  data: TaskDetail | null,
  warnings: string[] = [],
  usage: { data: unknown; warnings?: string[] } = { data: [] },
  extra: Served = {},
) {
  return renderRoute(
    Route,
    "/tasks/$taskId",
    {
      "/api/tasks/T-101": { data, warnings },
      "/api/tasks/T-101/usage": usage,
      "/api/debt": { data: [] },
      "/api/phases": { data: [] },
      "/api/learnings": { data: [] },
      "/api/tasks/T-101/comments": { data: { task_id: "T-101", comments: [] } },
      "/api/tasks": { data: [] },
      ...extra,
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

/** The panel a region's `h2` heads. */
function region(name: string): HTMLElement {
  return screen.getByRole("heading", { level: 2, name }).parentElement as HTMLElement;
}

describe("the task page's header", () => {
  it("names the title and the epic a sub-task belongs to, linked", async () => {
    await taskPage({ ...TASK, title: "pool lock owner", epic: "T-100" });
    await screen.findByText("task body");
    expect(header().textContent).toContain("pool lock owner");
    const link = within(header()).getByRole("link", { name: "T-100" });
    expect(link.getAttribute("href")).toBe("/tasks/T-100");
  });

  it("says no title, never null, and names no epic on a plain task", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    expect(header().textContent).toContain("no title");
    expect(header().textContent).not.toContain("null");
    expect(header().textContent).not.toContain("part of");
  });
});

describe("the description region for a task with no description", () => {
  it("says so", async () => {
    await taskPage({ ...TASK, description: null });
    // The title is drawn before the read lands; this is drawn after it.
    await screen.findByText("task body");
    expect(region("description").textContent).toContain("no description");
    expect(region("description").textContent).not.toContain("null");
  });
});

describe("the description region for a description that does not parse", () => {
  it("shows the raw text with the dispatcher's reason", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    const raw = within(region("description")).getByTestId("description-raw");
    expect(raw.textContent).toContain(DESCRIPTION);
    expect(raw.textContent).toContain("has text before its first section");
    expect(screen.queryByText("no description")).toBeNull();
  });
});

describe("the description region for a sectioned description", () => {
  it("draws each section under its heading, in the dataclass's order", async () => {
    const description =
      "## Done when\nthe owner shows\n## Goal\nname the owner\n## Steps\n- read it";
    await taskPage({ ...TASK, description });
    await screen.findByText("task body");
    const sections = within(region("description")).getByTestId("description-sections");
    const headings = within(sections).getAllByRole("heading", { level: 3 });
    expect(headings.map((h) => h.textContent)).toEqual(["Goal", "Done when", "Steps"]);
    expect(sections.textContent).toContain("name the owner");
    expect(sections.textContent).toContain("read it");
    expect(sections.textContent).not.toContain("## Goal");
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

function comment(over: Partial<TaskComment> = {}): TaskComment {
  return { at: "2026-10-14T10:00:00Z", author: "operator", text: "looks right", ...over };
}

function served(comments: TaskComment[]): Served {
  return { "/api/tasks/T-101/comments": { data: { task_id: "T-101", comments } } };
}

const POSTED = {
  id: "T-101",
  comment: comment({ text: "ship it" }),
  comment_count: 1,
};

describe("the comment thread", () => {
  it("lists each comment with its author, time, text and anchor", async () => {
    await taskPage(TASK, [], undefined, {
      ...served([
        comment(),
        comment({
          author: "revisor",
          text: "off by one",
          file: "dispatcher/tasks.py",
          line: 42,
          commit: "abc1234",
        }),
      ]),
    });
    const rows = await screen.findAllByTestId("comment");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("operator");
    expect(rows[0]!.textContent).toContain("2026-10-14");
    expect(rows[0]!.textContent).toContain("looks right");
    expect(rows[0]!.textContent).not.toContain("@");
    expect(rows[1]!.textContent).toContain("revisor");
    expect(within(rows[1]!).getByText("dispatcher/tasks.py:42@abc1234")).toBeTruthy();
  });

  it("says a task with none has none", async () => {
    await taskPage(TASK);
    await screen.findByText("No comments on this task yet.");
  });
});

describe("the comment form", () => {
  it("posts the text as the board-service takes it", async () => {
    const { sent } = await taskPage(TASK, [], undefined, {
      "POST /api/tasks/T-101/comments": { data: POSTED },
    });
    const box = await screen.findByLabelText("new comment");
    fireEvent.change(box, { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: "post comment" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("comment added to T-101"));
    expect(sent.find((s) => s.method === "POST")).toEqual({
      method: "POST",
      path: "/api/tasks/T-101/comments",
      body: { text: "ship it" },
    });
  });

  it("will not post blank text or text over the limit", async () => {
    const { sent } = await taskPage(TASK);
    const box = await screen.findByLabelText("new comment");
    const button = screen.getByRole("button", { name: "post comment" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box, { target: { value: "x".repeat(20_001) } });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("20,001 characters, over the 20,000 a comment can hold")).toBeTruthy();
    fireEvent.submit(box.closest("form")!);
    fireEvent.change(box, { target: { value: "x".repeat(20_000) } });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    expect(sent.some((s) => s.method === "POST")).toBe(false);
  });

  it("shows a refusal in a toast", async () => {
    await taskPage(TASK, [], undefined, {
      "POST /api/tasks/T-101/comments": { status: 400, error: "the text is blank" },
    });
    fireEvent.change(await screen.findByLabelText("new comment"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "post comment" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("the text is blank")),
    );
  });
});

/** The field panel's cell under `label`. */
function field(label: string): HTMLElement {
  return screen.getByText(label, { selector: "p.label-xs" }).parentElement as HTMLElement;
}

describe("the running phase", () => {
  it("names the phase a live lock holds", async () => {
    await taskPage({ ...TASK, status: "in_progress", phase: "revisor", lock_expired: false });
    await screen.findByText("task body");
    expect(field("phase").textContent).toContain("revisor");
    expect(field("phase").textContent).not.toContain("stale");
  });

  it("flags the phase stale when the api says the lock expired", async () => {
    await taskPage({ ...TASK, status: "in_progress", phase: "revisor", lock_expired: true });
    await screen.findByText("task body");
    expect(field("phase").textContent).toContain("revisor");
    expect(within(field("phase")).getByText("stale")).toBeTruthy();
  });

  it("says between phases when none runs", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    expect(field("phase").textContent).toBe("phasebetween phases");
  });
});

function row(id: string, over: Partial<Task> = {}): Task {
  const { body: _body, ...base } = TASK;
  return { ...base, task_id: id, epic: "T-101", ...over };
}

const EPIC_VIEW = "the tasks this epic was split into";

describe("the epic view", () => {
  const epic: TaskDetail = {
    ...TASK,
    status: "epic",
    split_into: ["T-104", "T-102", "T-109", "T-103"],
  };
  const list = [
    row("T-101", { status: "epic", split_into: epic.split_into, epic: null }),
    row("T-102", { title: "second", status: "done", merged: true }),
    row("T-103", { title: "dropped one", status: "dropped" }),
    row("T-104", { title: "first", status: "in_progress", depends_on: ["T-102", "T-103"] }),
  ];

  it("lists the children in split order, each linked, with a missing one named", async () => {
    await taskPage(epic, [], undefined, { "/api/tasks": { data: list } });
    await screen.findByTestId("child-T-104");
    const ids = within(region(EPIC_VIEW))
      .getAllByTestId(/^child-/)
      .map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual(["child-T-104", "child-T-102", "child-T-109", "child-T-103"]);
    const first = screen.getByTestId("child-T-104");
    expect(first.textContent).toContain("first");
    expect(first.textContent).toContain("in_progress");
    expect(first.textContent).toContain("not merged");
    expect(first.textContent).toContain("after T-102, T-103");
    expect(within(first).getByRole("link", { name: "T-104" }).getAttribute("href")).toBe(
      "/tasks/T-104",
    );
    expect(screen.getByTestId("child-T-102").textContent).toContain("merged");
    expect(screen.getByTestId("child-T-109").textContent).toContain("missing");
  });

  it("counts merged out of the children not dropped", async () => {
    await taskPage(epic, [], undefined, { "/api/tasks": { data: list } });
    const p = await screen.findByTestId("epic-progress");
    expect(p.textContent).toBe("1 of 3 merged · 1 dropped · 1 missing");
  });

  it("reads the children from one list read, not a detail read each", async () => {
    const { sent } = await taskPage(epic, [], undefined, { "/api/tasks": { data: list } });
    await screen.findByTestId("epic-progress");
    const paths = sent.map((s) => s.path);
    expect(paths.filter((p) => p === "/api/tasks")).toHaveLength(1);
    expect(paths.some((p) => /^\/api\/tasks\/T-10[2-9]$/.test(p))).toBe(false);
  });

  it("is not drawn for a sub-task", async () => {
    await taskPage({ ...TASK, epic: "T-100" });
    await screen.findByText("task body");
    expect(screen.queryByText(EPIC_VIEW)).toBeNull();
  });
});
