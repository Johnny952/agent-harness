/**
 * The inbox, `/notifications` (`docs/plans/notifications.md` step 2, ADR 225):
 * the api's records newest first, each a link to its record; the ones newer
 * than this browser's last-seen marker marked as new, and the marker moved on
 * by opening it; and the sidebar's unread count on every other screen. And the
 * `?thread=<id>` a turn's record links, which opens the dock on any screen
 * (ADR 228). And the control that turns Web Push on or off for this browser
 * (step 4, ADR 232), against a fake service worker, Push API and Notification.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route } from "./notifications";
import { Route as QueueRoute } from "./queue";
import { renderRoute, type Served } from "@/test/render-route";
import { LAST_SEEN_KEY, readLastSeen } from "@/lib/notifications";
import { browserZone } from "@/lib/push";
import { OPEN_THREAD_EVENT } from "@/hooks/use-console";
import type { ActionQueue, Notification } from "@/lib/api/types";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

const OLD = "n-20261020T110000000000Z-aaaa";
const MID = "n-20261020T120000000000Z-bbbb";
const NEW = "n-20261020T130000000000Z-cccc";

const RECORDS: Notification[] = [
  {
    id: NEW,
    kind: "awaiting_review",
    at: "2026-10-20T13:00:00+00:00",
    task: "T-301",
    title: "T-301 is waiting for review",
    url: "/tasks/T-301",
  },
  {
    id: MID,
    kind: "pool",
    at: "2026-10-20T12:00:00+00:00",
    account: "cuenta2",
    title: "cuenta2 is cooling",
    url: "/pool",
  },
  {
    id: OLD,
    kind: "merged",
    at: "2026-10-20T11:00:00+00:00",
    task: "T-300",
    title: "T-300 was merged",
    url: "/tasks/T-300",
  },
];

function rows(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Notifications" })).getAllByRole("listitem");
}

function row(i: number): HTMLElement {
  const li = rows()[i];
  if (!li) throw new Error(`no row ${i}`);
  return li;
}

describe("the inbox", () => {
  it("lists the records newest first, each linking to its record", async () => {
    await renderRoute(Route, "/notifications", { "/api/notifications": { data: RECORDS } });
    await screen.findByText("T-301 is waiting for review");
    const links = rows().map((li) => within(li).getByRole("link"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/tasks/T-301",
      "/pool",
      "/tasks/T-300",
    ]);
    expect(within(row(0)).getByText("awaiting review")).toBeTruthy();
    expect(within(row(1)).getByText("cuenta2")).toBeTruthy();
  });

  it("marks what this browser has not seen as new, then marks it seen", async () => {
    window.localStorage.setItem(LAST_SEEN_KEY, MID);
    await renderRoute(Route, "/notifications", { "/api/notifications": { data: RECORDS } });
    await screen.findByText("T-301 is waiting for review");
    expect(rows().map((li) => li.getAttribute("data-unread"))).toEqual(["true", "false", "false"]);
    await waitFor(() => expect(readLastSeen()).toBe(NEW));
    // Still highlighted while the page is open: it was new when it was opened.
    expect(row(0).getAttribute("data-unread")).toBe("true");
  });

  it("says read state is this browser's", async () => {
    await renderRoute(Route, "/notifications", { "/api/notifications": { data: RECORDS } });
    expect(await screen.findByText(/kept in this browser/)).toBeTruthy();
  });

  it("is the empty state when nothing has been notified", async () => {
    await renderRoute(Route, "/notifications", { "/api/notifications": { data: [] } });
    expect(await screen.findByText("Nothing to see yet")).toBeTruthy();
    expect(readLastSeen()).toBeNull();
  });

  it("is the error state when the read fails, and marks nothing seen", async () => {
    await renderRoute(Route, "/notifications", {
      "/api/notifications": { status: 503, error: "down" },
    });
    expect(await screen.findByText("The notifications could not be read")).toBeTruthy();
    expect(readLastSeen()).toBeNull();
  });

  it("shows the api's warning when the file could not be read", async () => {
    await renderRoute(Route, "/notifications", {
      "/api/notifications": { data: null, warnings: ["notifications.jsonl: permission denied"] },
    });
    expect(await screen.findByText("notifications.jsonl: permission denied")).toBeTruthy();
    expect(screen.getByText("The notifications could not be read")).toBeTruthy();
  });
});

const EMPTY_QUEUE: ActionQueue = {
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: true, stale_after_seconds: 30 },
};

describe("the sidebar's inbox entry", () => {
  function inbox(): HTMLElement {
    const harness = screen.getByRole("list", { name: "Harness" });
    return within(harness).getByRole("link", { name: /Inbox/ });
  }

  it("links to the inbox and counts what this browser has not seen", async () => {
    window.localStorage.setItem(LAST_SEEN_KEY, OLD);
    await renderRoute(QueueRoute, "/queue", {
      "/api/actions": { data: EMPTY_QUEUE },
      "/api/notifications": { data: RECORDS },
    });
    await waitFor(() => expect(within(inbox()).getByTestId("inbox-unread").textContent).toBe("2"));
    expect(inbox().getAttribute("href")).toBe("/notifications");
  });

  it("shows no count when everything is seen, or the read failed", async () => {
    window.localStorage.setItem(LAST_SEEN_KEY, NEW);
    const { queryClient } = await renderRoute(QueueRoute, "/queue", {
      "/api/actions": { data: EMPTY_QUEUE },
      "/api/notifications": { data: RECORDS },
    });
    await waitFor(() => expect(queryClient.getQueryData(["notifications"])).toBeTruthy());
    expect(within(inbox()).queryByTestId("inbox-unread")).toBeNull();
    cleanup();
    window.localStorage.clear();
    await renderRoute(QueueRoute, "/queue", {
      "/api/actions": { data: EMPTY_QUEUE },
      "/api/notifications": { status: 503, error: "down" },
    });
    await screen.findByText("Nothing has been queued");
    expect(within(inbox()).queryByTestId("inbox-unread")).toBeNull();
  });
});

describe("a thread's url, ?thread=<id>", () => {
  const SERVED = {
    "/api/actions": { data: EMPTY_QUEUE },
    "/api/notifications": { data: [] },
  };

  function listen(): { opened: unknown[]; stop: () => void } {
    const opened: unknown[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<unknown>).detail);
    window.addEventListener(OPEN_THREAD_EVENT, onOpen);
    return { opened, stop: () => window.removeEventListener(OPEN_THREAD_EVENT, onOpen) };
  }

  it("opens the dock on the thread and drops the param in place", async () => {
    const { opened, stop } = listen();
    try {
      const { router } = await renderRoute(QueueRoute, "/queue", SERVED, "/queue?thread=t-7");
      await waitFor(() => expect(opened).toEqual(["t-7"]));
      expect(await screen.findByRole("button", { name: "Close chat" })).toBeTruthy();
      await waitFor(() => expect(router.state.location.search).toEqual({}));
      expect(router.state.location.pathname).toBe("/queue");
      // Replaced, not pushed: a step back leaves the console, not into the param.
      expect(router.history.length).toBe(1);
    } finally {
      stop();
    }
  });

  it("keeps the screen's other params", async () => {
    const { router } = await renderRoute(
      QueueRoute,
      "/queue",
      SERVED,
      "/queue?view=current&thread=t-7",
    );
    await screen.findByRole("button", { name: "Close chat" });
    await waitFor(() => expect(router.state.location.search).toEqual({ view: "current" }));
  });

  it("opens nothing without it", async () => {
    const { opened, stop } = listen();
    try {
      await renderRoute(QueueRoute, "/queue", SERVED);
      await screen.findByText("Nothing has been queued");
      expect(opened).toEqual([]);
      expect(screen.queryByRole("button", { name: "Close chat" })).toBeNull();
    } finally {
      stop();
    }
  });
});

/**
 * A browser with push: `navigator.serviceWorker`, `PushManager` and a
 * `Notification` whose permission starts at `permission` and becomes `ask` when
 * asked. `subscribed` starts it holding a subscription under the key `AQID`;
 * `refuse` is what its `subscribe` rejects with.
 */
