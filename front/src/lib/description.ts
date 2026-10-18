/**
 * A task's `description.md` cut into its sections, as the dispatcher cuts it
 * (`dispatcher/tasks.py:parse_description`, ADR 139). The rules are the
 * dispatcher's, mirrored rather than loosened, so the page shows sections only
 * where the harness would read sections:
 *
 * - A section starts at a line that is exactly `## <name>` at column zero, with
 *   only trailing spaces or tabs, for one of the five names. Every other line —
 *   another `##` heading included — is body text.
 * - Non-blank text before the first section, a section named twice, or a
 *   missing or blank `Goal` / `Done when` does not parse.
 * - A body is its lines joined and stripped; a blank optional section is absent.
 *
 * What does not parse is not an error on this page: the caller shows the raw
 * text, with the reason beside it. The reasons are the dispatcher's own
 * `DescriptionError` wording, without the leading "the description".
 */

export const REQUIRED_SECTIONS = ["Goal", "Done when"] as const;
export const OPTIONAL_SECTIONS = ["Surface", "Steps", "Out of scope"] as const;
/** The dataclass's field order, which is the order the page draws them in. */
export const SECTION_ORDER = [...REQUIRED_SECTIONS, ...OPTIONAL_SECTIONS] as const;

export type SectionName = (typeof SECTION_ORDER)[number];

export interface DescriptionSection {
  name: SectionName;
  body: string;
}

export type ParsedDescription =
  { ok: true; sections: DescriptionSection[] } | { ok: false; reason: string };

const HEADER = /^## (Goal|Done when|Surface|Steps|Out of scope)[ \t]*$/;

/** Python's `str.strip()` on whitespace, which is what the dispatcher calls. */
function strip(s: string): string {
  return s.replace(/^\s+|\s+$/g, "");
}

export function parseDescription(text: string): ParsedDescription {
  const bodies = new Map<SectionName, string[]>();
  let current: SectionName | null = null;
  // `str.splitlines()` splits on `\r\n` and `\r` too.
  for (const line of text.split(/\r\n|\r|\n/)) {
    const m = HEADER.exec(line);
    if (m) {
      const name = m[1] as SectionName;
      if (bodies.has(name)) return { ok: false, reason: `has two \`## ${name}\` sections` };
      bodies.set(name, []);
      current = name;
    } else if (current === null) {
      if (strip(line)) {
        return { ok: false, reason: "has text before its first section; start with `## Goal`" };
      }
    } else {
      bodies.get(current)!.push(line);
    }
  }
  const joined = (name: SectionName) => strip((bodies.get(name) ?? []).join("\n"));
  // A required section with only whitespace under it counts as missing.
  const missing = REQUIRED_SECTIONS.filter((name) => !joined(name));
  if (missing.length) {
    const named = missing.map((name) => `\`## ${name}\``).join(" and ");
    return {
      ok: false,
      reason: `is missing its ${named} section${missing.length > 1 ? "s" : ""}`,
    };
  }
  const sections = SECTION_ORDER.map((name) => ({ name, body: joined(name) })).filter(
    (s) => s.body,
  );
  return { ok: true, sections };
}
