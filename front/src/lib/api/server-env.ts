/**
 * The three names the console reads from its environment, validated at import.
 *
 * Server-only: this module reads the api's bearer token, and the one rule
 * `docs/decisions.md` ADR 15 calls easy to undo by accident is that the token is
 * read on the server or it is published — Vite inlines every
 * `import.meta.env.VITE_*` into the client bundle, so none of these three carries
 * that prefix and none of them may grow it. `typeof window` below is the
 * mechanical half of that rule; `front/src/server.ts` importing this module
 * (through `forward.ts`) is what makes a missing token a refusal to boot rather
 * than a 401 from a screen.
 *
 * ADR 24 is the decision, including why `CONSOLE_PROJECT` has its own name rather
 * than reusing `BOARD_PROJECT`, the name the since-retired Jinja board read.
 */

// `tsconfig.json` sets `types: ["vite/client"]`, which admits no Node globals,
// and installing types is not something a phase in this repo may do. Declared
// module-locally so it shadows rather than collides if `@types/node` is ever
// admitted to that list.
declare const process: { env: Record<string, string | undefined> };

if (typeof window !== "undefined") {
  throw new Error(
    "server-env.ts reached a browser bundle: the api token is never read there (docs/decisions.md ADR 15).",
  );
}

const token = process.env["API_TOKEN"];
if (!token) {
  throw new Error(
    "API_TOKEN is not set. The console authenticates to observability/api as a " +
      "service and refuses to start without it (docs/decisions.md ADR 15 and " +
      "ADR 24); it is the same value the api reads from docker/compose/.env.",
  );
}

/** Where observability/api answers. The compose network name is the default. */
export const API_BASE_URL = (process.env["API_BASE_URL"] || "http://api:8789").replace(/\/+$/, "");

/** ADR 7's bearer credential. Never reaches a browser; see the header above. */
export const API_TOKEN = token;

/** Sent as ?project= on the debt call when set, omitted when unset. ADR 9's shape. */
export const CONSOLE_PROJECT = process.env["CONSOLE_PROJECT"] || null;

/** ADR 7's number, for ADR 7's reason: an unbounded read is a page that never arrives. */
export const API_TIMEOUT_MS = 5000;
