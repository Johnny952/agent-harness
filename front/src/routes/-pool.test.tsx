/**
 * The account card's ceilings, which are the pool screen's one claim that can be
 * wrong while looking right. Since `docs/decisions.md` ADR 48 the primary answers
 * to two numbers — `reserve_pct` on its session, a paced ceiling on its week — and
 * the card used to print the first as if it were both. ADR 49 serves the probe
 * each was measured against as `last_probe`, and these tests hold the card to
 * reading that record: the two windows as two numbers, the week's pacing or its
 * fallback, the probe's age before anything else, and configuration worded as
 * configuration when there is no record at all (`docs/debt/T-020-D1.md`).
 *
 * The `-` on the filename is load-bearing, for the reason `-index.test.tsx`
 * gives: `@tanstack/router-plugin` reads every other file under `src/routes/` as
 * a route.
 *
 * The probes come from `mockProbes`, which is built against `Date.now()`, so
 * `now` here is the same clock and an age is whatever the fixture stamped it.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { AccountCard } from "./pool";
import { mockProbes } from "@/lib/api/mock/fixtures";
import type { Account, LastProbe } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const LIMITS = { quota_threshold_pct: 90, reserve_pct: 60, quota_cooldown_seconds: 1800 };

function account(isPrimary: boolean, lastProbe: LastProbe | null, over: Partial<Account> = {}) {
  return {
    name: isPrimary ? "cuenta1" : "cuenta2",
    container: isPrimary ? "agent-cuenta1" : "agent-cuenta2",
    is_primary: isPrimary,
    ...LIMITS,
    state: "IDLE",
    current_task_id: null,
    rate_limited_at: null,
    last_probe: lastProbe,
    ...over,
  } satisfies Account;
}

/** The card's text with whitespace collapsed, so an assertion reads like the screen. */
function cardText(a: Account): string {
  const { container } = render(
    <AccountCard account={a} lockedTask={null} lockJoinBroken={false} now={Date.now()} />,
  );
  return (container.textContent ?? "").replace(/\s+/g, " ");
}

describe("a paced primary", () => {
  it("shows the session and the week as two numbers, each against its own ceiling", () => {
    const text = cardText(account(true, mockProbes().pacedPrimary));
    expect(text).toContain("session22% of 60%");
    expect(text).toContain("week31% of 40.4%");
    expect(text).toContain("paced week · 4.5 days to the reset");
  });

  it("never labels the reserve as the week's ceiling", () => {
    const text = cardText(account(true, mockProbes().pacedPrimary));
    expect(text).not.toContain("reserve");
    expect(text).not.toContain("week31% of 60%");
  });

  it("names the window it parked on, with the paced number", () => {
    const probe = { ...mockProbes().pacedPrimary, week_pct: 44, exceeds: true };
    const text = cardText(account(true, probe, { state: "PRE_COOLDOWN" }));
    expect(text).toContain("Parked: the week's 44% over its 40.4%.");
    expect(text).not.toContain("the session's");
  });
});

describe("a primary whose week fell back", () => {
  it("shows the reserve as the week's number, and says it is a fallback and why", () => {
    const text = cardText(account(true, mockProbes().fallbackPrimary));
    expect(text).toContain("week31% of 60%");
    expect(text).toContain(
      "The week fell back to the reserve, because the reset clause 'Oct 15 at 5pm' did not parse.",
    );
    expect(text).not.toContain("days to the reset");
  });
});

describe("a worker", () => {
  it("shows its probe against its one threshold on both windows, and no pacing", () => {
    const text = cardText(account(false, mockProbes().worker));
    expect(text).toContain("session47.5% of 90%");
    expect(text).toContain("week63% of 90%");
    expect(text).not.toContain("paced");
    expect(text).not.toContain("fell back");
  });
});

describe("the probe's age", () => {
  it("is read first, and not marked when fresh", () => {
    const text = cardText(account(false, mockProbes().worker));
    expect(text).toMatch(/last probe\d+s ago/);
    expect(text).not.toContain("stale");
  });

  it("is marked stale past half an hour, and the numbers stay", () => {
    const probe = { ...mockProbes().worker, probed_at: Math.floor(Date.now() / 1000) - 7200 };
    const text = cardText(account(false, probe));
    expect(text).toContain("stale · 2h 0m ago");
    expect(text).toContain("Older than 30 minutes");
    expect(text).toContain("week63% of 90%");
  });
});

describe("no probe recorded", () => {
  it("words the primary's reserve as its configured session ceiling, never the week's", () => {
    const text = cardText(account(true, null));
    expect(text).toContain("last probenone recorded");
    expect(text).toContain("configuredsession 60% reserve");
    expect(text).toContain("The week's ceiling is set at each probe");
    expect(text).not.toMatch(/week\s*60%/);
  });

  it("shows a worker's configured threshold", () => {
    const text = cardText(account(false, null));
    expect(text).toContain("configured90% threshold, both windows");
  });

  it("does not claim which ceiling a parked primary crossed", () => {
    const text = cardText(account(true, null, { state: "PRE_COOLDOWN" }));
    expect(text).toContain("no probe recorded to say which");
  });
});

describe("a refused account", () => {
  // The api serves `rate_limited_at` as `dispatcher/state_machine.py:
  // record_rate_limit` stores it: epoch seconds, from `time.time()`. Handed to
  // `new Date(...)` it is read as milliseconds, lands in January 1970, and the
  // cooldown reads as long over — so the card never said a cooling account was
  // refused (T-021). `now` is fixed so the time left is exact.
  const now = 1_760_123_456_700;

  function refusedText(secondsAgo: number): string {
    const a = account(false, null, {
      state: "COOLING_DOWN",
      rate_limited_at: now / 1000 - secondsAgo,
    });
    const { container } = render(
      <AccountCard account={a} lockedTask={null} lockJoinBroken={false} now={now} />,
    );
    return (container.textContent ?? "").replace(/\s+/g, " ");
  }

  it("reads an epoch-seconds stamp inside its cooldown as refused, with the time left", () => {
    expect(refusedText(620)).toContain(
      "Refused by the provider. Back in 19m 40s (1800s cooldown).",
    );
  });

  it("is not refused once its cooldown has run out", () => {
    expect(refusedText(1900)).not.toContain("Refused by the provider");
  });
});
