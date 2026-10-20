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
import { renderRoute, type Answer, type Served } from "@/test/render-route";
import type {
  ActionQueue,
  ActionRecord,
  EpicRun,
  Task,
  TaskComment,
  TaskDetail,
  Thread,
} from "@/lib/api/types";
import { OPEN_THREAD_EVENT } from "@/hooks/use-console";

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
  project: null,
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

describe("talk about this task (ADR 195)", () => {
  const THREAD: Thread = {
    id: "t-9",
    title: "T-101: the dock",
    project: "ia-harness",
    account: "cuenta1",
    model: "opus",
    effort: "high",
    segments: [],
    created: "2026-10-19T00:00:00+00:00",
    status: "open",
    task: "T-101",
  };

  it("creates a thread with the task and opens the dock on it", async () => {
    const opened: unknown[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<unknown>).detail);
    window.addEventListener(OPEN_THREAD_EVENT, onOpen);
    try {
      const { sent } = await taskPage({ ...TASK, title: "the dock" }, [], undefined, {
        "POST /api/threads": { bare: THREAD, status: 201 },
        "/api/threads": { data: [] },
      });
      fireEvent.click(await talkButton());
      await waitFor(() => expect(opened).toEqual(["t-9"]));
      expect(sent.filter((s) => s.method === "POST")).toEqual([
        { method: "POST", path: "/api/threads", body: { title: "T-101: the dock", task: "T-101" } },
      ]);
    } finally {
      window.removeEventListener(OPEN_THREAD_EVENT, onOpen);
    }
  });

  it("titles the thread with the id alone when the task has no title", async () => {
    const { sent } = await taskPage(TASK, [], undefined, {
      "POST /api/threads": { bare: THREAD, status: 201 },
      "/api/threads": { data: [] },
    });
    fireEvent.click(await talkButton());
    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    expect(sent.find((s) => s.method === "POST")?.body).toEqual({ title: "T-101", task: "T-101" });
  });

  it("shows a refusal by the button and opens nothing", async () => {
    const opened: unknown[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<unknown>).detail);
    window.addEventListener(OPEN_THREAD_EVENT, onOpen);
    try {
      await taskPage(TASK, [], undefined, {
        "POST /api/threads": { status: 400, error: "task 'T-101' has no task file" },
        "/api/threads": { data: [] },
      });
      fireEvent.click(await talkButton());
      expect((await screen.findByRole("alert")).textContent).toContain("has no task file");
      expect(opened).toEqual([]);
    } finally {
      window.removeEventListener(OPEN_THREAD_EVENT, onOpen);
    }
  });
});

