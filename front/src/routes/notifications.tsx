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
import { notificationsQuery } from "@/lib/api/queries";
import type { Notification } from "@/lib/api/types";
import { formatDateClock } from "@/lib/format";
import { isUnread, markSeen, readLastSeen } from "@/lib/notifications";
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
