/**
 * The dock's slots panel draws *Waiting for the project* (ADR 204): the line
 * names the task holding the project when the queue shows it, and says the
 * reason alone when it does not. The wording is `lib/slots.ts`'s; this checks
 * the panel draws it, through the same `fetch` seam the route tests use.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import type { AnyRoute } from "@tanstack/react-router";
import { SlotsPanel } from "./slots";
import { renderRoute } from "@/test/render-route";

afterEach(cleanup);

/** `renderRoute` takes only a route's `component`, so the panel is mounted as one. */
const panelRoute = { options: { component: SlotsPanel } } as unknown as AnyRoute;

const account = (name: string, state: string, current: string | null = null) => ({
  name,
  container: name,
  is_primary: false,
  quota_threshold_pct: 80,
  reserve_pct: 20,
  quota_cooldown_seconds: 3600,
  state,
  current_task_id: current,
  rate_limited_at: null,
  last_probe: null,
});

const start = (taskId: string, over: Record<string, unknown> = {}) => ({
  kind: "start",
  task_id: taskId,
  project: "ia-harness",
  waiting: null,
  ...over,
});

const worker = { heartbeat: null, stale: true, stale_after_seconds: 30 };

function panel(pending: unknown[], claimed: unknown[]) {
  return renderRoute(panelRoute, "/", {
    "/api/accounts": { data: [account("cuenta1", "IDLE"), account("cuenta2", "BUSY", "T-012")] },
    "/api/tasks": { data: [] },
    "/api/actions": { data: { pending, claimed, done: [], done_total: 0, worker } },
  });
}

describe("the dock's slots panel, for a start waiting for the project", () => {
  it("names the task holding the project", async () => {
    await panel([start("T-013", { waiting: "project" })], [start("T-012")]);
    expect(await screen.findByText("Waiting for the project — behind T-012")).toBeTruthy();
  });

  it("says the reason alone when the queue shows no holder", async () => {
    await panel([start("T-013", { waiting: "project" })], [start("T-012", { project: "other" })]);
    expect(await screen.findByText("Waiting for the project")).toBeTruthy();
    expect(screen.queryByText(/behind/)).toBeNull();
  });
});
