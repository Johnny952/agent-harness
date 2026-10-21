/**
 * Web Push for this browser (`docs/plans/notifications.md` step 4, ADR 232).
 *
 * The board service holds the VAPID key and the subscriptions (ADR 229); the
 * console forwards its five routes (`lib/api/forward.ts`). This module is the
 * browser's half: it registers `public/sw.js`, asks for permission, subscribes
 * with the server's public key and hands the subscription to the server, and
 * undoes all of it.
 *
 * **Permission is asked on a click, never on load.** `turnOn` is the only caller
 * of `Notification.requestPermission`, and the inbox calls it from its button.
 *
 * **The state is what the browser and the server say now**, not a remembered
 * flag. `readState` asks both each time: no service worker, Push API or
 * Notification API is *unsupported*; a denied permission is *denied*, which only
 * the browser's own settings can undo; a key route that does not answer a key
 * (404 with no key configured, 503 without cryptography, or the board service
 * down) is *server-off*; then *on* when this browser holds a subscription with
 * permission granted, else *off*. The server keeps its subscriptions by
 * endpoint and never says which browser one belongs to, so *on* is the
 * browser's word; a subscription the server dropped (a 404 or 410 from the push
 * service, ADR 229) is put back by turning push on again.
 *
 * **A browser that refuses the subscription is said in plain words** (ADR 233).
 * `pushManager.subscribe` rejects with the browser's own text, which names no
 * cause the person can act on ("Registration failed - permission denied" is what
 * Chrome says in a private window, with permission granted). `turnOn` throws
 * `subscribeRefusal`'s sentence instead, with the browser's text kept at its end.
 *
 * **The settings are the server's, read and written whole by name** (ADR 237,
 * ADR 246): which kinds are pushed and the quiet hours. `readSettings` and
 * `writeSettings` are the two routes' client; a write sends only what changed
 * and the server merges it, and a 400's `{"error"}` is given in the server's
 * own words. Their types live here and not in `lib/api/types.ts`, which is the
 * api's.
 */

export const PUSH_KEY_PATH = "/api/push/key";
export const PUSH_SETTINGS_PATH = "/api/push/settings";
export const PUSH_SUBSCRIPTIONS_PATH = "/api/push/subscriptions";
export const SERVICE_WORKER_PATH = "/sw.js";

export type PushState = "unsupported" | "denied" | "server-off" | "on" | "off";

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** The VAPID public key as `applicationServerKey` wants it: bytes, from base64url. */
export function urlBase64ToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded.replaceAll("-", "+").replaceAll("_", "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** The server's public key, or null when push is off there or it cannot be reached. */
export async function readPushKey(): Promise<string | null> {
  try {
    const res = await fetch(PUSH_KEY_PATH, { headers: { Accept: "application/json" } });
    if (!res.ok) return null;
    const body = (await res.json()) as unknown;
    const key =
      body !== null && typeof body === "object"
        ? (body as { publicKey?: unknown }).publicKey
        : null;
    return typeof key === "string" && key ? key : null;
  } catch {
    return null;
  }
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? await reg.pushManager.getSubscription() : null;
}

export async function readState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  if ((await readPushKey()) === null) return "server-off";
  const sub = await currentSubscription();
  return sub && Notification.permission === "granted" ? "on" : "off";
}

/** The `{"error"}` a refusal answers, or its status when it has none. */
async function refusal(res: Response, what: string): Promise<Error> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = (await res.json()) as unknown;
    const error =
      body !== null && typeof body === "object" ? (body as { error?: unknown }).error : null;
    if (typeof error === "string" && error) detail = error;
  } catch {
    /* No JSON body: the status says it. */
  }
  return new Error(`${what}: ${detail}`);
}

export type QuietHours = { start: string; end: string; zone: string };
export type PushSettings = { kinds: Record<string, boolean>; quiet_hours: QuietHours | null };
/** A write: a key left out is a field left alone; `quiet_hours: null` turns the window off. */
export type PushSettingsChange = {
  kinds?: Record<string, boolean>;
  quiet_hours?: QuietHours | null;
};

/** The settings in a body, or null when it is not their shape. Non-boolean kinds are dropped. */
export function parseSettings(body: unknown): PushSettings | null {
  if (body === null || typeof body !== "object" || Array.isArray(body)) return null;
  const record = body as Record<string, unknown>;
  const served = record["kinds"];
  if (served === null || typeof served !== "object" || Array.isArray(served)) return null;
  const kinds: Record<string, boolean> = {};
  for (const [kind, on] of Object.entries(served as Record<string, unknown>)) {
    if (typeof on === "boolean") kinds[kind] = on;
  }
  const window = record["quiet_hours"];
  if (window === null || window === undefined) return { kinds, quiet_hours: null };
  if (typeof window !== "object" || Array.isArray(window)) return null;
  const { start, end, zone } = window as Record<string, unknown>;
  if (typeof start !== "string" || typeof end !== "string" || typeof zone !== "string") return null;
  return { kinds, quiet_hours: { start, end, zone } };
}

