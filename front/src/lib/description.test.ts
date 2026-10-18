/**
 * `lib/description.ts` against `dispatcher/tasks.py:parse_description`'s rules
 * (ADR 139): what is a section, what is body text, and what does not parse.
 */
import { describe, expect, it } from "vitest";
import { parseDescription } from "./description";

describe("parseDescription", () => {
  it("cuts the five sections and orders them as the dataclass does", () => {
    const text = [
      "## Steps",
      "1. one",
      "",
      "## Done when",
      "  it is done  ",
      "## Goal",
      "the goal",
      "## Out of scope",
      "nothing else",
      "## Surface",
      "front/",
    ].join("\n");
    expect(parseDescription(text)).toEqual({
      ok: true,
      sections: [
        { name: "Goal", body: "the goal" },
        { name: "Done when", body: "it is done" },
        { name: "Surface", body: "front/" },
        { name: "Steps", body: "1. one" },
        { name: "Out of scope", body: "nothing else" },
      ],
    });
  });

  it("leaves a blank optional section out and keeps other headings as text", () => {
    const text =
      "\n## Goal\ng\n## Notes\nstill goal\n ## Steps\n## Done when \t\nd\n## Surface\n\n";
    expect(parseDescription(text)).toEqual({
      ok: true,
      sections: [
        { name: "Goal", body: "g\n## Notes\nstill goal\n ## Steps" },
        { name: "Done when", body: "d" },
      ],
    });
  });

  it("refuses text before the first section", () => {
    const r = parseDescription("intro\n## Goal\ng\n## Done when\nd");
    expect(r).toEqual({
      ok: false,
      reason: "has text before its first section; start with `## Goal`",
    });
  });

  it("refuses a section named twice", () => {
    const r = parseDescription("## Goal\ng\n## Done when\nd\n## Goal\nh");
    expect(r).toEqual({ ok: false, reason: "has two `## Goal` sections" });
  });

  it("refuses a missing or blank required section", () => {
    expect(parseDescription("## Goal\ng")).toEqual({
      ok: false,
      reason: "is missing its `## Done when` section",
    });
    expect(parseDescription("## Goal\n  \n## Steps\ns")).toEqual({
      ok: false,
      reason: "is missing its `## Goal` and `## Done when` sections",
    });
    expect(parseDescription("the pool screen says which account holds a lock").ok).toBe(false);
  });

  it("reads CRLF line ends as line ends", () => {
    expect(parseDescription("## Goal\r\ng\r\n## Done when\r\nd\r\n")).toEqual({
      ok: true,
      sections: [
        { name: "Goal", body: "g" },
        { name: "Done when", body: "d" },
      ],
    });
  });
});
