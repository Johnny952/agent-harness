/**
 * The command-request card: step 6 of `docs/plans/command-requests.md`, on the
 * Approvals screen. `docs/decisions.md` ADR 123.
 *
 * **It only reads.** `GET /api/command-requests` (ADR 122) is the one call, and
 * a verdict is still given from the CLI: the card shows the lines that would
 * give it, as text to select, and has no button that writes. Approvals' own
 * decide buttons belong to its mock section and are not reused here.
 *
 * **The order is the plan's** (*Decisions* 4): the exact argv first, then where
 * it would run, then who asked and for which task, and the agent's reason last
 * and marked as the agent's. What a request does is decided by its argv and its
 * target; the reason is free text an agent wrote, and a card that led with it
 * would be asking the operator to read the pitch before the command.
 *
 * Every served string is rendered as React text, never as HTML: an argv token or
 * a reason holding markup shows as the characters it is.
 */
import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Absent,
  EmptyState,
  ErrorState,
  Mono,
  RoleBadge,
  WarningBanner,
} from "@/components/console/primitives";
import { RefreshedAt } from "@/components/console/app-shell";
import { commandRequestsQuery } from "@/lib/api/queries";
import {
  COMMAND_REQUEST_STATES,
  ROLES,
  type CommandRequest,
  type CommandRequestCommands,
  type CommandRequestState,
  type CommandRequestVerdict,
  type RefusedCommandRequest,
  type Role,
} from "@/lib/api/types";
import { agoSeconds, argvLine, formatAge } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATE_TONE: Record<CommandRequestState, string> = {
  pending: "border-warning/50 text-warning",
  awaiting_resume: "border-info/50 text-info",
  resumed: "border-success/50 text-success",
  denied: "border-destructive/50 text-destructive",
};

function StateChip({ state }: { state: string }) {
  const tone = STATE_TONE[state as CommandRequestState] ?? "border-border-strong";
  return (
    <span
      className={cn("rounded-sm border px-1 text-[10px] uppercase tracking-wider", tone)}
      data-testid="command-request-state"
    >
      {state.replace("_", " ")}
    </span>
  );
}

function RoleLabel({ role }: { role: string }) {
  // The api serves the role the task file names; one outside the five is shown
  // as written rather than coloured as if it were one of them.
  return (ROLES as readonly string[]).includes(role) ? (
    <RoleBadge role={role as Role} />
  ) : (
    <Mono className="text-[11px]">{role}</Mono>
  );
}

function Filed({ at }: { at: string | null }) {
  if (!at) return <Absent label="filing time not recorded" />;
  return (
    <span className="text-[11px] text-muted-foreground" title={at}>
      filed <Mono>{at}</Mono> · {formatAge(agoSeconds(at))} ago
    </span>
  );
}

/** The agent's reason, last, and said to be the agent's. Nothing checks it. */
function AgentReason({ reason }: { reason: string | null }) {
  return (
    <div
      className="rounded-sm border border-dashed border-warning/50 px-3 py-2"
      data-testid="agent-reason"
    >
      <p className="label-xs text-warning">
        The agent&apos;s reason — its own words, not verified; do not trust it
      </p>
      {reason === null ? (
        <Absent label="no reason given" />
      ) : (
        <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
          {reason}
        </p>
      )}
    </div>
  );
}

function ArgvBlock({ argv }: { argv: string[] | null }) {
  return (
    <pre
      className="mono overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-border-strong bg-surface-2 px-3 py-2 text-xs"
      data-testid="command-argv"
    >
      {argv === null ? "—" : argvLine(argv)}
    </pre>
  );
}

