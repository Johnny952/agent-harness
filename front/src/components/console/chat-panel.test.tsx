/**
 * The chat dock over a stubbed `/api/threads` and thread log (`docs/decisions.md`
 * ADR 184): the selector's order, the switch and its memory, the meter, the
 * read-only notice, and the cursor the log is polled from.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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

/** Serves the thread list, and the log's rows past each request's `since`. */
function serve(threads: Thread[], log: { n: number; event: unknown }[] = LOG) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://console");
    if (url.pathname === "/api/threads") return json(threads);
    if (url.pathname.endsWith("/events")) {
      const since = Number(url.searchParams.get("since") ?? "0");
      return json(log.filter((r) => r.n > since));
    }
    return new Response("not found", { status: 404, headers: { "content-type": "text/html" } });
  });
}

function mount(pollMs = 60_000) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel onClose={() => {}} pollMs={pollMs} />
    </QueryClientProvider>,
  );
}

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
  it("is read-only and says which step brings the composer", async () => {
    serve([thread("main")]);
    const { container } = mount();
    expect(
      await screen.findByText(/Read-only\. Sending a turn and stopping one land/),
    ).toBeTruthy();
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.queryByRole("button", { name: /send|stop/i })).toBeNull();
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
