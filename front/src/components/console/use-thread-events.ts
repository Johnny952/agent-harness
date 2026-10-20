import { useEffect, useState } from "react";
import { listThreadEvents } from "@/lib/api/client";
import { POLL_MS } from "@/lib/api/queries";
import type { ThreadEventRow } from "@/lib/api/types";

/** The api's default page (ADR 180). A full page means there may be more behind it. */
const PAGE = 500;
/** How many full pages one poll reads before it waits for the next. */
const MAX_PAGES = 20;

export interface ThreadEvents {
  rows: ThreadEventRow[];
  warnings: string[];
  error: string | null;
  loaded: boolean;
}

const EMPTY: ThreadEvents = { rows: [], warnings: [], error: null, loaded: false };

/**
 * One thread's log, read from a cursor (`docs/decisions.md` ADR 180, ADR 184).
 *
 * The first read is `since=0`; every later one is `since=<last n>`, so a poll
 * asks only for the lines appended since and the rows already drawn are never
 * read again. A full page is followed at once, up to `MAX_PAGES`, so a long log
 * loads without waiting a poll per 500 lines. The log is append-only, which is
 * what makes a cursor enough; a row at or below the cursor is dropped rather
 * than drawn twice. A failed read keeps the rows it has and says so, and the
 * next poll tries again from the same cursor. A change of thread starts over.
 *
 * Not a React Query: a query's data is replaced whole on each fetch, and this
 * one grows. `pollMs` is for the tests.
 */
export function useThreadEvents(id: string | null, pollMs: number = POLL_MS): ThreadEvents {
  const [state, setState] = useState<ThreadEvents>(EMPTY);

  useEffect(() => {
    setState(EMPTY);
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let since = 0;

    async function poll() {
      try {
        for (let page = 0; page < MAX_PAGES; page++) {
          const { data, warnings } = await listThreadEvents(id!, since);
          if (cancelled) return;
          if (!Array.isArray(data)) {
            setState((s) => ({ ...s, loaded: true, error: "the log answered no list of rows" }));
            break;
          }
          const fresh = data.filter((row) => typeof row?.n === "number" && row.n > since);
          if (fresh.length > 0) since = fresh[fresh.length - 1]!.n;
          setState((s) => ({
            rows: fresh.length > 0 ? [...s.rows, ...fresh] : s.rows,
            warnings: [...s.warnings, ...warnings.filter((w) => !s.warnings.includes(w))],
            error: null,
            loaded: true,
          }));
          if (data.length < PAGE) break;
        }
      } catch (cause) {
        if (cancelled) return;
        const message = cause instanceof Error ? cause.message : String(cause);
        setState((s) => ({ ...s, loaded: true, error: message }));
      }
      if (!cancelled) timer = setTimeout(poll, pollMs);
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [id, pollMs]);

  return state;
}
