/**
 * Web Push for this browser (`docs/plans/notifications.md` step 4, ADR 232):
 * the key's decoding, the feature check and the refusal text (ADR 233), and the
 * settings' read and write (ADR 246). The flows are tested through the
 * inbox's control, in `routes/-notifications.test.tsx`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  parseSettings,
  pushSupported,
  readPushKey,
  readSettings,
  readState,
  subscribeRefusal,
  urlBase64ToUint8Array,
  writeSettings,
} from "./push";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the key's bytes", () => {
  it("decodes base64url, unpadded, to the bytes applicationServerKey wants", () => {
    expect(Array.from(urlBase64ToUint8Array("AQID"))).toEqual([1, 2, 3]);
    // `-` and `_` are base64's `+` and `/`; the padding is put back.
    expect(Array.from(urlBase64ToUint8Array("-_8"))).toEqual([0xfb, 0xff]);
    expect(Array.from(urlBase64ToUint8Array("AQ"))).toEqual([1]);
  });
});

describe("the feature check", () => {
  it("is unsupported in a browser with none of the three APIs", async () => {
    expect(pushSupported()).toBe(false);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect(await readState()).toBe("unsupported");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the key read", () => {
  it("is the key, or null on a refusal, a malformed body or no answer", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ publicKey: "AQID" })));
    expect(await readPushKey()).toBe("AQID");
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 404 }));
    expect(await readPushKey()).toBeNull();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ publicKey: 7 })));
    expect(await readPushKey()).toBeNull();
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect(await readPushKey()).toBeNull();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/push/key");
  });
});

describe("the settings", () => {
  const WINDOW = { start: "22:00", end: "07:00", zone: "UTC" };

  function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status });
  }

  it("parses a good body and drops what is not a boolean kind", () => {
    expect(parseSettings({ kinds: { merged: true, pool: false }, quiet_hours: WINDOW })).toEqual({
      kinds: { merged: true, pool: false },
      quiet_hours: WINDOW,
    });
    expect(parseSettings({ kinds: { merged: true, odd: "yes" }, quiet_hours: null })).toEqual({
      kinds: { merged: true },
      quiet_hours: null,
    });
  });

  it("is null for a window with a missing or numeric field, or a body of the wrong shape", () => {
    expect(parseSettings({ kinds: {}, quiet_hours: { start: "22:00", end: "07:00" } })).toBeNull();
    expect(parseSettings({ kinds: {}, quiet_hours: { ...WINDOW, zone: 3 } })).toBeNull();
    expect(parseSettings({ kinds: [], quiet_hours: null })).toBeNull();
    expect(parseSettings("x")).toBeNull();
    expect(parseSettings(null)).toBeNull();
  });

  it("reads them", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(json({ kinds: { merged: true }, quiet_hours: null }));
    expect(await readSettings()).toEqual({ kinds: { merged: true }, quiet_hours: null });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/push/settings");
  });

  it("says the board service cannot be reached on a 502 or no answer", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(json({ error: "x" }, 502));
    await expect(readSettings()).rejects.toThrow(/cannot be reached/);
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(readSettings()).rejects.toThrow(/cannot be reached/);
  });

  it("gives a refusal's own words, and a malformed body in plain ones", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(json({ error: "nothing here" }, 404));
    await expect(readSettings()).rejects.toThrow(/nothing here/);
    fetchMock.mockResolvedValueOnce(json({ nope: 1 }));
    await expect(readSettings()).rejects.toThrow(/answered no push settings/);
  });

  it("sends only the changed fields, as a PUT", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const answer = { kinds: { merged: false }, quiet_hours: null };
    fetchMock.mockImplementation(async () => json(answer));
    await writeSettings({ kinds: { merged: false } });
    await writeSettings({ quiet_hours: null });
    await writeSettings({ kinds: {}, quiet_hours: WINDOW });
    const sent = fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body]);
    expect(sent).toEqual([
      ["/api/push/settings", "PUT", '{"kinds":{"merged":false}}'],
      ["/api/push/settings", "PUT", '{"quiet_hours":null}'],
      ["/api/push/settings", "PUT", JSON.stringify({ quiet_hours: WINDOW })],
    ]);
  });

  it("sends nothing for an empty change", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(writeSettings({})).rejects.toThrow(/Nothing to save/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("gives the server's error on a 400", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(json({ error: "quiet_hours start equals its end" }, 400));
    await expect(writeSettings({ quiet_hours: WINDOW })).rejects.toThrow(
      "The push settings were not saved: quiet_hours start equals its end",
    );
  });
});

describe("the browser's refusal to subscribe", () => {
  it("names the cause the person can act on, and keeps the browser's own text", () => {
    const privateWindow = subscribeRefusal(
      new DOMException("Registration failed - permission denied", "NotAllowedError"),
    ).message;
    expect(privateWindow).toMatch(/Private or incognito windows cannot receive push/);
    expect(privateWindow).toMatch(/The browser said: Registration failed - permission denied/);
    const noService = subscribeRefusal(
      new DOMException("Registration failed - push service error", "AbortError"),
    ).message;
    expect(noService).toMatch(/push service did not answer/);
    expect(subscribeRefusal(new TypeError("odd")).message).toBe(
      "This browser could not subscribe to push. (The browser said: odd)",
    );
    expect(subscribeRefusal("").message).toBe("This browser could not subscribe to push.");
  });
});
