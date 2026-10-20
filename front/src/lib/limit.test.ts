/** `lib/limit.ts`: the start dialogs' spending limit field (ADR 218, ADR 230). */
import { describe, expect, it } from "vitest";
import { readLimit } from "./limit";

describe("readLimit", () => {
  it("reads blank as null, no limit", () => {
    expect(readLimit("")).toEqual({ ok: true, value: null });
    expect(readLimit("  ")).toEqual({ ok: true, value: null });
  });

  it("takes a whole number of at least 1", () => {
    expect(readLimit("1")).toEqual({ ok: true, value: 1 });
    expect(readLimit(" 50000 ")).toEqual({ ok: true, value: 50000 });
  });

  it("refuses zero, a fraction, a sign or a word", () => {
    for (const t of ["0", "2.5", "-1", "+3", "1e3", "ten"]) {
      expect(readLimit(t)).toEqual({ ok: false, why: `${t} is not a whole number of at least 1.` });
    }
  });

  it("refuses a limit not over what a paused run has spent", () => {
    expect(readLimit("100", 100)).toEqual({
      ok: false,
      why: "The run has already spent 100 measured tokens.",
    });
    expect(readLimit("101", 100)).toEqual({ ok: true, value: 101 });
  });
});
