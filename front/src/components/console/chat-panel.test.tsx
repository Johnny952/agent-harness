/**
 * The chat dock over a stubbed `/api/threads` and thread log (`docs/decisions.md`
 * ADR 184): the selector's order, the switch and its memory, the meter, and the
 * cursor the log is polled from; and the composer, *stop* and the dispatch
 * line's links (ADR 190); and creating, editing, archiving and reopening a
 * thread, and a screen's `openThread` arriving as `select` (ADR 195).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { Thread } from "@/lib/api/types";
import { VIEW_KEY, readStoredView, storeView } from "@/lib/thread";
import { ChatPanel } from "./chat-panel";

afterEach(cleanup);
beforeEach(() => window.localStorage.clear());

function thread(id: string, over: Partial<Thread> = {}): Thread {
  return {
    id,
    title: id === "main" ? "main" : `Thread ${id}`,
    project: id === "main" ? null : "ia-harness",
    account: "cuenta1",
    model: "opus",
    effort: "high",
    segments: [],
    created: "2026-10-01T00:00:00+00:00",
    status: "open",
    task: null,
    ...over,
  };
}

const LOG = [
  {
    n: 1,
    event: {
      type: "ia_harness",
      event: "turn_start",
      at: "2026-10-19T10:00:00+00:00",
      actor: "operator",
      text: "list the files",
      account: "cuenta1",
      model: "opus",
      effort: "high",
      session_id: null,
    },
  },
  {
    n: 2,
    event: {
      type: "assistant",
      message: {
        content: [
          { type: "text", text: "Listing." },
          { type: "tool_use", id: "tu-1", name: "Bash", input: { command: "ls" } },
        ],
      },
    },
  },
  {
    n: 3,
    event: {
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "tu-1", content: "a.txt" }] },
    },
  },
  {
    n: 4,
    event: {
      type: "result",
      is_error: false,
      total_cost_usd: 1.5,
      usage: { input_tokens: 0, cache_read_input_tokens: 50000, cache_creation_input_tokens: 0 },
      modelUsage: { opus: { contextWindow: 200000 } },
    },
  },
];

function json(data: unknown) {
  return new Response(JSON.stringify({ data, warnings: [] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const EMPTY_QUEUE = {
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: false, stale_after_seconds: 30 },
};

function record(id: string, kind: string, params: unknown, created = "2026-10-20T10:00:00Z") {
  return { id, kind, params, created_at: created, status: "pending" };
}

interface Extras {
  queue?: unknown;
  /** The answer to a `POST /api/threads/<id>/turns`; the default is a 201 record. */
  turn?: () => Response;
  /** The account list the *New thread* form reads. */
  accounts?: unknown;
  /** `/api/accounts` answers a 500. */
  accountsDown?: boolean;
  /**
   * The answer to a `POST /api/threads` or a `PATCH /api/threads/<id>`, given the
   * method, the path and the parsed body; the default echoes a record built from
   * the body.
   */
  write?: (method: string, path: string, body: Record<string, unknown>) => Response;
  /** The board `/api/tasks` answers (ADR 197), read by the executions and the slots; the default is none. */
  tasks?: unknown;
  /** `/api/tasks` answers a 502. */
  tasksDown?: boolean;
}

