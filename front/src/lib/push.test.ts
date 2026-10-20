/**
 * Web Push for this browser (`docs/plans/notifications.md` step 4, ADR 232):
 * the key's decoding and the feature check. The flows are tested through the
 * inbox's control, in `routes/-notifications.test.tsx`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { pushSupported, readPushKey, readState, urlBase64ToUint8Array } from "./push";

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
