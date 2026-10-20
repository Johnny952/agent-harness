/*
 * The console's service worker (`docs/plans/notifications.md` step 4, ADR 232).
 *
 * Hand-written and served as it is from `public/`: no build step, no plugin, no
 * cache. It does two things and nothing else.
 *
 * - `push`: shows the board service's payload (`dispatcher/push.py`, ADR 229),
 *   `{id, kind, title, url, tag}`. The title is the record's own line; the kind,
 *   underscores spaced, is the body, since the payload carries no other text. The
 *   tag is the payload's, so a newer push about the same task, thread or account
 *   replaces the older one instead of stacking beside it.
 * - `notificationclick`: focuses an open console window and navigates it to the
 *   record's url, or opens one there. Only a console path is followed, the
 *   inbox's rule (`routes/notifications.tsx` `internalPath`); anything else
 *   opens the inbox.
 *
 * It does not intercept `fetch`: the console reads live state, and a cached read
 * would be a stale one.
 */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function internalPath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//")
    ? url
    : "/notifications";
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  if (payload === null || typeof payload !== "object") payload = {};
  const title =
    typeof payload.title === "string" && payload.title ? payload.title : "Harness notification";
  const kind = typeof payload.kind === "string" && payload.kind ? payload.kind : "";
  const options = {
    body: kind.replaceAll("_", " "),
    icon: "/favicon.ico",
    data: { url: internalPath(payload.url) },
  };
  if (typeof payload.tag === "string" && payload.tag) {
    options.tag = payload.tag;
    options.renotify = true;
  }
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = internalPath(event.notification.data && event.notification.data.url);
  const target = new URL(path, self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        const focused = await client.focus();
        if ("navigate" in focused) {
          try {
            await focused.navigate(target);
            return;
          } catch {
            /* An uncontrolled window refuses navigate(): open one instead. */
          }
        }
        break;
      }
      await self.clients.openWindow(target);
    })(),
  );
});
