/**
 * The inbox's read state (`docs/plans/notifications.md` step 2, ADR NNN).
 *
 * **Per browser, until the console has users.** The console has no person to
 * hang "read" on, so read state is one marker in this browser's
 * `localStorage`: the id of the newest record the inbox has shown. Every record
 * with a greater id is unread. Ids sort as written (`n-<UTC timestamp to the
 * microsecond>Z-<hex>`, `dispatcher/notifications.py`), so the comparison is a
 * string one and needs no clock. Another browser, a private window or cleared
 * storage starts with no marker, and with no marker every record is unread.
 *
 * Opening the inbox marks everything it shows as read; there is no per-record
 * toggle. The marker never moves back. The api knows nothing of it.
 */
import { useSyncExternalStore } from "react";
import type { Notification } from "./api/types";

export const LAST_SEEN_KEY = "ia-harness.notifications-seen";

/** Fired on `window` after this page moves the marker, for the sidebar's count. */
const SEEN_EVENT = "ia-harness:notifications-seen";

export function readLastSeen(): string | null {
  try {
    return window.localStorage.getItem(LAST_SEEN_KEY);
  } catch {
    return null;
  }
}

/** Marks every record in `records` as read: moves the marker to the newest id. */
export function markSeen(records: readonly Notification[]): void {
  const newest = records.reduce<string | null>(
    (best, r) => (typeof r.id === "string" && (best === null || r.id > best) ? r.id : best),
    null,
  );
  if (newest === null) return;
  const seen = readLastSeen();
  if (seen !== null && seen >= newest) return;
  try {
    window.localStorage.setItem(LAST_SEEN_KEY, newest);
  } catch {
    /* A private window or blocked storage: the inbox still reads, unremembered. */
  }
  window.dispatchEvent(new Event(SEEN_EVENT));
}

export function isUnread(record: Notification, lastSeen: string | null): boolean {
  return lastSeen === null || (typeof record.id === "string" && record.id > lastSeen);
}

export function unreadCount(
  records: readonly Notification[] | null | undefined,
  lastSeen: string | null,
): number {
  return records ? records.filter((r) => isUnread(r, lastSeen)).length : 0;
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === LAST_SEEN_KEY) onChange();
  };
  window.addEventListener(SEEN_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(SEEN_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** The marker, kept current when this page or another tab moves it. */
export function useLastSeen(): string | null {
  return useSyncExternalStore(subscribe, readLastSeen, () => null);
}