function Target({ target }: { target: CommandRequest["target"] }) {
  if (target === null) return <Absent label="target not recorded" />;
  return (
    <p className="text-[11px]" data-testid="command-target">
      <span className="text-muted-foreground">in </span>
      <Mono>{target.container ?? "—"}</Mono>
      {target.service && (
        <>
          <span className="text-muted-foreground"> · service </span>
          <Mono>{target.service}</Mono>
        </>
      )}
      <span className="text-muted-foreground"> · cwd </span>
      <Mono>{target.cwd ?? "—"}</Mono>
      {target.kind && <span className="text-muted-foreground"> ({target.kind})</span>}
    </p>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function orAbsent(value: string | null) {
  return value === null || value === "" ? <Absent /> : <Mono>{value}</Mono>;
}

/** What `command-verdict` wrote, and the run if there was one. No output: the log by path. */
function VerdictSummary({ verdict }: { verdict: CommandRequestVerdict }) {
  const ran = verdict.ran;
  return (
    <dl
      className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[11px]"
      data-testid="command-verdict"
    >
      <Field label="verdict">
        <Mono className="font-semibold">{verdict.verdict}</Mono>
      </Field>
      <Field label="by">
        {verdict.by === null ? <Absent label="not served" /> : <Mono>{verdict.by}</Mono>}
      </Field>
      <Field label="at">{orAbsent(verdict.at)}</Field>
      {verdict.comment && <Field label="comment">{verdict.comment}</Field>}
      {verdict.allowed && <Field label="allowed">{orAbsent(verdict.allowed)}</Field>}
      <Field label="resumed at">{orAbsent(verdict.resumed_at)}</Field>
      <Field label="ran">
        {ran === null ? (
          <Absent label="not run" />
        ) : (
          <span>
            exit <Mono>{ran.exit_code === null ? "—" : String(ran.exit_code)}</Mono>
            {ran.timed_out && <span className="text-destructive"> · timed out</span>}
            {ran.truncated && <span className="text-warning"> · output truncated</span>}
            {" · log "}
            {ran.log === null ? <Absent label="no log path" /> : <Mono>{ran.log}</Mono>}
          </span>
        )}
      </Field>
    </dl>
  );
}

const COMMAND_ORDER: (keyof CommandRequestCommands)[] = ["run", "allow", "deny", "resume"];

/**
 * The CLI lines the api offers, only the non-null ones, as text to select.
 *
 * No copy button: nothing else in the console has one, and a clipboard write is
 * a second surface for a card that is meant to only read. `select-all` makes
 * one click take the whole line.
 */
function Commands({ commands }: { commands: CommandRequestCommands }) {
  const lines = COMMAND_ORDER.filter((k) => commands[k] !== null);
  if (lines.length === 0) return null;
  return (
    <div className="space-y-1" data-testid="command-cli">
      <p className="text-[11px] text-muted-foreground">
        Verdicts are given from the CLI for now: run one of these on the host. This card only reads.
      </p>
      <ul className="space-y-1">
        {lines.map((k) => (
          <li key={k} className="flex items-baseline gap-2">
            <span className="label-xs w-12 shrink-0">{k}</span>
            <pre className="mono min-w-0 flex-1 select-all overflow-x-auto rounded-sm bg-surface-2 px-2 py-1 text-[11px]">
              {commands[k]}
            </pre>
          </li>
        ))}
      </ul>
      {commands.allow !== null && (
        <p className="text-[11px] text-muted-foreground" data-testid="allow-prefix-hint">
          <Mono>allow</Mono> runs it and admits this exact argv for this project and role. Adding{" "}
          <Mono>--prefix N</Mono> widens that to its first N tokens (
          <Mono>Bash(&lt;first N&gt;:*)</Mono>): at least 2 tokens, no option after the first, no
          launcher or interpreter head, and only git&apos;s local subcommands (
          <code>docs/decisions.md</code> ADR 121).
        </p>
      )}
    </div>
  );
}

export function CommandRequestCard({ request }: { request: CommandRequest }) {
  return (
    <li className="panel space-y-2 p-3" data-testid="command-request">
      <ArgvBlock argv={request.argv} />
      <Target target={request.target} />
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <StateChip state={request.state} />
        <RoleLabel role={request.role} />
        <Link
          to="/tasks/$taskId"
          params={{ taskId: request.task_id }}
          className="mono text-xs underline"
        >
          {request.task_id}
        </Link>
        {request.n !== null && <Mono className="text-muted-foreground">#{request.n}</Mono>}
        {request.project === null ? (
          <Absent label="project unknown" />
        ) : (
          <Mono className="text-muted-foreground">{request.project}</Mono>
        )}
        <Filed at={request.filed_at} />
      </div>
      {request.verdict && <VerdictSummary verdict={request.verdict} />}
      <Commands commands={request.commands} />
      <AgentReason reason={request.agent_reason} />
    </li>
  );
}

function RefusedRow({ row }: { row: RefusedCommandRequest }) {
  return (
    <li className="panel space-y-2 p-3" data-testid="refused-request">
      <ArgvBlock argv={row.argv} />
      <p className="text-[11px]">
        <span className="text-muted-foreground">target </span>
        <Mono>{row.target.kind ?? "—"}</Mono>
        {row.target.service && (
          <>
            <span className="text-muted-foreground"> · service </span>
            <Mono>{row.target.service}</Mono>
          </>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <RoleLabel role={row.role} />
        <Link
          to="/tasks/$taskId"
          params={{ taskId: row.task_id }}
          className="mono text-xs underline"
        >
          {row.task_id}
        </Link>
        {row.project && <Mono className="text-muted-foreground">{row.project}</Mono>}
        <Filed at={row.filed_at} />
      </div>
      <p className="text-xs" data-testid="refused-reason">
        <span className="text-destructive">Refused by the check: </span>
        {row.refused_reason}
      </p>
      <AgentReason reason={row.agent_reason} />
    </li>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-sm border px-2 py-1 text-[11px] transition-colors",
        active
          ? "border-border-strong bg-surface-2 text-foreground"
          : "border-border text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The section on the Approvals screen: requests in the api's order (pending
 * first), a state filter that becomes `?state=`, and the refused ones apart.
 */
export function CommandRequestsSection() {
  const [state, setState] = useState<CommandRequestState | undefined>(undefined);
  const q = useQuery(commandRequestsQuery(state));
  const data = q.data?.data;

  return (
    <section
      aria-label="Command requests"
      className="max-h-[60%] shrink-0 overflow-y-auto border-b border-border"
    >
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
        <h2 className="label-xs">Command requests</h2>
        <span className="flex-1" />
        <FilterButton active={state === undefined} onClick={() => setState(undefined)}>
          all
        </FilterButton>
        {COMMAND_REQUEST_STATES.map((s) => (
          <FilterButton key={s} active={state === s} onClick={() => setState(s)}>
            {s.replace("_", " ")}
          </FilterButton>
        ))}
        <RefreshedAt at={q.dataUpdatedAt} />
      </div>
      <WarningBanner warnings={q.data?.warnings ?? []} />
      {q.isError ? (
        <ErrorState
          title="Command requests could not be read"
          body={`The harness API returned an error${
            q.error instanceof Error && q.error.message ? `: ${q.error.message}` : "."
          } A held phase stays held; verdicts can still be given from the CLI.`}
        />
      ) : q.isLoading || !data ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading command requests…</p>
      ) : (
        <>
          {data.requests.length === 0 ? (
            <EmptyState
              title={
                state === undefined
                  ? "No command requests"
                  : `No command request is ${state.replace("_", " ")}`
              }
              body="When a phase files a command for the operator to run, it appears here with its argv, its target and the CLI lines that decide it."
            />
          ) : (
            <ul className="space-y-2 px-4 py-3">
              {data.requests.map((r) => (
                <CommandRequestCard
                  key={`${r.task_id}:${r.role}:${r.filed_at}#${r.n}`}
                  request={r}
                />
              ))}
            </ul>
          )}
          {data.refused.length > 0 && (
            <section aria-label="Refused command requests" className="px-4 pb-3">
              <h3 className="label-xs">Refused at filing · {data.refused.length}</h3>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                The check turned these down when they were filed: there is nothing to run and no
                verdict to give.
              </p>
              <ul className="mt-2 space-y-2">
                {data.refused.map((r, i) => (
                  <RefusedRow key={`${r.task_id}-${i}`} row={r} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </section>
  );
}