function created(body: unknown, status = 201) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Serves the thread list, and the log's rows past each request's `since`. */
function serve(threads: Thread[], log: { n: number; event: unknown }[] = LOG, extras: Extras = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://console");
    const method = init?.method ?? "GET";
    const threadPath = /^\/api\/threads(\/[^/]+)?$/.test(url.pathname);
    if (threadPath && (method === "POST" || method === "PATCH")) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      if (extras.write) return extras.write(method, url.pathname, body);
      // The default write lands in `threads`, so the refetch after it reads it back.
      if (method === "POST") {
        const record = thread("t-new", body as Partial<Thread>);
        threads.push(record);
        return created(record);
      }
      const id = decodeURIComponent(url.pathname.split("/").pop() ?? "");
      const at = threads.findIndex((t) => t.id === id);
      const record = { ...(threads[at] ?? thread(id)), ...body } as Thread;
      if (at >= 0) threads[at] = record;
      return created(record, 200);
    }
    if (url.pathname === "/api/threads") return json(threads);
    if (url.pathname === "/api/accounts") {
      if (extras.accountsDown) return refusal(500, "boom")();
      return json(extras.accounts ?? []);
    }
    // An execution's reads (ADR 197), before the thread log's `/events`.
    if (url.pathname === "/api/tasks") {
      if (extras.tasksDown) return refusal(502, "down")();
      return json(extras.tasks ?? []);
    }
    const taskPath = /^\/api\/tasks\/([^/]+)(\/[a-z]+)?$/.exec(url.pathname);
    if (taskPath) {
      if (taskPath[2] === "/events" || taskPath[2] === "/usage") return json([]);
      const id = decodeURIComponent(taskPath[1]!);
      return json({ task_id: id, status: "in_progress", body: "" });
    }
    if (url.pathname.endsWith("/events")) {
      const since = Number(url.searchParams.get("since") ?? "0");
      return json(log.filter((r) => r.n > since));
    }
    if (url.pathname === "/api/actions") return json(extras.queue ?? EMPTY_QUEUE);
    if (init?.method === "POST" && url.pathname.endsWith("/turns")) {
      return (
        extras.turn?.() ??
        new Response(JSON.stringify(record("A-new", "turn", {})), {
          status: 201,
          headers: { "content-type": "application/json" },
        })
      );
    }
    if (init?.method === "POST" && url.pathname.endsWith("/cancel")) {
      return new Response(JSON.stringify(record("C-1", "cancel", {})), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("not found", { status: 404, headers: { "content-type": "text/html" } });
  });
}

function refusal(status: number, error: string) {
  return () =>
    new Response(JSON.stringify({ error }), {
      status,
      headers: { "content-type": "application/json" },
    });
}

/** The thread writes the dock made: `[method, path, parsed body]`. */
function writes(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls
    .filter(([u, init]) => /^\/api\/threads(\/[^/]+)?$/.test(String(u)) && init?.method)
    .filter(([, init]) => init?.method === "POST" || init?.method === "PATCH")
    .map(([u, init]) => [init?.method, String(u), JSON.parse(String(init?.body))] as const);
}

/** The POSTs the dock made: `[path, parsed body]`. */
function posts(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([u, init]) => [String(u), JSON.parse(String(init?.body))] as const);
}

function mount(pollMs = 60_000, select: { threadId: string; n: number } | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel onClose={() => {}} pollMs={pollMs} select={select} />
    </QueryClientProvider>,
  );
}

/** The dock under a router, for the views that link to a screen. */
function mountRouted() {
  const root = createRootRoute();
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/tasks/$taskId" }),
      createRoute({ getParentRoute: () => root, path: "/queue" }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterContextProvider router={router}>
        <ChatPanel onClose={() => {}} pollMs={60_000} />
      </RouterContextProvider>
    </QueryClientProvider>,
  );
}

/** The thread the selector shows. */
async function selected(): Promise<string> {
  const select = (await screen.findByRole("combobox", { name: "Thread" })) as HTMLSelectElement;
  return select.value;
}

/** The log of a turn the worker has claimed and not ended: `turn_start` with its action. */
const RUNNING = [
  {
    n: 1,
    event: {
      type: "ia_harness",
      event: "turn_start",
      at: "2026-10-20T10:00:00+00:00",
      action: "A-1",
      actor: "operator",
      text: "first",
      account: "cuenta1",
      model: "opus",
      effort: "high",
      session_id: null,
    },
  },
];

describe("the selector", () => {
  it("lists main first with account and model, and marks an archived thread", async () => {
    serve([
      thread("t-old", { status: "archived", account: "cuenta2", model: "sonnet" }),
      thread("t-1"),
      thread("main"),
    ]);
    mount();
    const select = await screen.findByRole("combobox", { name: "Thread" });
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      "main — cuenta1 · opus",
      "Thread t-1 · ia-harness — cuenta1 · opus",
      "Thread t-old · ia-harness — cuenta2 · sonnet (archived)",
    ]);
  });

  it("says when there is no main thread yet, and reads the first one there is", async () => {
    const fetch = serve([thread("t-1")]);
    mount();
    expect(await screen.findByText(/^No\s+thread yet\.\s+opens it with/)).toBeTruthy();
    await waitFor(() =>
      expect(fetch.mock.calls.map(([u]) => String(u))).toContain("/api/threads/t-1/events?since=0"),
    );
  });
});