const UNREACHABLE_READ =
  "The board service cannot be reached, so the push settings cannot be read.";
const UNREACHABLE_WRITE =
  "The board service cannot be reached, so the push settings were not saved.";
const NO_SETTINGS = "The board service answered no push settings.";

/** Reads the settings. Throws a sentence for the person: unreachable, refused, or malformed. */
export async function readSettings(): Promise<PushSettings> {
  let res: Response;
  try {
    res = await fetch(PUSH_SETTINGS_PATH, { headers: { Accept: "application/json" } });
  } catch {
    throw new Error(UNREACHABLE_READ);
  }
  if (res.status === 502 || res.status === 504) throw new Error(UNREACHABLE_READ);
  if (!res.ok) throw await refusal(res, "The push settings could not be read");
  const settings = parseSettings(await res.json().catch(() => null));
  if (!settings) throw new Error(NO_SETTINGS);
  return settings;
}

/**
 * Saves a change and resolves to the settings the server now holds. Only the
 * fields present are sent. Throws on an empty change without sending, and with
 * the server's `{"error"}` on a refusal.
 */
export async function writeSettings(partial: PushSettingsChange): Promise<PushSettings> {
  const body: PushSettingsChange = {};
  if (partial.kinds && Object.keys(partial.kinds).length > 0) body.kinds = partial.kinds;
  if ("quiet_hours" in partial && partial.quiet_hours !== undefined) {
    body.quiet_hours = partial.quiet_hours;
  }
  if (Object.keys(body).length === 0) throw new Error("Nothing to save.");
  let res: Response;
  try {
    res = await fetch(PUSH_SETTINGS_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(UNREACHABLE_WRITE);
  }
  if (res.status === 502 || res.status === 504) throw new Error(UNREACHABLE_WRITE);
  if (!res.ok) throw await refusal(res, "The push settings were not saved");
  const settings = parseSettings(await res.json().catch(() => null));
  if (!settings) throw new Error(NO_SETTINGS);
  return settings;
}

/** The browser's time zone, the quiet hours' default; UTC where it cannot say. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * What a rejected `pushManager.subscribe` means for the person. `NotAllowedError`
 * is a browser that will not push to this window though permission is granted:
 * a private or incognito window, or a profile whose push is turned off.
 * `AbortError` is the browser's push service not answering (some browsers ship
 * it turned off). Anything else keeps a generic sentence.
 */
export function subscribeRefusal(e: unknown): Error {
  const name = e instanceof Error || e instanceof DOMException ? e.name : "";
  const said = e instanceof Error || e instanceof DOMException ? e.message : String(e);
  const plain =
    name === "NotAllowedError"
      ? "This browser refused the push subscription. Private or incognito windows cannot receive push; open the console in a normal window, or check that push is not turned off for this browser profile."
      : name === "AbortError"
        ? "This browser's push service did not answer. Some browsers turn it off by default (Brave has a setting for it); turn it on or use another browser, then try again."
        : "This browser could not subscribe to push.";
  return new Error(said ? `${plain} (The browser said: ${said})` : plain);
}

function sameKey(sub: PushSubscription, key: Uint8Array): boolean {
  const held = sub.options?.applicationServerKey;
  if (!held) return false;
  const bytes = new Uint8Array(held);
  return bytes.length === key.length && bytes.every((b, i) => b === key[i]);
}

function send(method: "POST" | "DELETE", body: unknown): Promise<Response> {
  return fetch(PUSH_SUBSCRIPTIONS_PATH, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Turn push on for this browser. Asks permission (call it from a click), then
 * registers the worker, subscribes and stores the subscription on the server.
 * Resolves to the state it left; throws when the browser refused the
 * subscription (`subscribeRefusal`), or when the server refused the write, after
 * dropping the browser's subscription so the state stays *off*.
 */
export async function turnOn(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission === "denied") return "denied";
  if (permission !== "granted") return "off";
  const key = await readPushKey();
  if (key === null) return "server-off";
  const applicationServerKey = urlBase64ToUint8Array(key);
  await navigator.serviceWorker.register(SERVICE_WORKER_PATH, { scope: "/" });
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  // A subscription made under another key cannot be pushed to with this one.
  if (sub && !sameKey(sub, applicationServerKey)) {
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) {
    try {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
    } catch (e) {
      throw subscribeRefusal(e);
    }
  }
  const res = await send("POST", sub.toJSON());
  if (!res.ok) {
    const error = await refusal(res, "The subscription was not stored");
    await sub.unsubscribe().catch(() => false);
    throw error;
  }
  return "on";
}

/**
 * Turn push off for this browser: the server forgets the endpoint, then the
 * browser drops the subscription. A 404 is an endpoint the server had already
 * dropped, which is off as well. Any other refusal throws and leaves both as
 * they were, so the state still reads *on*.
 */
export async function turnOff(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const sub = await currentSubscription();
  if (sub) {
    const res = await send("DELETE", { endpoint: sub.endpoint });
    if (!res.ok && res.status !== 404) throw await refusal(res, "The subscription was not removed");
    await sub.unsubscribe();
  }
  return "off";
}
