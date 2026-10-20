/**
 * The execution thread's view (`docs/decisions.md` ADR 197) over a stubbed task,
 * usage, queue, threads and task events: the start note, each phase's segment
 * with its account and model, the *Text* / *Text and commands* switch, the
 * handoff and the gates; the empty state and a read that fails; and the way to
 * the task's own thread.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useState } from "react";
import type { Task } from "@/lib/api/types";
import type { ThreadView } from "@/lib/thread";
import { ExecutionView } from "./execution-view";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TASK: Task = {
  task_id: "T-7",
  status: "in_progress",
  owner: "cuenta2",
  depends_on: [],
  heartbeat: null,
  description: null,
  resolved_debt: [],
  phase: null,
  lock_expired: null,
  priority: "normal",
  source: "operator",
  tags: [],
  rank: null,
  debt_id: null,
  epic: null,
  split_into: [],
  merged: false,
  title: "Seven",
};

const BODY = [
  "The task.",
  "",
  "## cartografo",
  "",
  "Mapped **the** module.",
  "",
  "## implementador",
  "",
  "Built it.",
  "",
  "**Dispatcher gates**",
  "- `tests-run` (blocking) — no test run recorded",
].join("\n");

const CARTO = {
  call: "phase",
  session_id: "s-c",
  recorded_at: "2026-10-20T10:10:00Z",
  model: "opus",
};
const IMPL = {
  call: "phase",
  session_id: "s-i",
  recorded_at: "2026-10-20T11:00:00Z",
  model: "sonnet",
};

const USAGE = [
  {
    task_id: "T-7",
    role: "cartografo",
    round: null,
    account: "cuenta1",
    calls: [CARTO],
    call_count: 1,
    call_kinds: ["phase"],
    measured_calls: 1,
    unmeasured_calls: 0,
    phase_call: CARTO,
  },
  {
    task_id: "T-7",
    role: "implementador",
    round: null,
    account: "cuenta2",
    calls: [IMPL],
    call_count: 1,
    call_kinds: ["phase"],
    measured_calls: 1,
    unmeasured_calls: 0,
    phase_call: IMPL,
  },
];

const EVENTS = [
  {
    id: 1,
    source_app: "ia-harness",
    event_type: "UserPromptSubmit",
    payload: { session_id: "s-c", prompt: "map the module" },
    created_at: "2026-10-20T10:00:00Z",
  },
  {
    id: 2,
    source_app: "ia-harness",
    event_type: "PreToolUse",
    payload: {
      session_id: "s-c",
      tool_name: "Bash",
      tool_input: { command: "ls" },
      tool_use_id: "u",
    },
    created_at: "2026-10-20T10:01:00Z",
  },
  {
    id: 3,
    source_app: "ia-harness",
    event_type: "PostToolUse",
    payload: { session_id: "s-c", tool_name: "Bash", tool_response: "a.txt", tool_use_id: "u" },
    created_at: "2026-10-20T10:01:01Z",
  },
];

const QUEUE = {
  pending: [],
  claimed: [],
  done: [
    {
      id: "a-20261020T095900000000Z-0001",
      kind: "start",
      task_id: "T-7",
      note: "mind the cache",
      actor: "operator",
      status: "done",
      created_at: "2026-10-20T09:59:00Z",
    },
  ],
  done_total: 1,
  worker: { heartbeat: null, stale: false, stale_after_seconds: 30 },
};

function json(data: unknown) {
  return new Response(JSON.stringify({ data, warnings: [] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

interface Stubs {
  body?: string;
  usage?: unknown;
  events?: unknown;
  queue?: unknown;
  threads?: unknown;
  /** Paths answered with a 500 instead. */
  fail?: string[];
}

function serve(stubs: Stubs = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://console");
    const path = url.pathname;
    if (stubs.fail?.includes(path)) {
      return new Response(JSON.stringify({ error: "boom" }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
    if (path === "/api/tasks/T-7") return json({ ...TASK, body: stubs.body ?? BODY });
    if (path === "/api/tasks/T-7/usage") return json(stubs.usage ?? USAGE);
    if (path === "/api/tasks/T-7/events") {
      const since = Number(url.searchParams.get("since") ?? "0");
      const rows = (stubs.events ?? EVENTS) as { id: number }[];
      return json(rows.filter((r) => r.id > since));
    }
    if (path === "/api/actions") return json(stubs.queue ?? QUEUE);
    if (path === "/api/threads") return json(stubs.threads ?? []);
    return new Response("not found", { status: 404, headers: { "content-type": "text/html" } });
  });
}

function mount(onOpenThread: (id: string) => void = () => {}) {
  const root = createRootRoute();
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/tasks/$taskId" }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Harness() {
    const [view, setView] = useState<ThreadView>("text");
    return (
      <ExecutionView
        task={TASK}
        view={view}
        onView={setView}
        onOpenThread={onOpenThread}
        pollMs={60_000}
      />
    );
  }
  return render(
    <QueryClientProvider client={client}>
      <RouterContextProvider router={router}>
        <Harness />
      </RouterContextProvider>
    </QueryClientProvider>,
  );
}