describe("the dock", () => {
  it("has a composer, and no Stop on a turn whose log line carries no action", async () => {
    serve([thread("main")]);
    mount();
    expect(await screen.findByRole("textbox", { name: "Message" })).toBeTruthy();
    expect(screen.queryByText(/Read-only/)).toBeNull();
    await screen.findByText("Listing.");
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("shows the context and cost from the result events", async () => {
    serve([thread("main")]);
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("thread-meter").textContent).toBe(
        "context 50,000 / 200,000 (25%) · cost $1.50",
      ),
    );
  });

  it("shows the last turn as running when it has no turn_end", async () => {
    serve([thread("main")]);
    const { container } = mount();
    await waitFor(() =>
      expect(container.querySelector('[data-kind="turn"]')?.getAttribute("data-status")).toBe(
        "running",
      ),
    );
  });
});

describe("the switch", () => {
  it("starts on Text, shows commands on the other position, and is remembered", async () => {
    serve([thread("main")]);
    const first = mount();
    await screen.findByText("Listing.");
    expect(first.container.querySelector('[data-kind="tool"]')).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Text and commands" }));
    const tool = first.container.querySelector('[data-kind="tool"]');
    expect(tool?.textContent).toContain("Bash");
    expect(window.localStorage.getItem(VIEW_KEY)).toBe("commands");

    fireEvent.click(within(tool as HTMLElement).getByRole("button"));
    expect(screen.getByText("a.txt")).toBeTruthy();

    cleanup();
    const second = mount();
    await screen.findByText("Listing.");
    expect(
      screen.getByRole("button", { name: "Text and commands" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(second.container.querySelector('[data-kind="tool"]')).not.toBeNull();
  });

  it("falls back to Text, and keeps working, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readStoredView()).toBe("text");
    expect(() => storeView("commands")).not.toThrow();
  });
});

describe("the composer (ADR 190)", () => {
  async function type(text: string) {
    const box = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: text } });
    return box;
  }

  it("keeps Send disabled while the text is blank", async () => {
    serve([thread("main")]);
    mount();
    await type("   ");
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("sends {text} to the thread's turns route and clears on success", async () => {
    const fetch = serve([thread("main")]);
    mount();
    const box = await type("hello there");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(posts(fetch)).toEqual([["/api/threads/main/turns", { text: "hello there" }]]),
    );
    await waitFor(() => expect(box.value).toBe(""));
  });

  it("sends on Ctrl+Enter and not on Enter", async () => {
    const fetch = serve([thread("main")]);
    mount();
    const box = await type("go");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(posts(fetch)).toEqual([]);
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(posts(fetch)).toHaveLength(1));
  });

  it("sends while a turn runs, and draws the queued message from the queue", async () => {
    const queue = {
      ...EMPTY_QUEUE,
      pending: [record("A-2", "turn", { thread: "main", text: "second message" })],
    };
    const fetch = serve([thread("main")], RUNNING, { queue });
    mount();
    await screen.findByText("first");
    await type("second message");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(fetch)).toHaveLength(1));
    const queued = (await screen.findByText("second message", { selector: "p" })).closest(
      '[data-kind="queued"]',
    );
    expect(queued?.getAttribute("data-state")).toBe("queued");
  });

  it("does not draw a queued record the log already shows", async () => {
    const queue = {
      ...EMPTY_QUEUE,
      claimed: [record("A-1", "turn", { thread: "main", text: "first" })],
    };
    const { container } = (serve([thread("main")], RUNNING, { queue }), mount());
    await screen.findByText("first");
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector('[data-kind="queued"]')).toBeNull();
  });

  it.each([
    [400, "text is longer than 20000 characters"],
    [404, "no such thread"],
    [409, "thread main is archived"],
    [503, "BOARD_ACTIONS_TOKEN is not set"],
  ])("shows a %s and keeps the text", async (status, message) => {
    serve([thread("main")], LOG, { turn: refusal(status, message) });
    mount();
    const box = await type("do not lose me");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(box.value).toBe("do not lose me");
  });

  it("keeps a draft per thread and does not send it to the other one", async () => {
    const fetch = serve([thread("main"), thread("t-1")]);
    mount();
    const box = await type("for main");
    fireEvent.change(screen.getByRole("combobox", { name: "Thread" }), {
      target: { value: "t-1" },
    });
    await waitFor(() => expect(box.value).toBe(""));
    fireEvent.change(screen.getByRole("combobox", { name: "Thread" }), {
      target: { value: "main" },
    });
    await waitFor(() => expect(box.value).toBe("for main"));
    expect(posts(fetch)).toEqual([]);
  });

  it("gives an archived thread no composer", async () => {
    serve([thread("main", { status: "archived" })]);
    mount();
    expect(await screen.findByText(/archived and takes no new turn/)).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("says when the worker is not running", async () => {
    const queue = { ...EMPTY_QUEUE, worker: { ...EMPTY_QUEUE.worker, stale: true } };
    serve([thread("main")], LOG, { queue });
    mount();
    expect(await screen.findByText(/worker is not running/)).toBeTruthy();
  });
});

