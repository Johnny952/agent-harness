import { useEffect, useRef, useState } from "react";

/** Ticking clock so heartbeat ages stay honest without re-fetching. */
export function useNow(intervalMs = 2000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export const FOCUS_SEARCH_EVENT = "console:focus-search";

/** `/` focuses the screen's search field. */
export function useSearchHotkey() {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const handler = () => ref.current?.focus();
    window.addEventListener(FOCUS_SEARCH_EVENT, handler);
    return () => window.removeEventListener(FOCUS_SEARCH_EVENT, handler);
  }, []);
  return ref;
}

/** Last-refreshed stamp for a screen. */
export function useLastRefreshed(dep: unknown) {
  const [at, setAt] = useState<number>(() => Date.now());
  useEffect(() => {
    setAt(Date.now());
  }, [dep]);
  return at;
}
