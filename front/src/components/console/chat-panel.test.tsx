/**
 * The chat dock over a stubbed `/api/threads` and thread log (`docs/decisions.md`
 * ADR 184): the selector's order, the switch and its memory, the meter, and the
 * cursor the log is polled from; and the composer, *stop* and the dispatch
 * line's links (ADR 190).
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
}

/** Serves the thread list, and the log's rows past each request's `since`. */
function serve(threads: Thread[], log: { n: number; event: unknown }[] = LOG, extras: Extras = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://console");
    if (url.pathname === "/api/threads") return json(threads);
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

/** The POSTs the dock made: `[path, parsed body]`. */
function posts(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([u, init]) => [String(u), JSON.parse(String(init?.body))] as const);
}

function mount(pollMs = 60_000) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel onClose={() => {}} pollMs={pollMs} />
    </QueryClientProvider>,
  );
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
    expect(await screen.findByText(/thread yet\. The board service opens it/)).toBeTruthy();
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