describe("stop (ADR 190)", () => {
  it("cancels the running turn by its action id", async () => {
    const fetch = serve([thread("main")], RUNNING);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(posts(fetch)).toEqual([["/api/actions/A-1/cancel", {}]]));
  });

  it("shows stopping… while a cancel for the turn is in the queue", async () => {
    const queue = { ...EMPTY_QUEUE, pending: [record("C-1", "cancel", { target: "A-1" })] };
    serve([thread("main")], RUNNING, { queue });
    mount();
    const button = (await screen.findByRole("button", {
      name: "stopping…",
    })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("cancels a queued turn by its own id, leaving the running one", async () => {
    const queue = {
      ...EMPTY_QUEUE,
      pending: [record("A-2", "turn", { thread: "main", text: "wait for me" })],
    };
    const fetch = serve([thread("main")], RUNNING, { queue });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(posts(fetch)).toEqual([["/api/actions/A-2/cancel", {}]]));
  });
});

describe("a dispatch line (ADR 190)", () => {
  it("links to the task and to the queue", async () => {
    window.localStorage.setItem(VIEW_KEY, "commands");
    const log = [
      LOG[0]!,
      {
        n: 2,
        event: {
          type: "assistant",
          message: {
            content: [
              {
                type: "tool_use",
                id: "tu-9",
                name: "mcp__board__task_start",
                input: { task_id: "T-114" },
              },
            ],
          },
        },
      },
      {
        n: 3,
        event: {
          type: "user",
          message: {
            content: [
              {
                type: "tool_result",
                tool_use_id: "tu-9",
                content: JSON.stringify({ id: "A-9", task_id: "T-114" }),
              },
            ],
          },
        },
      },
    ];
    serve([thread("main")], log);
    const root = createRootRoute();
    const router = createRouter({
      routeTree: root.addChildren([
        createRoute({ getParentRoute: () => root, path: "/tasks/$taskId" }),
        createRoute({ getParentRoute: () => root, path: "/queue" }),
      ]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RouterContextProvider router={router}>
          <ChatPanel onClose={() => {}} pollMs={60_000} />
        </RouterContextProvider>
      </QueryClientProvider>,
    );
    const task = await screen.findByRole("link", { name: "T-114" });
    expect(task.getAttribute("href")).toBe("/tasks/T-114");
    expect(screen.getByRole("link", { name: "A-9" }).getAttribute("href")).toBe("/queue");
  });
});

describe("the poll", () => {
  it("reads from since=0, then from the last row it has", async () => {
    const fetch = serve([thread("main")]);
    mount(20);
    await waitFor(() =>
      expect(fetch.mock.calls.map(([u]) => String(u))).toContain(
        "/api/threads/main/events?since=4",
      ),
    );
    const events = fetch.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/events"));
    expect(events[0]).toBe("/api/threads/main/events?since=0");
    expect(events.slice(1).every((u) => u.endsWith("since=4"))).toBe(true);
    // The rows already drawn are not drawn again.
    expect(screen.getAllByText("Listing.")).toHaveLength(1);
  });

  it("shows a bad row in place and draws the rows after it", async () => {
    serve([thread("main")], [LOG[0]!, { n: 2, event: null }, LOG[1]!]);
    const { container } = mount();
    await screen.findByText("Listing.");
    expect(container.querySelector('[data-kind="bad"]')?.textContent).toContain("line 2");
  });
});

describe("a new thread (ADR 195)", () => {
  it("posts the title, account and effort chosen, and selects the record", async () => {
    const fetch = serve([thread("main"), thread("t-1")], LOG, {
      accounts: [{ name: "cuenta1" }, { name: "cuenta2" }],
    });
    mount();
    await waitFor(async () => expect(await selected()).toBe("main"));
    fireEvent.click(screen.getByRole("button", { name: "New thread" }));
    const form = screen.getByRole("form", { name: "New thread" });
    const create = within(form).getByRole("button", { name: "Create" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "  the dock  " } });
    await within(form).findByRole("option", { name: "cuenta2" });
    fireEvent.change(within(form).getByLabelText("Account"), { target: { value: "cuenta2" } });
    fireEvent.change(within(form).getByLabelText("Effort"), { target: { value: "max" } });
    // `main` exists, so the form does not offer to open it.
    expect(within(form).queryByLabelText("the main thread")).toBeNull();
    fireEvent.click(create);
    await waitFor(() => expect(screen.queryByRole("form", { name: "New thread" })).toBeNull());
    expect(writes(fetch)).toEqual([
      ["POST", "/api/threads", { title: "the dock", account: "cuenta2", effort: "max" }],
    ]);
    expect(await selected()).toBe("t-new");
  });

  it("leaves account and effort to the service when they are not picked", async () => {
    const fetch = serve([thread("main")]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "New thread" }));
    const form = screen.getByRole("form", { name: "New thread" });
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "plain" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(writes(fetch)).toHaveLength(1));
    expect(writes(fetch)[0]).toEqual(["POST", "/api/threads", { title: "plain" }]);
  });

  it("opens the main thread with main: true when there is none", async () => {
    const fetch = serve([thread("t-1")], LOG, {
      write: (_m, _p, body) => created(thread("main", { title: String(body["title"]) })),
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "New thread" }));
    const form = screen.getByRole("form", { name: "New thread" });
    fireEvent.click(within(form).getByLabelText("the main thread"));
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(writes(fetch)).toHaveLength(1));
    expect(writes(fetch)[0]).toEqual([
      "POST",
      "/api/threads",
      { title: "Main thread", main: true },
    ]);
  });

  it("shows a refusal in the form and keeps what was typed", async () => {
    serve([thread("main")], LOG, {
      write: refusal(400, "task 'T-999' has no task file"),
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "New thread" }));
    const form = screen.getByRole("form", { name: "New thread" });
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "kept" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("has no task file");
    expect((within(form).getByLabelText("Title") as HTMLInputElement).value).toBe("kept");
    expect(await selected()).toBe("main");
  });
});