function fakePush({
  permission = "default",
  ask = "granted",
  subscribed = false,
  refuse,
}: {
  permission?: NotificationPermission;
  ask?: NotificationPermission;
  subscribed?: boolean;
  refuse?: DOMException;
} = {}) {
  let perm = permission;
  let registered = subscribed;
  const unsubscribe = vi.fn(async () => {
    sub = null;
    return true;
  });
  function makeSub(key: Uint8Array) {
    return {
      endpoint: "https://push.example/send/abc",
      options: { applicationServerKey: key.slice().buffer },
      unsubscribe,
      toJSON: () => ({
        endpoint: "https://push.example/send/abc",
        expirationTime: null,
        keys: { p256dh: "p256", auth: "auth" },
      }),
    };
  }
  let sub: ReturnType<typeof makeSub> | null = subscribed
    ? makeSub(new Uint8Array([1, 2, 3]))
    : null;
  const subscribe = vi.fn(async (opts: PushSubscriptionOptionsInit) => {
    if (refuse) throw refuse;
    sub = makeSub(opts.applicationServerKey as Uint8Array);
    return sub;
  });
  const reg = { pushManager: { getSubscription: async () => sub, subscribe } };
  const register = vi.fn(async () => {
    registered = true;
    return reg;
  });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      register,
      ready: Promise.resolve(reg),
      getRegistration: async () => (registered ? reg : undefined),
    },
  });
  const requestPermission = vi.fn(async () => {
    perm = ask;
    return ask;
  });
  vi.stubGlobal("PushManager", function PushManager() {});
  vi.stubGlobal(
    "Notification",
    class {
      static get permission() {
        return perm;
      }
      static requestPermission = requestPermission;
    },
  );
  return { register, requestPermission, subscribe, unsubscribe };
}

