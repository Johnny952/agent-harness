import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { Button } from "@/components/ui/button";
import { notificationsQuery } from "@/lib/api/queries";
import type { Notification } from "@/lib/api/types";
import { formatDateClock } from "@/lib/format";
import { isUnread, markSeen, readLastSeen } from "@/lib/notifications";
import { readState, turnOff, turnOn, type PushState } from "@/lib/push";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/notifications")({
  head: () => ({
    meta: [
      { title: "Inbox — harness operations console" },
      {
        name: "description",
        content:
          "The harness's notifications, newest first: what needs the person and what only informs, each linking to its record.",
      },
      { property: "og:title", content: "Inbox — harness operations console" },
      { property: "og:description", content: "What the harness has told the person." },
    ],
  }),
  component: NotificationsPage,
});

/** The four kinds that need the person (`dispatcher/notifications.py`). */
const NEEDS_PERSON = new Set(["awaiting_review", "conflict", "command_request", "blocked"]);

function kindLabel(kind: unknown): string {
  return typeof kind === "string" && kind ? kind.replaceAll("_", " ") : "unknown";
}

/** What the record is about: exactly one of the three, by the writer's rule. */
function subject(n: Notification): string | null {
  for (const v of [n.task, n.thread, n.account]) if (typeof v === "string" && v) return v;
  return null;
}

/** A console path only: anything else is shown, not followed. */
function internalPath(url: unknown): string | null {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : null;
}

function NotificationsPage() {
  const query = useQuery(notificationsQuery);
  // The marker as it stood when the inbox opened: what was new then stays
  // marked new while the page is open, though it is seen from now on.
  const [seenBefore] = useState(readLastSeen);
  const records = query.data?.data;
  const list = Array.isArray(records) ? records : null;

  useEffect(() => {
    if (list) markSeen(list);
  }, [list]);

  return (
    <AppShell>
      <PageHeader
        title="Inbox"
        subtitle="What the harness has told the person, newest first. Read state is kept in this browser, not on the server: another browser counts its own."
        right={<RefreshedAt at={query.dataUpdatedAt} />}
      />

      <PushControl />

      <WarningBanner warnings={query.data?.warnings ?? []} />

      {query.isError || (query.data && !list) ? (
        <ErrorState
          title="The notifications could not be read"
          body="GET /api/notifications did not answer with records. The harness still writes them; this screen cannot show them."
        />
      ) : query.isLoading || !list ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading notifications…</p>
      ) : list.length === 0 ? (
        <EmptyState
          title="Nothing to see yet"
          body="A task waiting for review, a merge, a conflict or an account cooling down each adds a line here."
        />
      ) : (
        <ul aria-label="Notifications" className="divide-y divide-border">
          {list.map((n, i) => (
            <Row key={typeof n.id === "string" ? n.id : i} n={n} unread={isUnread(n, seenBefore)} />
          ))}
        </ul>
      )}
    </AppShell>
  );
}

/** What each state says, in the browser's words and the server's. */
const PUSH_TEXT: Record<PushState, string> = {
  unsupported:
    "This browser cannot receive push notifications: it has no service worker, Push API or Notification API.",
  denied:
    "Notifications are blocked for this console in this browser. Allow them in the browser's site settings, then come back.",
  "server-off":
    "Push is off on the server: the board service has no VAPID key configured, or cannot be reached.",
  on: "Push is on for this browser: a new notification shows here even with the console closed.",
  off: "Push is off for this browser. Turning it on asks the browser for permission.",
};

/**
 * Push for this browser, on or off (ADR NNN). Permission is asked on the
 * click, not on load; every other state is read from the browser and the key
 * route each time the inbox opens (`lib/push.ts`).
 */
function PushControl() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    readState().then(
      (s) => live && setState(s),
      () => live && setState("off"),
    );
    return () => {
      live = false;
    };
  }, []);

  async function toggle(to: "on" | "off") {
    setBusy(true);
    setError(null);
    try {
      setState(await (to === "on" ? turnOn() : turnOff()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState(await readState().catch((): PushState => "off"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="Push notifications"
      data-push-state={state ?? "reading"}
      className="mx-4 mt-3 flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs"
    >
      <p className="min-w-0 flex-1 text-muted-foreground">
        {state === null ? "Reading push for this browser…" : PUSH_TEXT[state]}
        {error && <span className="mt-1 block text-destructive">{error}</span>}
      </p>
      {(state === "off" || state === "on") && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => void toggle(state === "on" ? "off" : "on")}
        >
          {state === "on" ? "Turn push off" : "Turn push on"}
        </Button>
      )}
    </section>
  );
}

function Row({ n, unread }: { n: Notification; unread: boolean }) {
  const path = internalPath(n.url);
  const about = subject(n);
  const body = (
    <>
      <span
        aria-hidden
        className={cn("size-1.5 shrink-0 rounded-full", unread ? "bg-info" : "bg-transparent")}
      />
      <span
        className={cn(
          "mono w-32 shrink-0 text-[10px]",
          NEEDS_PERSON.has(String(n.kind)) ? "text-warning" : "text-muted-foreground",
        )}
      >
        {kindLabel(n.kind)}
      </span>
      <span className={cn("min-w-0 flex-1 truncate", unread && "font-medium")}>
        {typeof n.title === "string" && n.title ? n.title : "(no title)"}
      </span>
      {about && <Mono className="shrink-0 text-[10px] text-muted-foreground">{about}</Mono>}
      <span className="mono w-36 shrink-0 text-right text-[10px] text-muted-foreground">
        {formatDateClock(typeof n.at === "string" ? n.at : null)}
      </span>
    </>
  );
  const cls = "flex items-center gap-3 px-4 py-2 text-xs";
  return (
    <li data-unread={unread ? "true" : "false"} className={cn(unread && "bg-surface-2/50")}>
      {path ? (
        <Link to={path} className={cn(cls, "hover:bg-surface-2")}>
          {body}
        </Link>
      ) : (
        <div className={cls}>{body}</div>
      )}
    </li>
  );
}
