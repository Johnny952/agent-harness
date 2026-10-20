import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Cpu,
  KeyRound,
  Folder,
  Hourglass,
  Layers,
  Play,
  Terminal,
  LayoutGrid,
  ListOrdered,
  MessageSquare,
  Milestone,
  Server,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { FOCUS_SEARCH_EVENT, OPEN_THREAD_EVENT } from "@/hooks/use-console";
import { actionBackendQuery } from "@/lib/api/queries";
import {
  listedProjects,
  useProjectCounts,
  useProjectList,
  type ProjectCounts,
} from "@/lib/projects";
import { ChatPanel } from "./chat-panel";
import { Kbd } from "./primitives";

const NAV = [
  { to: "/", key: "b", label: "Board", icon: LayoutGrid },
  { to: "/approvals", key: "a", label: "Approvals", icon: CheckCircle2 },
  { to: "/pool", key: "p", label: "Pool", icon: Server },
  { to: "/tokens", key: "t", label: "Tokens", icon: KeyRound },
  { to: "/sessions", key: "s", label: "Session logs", icon: Terminal },
  { to: "/tail", key: "l", label: "Live tail", icon: Activity },
  { to: "/debt", key: "d", label: "Debt", icon: AlertTriangle },
  { to: "/learnings", key: "n", label: "Learnings", icon: BookOpen },
  { to: "/roadmap", key: "r", label: "Roadmap", icon: Milestone },
  { to: "/queue", key: "q", label: "Queue", icon: ListOrdered },
  { to: "/backlog", key: "k", label: "Backlog", icon: Layers },
  { to: "/models", key: "m", label: "Settings", icon: Cpu },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [chatOpen, setChatOpen] = useState(false);
  // The thread a screen asked the dock to show (`openThread`, ADR 195). `n`
  // counts the asks, so asking for the same thread twice selects it again.
  const [chatSelect, setChatSelect] = useState<{ threadId: string; n: number } | null>(null);
  const [pendingG, setPendingG] = useState(false);
  const { data: backendUp } = useQuery(actionBackendQuery);

  useEffect(() => {
    function onOpen(e: Event) {
      const threadId = (e as CustomEvent<unknown>).detail;
      if (typeof threadId !== "string" || !threadId) return;
      setChatSelect((s) => ({ threadId, n: (s?.n ?? 0) + 1 }));
      setChatOpen(true);
    }
    window.addEventListener(OPEN_THREAD_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_THREAD_EVENT, onOpen);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const typing =
        el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

      if (e.key === "Escape") {
        if (typing) (el as HTMLElement).blur();
        setChatOpen(false);
        setPendingG(false);
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "/") {
        e.preventDefault();
        window.dispatchEvent(new Event(FOCUS_SEARCH_EVENT));
        return;
      }
      if (e.key === "c") {
        setChatOpen((o) => !o);
        return;
      }
      if (e.key === "g") {
        setPendingG(true);
        setTimeout(() => setPendingG(false), 1200);
        return;
      }
      if (pendingG) {
        const target = NAV.find((n) => n.key === e.key);
        if (target) {
          e.preventDefault();
          void navigate({ to: target.to });
        }
        setPendingG(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingG, navigate]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      <nav className="flex w-[186px] shrink-0 flex-col border-r border-border bg-surface">
        <div className="flex items-center gap-2 border-b border-border px-3 py-3">
          <span className="inline-block size-2 rounded-full bg-success" />
          <span className="mono text-[11px] font-semibold tracking-tight">harness/console</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <ProjectNav pathname={pathname} />
          <SectionLabel>Harness</SectionLabel>
          <ul className="space-y-0.5" aria-label="Harness">
            {NAV.map((item) => {
              const active = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
              const Icon = item.icon;
              return (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    className={cn(
                      "flex items-center gap-2 rounded-sm px-2 py-1.5 text-xs transition-colors",
                      active
                        ? "bg-surface-2 text-foreground"
                        : "text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
                    )}
                  >
                    <Icon className="size-3.5" />
                    <span className="flex-1">{item.label}</span>
                    <Kbd>g {item.key}</Kbd>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="space-y-1 border-t border-border p-2">
          <button
            onClick={() => setChatOpen((o) => !o)}
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
          >
            <MessageSquare className="size-3.5" />
            <span className="flex-1 text-left">Chat</span>
            <Kbd>c</Kbd>
          </button>
          <p className="px-2 pb-1 text-[10px] leading-relaxed text-muted-foreground/70">
            <Kbd>/</Kbd> search · <Kbd>esc</Kbd> close
          </p>
        </div>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {backendUp === false && (
          <div className="border-b border-destructive/50 bg-destructive/10 px-4 py-1.5 text-[11px] text-destructive">
            The action backend is unreachable. These screens are still reading live state; queueing
            and releasing are disabled until it answers.
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </main>

      {chatOpen && <ChatPanel onClose={() => setChatOpen(false)} select={chatSelect} />}
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
      {children}
    </p>
  );
}

const PROJECT_PAGES = [
  { to: "/p/$slug", suffix: "", label: "Board" },
  { to: "/p/$slug/backlog", suffix: "/backlog", label: "Backlog" },
  // The project's own debt, learnings and roadmap (ADR 210).
  { to: "/p/$slug/debt", suffix: "/debt", label: "Debt" },
  { to: "/p/$slug/learnings", suffix: "/learnings", label: "Learnings" },
  { to: "/p/$slug/roadmap", suffix: "/roadmap", label: "Roadmap" },
] as const;

/**
 * The sidebar's "Projects" section: one entry per active project record
 * (`GET /api/projects`, ADR 209), by title with the slug as its fallback, each
 * linking to its `/p/$slug/` screens; a project with no task yet is listed, an
 * archived one is not. When the records read fails the list is the slugs the
 * board's tasks name (ADR 208), and says so. The open project lists its screens
 * beneath it. Read-only: no project is made or edited here. ADR 208, ADR 209.
 *
 * Each entry draws what it has running and waiting (`projectCounts`, ADR 217),
 * a count only above zero, and none at all when the read it comes from failed:
 * a note under the list says which. *Show archived* lists the archived records
 * too, marked; the choice is this viewer's, held in the component.
 */
export function ProjectNav({ pathname }: { pathname: string }) {
  const list = useProjectList();
  const { counts, tasksFailed, queueFailed } = useProjectCounts();
  const [showArchived, setShowArchived] = useState(false);
  const archived = list.entries?.filter((e) => e.status === "archived").length ?? 0;
  const projects = !list.entries ? [] : showArchived ? list.entries : listedProjects(list.entries);
  let body: ReactNode;
  if (list.failed) {
    body = (
      <p className="px-2 py-1 text-[11px] text-destructive">
        Projects unread: neither the project records nor the task list answered.
      </p>
    );
  } else if (!list.entries) {
    body = <p className="px-2 py-1 text-[11px] text-muted-foreground">Reading projects…</p>;
  } else if (projects.length === 0) {
    body = (
      <p className="px-2 py-1 text-[11px] text-muted-foreground">
        {list.source === "records"
          ? "No active project recorded yet."
          : "No project on the board yet."}
      </p>
    );
  } else {
    body = (
      <ul className="space-y-0.5" aria-label="Projects">
        {projects.map(({ slug, title, status }) => {
          const base = `/p/${slug}`;
          const open = pathname === base || pathname.startsWith(`${base}/`);
          return (
            <li key={slug}>
              <Link
                to="/p/$slug"
                params={{ slug }}
                data-testid={`project-link-${slug}`}
                className={cn(
                  "flex items-center gap-2 rounded-sm px-2 py-1.5 text-xs transition-colors",
                  open
                    ? "bg-surface-2 text-foreground"
                    : "text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
                )}
              >
                <Folder className="size-3.5" />
                <span
                  className={cn("flex-1 truncate", title === slug && "mono")}
                  title={title === slug ? undefined : slug}
                >
                  {title}
                </span>
                {status === "archived" && (
                  <span className="text-[9px] uppercase tracking-wider text-muted-foreground/70">
                    archived
                  </span>
                )}
                {counts && <CountMarks slug={slug} counts={counts.get(slug)} />}
              </Link>
              {open && (
                <ul className="mt-0.5 space-y-0.5 pl-5">
                  {PROJECT_PAGES.map((page) => {
                    const here =
                      page.suffix === ""
                        ? pathname === base || pathname === `${base}/`
                        : pathname.startsWith(`${base}${page.suffix}`);
                    return (
                      <li key={page.label}>
                        <Link
                          to={page.to}
                          params={{ slug }}
                          className={cn(
                            "block rounded-sm px-2 py-1 text-[11px] transition-colors",
                            here
                              ? "text-foreground"
                              : "text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
                          )}
                        >
                          {page.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    );
  }
  return (
    <div className="mb-2">
      <SectionLabel>Projects</SectionLabel>
      {body}
      {list.entries && (tasksFailed || queueFailed) && (
        <p
          data-testid="project-counts-unread"
          className="px-2 py-1 text-[10px] text-muted-foreground"
        >
          {tasksFailed
            ? "Counts unread: the task list did not answer."
            : "Waiting unread: the action queue did not answer."}
        </p>
      )}
      {archived > 0 && (
        <button
          type="button"
          onClick={() => setShowArchived((v) => !v)}
          aria-pressed={showArchived}
          className="flex w-full items-center rounded-sm px-2 py-1 text-left text-[11px] text-muted-foreground transition-colors hover:bg-surface-2/60 hover:text-foreground"
        >
          {showArchived ? `Hide archived (${archived})` : `Show archived (${archived})`}
        </button>
      )}
      {list.source === "tasks" && list.entries && (
        <p data-testid="projects-fallback" className="px-2 py-1 text-[10px] text-muted-foreground">
          Project records unread: listed from the tasks, by slug.
        </p>
      )}
      {/* The New project form (ADR 212). */}
      <Link
        to="/projects/new"
        className={cn(
          "mt-0.5 flex items-center gap-2 rounded-sm px-2 py-1.5 text-[11px] transition-colors",
          pathname === "/projects/new"
            ? "bg-surface-2 text-foreground"
            : "text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
        )}
      >
        <Plus className="size-3.5" />
        New project
      </Link>
    </div>
  );
}

/**
 * A project's running and waiting counts, each drawn only above zero. No entry
 * means neither; a `null` waiting was not read, and draws nothing.
 */
function CountMarks({ slug, counts }: { slug: string; counts: ProjectCounts | undefined }) {
  const running = counts?.running ?? 0;
  const waiting = counts?.waiting ?? 0;
  if (running === 0 && waiting === 0) return null;
  return (
    <span className="mono flex shrink-0 items-center gap-1 text-[10px]">
      {running > 0 && (
        <span
          data-testid={`project-running-${slug}`}
          title={`${running} running`}
          aria-label={`${running} running`}
          className="flex items-center gap-0.5 text-success"
        >
          <Play className="size-2.5" />
          {running}
        </span>
      )}
      {waiting > 0 && (
        <span
          data-testid={`project-waiting-${slug}`}
          title={`${waiting} waiting`}
          aria-label={`${waiting} waiting`}
          className="flex items-center gap-0.5 text-muted-foreground"
        >
          <Hourglass className="size-2.5" />
          {waiting}
        </span>
      )}
    </span>
  );
}

export function RefreshedAt({ at }: { at: number }) {
  // Rendered only after hydration: the age is clock-dependent and would
  // otherwise mismatch the server HTML.
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    const render = () => {
      if (!at) return setLabel("never refreshed");
      const secs = Math.round((Date.now() - at) / 1000);
      setLabel(secs < 5 ? "refreshed just now" : `refreshed ${secs}s ago`);
    };
    render();
    const t = setInterval(render, 2000);
    return () => clearInterval(t);
  }, [at]);
  return <span className="text-[10px] text-muted-foreground">{label ?? ""}</span>;
}