/** *Talk about this task*, once the thread list is read and it can be clicked (ADR 196). */
async function talkButton(): Promise<HTMLButtonElement> {
  const button = await screen.findByRole("button", { name: "Talk about this task" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  return button as HTMLButtonElement;
}

describe("talk about this task reuses an open thread (ADR 196)", () => {
  const ON_TASK: Thread = {
    id: "t-1",
    title: "T-101: the dock",
    project: "ia-harness",
    account: "cuenta1",
    model: "opus",
    effort: "high",
    segments: [],
    created: "2026-10-18T00:00:00+00:00",
    status: "open",
    task: "T-101",
  };
  const CREATED: Thread = { ...ON_TASK, id: "t-new", created: "2026-10-19T00:00:00+00:00" };

  /** Click the button with `threads` as the list; what opened and what was posted. */
  async function click(threads: Answer) {
    const opened: unknown[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<unknown>).detail);
    window.addEventListener(OPEN_THREAD_EVENT, onOpen);
    try {
      const { sent } = await taskPage(TASK, [], undefined, {
        "POST /api/threads": { bare: CREATED, status: 201 },
        "/api/threads": threads,
      });
      fireEvent.click(await talkButton());
      await waitFor(() => expect(opened.length).toBe(1));
      return { opened, posts: sent.filter((s) => s.method === "POST") };
    } finally {
      window.removeEventListener(OPEN_THREAD_EVENT, onOpen);
    }
  }

  it("opens the open thread on the task and posts nothing", async () => {
    const { opened, posts } = await click({ data: [ON_TASK] });
    expect(opened).toEqual(["t-1"]);
    expect(posts).toEqual([]);
  });

  it("opens the most recently created when several are open", async () => {
    const { opened, posts } = await click({
      data: [
        ON_TASK,
        { ...ON_TASK, id: "t-2", created: "2026-10-18T12:00:00+00:00" },
        { ...ON_TASK, id: "t-0", created: "2026-10-01T00:00:00+00:00" },
      ],
    });
    expect(opened).toEqual(["t-2"]);
    expect(posts).toEqual([]);
  });

  it("creates when no thread is on the task", async () => {
    const { opened, posts } = await click({
      data: [
        { ...ON_TASK, id: "t-3", task: "T-202" },
        { ...ON_TASK, id: "main", project: null, task: null },
      ],
    });
    expect(opened).toEqual(["t-new"]);
    expect(posts).toHaveLength(1);
  });

  it("ignores an archived thread on the task and creates", async () => {
    const { opened, posts } = await click({
      data: [{ ...ON_TASK, status: "archived", created: "2026-10-19T06:00:00+00:00" }],
    });
    expect(opened).toEqual(["t-new"]);
    expect(posts).toHaveLength(1);
  });

  it("creates when the thread list could not be read", async () => {
    const { opened, posts } = await click({ status: 503, error: "the api is down" });
    expect(opened).toEqual(["t-new"]);
    expect(posts).toHaveLength(1);
  });

  it("waits while the thread list is first read", async () => {
    const { fetchMock, queryClient } = await taskPage(TASK, [], undefined, {
      "/api/threads": { data: [ON_TASK] },
    });
    // Hold the list's next read and drop what was read, so the list is pending again.
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      new URL(String(input), "http://console.test").pathname === "/api/threads"
        ? new Promise<Response>(() => {})
        : answer(input, init),
    );
    // Not awaited: the refetch it starts never settles.
    void queryClient.resetQueries({ queryKey: ["threads"] });
    const button = await screen.findByRole("button", { name: "Talk about this task" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
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

describe("the task's project (ADR 204)", () => {
  it("names the project slug", async () => {
    await taskPage({ ...TASK, project: "ia-harness" });
    await screen.findByText("task body");
    expect(field("project").textContent).toBe("projectia-harness");
  });

  it("draws the absent mark for a task with no project", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    expect(field("project").textContent).toContain("no project");
    expect(field("project").textContent).not.toContain("null");
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
    // The list is read, by the epic view and by the sidebar's project list (ADR 208),
    // and no child is read on its own.
    expect(paths.filter((p) => p === "/api/tasks").length).toBeGreaterThanOrEqual(1);
    expect(paths.filter((p) => p === "/api/tasks").length).toBeLessThanOrEqual(2);
    expect(paths.some((p) => /^\/api\/tasks\/T-10[2-9]$/.test(p))).toBe(false);
  });

  it("is not drawn for a sub-task", async () => {
    await taskPage({ ...TASK, epic: "T-100" });
    await screen.findByText("task body");
    expect(screen.queryByText(EPIC_VIEW)).toBeNull();
  });
});

/** An action record as `dispatcher/actions.py:create` writes it, an epic's start by default. */
function action(taskId: string, over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    version: 1,
    id: `act-${taskId}`,
    kind: "start",
    project: "ia-harness",
    task_id: taskId,
    params: { limit: null, epic: "T-101" },
    actor: "operator",
    note: null,
    created_at: "2026-10-20T12:00:00Z",
    claimed_at: null,
    started_at: null,
    finished_at: null,
    status: "pending",
    waiting: null,
    result: null,
    error: null,
    ...over,
  } as ActionRecord;
}

/** An epic's run as `/api/actions` serves it (`dispatcher/epic_runs.py:view`). */
function epicRun(over: Partial<EpicRun> = {}): EpicRun {
  return {
    epic: "T-101",
    project: "ia-harness",
    note: null,
    limit: null,
    started_at: "2026-10-20T12:00:00Z",
    stopped_at: null,
    paused: null,
    paused_at: null,
    queued: { "T-109": { action: "act-T-109", usage_offset: 0 } },
    spent: { tokens: 1200, unmeasured: 0 },
    limit_reached: false,
    ...over,
  };
}

function queueOf(over: Partial<ActionQueue> = {}): { data: ActionQueue } {
  return {
    data: {
      pending: [],
      claimed: [],
      done: [],
      done_total: 0,
      worker: { heartbeat: null, stale: true, stale_after_seconds: 30 },
      ...over,
    } as ActionQueue,
  };
}

describe("Start epic and Stop epic (ADR 211)", () => {
  const epic: TaskDetail = {
    ...TASK,
    status: "epic",
    split_into: ["T-104", "T-102", "T-109", "T-103"],
  };
  const list = [
    row("T-101", { status: "epic", split_into: epic.split_into, epic: null }),
    row("T-102", { status: "done", merged: true }),
    row("T-103", { status: "pending" }),
    row("T-104", { status: "pending" }),
    row("T-109", { status: "pending" }),
  ];

  async function epicPage(extra: Served = {}) {
    const out = await taskPage(epic, [], undefined, {
      "/api/tasks": { data: list },
      // The dispatcher's order, which is not the split's: the dialog follows it.
      "/api/ready": { data: ["T-109", "T-104", "T-200"] },
      "/api/accounts": { data: [] },
      "/api/actions": queueOf(),
      ...extra,
    });
    await screen.findByTestId("epic-actions");
    return out;
  }

  async function open(name: string) {
    fireEvent.click(await screen.findByRole("button", { name }));
    return await screen.findByRole("dialog");
  }

  it("says what Start epic would queue and what it leaves out, with the slots and a limit field", async () => {
    await epicPage();
    const dialog = await open("Start epic");
    const summary = within(dialog).getByTestId("epic-start-summary");
    await waitFor(() =>
      expect(summary.textContent).toContain("2 starts would be queued: T-109, T-104"),
    );
    expect(summary.textContent).toContain("Not in Ready, not queued: T-102, T-103");
    expect(within(dialog).getByTestId("standing-line").textContent).toMatch(
      /^For the first of them: /,
    );
    expect(within(dialog).getByTestId("epic-limit").textContent).toContain(
      "Spending limit (measured tokens, optional)",
    );
    expect(screen.queryByTestId("epic-run")).toBeNull();
  });

  it("sends the note, toasts what was queued and what was left out", async () => {
    const { sent } = await epicPage({
      "POST /api/epics/T-101/start": {
        bare: {
          epic: "T-101",
          queued: [action("T-109"), action("T-104")],
          not_ready: ["T-102"],
          limit: null,
        },
        status: 201,
      },
    });
    const dialog = await open("Start epic");
    await within(dialog).findByText(/would be queued/);
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "go" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "2 starts of T-101 queued; 1 task not in Ready left out",
      ),
    );
    const post = sent.find((r) => r.method === "POST");
    expect(post?.path).toBe("/api/epics/T-101/start");
    expect(post?.body).toEqual({ note: "go", limit: null });
  });

  it("sends the spending limit, and refuses one that is not a whole number", async () => {
    const { sent } = await epicPage({
      "POST /api/epics/T-101/start": {
        bare: { epic: "T-101", queued: [action("T-109")], not_ready: [], limit: 5000 },
        status: 201,
      },
    });
    const dialog = await open("Start epic");
    await within(dialog).findByText(/would be queued/);
    const field = within(dialog).getByLabelText("Spending limit");
    const button = within(dialog).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    fireEvent.change(field, { target: { value: "0" } });
    expect(within(dialog).getByText("0 is not a whole number of at least 1.")).toBeTruthy();
    expect(button.disabled).toBe(true);
    fireEvent.change(field, { target: { value: "5000" } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "1 start of T-101 queued; limit 5000 measured tokens",
      ),
    );
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null, limit: 5000 });
  });

  it("shows a run's spend against its limit", async () => {
    await epicPage({ "/api/actions": queueOf({ epics: [epicRun({ limit: 9000 })] }) });
    expect((await screen.findByTestId("epic-run")).textContent).toBe(
      "1200 of 9000 measured tokens spent",
    );
    expect(screen.getByRole("button", { name: "Start epic" })).toBeTruthy();
    // A going run is stoppable with nothing queued: it is between two tasks.
    expect(screen.getByRole("button", { name: "Stop epic" })).toBeTruthy();
  });

  it("shows Paused: limit reached and resumes with a new limit above the spend", async () => {
    const { sent } = await epicPage({
      "/api/ready": { data: ["T-200"] },
      "/api/actions": queueOf({
        epics: [
          epicRun({ limit: 1000, paused: "limit_reached", limit_reached: true }),
          epicRun({ epic: "T-300", limit: 1 }),
        ],
      }),
      "POST /api/epics/T-101/start": {
        bare: { epic: "T-101", queued: [], not_ready: [], limit: 3000 },
        status: 201,
      },
    });
    expect((await screen.findByTestId("epic-run")).textContent).toBe(
      "Paused: limit reached · 1200 of 1000 measured tokens spent",
    );
    const dialog = await open("Resume epic");
    expect(within(dialog).getByTestId("epic-limit").textContent).toContain(
      "New spending limit (measured tokens; 1200 spent of 1000)",
    );
    const field = within(dialog).getByLabelText("Spending limit");
    const button = within(dialog).getByRole("button", { name: "Resume" }) as HTMLButtonElement;
    fireEvent.change(field, { target: { value: "1200" } });
    expect(
      within(dialog).getByText("The run has already spent 1200 measured tokens."),
    ).toBeTruthy();
    expect(button.disabled).toBe(true);
    // Nothing in Ready does not hold a resume back: the merges queue the rest.
    fireEvent.change(field, { target: { value: "3000" } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "0 starts of T-101 queued; limit 3000 measured tokens",
      ),
    );
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null, limit: 3000 });
  });

  it("prefills the limit from the merged children's cycles, and lets a typed one win", async () => {
    const { sent } = await epicPage({
      // T-102 is the one merged child: 1000 measured over its two calls.
      "/api/tasks/T-102/usage": {
        data: [group([line({ input_tokens: 600 }), line({ output_tokens: 400, call: "resume" })])],
      },
      "POST /api/epics/T-101/start": {
        bare: { epic: "T-101", queued: [action("T-109")], not_ready: [], limit: 2500 },
        status: 201,
      },
    });
    const dialog = await open("Start epic");
    const field = within(dialog).getByLabelText("Spending limit") as HTMLInputElement;
    // Three children still to land, at the one merged child's 1000 each.
    await waitFor(() => expect(field.value).toBe("3000"));
    expect(within(dialog).getByTestId("epic-limit-prefill").textContent).toBe(
      "Prefilled from 1 merged task of this epic: about 1000 measured tokens each, for 3 tasks still to land.",
    );
    fireEvent.change(field, { target: { value: "2500" } });
    expect(within(dialog).queryByTestId("epic-limit-prefill")).toBeNull();
    await within(dialog).findByText(/would be queued/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(sent.some((r) => r.method === "POST")).toBe(true));
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null, limit: 2500 });
  });

  it("prefills a resume's limit on top of the run's spend, and is blank with no measured child", async () => {
    await epicPage({
      "/api/tasks/T-102/usage": { data: [group([line({ input_tokens: 1000 })])] },
      "/api/actions": queueOf({
        epics: [epicRun({ limit: 1000, paused: "limit_reached", limit_reached: true })],
      }),
    });
    const dialog = await open("Resume epic");
    const field = within(dialog).getByLabelText("Spending limit") as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe("4200"));
    expect(within(dialog).getByTestId("epic-limit-prefill").textContent).toContain(
      ", on top of the 1200 spent.",
    );
    cleanup();

    // T-102's log served nothing measured: the field stays blank.
    await epicPage({ "/api/tasks/T-102/usage": { data: [group([line()])] } });
    const blank = await open("Start epic");
    await within(blank).findByText(/would be queued/);
    expect((within(blank).getByLabelText("Spending limit") as HTMLInputElement).value).toBe("");
    expect(within(blank).queryByTestId("epic-limit-prefill")).toBeNull();
  });

  it("toasts a refusal and keeps the dialog", async () => {
    await epicPage({
      "POST /api/epics/T-101/start": {
        status: 409,
        error: "a start for task T-109 is already queued",
      },
    });
    const dialog = await open("Start epic");
    await within(dialog).findByText(/would be queued/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("a start for task T-109 is already queued"),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("does not let Start through when no child is in Ready", async () => {
    await epicPage({ "/api/ready": { data: ["T-200"] } });
    const dialog = await open("Start epic");
    await within(dialog).findByText(/No task of this epic is in Ready/);
    expect(
      (within(dialog).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("offers no Stop epic while the queue holds none of the epic's starts", async () => {
    // A start of a child queued by itself carries no `params.epic`: not the epic's.
    await epicPage({
      "/api/actions": queueOf({ pending: [action("T-104", { params: { limit: null } })] }),
    });
    await screen.findByRole("button", { name: "Start epic" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop epic" })).toBeNull());
    expect(screen.queryByTestId("epic-queued")).toBeNull();
  });

  it("names the waiting starts it drops and the running one it leaves, then toasts the answer", async () => {
    const { sent } = await epicPage({
      "/api/actions": queueOf({
        pending: [action("T-104"), action("T-103", { params: { limit: null } })],
        claimed: [action("T-109", { status: "claimed" })],
      }),
      "POST /api/epics/T-101/stop": {
        bare: {
          epic: "T-101",
          cancels: [action("A-1", { kind: "cancel", task_id: null })],
          dropped: [{ action: "act-T-104", task_id: "T-104" }],
          left: [{ action: "act-T-109", task_id: "T-109" }],
        },
        status: 201,
      },
    });
    expect((await screen.findByTestId("epic-queued")).textContent).toBe(
      "1 start waiting, 1 running",
    );
    const dialog = await open("Stop epic");
    const summary = within(dialog).getByTestId("epic-stop-summary");
    expect(summary.textContent).toContain("1 waiting start would be dropped: T-104");
    expect(summary.textContent).toContain("Running, left alone: T-109");
    expect(summary.textContent).not.toContain("T-103");
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "1 waiting start of T-101 dropped; 1 running start left alone",
      ),
    );
    const post = sent.find((r) => r.method === "POST");
    expect(post?.path).toBe("/api/epics/T-101/stop");
    expect(post?.body).toEqual({});
  });
});
