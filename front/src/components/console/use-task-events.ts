import { useEffect, useState } from "react";
import { listTaskEvents } from "@/lib/api/client";
import { POLL_MS } from "@/lib/api/queries";
import type { HookEvent } from "@/lib/api/types";

/** The route's default page (ADR NNN). A full page means there may be more behind it. */
const PAGE = 500;
/** How many full pages one poll reads before it waits for the next. */
const MAX_PAGES = 20;

export interface TaskEvents {
  rows: HookEvent[];
  warnings: string[];
  error: string | null;
  loaded: boolean;
}

const EMPTY: TaskEvents = { rows: [], warnings: [], error: null, loaded: false };

/**
 * A task's hook events, read from a cursor (`docs/decisions.md` ADR NNN), on
 * `useThreadEvents`'s model: the first read is `since=0`, every later one
 * `since=<last id>`, a full page is followed at once up to `MAX_PAGES`, and a
 * row at or below the cursor is dropped rather than drawn twice. The events
 * table only grows, so a cursor is enough. A failed read keeps the rows it has
 * and says so; the next poll tries again from the same cursor. A change of task
 * starts over. `pollMs` is for the tests.
 */
export function useTaskEvents(id: string | null, pollMs: number = POLL_MS): TaskEvents {
  const [state, setState] = useState<TaskEvents>(EMPTY);

  useEffect(() => {
    setState(EMPTY);
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let since = 0;

    async function poll() {
      try {
        for (let page = 0; page < MAX_PAGES; page++) {
          const { data, warnings } = await listTaskEvents(id!, since);
          if (cancelled) return;
          if (!Array.isArray(data)) {
            setState((s) => ({ ...s, loaded: true, error: "the events answered no list of rows" }));
            break;
          }
          const fresh = data.filter((row) => typeof row?.id === "number" && row.id > since);
          if (fresh.length > 0) since = fresh[fresh.length - 1]!.id;
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
