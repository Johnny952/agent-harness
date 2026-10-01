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
  Layers,
  Terminal,
  LayoutGrid,
  ListOrdered,
  MessageSquare,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { FOCUS_SEARCH_EVENT } from "@/hooks/use-console";
import { actionBackendQuery } from "@/lib/api/queries";
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
  { to: "/queue", key: "q", label: "Queue", icon: ListOrdered },
  { to: "/backlog", key: "k", label: "Backlog", icon: Layers },
  { to: "/models", key: "m", label: "Role models", icon: Cpu },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [chatOpen, setChatOpen] = useState(false);
  const [pendingG, setPendingG] = useState(false);
  const { data: backendUp } = useQuery(actionBackendQuery);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const typing =
        el &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

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
        <ul className="flex-1 space-y-0.5 p-2">
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

      {chatOpen && <ChatPanel onClose={() => setChatOpen(false)} />}
    </div>
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
