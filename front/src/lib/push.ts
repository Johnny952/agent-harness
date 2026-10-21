/**
 * Web Push for this browser (`docs/plans/notifications.md` step 4, ADR 232).
 *
 * The board service holds the VAPID key and the subscriptions (ADR 229); the
 * console forwards its three routes (`lib/api/forward.ts`). This module is the
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
 */

export const PUSH_KEY_PATH = "/api/push/key";
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