describe("push for this browser", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "serviceWorker");
  });

  const KEY: Served = {
    "/api/notifications": { data: [] },
    "/api/push/key": { bare: { publicKey: "AQID" }, status: 200 },
  };

  function control(): HTMLElement {
    return screen.getByRole("region", { name: "Push notifications" });
  }

  async function settled(state: string): Promise<void> {
    await waitFor(() => expect(control().getAttribute("data-push-state")).toBe(state));
  }

  it("says so in a browser without push, and asks nothing of it", async () => {
    const { sent } = await renderRoute(Route, "/notifications", KEY);
    await settled("unsupported");
    expect(within(control()).getByText(/cannot receive push notifications/)).toBeTruthy();
    expect(within(control()).queryByRole("button")).toBeNull();
    // The settings section reads the key; the control subscribes nothing.
    expect(sent.some((r) => r.method === "POST")).toBe(false);
  });

  it("asks permission only on the click, then subscribes with the key and stores it", async () => {
    const fake = fakePush();
    const { sent } = await renderRoute(Route, "/notifications", {
      ...KEY,
      "POST /api/push/subscriptions": { bare: { endpoint: "x" }, status: 201 },
    });
    await settled("off");
    expect(fake.requestPermission).not.toHaveBeenCalled();
    fireEvent.click(within(control()).getByRole("button", { name: "Turn push on" }));
    await settled("on");
    expect(fake.requestPermission).toHaveBeenCalledTimes(1);
    expect(fake.register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
    const opts = fake.subscribe.mock.calls[0]?.[0];
    expect(opts?.userVisibleOnly).toBe(true);
    expect(Array.from((opts?.applicationServerKey as Uint8Array | undefined) ?? [])).toEqual([
      1, 2, 3,
    ]);
    const post = sent.find((r) => r.method === "POST");
    expect(post).toEqual({
      method: "POST",
      path: "/api/push/subscriptions",
      body: {
        endpoint: "https://push.example/send/abc",
        expirationTime: null,
        keys: { p256dh: "p256", auth: "auth" },
      },
    });
    expect(within(control()).getByRole("button", { name: "Turn push off" })).toBeTruthy();
  });

  it("turns off: the server forgets the endpoint, then the browser unsubscribes", async () => {
    const fake = fakePush({ permission: "granted", subscribed: true });
    const { sent } = await renderRoute(Route, "/notifications", {
      ...KEY,
      "DELETE /api/push/subscriptions": { bare: { removed: true }, status: 200 },
    });
    await settled("on");
    fireEvent.click(within(control()).getByRole("button", { name: "Turn push off" }));
    await settled("off");
    expect(sent.find((r) => r.method === "DELETE")).toEqual({
      method: "DELETE",
      path: "/api/push/subscriptions",
      body: { endpoint: "https://push.example/send/abc" },
    });
    expect(fake.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("is denied when the browser blocks it, with no button and no subscription", async () => {
    const fake = fakePush({ permission: "denied" });
    const { sent } = await renderRoute(Route, "/notifications", KEY);
    await settled("denied");
    expect(within(control()).getByText(/blocked for this console/)).toBeTruthy();
    expect(within(control()).queryByRole("button")).toBeNull();
    expect(fake.subscribe).not.toHaveBeenCalled();
    expect(sent.some((r) => r.method === "POST")).toBe(false);
  });

  it("is denied when the person refuses on the click, and subscribes nothing", async () => {
    const fake = fakePush({ ask: "denied" });
    const { sent } = await renderRoute(Route, "/notifications", KEY);
    await settled("off");
    fireEvent.click(within(control()).getByRole("button", { name: "Turn push on" }));
    await settled("denied");
    expect(fake.subscribe).not.toHaveBeenCalled();
    expect(sent.some((r) => r.method === "POST")).toBe(false);
  });

  it("is off on the server when the key route answers no key", async () => {
    fakePush();
    await renderRoute(Route, "/notifications", {
      "/api/notifications": { data: [] },
      "/api/push/key": { status: 404, error: "push is not configured" },
    });
    await settled("server-off");
    expect(within(control()).getByText(/Push is off on the server/)).toBeTruthy();
    expect(within(control()).queryByRole("button")).toBeNull();
  });

  it("shows the server's refusal and stays off when the subscription is not stored", async () => {
    const fake = fakePush();
    await renderRoute(Route, "/notifications", {
      ...KEY,
      "POST /api/push/subscriptions": { status: 409, error: "the subscription store is full" },
    });
    await settled("off");
    fireEvent.click(within(control()).getByRole("button", { name: "Turn push on" }));
    expect(await within(control()).findByText(/the subscription store is full/)).toBeTruthy();
    await settled("off");
    expect(fake.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("says in plain words why a private window refuses the subscription, and stays off", async () => {
    const fake = fakePush({
      refuse: new DOMException("Registration failed - permission denied", "NotAllowedError"),
    });
    const { sent } = await renderRoute(Route, "/notifications", KEY);
    await settled("off");
    fireEvent.click(within(control()).getByRole("button", { name: "Turn push on" }));
    expect(
      await within(control()).findByText(/Private or incognito windows cannot receive push/),
    ).toBeTruthy();
    expect(within(control()).getByText(/Registration failed - permission denied/)).toBeTruthy();
    await settled("off");
    expect(fake.subscribe).toHaveBeenCalledTimes(1);
    expect(sent.some((r) => r.method === "POST")).toBe(false);
  });
});

describe("the push settings", () => {
  const WINDOW = { start: "23:00", end: "06:30", zone: "Europe/Madrid" };
  // Served alphabetically, as Flask does: the page has to put the four first.
  const KINDS = {
    awaiting_review: true,
    blocked: true,
    command_request: false,
    conflict: true,
    merged: true,
    phase_failed: false,
    pool: true,
    turn_finished: true,
  };

  function served(over: Served = {}, quiet: unknown = null): Served {
    return {
      "/api/notifications": { data: [] },
      "/api/push/key": { bare: { publicKey: "AQID" }, status: 200 },
      "/api/push/settings": { bare: { kinds: KINDS, quiet_hours: quiet }, status: 200 },
      ...over,
    };
  }

  function section(): HTMLElement {
    return screen.getByRole("region", { name: "Push settings" });
  }

  async function ready(): Promise<void> {
    await waitFor(() => expect(section().getAttribute("data-settings-state")).toBe("ready"));
  }

  function puts(sent: { method: string; path: string; body?: unknown }[]) {
    return sent.filter((r) => r.method === "PUT" && r.path === "/api/push/settings");
  }

  it("lists one switch per kind, the four that need the person first", async () => {
    await renderRoute(Route, "/notifications", served());
    await ready();
    const names = within(within(section()).getByRole("list", { name: "Pushed kinds" }))
      .getAllByRole("switch")
      .map((s) => s.getAttribute("aria-label"));
    expect(names).toEqual([
      "awaiting review",
      "conflict",
      "command request",
      "blocked",
      "merged",
      "phase failed",
      "pool",
      "turn finished",
    ]);
    expect(
      within(section()).getByRole("switch", { name: "merged" }).getAttribute("aria-checked"),
    ).toBe("true");
    expect(
      within(section()).getByRole("switch", { name: "phase failed" }).getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("sends only the flipped kind and shows the server's answer", async () => {
    const answer = { kinds: { ...KINDS, merged: false }, quiet_hours: null };
    const { sent } = await renderRoute(
      Route,
      "/notifications",
      served({ "PUT /api/push/settings": { bare: answer, status: 200 } }),
    );
    await ready();
    fireEvent.click(within(section()).getByRole("switch", { name: "merged" }));
    await waitFor(() =>
      expect(
        within(section()).getByRole("switch", { name: "merged" }).getAttribute("aria-checked"),
      ).toBe("false"),
    );
    expect(puts(sent).map((r) => r.body)).toEqual([{ kinds: { merged: false } }]);
  });

  it("turns quiet hours on with the browser's zone, writing nothing until saved", async () => {
    const answer = { kinds: KINDS, quiet_hours: { start: "21:00", end: "07:00", zone: "UTC" } };
    const { sent } = await renderRoute(
      Route,
      "/notifications",
      served({ "PUT /api/push/settings": { bare: answer, status: 200 } }),
    );
    await ready();
    fireEvent.click(within(section()).getByRole("switch", { name: "Quiet hours" }));
    const start = within(section()).getByLabelText("Quiet hours start") as HTMLInputElement;
    const end = within(section()).getByLabelText("Quiet hours end") as HTMLInputElement;
    const zone = within(section()).getByLabelText("Time zone") as HTMLInputElement;
    expect([start.value, end.value, zone.value]).toEqual(["22:00", "07:00", browserZone()]);
    expect(puts(sent)).toEqual([]);
    fireEvent.change(start, { target: { value: "21:00" } });
    fireEvent.change(zone, { target: { value: "UTC" } });
    fireEvent.click(within(section()).getByRole("button", { name: "Save quiet hours" }));
    await waitFor(() => expect(puts(sent)).toHaveLength(1));
    expect(puts(sent)[0]!.body).toEqual({
      quiet_hours: { start: "21:00", end: "07:00", zone: "UTC" },
    });
  });

  it("turns a served window off with quiet_hours null", async () => {
    const answer = { kinds: KINDS, quiet_hours: null };
    const { sent } = await renderRoute(
      Route,
      "/notifications",
      served({ "PUT /api/push/settings": { bare: answer, status: 200 } }, WINDOW),
    );
    await ready();
    expect((within(section()).getByLabelText("Time zone") as HTMLInputElement).value).toBe(
      "Europe/Madrid",
    );
    fireEvent.click(within(section()).getByRole("switch", { name: "Quiet hours" }));
    await waitFor(() => expect(within(section()).queryByLabelText("Time zone")).toBeNull());
    expect(puts(sent).map((r) => r.body)).toEqual([{ quiet_hours: null }]);
  });

  it("shows the server's words on a 400 and keeps the served switch", async () => {
    await renderRoute(
      Route,
      "/notifications",
      served({
        "PUT /api/push/settings": { status: 400, error: "unknown notification kind 'x'" },
      }),
    );
    await ready();
    fireEvent.click(within(section()).getByRole("switch", { name: "merged" }));
    expect(
      await within(section()).findByText(
        "The push settings were not saved: unknown notification kind 'x'",
      ),
    ).toBeTruthy();
    expect(
      within(section()).getByRole("switch", { name: "merged" }).getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("says when the board service cannot be reached, and shows no switches", async () => {
    await renderRoute(
      Route,
      "/notifications",
      served({
        "/api/push/settings": { status: 502, error: "The board-service could not be reached." },
      }),
    );
    await waitFor(() => expect(section().getAttribute("data-settings-state")).toBe("unreadable"));
    expect(within(section()).getByText(/cannot be reached/)).toBeTruthy();
    expect(within(section()).queryByRole("switch")).toBeNull();
  });

  it("disables the writes while the server has push off", async () => {
    await renderRoute(
      Route,
      "/notifications",
      served({ "/api/push/key": { status: 404, error: "push is off" } }, WINDOW),
    );
    await waitFor(() => expect(section().getAttribute("data-settings-state")).toBe("server-off"));
    expect(within(section()).getByText(/Push is off on the server/)).toBeTruthy();
    const switches = within(section()).getAllByRole("switch");
    expect(switches.length).toBe(9);
    expect(switches.every((s) => s.hasAttribute("disabled"))).toBe(true);
    expect(
      (within(section()).getByRole("button", { name: "Save quiet hours" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});
