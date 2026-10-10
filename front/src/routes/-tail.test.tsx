/**
 * The Live tail's third bare state.
 *
 * Its other two were already right — both test the unfiltered buffer, so neither
 * ever claimed a quiet harness on a filter's behalf — and what the screen had no
 * sentence for at all was a buffer holding events with every one of them filtered
 * out. `docs/ui.md` *Absent, empty and broken are three different things* gives
 * that state its words, and a screen with more than one filter names every
 * control it would take to bring the rows back rather than one query.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Route, TailNoMatch } from "./tail";
import { renderRoute } from "@/test/render-route";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

describe("a tail whose filters matched nothing", () => {
  it("names its three controls and the buffered count", () => {
    const { container } = render(<TailNoMatch buffered={312} />);
    expect(container.textContent).toContain("No event matches these filters");
    expect(container.textContent).toContain("payload filter");
    expect(container.textContent).toContain("source and type");
    expect(container.textContent).toContain("312 events");
  });

  // What this state may not say: the collection is not empty, and the screen's
  // own Empty sentence is about a collector holding nothing.
  it("says nothing about the collector being quiet", () => {
    const { container } = render(<TailNoMatch buffered={312} />);
    expect(container.textContent).not.toContain("No events yet");
    expect(container.textContent).not.toContain("holds no events");
  });

  it("counts one buffered event in the singular", () => {
    const { container } = render(<TailNoMatch buffered={1} />);
    expect(container.textContent).toContain("the one event this tail has buffered");
    expect(container.textContent).not.toContain("1 events");
  });
});

// The tests above hand `TailNoMatch` its count; this one lets the screen pass it.
// This screen reads `client.ts` from a `useEffect` rather than through
// `useQuery`, which is why `renderRoute` stubs `fetch` and not the query cache.
// `docs/debt/T-017-D1.md`.
describe("the Live tail with a payload filter that matches none of its events", () => {
  it("says no event matches, and counts what was buffered", async () => {
    const event = (id: number) => ({
      id,
      source_app: "dispatcher",
      event_type: "phase_start",
      payload: { task: `T-00${id}` },
      created_at: "2026-10-10T12:00:00Z",
    });
    await renderRoute(Route, "/tail", {
      "/api/events": { data: [event(3), event(2), event(1)] },
    });
    await screen.findByText('{"task":"T-002"}');

    fireEvent.change(screen.getByPlaceholderText(/^Filter payload/), {
      target: { value: "zzz" },
    });

    expect(await screen.findByText("No event matches these filters")).toBeTruthy();
    expect(document.body.textContent).toContain("the 3 events this tail has buffered");
    expect(document.body.textContent).not.toContain("No events yet");
  });
});
