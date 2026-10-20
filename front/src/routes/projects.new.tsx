import { useState, type FormEvent, type ReactNode } from "react";
import { Link, createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/console/app-shell";
import { Banner, Mono, PageHeader } from "@/components/console/primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import * as api from "@/lib/api/client";
import { actionQuery, projectQuery } from "@/lib/api/queries";
import type { ActionRecord, ProjectRecord } from "@/lib/api/types";

/**
 * *New project* (ADR 212, step 5 of `docs/plans/projects.md`): writes the
 * project's record, then queues the worker's setup actions one at a time.
 *
 * With a remote the order is record, `keygen`, the public key shown to be added
 * as the repository's deploy key, `check`, then `clone`: a record's remote is SSH
 * only and the clone reaches it with that key, so the key comes first. With no
 * remote it is record, then `init`, an empty repository that never pushes. Each
 * action's outcome is read back from `GET /api/actions/<id>` by the id its
 * queueing answered (ADR NNN), so it is found however many other actions have
 * finished since; nothing here invents a state the queue does not serve. Only
 * the public half of the key is ever on this screen: it is what the finished
 * `keygen` reports.
 *
 * The url holds where the setup is (ADR NNN): `?slug=` once the record is
 * written, and each queued action's id under its kind. A reload reads the record
 * back by that slug and each action by its id, so the screen resumes where it
 * was; nothing is kept in the browser.
 */
export const Route = createFileRoute("/projects/new")({
  validateSearch: (search: Record<string, unknown>): SetupSearch => readSearch(search),
  head: () => ({
    meta: [
      { title: "New project — harness operations console" },
      {
        name: "description",
        content:
          "Make a project: its record, its deploy key, and a clone of its remote or an empty repository.",
      },
    ],
  }),
  component: NewProjectPage,
});

type Setup = "keygen" | "check" | "clone" | "init";

const SETUPS: readonly Setup[] = ["keygen", "check", "clone", "init"];

/** The url's part of the setup: the record's slug, and each queued action's id by kind. */
type SetupSearch = { slug?: string } & Partial<Record<Setup, string>>;

/** The search as this screen keeps it; any other key, or a value not a non-empty string, is dropped. */
function readSearch(search: unknown): SetupSearch {
  const raw = (search ?? {}) as Record<string, unknown>;
  const out: SetupSearch = {};
  for (const key of ["slug", ...SETUPS] as const) {
    const v = raw[key];
    if (typeof v === "string" && v !== "") out[key] = v;
  }
  return out;
}

/** A finished action's `result` field, if it is an object; the worker's own shape otherwise unchecked. */
function resultOf(a: ActionRecord | undefined): Record<string, unknown> | null {
  const r = a?.result;
  return r !== null && typeof r === "object" && !Array.isArray(r)
    ? (r as Record<string, unknown>)
    : null;
}

function text(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function message(e: unknown, otherwise: string): string {
  return e instanceof Error ? e.message : otherwise;
}

/**
 * One queued action, read by its id (ADR NNN): its record once read, or why it
 * is not shown. `undefined` when nothing of that kind is in the url.
 */
function useStep(id: string | undefined): Step {
  const read = useQuery({ ...actionQuery(id ?? ""), enabled: id !== undefined });
  if (id === undefined) return undefined;
  const answer = read.data;
  let problem: string | null = null;
  if (read.isError) {
    problem =
      read.error instanceof api.ApiError && read.error.status === 404
        ? "the queue has no record of it."
        : `GET /api/actions/${id} did not answer, so its progress is not shown here.`;
  } else if (answer && answer.data === null) {
    problem = `its record is unreadable${answer.warnings.length ? `: ${answer.warnings.join(" ")}` : "."}`;
  }
  return { id, action: answer?.data ?? undefined, problem };
}

function NewProjectPage() {
  const qc = useQueryClient();
  // `strict: false` and a parse of its own, as the Roadmap screen reads its
  // `?view=`: the route test mounts this component on a route with no
  // `validateSearch`, and the screen must read the same value under both.
  const search = readSearch(useSearch({ strict: false }));
  const navigate = useNavigate();
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<"remote" | "empty">("remote");
  const [remote, setRemote] = useState("");
  const [record, setRecord] = useState<ProjectRecord | null>(null);

  // After a reload the record is read back by the url's slug; a record this
  // screen just wrote is its own answer and is not read again.
  const resuming = record === null && search.slug !== undefined;
  const resumed = useQuery({ ...projectQuery(search.slug ?? ""), enabled: resuming });
  const shown = record ?? (resuming ? (resumed.data?.data ?? null) : null);

  const steps: Record<Setup, Step> = {
    keygen: useStep(search.keygen),
    check: useStep(search.check),
    clone: useStep(search.clone),
    init: useStep(search.init),
  };

  const settle = () => qc.invalidateQueries({ queryKey: ["action-queue"] });

  /** Put the setup in the url, in place of the entry it was on: a reload resumes from it. */
  const keep = (next: SetupSearch) => navigate({ to: ".", search: next, replace: true });

  const create = useMutation({
    mutationKey: ["projects", "create"],
    mutationFn: async () => {
      const made = await api.createProject({
        slug: slug.trim(),
        ...(title.trim() ? { title: title.trim() } : {}),
        remote: mode === "remote" ? remote.trim() : null,
      });
      setRecord(made);
      await keep({ slug: made.slug });
      const first = made.remote
        ? await api.keygenProject(made.slug)
        : await api.initProject(made.slug);
      await keep({ slug: made.slug, [made.remote ? "keygen" : "init"]: first.id });
    },
    onSettled: settle,
  });

  const queue1 = useMutation({
    mutationKey: ["projects", "setup"],
    mutationFn: async ({ kind, replace }: { kind: Setup; replace?: boolean }) => {
      if (!shown) throw new Error("no project record yet");
      const s = shown.slug;
      const a =
        kind === "keygen"
          ? await api.keygenProject(s, replace === true)
          : kind === "check"
            ? await api.checkProject(s)
            : kind === "clone"
              ? await api.cloneProject(s)
              : await api.initProject(s);
      await keep({ ...search, slug: s, [kind]: a.id });
    },
    onSettled: settle,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  const locked = shown !== null || create.isPending || (resuming && resumed.isPending);
  // A record read back shows its own values; the inputs are locked then.
  const view = shown
    ? {
        slug: shown.slug,
        title: shown.title,
        mode: shown.remote ? ("remote" as const) : ("empty" as const),
        remote: shown.remote ?? "",
      }
    : { slug, title, mode, remote };
  // The record is there and its first action is not in the url: the page was
  // left after the record was written and before the action's id came back.
  // That action can be queued from here.
  const first: Setup | null = shown ? (shown.remote ? "keygen" : "init") : null;
  const firstMissing =
    first !== null && steps[first] === undefined && !create.isPending && !queue1.isPending;

  return (
    <AppShell>
      <PageHeader
        title="New project"
        subtitle="A project record, then its repository: a clone of a remote, or an empty one."
      />
      <div className="max-w-2xl space-y-4 p-4 text-xs">
        <form onSubmit={submit} className="space-y-3" aria-label="New project">
          <Field label="Slug" hint="Lowercase letters, digits, - and _; the directory's name.">
            <Input
              aria-label="Slug"
              className="h-7 text-xs"
              value={view.slug}
              disabled={locked}
              onChange={(e) => setSlug(e.target.value)}
            />
          </Field>
          <Field label="Title" hint="Left empty, the record's title is its slug.">
            <Input
              aria-label="Title"
              className="h-7 text-xs"
              value={view.title}
              disabled={locked}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <fieldset className="space-y-1" disabled={locked}>
            <legend className="label-xs">Repository</legend>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="mode"
                checked={view.mode === "remote"}
                onChange={() => setMode("remote")}
              />
              Clone a remote
            </label>
            {view.mode === "remote" && (
              <Input
                aria-label="Remote"
                className="ml-5 h-7 text-xs"
                placeholder="git@github.com:owner/repo.git"
                value={view.remote}
                onChange={(e) => setRemote(e.target.value)}
              />
            )}
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="mode"
                checked={view.mode === "empty"}
                onChange={() => setMode("empty")}
              />
              Empty repository
            </label>
            {view.mode === "empty" && (
              <p className="ml-5 text-[11px] text-muted-foreground">
                A project with no remote has no deploy key and never pushes.
              </p>
            )}
          </fieldset>
          {shown === null && (
            <Button
              type="submit"
              size="sm"
              disabled={locked || slug.trim() === "" || (mode === "remote" && remote.trim() === "")}
            >
              {create.isPending ? "Creating…" : "Create project"}
            </Button>
          )}
          {create.isError && (
            <Banner tone="danger">{message(create.error, "The project was not created.")}</Banner>
          )}
        </form>

        {resuming && resumed.isPending && (
          <p className="text-muted-foreground">
            Reading the record of <Mono>{search.slug}</Mono> back…
          </p>
        )}
        {resuming && resumed.isError && (
          <Banner tone="danger">
            The record of {search.slug} could not be read back:{" "}
            {message(resumed.error, "the api did not answer.")}
          </Banner>
        )}
        {resuming && resumed.isSuccess && resumed.data.data === null && (
          <Banner tone="danger">
            The record of {search.slug} is unreadable
            {resumed.data.warnings.length ? `: ${resumed.data.warnings.join(" ")}` : "."}
          </Banner>
        )}

        {shown && (
          <p>
            Record <Mono>{shown.slug}</Mono> written
            {shown.remote ? (
              <>
                {" "}
                for <Mono>{shown.remote}</Mono>.
              </>
            ) : (
              ", with no remote."
            )}
          </p>
        )}

        {first !== null && firstMissing && (
          <div className="space-y-2">
            <p className="text-muted-foreground">
              No {first} of this record is known here: the page was left before its id came back, or
              it was never queued.
            </p>
            <Button size="sm" onClick={() => queue1.mutate({ kind: first })}>
              {first === "keygen" ? "Make the deploy key" : "Make the empty repository"}
            </Button>
          </div>
        )}

        {shown?.remote && (
          <KeyStep
            step={steps.keygen}
            busy={queue1.isPending}
            pushes={shown.settings.push_task_branch}
            onReplace={() => queue1.mutate({ kind: "keygen", replace: true })}
          />
        )}
        {shown?.remote && steps.keygen?.action?.status === "done" && (
          <section className="space-y-2" aria-label="Check and clone">
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={queue1.isPending || isOpen(steps.check)}
                onClick={() => queue1.mutate({ kind: "check" })}
              >
                Check
              </Button>
              <Button
                size="sm"
                disabled={queue1.isPending || steps.clone !== undefined}
                onClick={() => queue1.mutate({ kind: "clone" })}
              >
                Clone
              </Button>
            </div>
            <CheckLine step={steps.check} pushes={shown.settings.push_task_branch} />
            <SetupLine label="Clone" step={steps.clone} slug={shown.slug} />
          </section>
        )}
        {shown && !shown.remote && <SetupLine label="Init" step={steps.init} slug={shown.slug} />}

        {queue1.isError && (
          <Banner tone="danger">{message(queue1.error, "The action was not queued.")}</Banner>
        )}
      </div>
    </AppShell>
  );
}

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <span className="label-xs">{label}</span>
      {children}
      <p className="text-[10px] text-muted-foreground">{hint}</p>
    </div>
  );
}

/**
 * Pending or claimed: the worker has it, or will. A step queued and not read yet
 * counts as open, so its button is not pressed twice while the first read is out.
 */
function isOpen(step: Step): boolean {
  if (!step) return false;
  const a = step.action;
  if (!a) return step.problem === null;
  return a.status === "pending" || a.status === "claimed";
}

/** A queued action: its id, its record once read, and why it is not shown when it is not. */
type Step = { id: string; action: ActionRecord | undefined; problem: string | null } | undefined;

/** Where a queued action is, by the queue's own status; the error when it failed. */
function Progress({ label, step }: { label: string; step: Step }) {
  if (!step) return null;
  const a = step.action;
  if (!a) {
    if (step.problem !== null) {
      return (
        <Banner tone="warning">
          {label} <Mono>{step.id}</Mono>: {step.problem}
        </Banner>
      );
    }
    return (
      <p className="text-muted-foreground">
        {label} queued as <Mono>{step.id}</Mono>; not read yet.
      </p>
    );
  }
  if (a.status === "failed" || a.status === "interrupted") {
    return (
      <Banner tone="danger">
        {label} {a.status}: {a.error ?? "no error was recorded"}
      </Banner>
    );
  }
  if (a.status !== "done") {
    return (
      <p className="text-muted-foreground">
        {label} <Mono>{a.id}</Mono>{" "}
        {a.status === "claimed" ? "is running" : "is waiting for the worker"}…
      </p>
    );
  }
  return null;
}

function KeyStep({
  step,
  busy,
  pushes,
  onReplace,
}: {
  step: Step;
  busy: boolean;
  /** The record's `push_task_branch` override; `undefined` when it has none and the harness default applies. */
  pushes: boolean | undefined;
  onReplace: () => void;
}) {
  const a = step?.action;
  const r = resultOf(a);
  const publicKey = text(r?.["public"]);
  const fingerprint = text(r?.["fingerprint"]);
  return (
    <section className="space-y-2" aria-label="Deploy key">
      <Progress label="Key" step={step} />
      {a?.status === "failed" && (
        <Button size="sm" variant="outline" disabled={busy} onClick={onReplace}>
          Make a new key, replacing the old one
        </Button>
      )}
      {a?.status === "done" && (
        <>
          {publicKey ? (
            <pre
              className="mono overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-surface-2 p-2 text-[11px]"
              data-testid="public-key"
            >
              {publicKey}
            </pre>
          ) : (
            <Banner tone="warning">The finished keygen reported no public key.</Banner>
          )}
          {fingerprint && (
            <p>
              Fingerprint <Mono>{fingerprint}</Mono>
            </p>
          )}
          <p>
            Add this key to the repository as a deploy key: on GitHub, the repository's Settings →
            Deploy keys → Add deploy key.{" "}
            {pushes === true
              ? "Tick “Allow write access”: this project pushes its task branches."
              : pushes === false
                ? "Leave write access off: this project does not push its task branches."
                : "Tick “Allow write access” only if the harness's push_task_branch is on: the record sets no override."}{" "}
            A key that was replaced stays on GitHub until it is deleted there. Then press Check.
          </p>
        </>
      )}
    </section>
  );
}

/**
 * The check's `{reachable, detail, writable}` (ADR 214). `writable` is a
 * dry-run push's answer, `null` or absent when it was not tried; a refused one
 * is a warning only when the record may push, judged by the same override the
 * deploy-key step reads.
 */
function CheckLine({ step, pushes }: { step: Step; pushes: boolean | undefined }) {
  const a = step?.action;
  if (a?.status !== "done") return <Progress label="Check" step={step} />;
  const r = resultOf(a);
  const detail = text(r?.["detail"]);
  const tail = detail ? ` ${detail}` : "";
  if (r?.["reachable"] === true) {
    const writable = r["writable"];
    if (writable === true) {
      return (
        <Banner tone="info">
          The remote answers this key and would take a push from it.
          {pushes === false
            ? " This project does not push its task branches, so the key does not need write access."
            : ""}
          {tail}
        </Banner>
      );
    }
    if (writable === false) {
      return pushes === false ? (
        <Banner tone="info">
          The remote answers this key, read only: this project does not push.{tail}
        </Banner>
      ) : (
        <Banner tone="warning">
          The remote answers this key but refuses a push from it.{" "}
          {pushes === true
            ? "This project pushes its task branches: tick “Allow write access” on the deploy key, then check again."
            : "If the harness's push_task_branch is on, tick “Allow write access” on the deploy key, then check again."}
          {tail}
        </Banner>
      );
    }
    return (
      <Banner tone="info">The remote answers this key; write access was not tested.{tail}</Banner>
    );
  }
  return (
    <Banner tone="warning">
      The remote does not answer this key yet{detail ? `: ${detail}` : "."}
    </Banner>
  );
}

function SetupLine({ label, step, slug }: { label: string; step: Step; slug: string }) {
  const a = step?.action;
  if (a?.status !== "done") return <Progress label={label} step={step} />;
  const detail = text(resultOf(a)?.["detail"]);
  return (
    <Banner tone="info">
      {label} done{detail ? `: ${detail}` : "."}{" "}
      <Link to="/p/$slug" params={{ slug }} className="underline">
        Open {slug}
      </Link>
    </Banner>
  );
}