describe("editing a thread (ADR 195)", () => {
  it("renames with a PATCH that carries only the title", async () => {
    const fetch = serve([thread("main"), thread("t-1")]);
    mount();
    await waitFor(async () => expect(await selected()).toBe("main"));
    fireEvent.change(screen.getByRole("combobox", { name: "Thread" }), {
      target: { value: "t-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit thread" }));
    const form = screen.getByRole("form", { name: "Edit thread" });
    expect((within(form).getByLabelText("Title") as HTMLInputElement).value).toBe("Thread t-1");
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: " renamed " } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit thread" })).toBeNull());
    expect(writes(fetch)).toEqual([["PATCH", "/api/threads/t-1", { title: "renamed" }]]);
  });

  it("sends a new model and effort, and nothing when nothing changed", async () => {
    const fetch = serve([thread("main")]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit thread" }));
    let form = screen.getByRole("form", { name: "Edit thread" });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit thread" })).toBeNull());
    expect(writes(fetch)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Edit thread" }));
    form = screen.getByRole("form", { name: "Edit thread" });
    fireEvent.change(within(form).getByLabelText("Model"), { target: { value: "sonnet" } });
    fireEvent.change(within(form).getByLabelText("Effort"), { target: { value: "low" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(fetch)).toHaveLength(1));
    expect(writes(fetch)[0]).toEqual([
      "PATCH",
      "/api/threads/main",
      { model: "sonnet", effort: "low" },
    ]);
  });

  it("shows a refusal in the form and keeps what was typed", async () => {
    serve([thread("main")], LOG, { write: refusal(400, "title is longer than 200 characters") });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit thread" }));
    const form = screen.getByRole("form", { name: "Edit thread" });
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "too long" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("longer than 200");
    expect((within(form).getByLabelText("Title") as HTMLInputElement).value).toBe("too long");
  });
});

describe("archive and reopen (ADR 195)", () => {
  it("archives an open thread with {status: archived}", async () => {
    const fetch = serve([thread("main"), thread("t-1")]);
    mount();
    await waitFor(async () => expect(await selected()).toBe("main"));
    fireEvent.change(screen.getByRole("combobox", { name: "Thread" }), {
      target: { value: "t-1" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => expect(writes(fetch)).toHaveLength(1));
    expect(writes(fetch)[0]).toEqual(["PATCH", "/api/threads/t-1", { status: "archived" }]);
    // The record goes into the list: the thread stays listed, as archived, with Reopen.
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeTruthy();
    expect(
      within(screen.getByRole("combobox", { name: "Thread" }))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toContain("Thread t-1 · ia-harness — cuenta1 · opus (archived)");
  });

  it("reopens an archived thread with {status: open}", async () => {
    const fetch = serve([thread("t-old", { status: "archived" })]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(writes(fetch)).toHaveLength(1));
    expect(writes(fetch)[0]).toEqual(["PATCH", "/api/threads/t-old", { status: "open" }]);
  });

  it("gives main no Archive", async () => {
    serve([thread("main")]);
    mount();
    expect(await screen.findByRole("button", { name: "Edit thread" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reopen" })).toBeNull();
  });

  it("shows a refused archive by the composer", async () => {
    serve([thread("main"), thread("t-1")], LOG, { write: refusal(409, "the thread is busy") });
    mount(60_000, { threadId: "t-1", n: 1 });
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    expect((await screen.findByRole("alert")).textContent).toContain("the thread is busy");
  });
});

describe("select (ADR 195)", () => {
  it("opens on the thread a screen asked for, and follows a later ask", async () => {
    serve([thread("main"), thread("t-1"), thread("t-2")]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (select: { threadId: string; n: number }) => (
      <QueryClientProvider client={client}>
        <ChatPanel onClose={() => {}} pollMs={60_000} select={select} />
      </QueryClientProvider>
    );
    const { rerender } = render(view({ threadId: "t-1", n: 1 }));
    await waitFor(async () => expect(await selected()).toBe("t-1"));
    rerender(view({ threadId: "t-2", n: 2 }));
    await waitFor(async () => expect(await selected()).toBe("t-2"));
  });
});

function boardTask(id: string, over: Record<string, unknown> = {}) {
  return {
    task_id: id,
    status: "pending",
    owner: null,
    depends_on: [],
    phase: null,
    merged: false,
    title: `Task ${id}`,
    ...over,
  };
}

describe("executions (ADR 197)", () => {
  const TASKS = [
    boardTask("T-1", { status: "in_progress", phase: "implementador", owner: "cuenta2" }),
    boardTask("T-2", { status: "done" }),
    boardTask("T-3", { status: "done", merged: true }),
    boardTask("T-4", { status: "dropped" }),
    boardTask("T-5"),
    boardTask("T-6", { status: "epic" }),
    boardTask("T-7"),
  ];
  const QUEUE = { ...EMPTY_QUEUE, pending: [{ id: "a-1", kind: "start", task_id: "T-7" }] };

  it("lists the tasks in play under the threads, and not the closed or unstarted ones", async () => {
    serve([thread("main")], LOG, { tasks: TASKS, queue: QUEUE });
    mount();
    const select = await screen.findByRole("combobox", { name: "Thread" });
    const group = await waitFor(() => within(select).getByRole("group", { name: "Executions" }));
    expect(
      within(group)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      "T-1 · Task T-1 — running implementador",
      "T-2 · Task T-2 — done",
      "T-7 · Task T-7 — pending",
    ]);
    // main stays first, outside the group.
    expect(within(select).getAllByRole("option")[0]!.textContent).toBe("main — cuenta1 · opus");
  });

  it("lists no group when no task is in play", async () => {
    serve([thread("main")], LOG, { tasks: [TASKS[2], TASKS[3], TASKS[4]] });
    mount();
    const select = await screen.findByRole("combobox", { name: "Thread" });
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(1));
    expect(within(select).queryByRole("group")).toBeNull();
  });

  it("shows the execution with no composer when one is chosen, and the thread again after", async () => {
    serve([thread("main")], LOG, { tasks: TASKS });
    mountRouted();
    const select = await screen.findByRole("combobox", { name: "Thread" });
    await waitFor(() => within(select).getByRole("option", { name: /^T-1 / }));
    expect(screen.getByRole("textbox")).toBeTruthy();

    fireEvent.change(select, { target: { value: "task:T-1" } });
    expect(await screen.findByTestId("execution")).toBeTruthy();
    expect(await selected()).toBe("task:T-1");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("link", { name: "T-1" }).getAttribute("href")).toBe("/tasks/T-1");

    fireEvent.change(select, { target: { value: "main" } });
    expect(await screen.findByRole("textbox")).toBeTruthy();
    expect(screen.queryByTestId("execution")).toBeNull();
  });

  it("says when the board did not answer", async () => {
    const fetch = serve([thread("main")]);
    const real = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (input, init) =>
      String(input) === "/api/tasks"
        ? new Response("down", { status: 502, headers: { "content-type": "text/html" } })
        : real(input, init),
    );
    mount();
    expect(
      await screen.findByText("/api/tasks did not answer, so no execution is listed."),
    ).toBeTruthy();
  });
});

describe("the slots region (ADR 198)", () => {
  const acct = (name: string, over: Record<string, unknown> = {}) => ({
    name,
    container: name,
    is_primary: false,
    quota_threshold_pct: 80,
    reserve_pct: 20,
    quota_cooldown_seconds: 3600,
    state: "IDLE",
    current_task: null,
    rate_limited_at: null,
    last_probe: null,
    ...over,
  });
  const startRecord = (taskId: string, over: Record<string, unknown> = {}) => ({
    id: `act-${taskId}`,
    kind: "start",
    task_id: taskId,
    project: "ia-harness",
    waiting: null,
    created_at: "2026-10-20T10:00:00Z",
    status: "pending",
    ...over,
  });
  const slots = () => screen.getByRole("region", { name: "Slots" });

  it("draws a busy slot, an empty primary with its reserve, and what waits", async () => {
    serve([thread("main")], LOG, {
      accounts: [
        acct("cuenta1", { state: "BUSY", current_task: "T-9" }),
        acct("cuenta2", { is_primary: true, reserve_pct: 30 }),
      ],
      tasks: [
        { task_id: "T-9", phase: "implementador" },
        { task_id: "T-8", phase: null },
      ],
      queue: {
        ...EMPTY_QUEUE,
        claimed: [
          startRecord("T-9", { status: "claimed" }),
          startRecord("T-8", { status: "claimed" }),
        ],
        pending: [startRecord("T-7", { waiting: "account" })],
      },
    });
    mount();
    await within(await screen.findByRole("region", { name: "Slots" })).findByText("implementador");
    const text = slots().textContent ?? "";
    expect(text).toContain("1 free of 2");
    expect(text).toContain("T-9");
    expect(text).toContain("ia-harness");
    expect(text).toContain("primary · 30% reserve");
    expect(text).toContain("empty");
    expect(text).toContain("Between phases");
    expect(text).toContain("Waiting for an account — every slot is full");
  });

  it("draws a closed slot with its reset time", async () => {
    serve([thread("main")], LOG, {
      accounts: [
        acct("cuenta1", {
          state: "COOLING_DOWN",
          rate_limited_at: 1000,
          quota_cooldown_seconds: 500,
        }),
      ],
      tasks: [],
    });
    mount();
    expect(
      await within(await screen.findByRole("region", { name: "Slots" })).findByText(
        /closed — refused by the provider/,
      ),
    ).toBeTruthy();
    expect(slots().textContent).toContain("00:25:00 UTC");
  });

  it("says the slots are not known when /api/accounts does not answer", async () => {
    serve([thread("main")], LOG, { accountsDown: true });
    mount();
    expect(
      await screen.findByText(/\/api\/accounts did not answer, so the slots are not known/),
    ).toBeTruthy();
  });

  it("says phase not known when /api/tasks does not answer", async () => {
    serve([thread("main")], LOG, {
      accounts: [acct("cuenta1", { state: "BUSY", current_task: "T-9" })],
      tasksDown: true,
    });
    mount();
    expect(await screen.findByText("phase not known")).toBeTruthy();
  });
});
