/**
 * The seven names the console reads from its environment, validated at import.
 *
 * Server-only: this module reads the api's bearer token, and the one rule
 * `docs/decisions.md` ADR 15 calls easy to undo by accident is that the token is
 * read on the server or it is published — Vite inlines every
 * `import.meta.env.VITE_*` into the client bundle, so none of these seven carries
 * that prefix and none of them may grow it. `typeof window` below is the
 * mechanical half of that rule; `front/src/server.ts` importing this module
 * (through `forward.ts`) is what makes a missing token a refusal to boot rather
 * than a 401 from a screen.
 *
 * ADR 24 is the decision, including why `CONSOLE_PROJECT` has its own name rather
 * than reusing `BOARD_PROJECT`, the name the since-retired Jinja board read.
 *
 * ADR 112 adds the config writer's pair, `CONFIG_WRITER_URL` and
 * `CONFIG_WRITER_TOKEN`, for the settings forward. The token is the one name here
 * that is *not* a refusal to boot: every other screen reads the api and keeps
 * working without it, so its absence is the settings forward's 503 rather than
 * the whole console's crash.
 *
 * ADR 136 adds `BOARD_SERVICE_URL`, for the board-service's writes: the backlog
 * writes (`POST /api/backlog`, and a task's `PATCH`, comment, accept and
 * verdict) and every write `forward.ts`'s `boardTarget` sends there since. The
 * backlog writes carry no token: the board-service takes none there, and refuses
 * any request carrying a browser `Origin` instead (`board_service/app.py:_http`).
 *
 * ADR 157 adds `BOARD_ACTIONS_TOKEN`, the bearer of the board-service's action
 * routes (ADR 151) and of every board-service write since that asks for it: each
 * route `forward.ts`'s `boardTarget` resolves with an `action`, listed in
 * `forwardBoard`'s doc comment. Like the writer's token it is not a refusal to
 * boot: unset, each of those routes answers a 503 naming it.
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

/**
 * Where the config writer answers (`docs/decisions.md` ADR 112 part 3). The host
 * default is ADR 109's port, 8791, because 8790 is this console's own; in compose
 * both files set `http://config-writer:8791`. Trimmed as `API_BASE_URL` is, so a
 * trailing slash in the environment cannot become `//settings`.
 */
export const CONFIG_WRITER_URL = (
  process.env["CONFIG_WRITER_URL"] || "http://127.0.0.1:8791"
).replace(/\/+$/, "");

/**
 * The writer's bearer credential, or `null` when unset. Empty and whitespace-only
 * read as unset, the way the writer's own `main` reads the same variable, so the
 * two sides never disagree about whether a token was configured. Never thrown on
 * here: `forward.ts` answers each settings request with a JSON 503 naming the
 * variable instead (ADR 112 part 3).
 */
export const CONFIG_WRITER_TOKEN = process.env["CONFIG_WRITER_TOKEN"]?.trim() || null;

/**
 * ADR 7's number again, as its own constant (ADR 112 part 6): a save is one small
 * file write under a lock, so nothing argues for a longer wait than the api's.
 */
export const CONFIG_WRITER_TIMEOUT_MS = 5000;

/**
 * Where the board-service answers the console's writes (`docs/decisions.md`
 * ADR 136; `forward.ts`'s `boardTarget` names them). The default is the
 * compose service name and its port, 8792, the way `API_BASE_URL`'s is.
 * Trimmed as the others are, so a trailing slash in the environment cannot
 * become `//api/backlog`. No `VITE_` prefix: the address is the server half's
 * topology, and a failure body names the service, never it.
 */
export const BOARD_SERVICE_URL = (
  process.env["BOARD_SERVICE_URL"] || "http://board-service:8792"
).replace(/\/+$/, "");

/**
 * ADR 7's number once more, as its own constant: each write is one task file
 * written under its lock, so nothing argues for a longer wait than the api's.
 */
export const BOARD_SERVICE_TIMEOUT_MS = 5000;

/**
 * The bearer of the board-service routes `forward.ts`'s `boardTarget` resolves
 * with an `action` (`docs/decisions.md` ADR 157, on ADR 151; `forwardBoard`'s doc
 * comment lists them), or `null` when unset. Empty and whitespace-only read as
 * unset, as `CONFIG_WRITER_TOKEN` does. Never thrown on here: `forward.ts`
 * answers each of those routes with a JSON 503 naming the variable, and the value
 * is never logged or put in a response.
 */
export const BOARD_ACTIONS_TOKEN = process.env["BOARD_ACTIONS_TOKEN"]?.trim() || null;
