import { useState, type FormEvent, type ReactNode } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/console/app-shell";
import { Banner, Mono, PageHeader } from "@/components/console/primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import * as api from "@/lib/api/client";
import { actionQueueListQuery } from "@/lib/api/queries";
import type { ActionRecord, ProjectRecord } from "@/lib/api/types";
import { findAction } from "@/lib/project-setup";

/**
 * *New project* (ADR 212, step 5 of `docs/plans/projects.md`): writes the
 * project's record, then queues the worker's setup actions one at a time.
 *
 * With a remote the order is record, `keygen`, the public key shown to be added
 * as the repository's deploy key, `check`, then `clone`: a record's remote is SSH
 * only and the clone reaches it with that key, so the key comes first. With no
 * remote it is record, then `init`, an empty repository that never pushes. Each
 * action's outcome is read back from `GET /api/actions` by the id its queueing
 * answered; nothing here invents a state the queue does not serve. Only the
 * public half of the key is ever on this screen: it is what the finished
 * `keygen` reports.
 */
export const Route = createFileRoute("/projects/new")({
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

function NewProjectPage() {
  const qc = useQueryClient();
  const queue = useQuery(actionQueueListQuery);
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<"remote" | "empty">("remote");
  const [remote, setRemote] = useState("");
  const [record, setRecord] = useState<ProjectRecord | null>(null);
  const [queued, setQueued] = useState<Partial<Record<Setup, string>>>({});

  const settle = () => qc.invalidateQueries({ queryKey: ["action-queue"] });

  const create = useMutation({
    mutationKey: ["projects", "create"],
    mutationFn: async () => {
      const made = await api.createProject({
        slug: slug.trim(),
        ...(title.trim() ? { title: title.trim() } : {}),
        remote: mode === "remote" ? remote.trim() : null,
      });
      setRecord(made);
      const first = made.remote
        ? await api.keygenProject(made.slug)
        : await api.initProject(made.slug);
      setQueued({ [made.remote ? "keygen" : "init"]: first.id });
    },
    onSettled: settle,
  });

  const queue1 = useMutation({
    mutationKey: ["projects", "setup"],
    mutationFn: async ({ kind, replace }: { kind: Setup; replace?: boolean }) => {
      if (!record) throw new Error("no project record yet");
      const s = record.slug;
      const a =
        kind === "keygen"
          ? await api.keygenProject(s, replace === true)
          : kind === "check"
            ? await api.checkProject(s)
            : kind === "clone"
              ? await api.cloneProject(s)
              : await api.initProject(s);
      setQueued((cur) => ({ ...cur, [kind]: a.id }));
    },
    onSettled: settle,
  });

  const q = queue.data?.data;
  const of = (kind: Setup) => {
    const id = queued[kind];
    return id === undefined ? undefined : { id, action: findAction(q, id) };
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

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
              value={slug}
              disabled={record !== null || create.isPending}
              onChange={(e) => setSlug(e.target.value)}
            />
          </Field>
          <Field label="Title" hint="Left empty, the record's title is its slug.">
            <Input
              aria-label="Title"
              className="h-7 text-xs"
              value={title}
              disabled={record !== null || create.isPending}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <fieldset className="space-y-1" disabled={record !== null || create.isPending}>
            <legend className="label-xs">Repository</legend>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="mode"
                checked={mode === "remote"}
                onChange={() => setMode("remote")}
              />
              Clone a remote
            </label>
            {mode === "remote" && (
              <Input
                aria-label="Remote"
                className="ml-5 h-7 text-xs"
                placeholder="git@github.com:owner/repo.git"
                value={remote}
                onChange={(e) => setRemote(e.target.value)}
              />
            )}
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="mode"
                checked={mode === "empty"}
                onChange={() => setMode("empty")}
              />
              Empty repository
            </label>
            {mode === "empty" && (
              <p className="ml-5 text-[11px] text-muted-foreground">
                A project with no remote has no deploy key and never pushes.
              </p>
            )}
          </fieldset>
          {record === null && (
            <Button
              type="submit"
              size="sm"
              disabled={
                create.isPending ||
                slug.trim() === "" ||
                (mode === "remote" && remote.trim() === "")
              }
            >
              {create.isPending ? "Creating…" : "Create project"}
            </Button>
          )}
          {create.isError && (
            <Banner tone="danger">
              {create.error instanceof Error
                ? create.error.message
                : "The project was not created."}
            </Banner>
          )}
        </form>

        {record && (
          <p>
            Record <Mono>{record.slug}</Mono> written
            {record.remote ? (
              <>
                {" "}
                for <Mono>{record.remote}</Mono>.
              </>
            ) : (
              ", with no remote."
            )}
          </p>
        )}

        {record?.remote && (
          <KeyStep
            step={of("keygen")}
            busy={queue1.isPending}
            pushes={record.settings.push_task_branch}
            onReplace={() => queue1.mutate({ kind: "keygen", replace: true })}
          />
        )}
        {record?.remote && of("keygen")?.action?.status === "done" && (
          <section className="space-y-2" aria-label="Check and clone">
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={queue1.isPending || isOpen(of("check")?.action)}
                onClick={() => queue1.mutate({ kind: "check" })}
              >
                Check
              </Button>
              <Button
                size="sm"
                disabled={queue1.isPending || of("clone") !== undefined}
                onClick={() => queue1.mutate({ kind: "clone" })}
              >
                Clone
              </Button>
            </div>
            <CheckLine step={of("check")} pushes={record.settings.push_task_branch} />
            <SetupLine label="Clone" step={of("clone")} slug={record.slug} />
          </section>
        )}
        {record && !record.remote && (
          <SetupLine label="Init" step={of("init")} slug={record.slug} />
        )}

        {queue1.isError && (
          <Banner tone="danger">
            {queue1.error instanceof Error ? queue1.error.message : "The action was not queued."}
          </Banner>
        )}
        {record && queue.isError && (
          <Banner tone="warning">
            GET /api/actions did not answer, so the setup's progress is not shown here.
          </Banner>
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

/** Pending or claimed: the worker has it, or will. */
function isOpen(a: ActionRecord | undefined): boolean {
  return a !== undefined && (a.status === "pending" || a.status === "claimed");
}

type Step = { id: string; action: ActionRecord | undefined } | undefined;

/** Where a queued action is, by the queue's own status; the error when it failed. */
function Progress({ label, step }: { label: string; step: Step }) {
  if (!step) return null;
  const a = step.action;
  if (!a) {
    return (
      <p className="text-muted-foreground">
        {label} queued as <Mono>{step.id}</Mono>; not in the queue's answer yet.
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
