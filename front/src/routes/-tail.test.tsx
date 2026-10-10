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
import { cleanup, render } from "@testing-library/react";
import { TailNoMatch } from "./tail";

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
});
