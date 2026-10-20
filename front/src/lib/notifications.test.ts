/**
 * The inbox's read state (`docs/plans/notifications.md` step 2): a last-seen
 * marker per browser, in `localStorage`, until the console has users.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import {
  LAST_SEEN_KEY,
  isUnread,
  markSeen,
  readLastSeen,
  unreadCount,
  useLastSeen,
} from "./notifications";
import type { Notification } from "./api/types";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function note(id: string): Notification {
  return { id, kind: "merged", at: "2026-10-20T12:00:00+00:00", title: id, url: "/" };
}

const OLD = "n-20261020T110000000000Z-aaaa";
const MID = "n-20261020T120000000000Z-bbbb";
const NEW = "n-20261020T130000000000Z-cccc";

describe("the last-seen marker", () => {
  it("is null in a browser that has not opened the inbox", () => {
    expect(readLastSeen()).toBeNull();
  });

  it("is the newest id the inbox showed, kept in localStorage", () => {
    markSeen([note(MID), note(OLD)]);
    expect(window.localStorage.getItem(LAST_SEEN_KEY)).toBe(MID);
    expect(readLastSeen()).toBe(MID);
  });

  it("never moves back: an older newest-record leaves it where it was", () => {
    markSeen([note(NEW)]);
    markSeen([note(MID)]);
    expect(readLastSeen()).toBe(NEW);
  });

  it("is left alone by an empty inbox", () => {
    markSeen([]);
    expect(readLastSeen()).toBeNull();
  });

  it("reads as unset, and writes as nothing, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readLastSeen()).toBeNull();
    expect(() => markSeen([note(NEW)])).not.toThrow();
  });
});

describe("unread", () => {
  it("is every record newer than the marker", () => {
    const records = [note(NEW), note(MID), note(OLD)];
    expect(unreadCount(records, MID)).toBe(1);
    expect(isUnread(note(NEW), MID)).toBe(true);
    expect(isUnread(note(MID), MID)).toBe(false);
  });

  it("is every record when there is no marker yet", () => {
    expect(unreadCount([note(NEW), note(OLD)], null)).toBe(2);
  });

  it("is nothing when there is nothing", () => {
    expect(unreadCount(null, null)).toBe(0);
  });
});

describe("useLastSeen", () => {
  it("follows a mark made elsewhere in the page", () => {
    const { result } = renderHook(() => useLastSeen());
    expect(result.current).toBeNull();
    act(() => markSeen([note(MID)]));
    expect(result.current).toBe(MID);
  });

  it("follows a mark made in another tab", () => {
    const { result } = renderHook(() => useLastSeen());
    act(() => {
      window.localStorage.setItem(LAST_SEEN_KEY, NEW);
      window.dispatchEvent(new StorageEvent("storage", { key: LAST_SEEN_KEY }));
    });
    expect(result.current).toBe(NEW);
  });
});
