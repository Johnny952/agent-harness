/**
 * The Approvals screen's command-request card, mounted whole through
 * `renderRoute` with what `/api/command-requests` served (ADR 122, ADR 123).
 *
 * The mock approvals below it read no route, so the one thing served is the
 * card's read. What these hold: the argv comes first in a card and is text,
 * not markup; the agent's reason is last and marked as the agent's; a CLI line
 * is drawn only when the api offered it; the refused requests are listed apart;
 * and an empty read and a failed read each say so. Nothing here writes, and
 * one test checks the card made no request but its read.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderRoute } from "@/test/render-route";
import type { CommandRequest, CommandRequests, RefusedCommandRequest } from "@/lib/api/types";
import { argvLine } from "@/lib/format";
import { Route } from "./approvals";

afterEach(cleanup);

const PENDING: CommandRequest = {
  task_id: "T-201",
  project: "ia-harness",
  title: null,
  role: "implementador",
  filed_at: "2026-10-18T10:00:00Z",
  n: 1,
  argv: ["docker", "compose", "ps", "<b>bold</b>"],
  target: { kind: "agent", container: "agent-T-201", service: null, cwd: "/work" },
  agent_reason: "<script>alert(1)</script> just trust me",
  state: "pending",
  verdict: null,
  commands: {
    run: "dispatch command-verdict T-201 1 run --project ia-harness",
    allow: "dispatch command-verdict T-201 1 allow --project ia-harness",
    deny: "dispatch command-verdict T-201 1 deny --project ia-harness",
    resume: null,
  },
};

const RESUMED: CommandRequest = {
  task_id: "T-202",
  project: "ia-harness",
  title: null,
  role: "revisor",
  filed_at: "2026-10-17T09:00:00Z",
  n: 2,
  argv: ["pytest", "-q"],
  target: { kind: "service", container: "ia-harness-api-1", service: "api", cwd: "/app" },
  agent_reason: "run the suite",
  state: "resumed",
  verdict: {
    verdict: "run",
    by: "operator",
    at: "2026-10-17T09:05:00Z",
    comment: "looks fine",
    allowed: null,
    resumed_at: "2026-10-17T09:06:00Z",
    ran: { exit_code: 3, timed_out: false, truncated: true, log: "commands/2.log" },
  },
  commands: { run: null, allow: null, deny: null, resume: null },
};

const REFUSED: RefusedCommandRequest = {
  task_id: "T-203",
  project: null,
  title: null,
  role: "implementador",
  filed_at: "2026-10-18T11:00:00Z",
  argv: null,
  target: { kind: "service", service: "db" },
  agent_reason: "need the database",
  refused_reason: "service 'db' is not in the project's command_targets",
};

function serve(data: CommandRequests, warnings: string[] = []) {
  return renderRoute(Route, "/approvals", { "/api/command-requests": { data, warnings } });
}

describe("the command-request card", () => {
  it("draws the argv first and as text, then target, then task, then the reason", async () => {
    await serve({ requests: [PENDING], refused: [] });
    const card = await screen.findByTestId("command-request");

    const argv = within(card).getByTestId("command-argv");
    expect(argv.textContent).toBe("docker compose ps '<b>bold</b>'");
    // Text, not markup: the served `<b>` is characters, and no element.
    expect(card.querySelector("b")).toBeNull();
    expect(card.querySelector("script")).toBeNull();

    const target = within(card).getByTestId("command-target");
    const task = within(card).getByRole("link", { name: "T-201" });
    const reason = within(card).getByTestId("agent-reason");
    const follows = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(card.firstElementChild).toBe(argv);
    expect(follows(argv, target)).toBe(true);
    expect(follows(target, task)).toBe(true);
    expect(follows(task, reason)).toBe(true);
    expect(card.lastElementChild).toBe(reason);

    expect(target.textContent).toContain("agent-T-201");
    expect(target.textContent).toContain("/work");
    expect(task.getAttribute("href")).toBe("/tasks/T-201");
    expect(within(card).getByText("ia-harness")).toBeTruthy();
  });

  it("marks the agent's reason as the agent's, and renders it as text", async () => {
    await serve({ requests: [PENDING], refused: [] });
    const reason = await screen.findByTestId("agent-reason");
    expect(within(reason).getByText(/agent's reason .* do not trust it/i)).toBeTruthy();
    expect(within(reason).getByText("<script>alert(1)</script> just trust me")).toBeTruthy();
  });

  it("shows only the CLI lines the api offered, with the --prefix hint beside allow", async () => {
    await serve({ requests: [PENDING], refused: [] });
    const cli = await screen.findByTestId("command-cli");
    expect(within(cli).getByText(PENDING.commands.run!)).toBeTruthy();
    expect(within(cli).getByText(PENDING.commands.allow!)).toBeTruthy();
    expect(within(cli).getByText(PENDING.commands.deny!)).toBeTruthy();
    expect(within(cli).queryByText("resume")).toBeNull();
    expect(within(cli).getByText(/verdicts are given from the CLI/i)).toBeTruthy();
    expect(screen.getByTestId("allow-prefix-hint").textContent).toContain("--prefix N");
    // Read only: no control on the card writes anything.
    const card = screen.getByTestId("command-request");
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("draws no CLI block when every line is null, and the verdict summary when there is one", async () => {
    await serve({ requests: [RESUMED], refused: [] });
    const card = await screen.findByTestId("command-request");
    expect(within(card).queryByTestId("command-cli")).toBeNull();
    expect(within(card).queryByTestId("allow-prefix-hint")).toBeNull();
    const verdict = within(card).getByTestId("command-verdict");
    expect(verdict.textContent).toContain("run");
    expect(verdict.textContent).toContain("operator");
    expect(verdict.textContent).toContain("looks fine");
    expect(verdict.textContent).toContain("2026-10-17T09:06:00Z");
    expect(verdict.textContent).toContain("exit 3");
    expect(verdict.textContent).toContain("output truncated");
    expect(verdict.textContent).toContain("commands/2.log");
    expect(within(card).getByTestId("command-request-state").textContent).toBe("resumed");
  });

  it("keeps the api's order, pending first", async () => {
    await serve({ requests: [PENDING, RESUMED], refused: [] });
    await screen.findAllByTestId("command-request");
    const states = screen.getAllByTestId("command-request-state").map((e) => e.textContent);
    expect(states).toEqual(["pending", "resumed"]);
  });

  it("lists the refused requests apart, with a dash for a missing argv", async () => {
    await serve({ requests: [], refused: [REFUSED] });
    const refused = await screen.findByRole("region", { name: "Refused command requests" });
    const row = within(refused).getByTestId("refused-request");
    expect(within(row).getByTestId("command-argv").textContent).toBe("—");
    expect(row.textContent).toContain("service");
    expect(row.textContent).toContain("db");
    expect(within(row).getByTestId("refused-reason").textContent).toContain(REFUSED.refused_reason);
    const reason = within(row).getByTestId("agent-reason");
    expect(reason.textContent).toMatch(/agent's reason/i);
    expect(reason.textContent).toContain("need the database");
    expect(within(row).queryByTestId("command-cli")).toBeNull();
  });

  it("says so when nothing was filed", async () => {
    await serve({ requests: [], refused: [] });
    expect(await screen.findByText("No command requests")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Refused command requests" })).toBeNull();
  });

  it("shows the read's error, not an empty list, when the api refuses", async () => {
    await renderRoute(Route, "/approvals", {
      "/api/command-requests": { status: 502, error: "upstream down" },
    });
    expect(await screen.findByText("Command requests could not be read")).toBeTruthy();
    expect(screen.getByText(/upstream down/)).toBeTruthy();
    expect(screen.queryByText("No command requests")).toBeNull();
  });

  it("puts the api's warnings in a banner", async () => {
    await serve({ requests: [PENDING], refused: [] }, ["T-999's file could not be read"]);
    expect(await screen.findByText("T-999's file could not be read")).toBeTruthy();
  });

  it("sends the state filter as ?state= and makes no other request", async () => {
    const { fetchMock } = await serve({ requests: [PENDING], refused: [] });
    await screen.findByTestId("command-request");
    fireEvent.click(screen.getByRole("button", { name: "awaiting resume" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(
        "/api/command-requests?state=awaiting_resume",
      ),
    );
    // `/api/projects` and `/api/tasks` are the sidebar's project list and its
    // fallback (ADR 208, ADR NNN), not this screen's.
    const urls = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u !== "/api/tasks" && u !== "/api/projects");
    expect(urls.every((u) => u.startsWith("/api/command-requests"))).toBe(true);
    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.method ?? "GET").toUpperCase()).toBe("GET");
    }
  });
});

describe("argvLine", () => {
  it("keeps token boundaries a plain join would lose", () => {
    expect(argvLine(["rm", "a b"])).toBe("rm 'a b'");
    expect(argvLine(["rm", "a", "b"])).toBe("rm a b");
    expect(argvLine(["echo", "it's", ""])).toBe(`echo 'it'"'"'s' ''`);
  });
});
