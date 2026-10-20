/** `lib/floor.ts`: the start dialogs' pool floor field (ADR 226, ADR 227). */
import { describe, expect, it } from "vitest";
import { readFloor } from "./floor";

describe("readFloor", () => {
  it("reads blank as null, the worker's default", () => {
    expect(readFloor("")).toEqual({ ok: true, value: null });
    expect(readFloor("  ")).toEqual({ ok: true, value: null });
  });

  it("takes a whole percent from 0 to 100", () => {
    expect(readFloor("0")).toEqual({ ok: true, value: 0 });
    expect(readFloor(" 20 ")).toEqual({ ok: true, value: 20 });
    expect(readFloor("100")).toEqual({ ok: true, value: 100 });
  });

  it("refuses a fraction, a sign, a word or more than 100", () => {
    for (const t of ["2.5", "-1", "+3", "1e1", "ten", "101"]) {
      expect(readFloor(t)).toEqual({
        ok: false,
        why: `${t} is not a whole percent from 0 to 100.`,
      });
    }
  });
});