function segments(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-kind="segment"]'));
}

describe("the execution view", () => {
  it("draws the start note, then each phase with its account, model and handoff", async () => {
    serve();
    mount();
    await waitFor(() => expect(segments()).toHaveLength(2));
    const note = document.querySelector<HTMLElement>('[data-kind="start-note"]')!;
    expect(within(note).getByText("mind the cache")).toBeTruthy();
    expect(within(note).getByText("09:59:00")).toBeTruthy();

    const [carto, impl] = segments();
    expect(carto!.getAttribute("data-role")).toBe("cartografo");
    expect(carto!.textContent).toContain("cuenta1 · opus");
    const handoff = carto!.querySelector<HTMLElement>('[data-kind="handoff"]')!;
    expect(handoff.textContent).toContain("Mapped **the** module.");
    expect(impl!.textContent).toContain("cuenta2 · sonnet");
    expect(within(impl!).getByText("Built it.")).toBeTruthy();

    // The view is read only: a link to the task page and no composer.
    expect(screen.getByRole("link", { name: "T-7" }).getAttribute("href")).toBe("/tasks/T-7");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows a phase's gates under its handoff", async () => {
    serve();
    mount();
    await waitFor(() => expect(segments()).toHaveLength(2));
    const gates = segments()[1]!.querySelector<HTMLElement>('[data-kind="gates"]')!;
    expect(within(gates).getByText("tests-run")).toBeTruthy();
    expect(within(gates).getByText("no test run recorded")).toBeTruthy();
    expect(segments()[0]!.querySelector('[data-kind="gates"]')).toBeNull();
  });

  it("shows the prompt under Text, and the commands on the other position", async () => {
    serve();
    mount();
    await screen.findByText("map the module");
    const carto = () => segments()[0]!;
    expect(carto().querySelector('[data-kind="tool"]')).toBeNull();
    expect(within(segments()[1]!).getByText("No text event recorded.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Text and commands" }));
    expect(
      screen.getByRole("button", { name: "Text and commands" }).getAttribute("aria-pressed"),
    ).toBe("true");
    const tool = carto().querySelector<HTMLElement>('[data-kind="tool"]')!;
    expect(tool.textContent).toContain("Bash");
    expect(tool.textContent).toContain("ls");
    fireEvent.click(within(tool).getByRole("button"));
    expect(within(tool).getByText("a.txt")).toBeTruthy();
    expect(within(segments()[1]!).getByText("No event recorded.")).toBeTruthy();
  });

  it("says when no phase has run or written yet, and when there is no start note", async () => {
    serve({ body: "Only the description.", usage: [], events: [], queue: { ...QUEUE, done: [] } });
    mount();
    expect(await screen.findByText("No phase of this task has run or written yet.")).toBeTruthy();
    expect(screen.getByText("none in the queue's latest records")).toBeTruthy();
    expect(segments()).toHaveLength(0);
  });

  it("says which read failed, and keeps drawing what the others answered", async () => {
    serve({ fail: ["/api/tasks/T-7/events", "/api/tasks/T-7/usage"] });
    mount();
    expect(await screen.findByText(/^Its events did not answer:/)).toBeTruthy();
    expect(await screen.findByText(/^Its usage did not answer:/)).toBeTruthy();
    // The body still gives the phases; without usage they have no account or model.
    await waitFor(() => expect(segments()).toHaveLength(2));
    expect(segments()[0]!.textContent).toContain("— · —");
  });

  it("says when the task itself did not answer", async () => {
    serve({ fail: ["/api/tasks/T-7"], usage: [], events: [] });
    mount();
    expect(await screen.findByText(/^The task did not answer:/)).toBeTruthy();
    expect(screen.queryByText("Reading the task…")).toBeNull();
  });

  it("opens the task's own thread when it has one", async () => {
    serve({
      threads: [
        { id: "t-old", task: "T-7", status: "archived" },
        { id: "t-talk", task: "T-7", status: "open" },
      ],
    });
    const open = vi.fn();
    mount(open);
    fireEvent.click(await screen.findByRole("button", { name: "Open its thread" }));
    expect(open).toHaveBeenCalledWith("t-talk");
  });

  it("offers no thread when the task has none", async () => {
    serve();
    mount();
    await waitFor(() => expect(segments()).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Open its thread" })).toBeNull();
  });
});
