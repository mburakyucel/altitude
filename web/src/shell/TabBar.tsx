import { Link, useLocation, useMatch } from "react-router";
import type { Overview } from "../data/api";
import { scopedProject } from "./projects";
import { useSelectedProject } from "./scope";
import { attentionCount } from "../data/decisions";

export type Tab = "chat" | "work" | "needs" | "monitor";

const ICONS: Record<Tab, string> = {
  chat: "M3 4.5h14v9H8l-4 3v-3H3z",
  work: "M4 5h12M4 10h12M4 15h8",
  needs: "M3.5 8.5 10 3l6.5 5.5V17h-4.5v-5h-4v5H3.5z",
  monitor: "M3 15a7 7 0 1 1 14 0M10 15l3-5",
};

/** Which tab the current page belongs to; a task, decision or Settings page pushed over a tab keeps that tab lit, and a
 * repair's or agent chat's page keeps Work lit, where they are listed. */
export function activeTab(pathname: string, state: unknown, search: string): Tab | null {
  if (pathname === "/") return "needs";
  if (pathname.startsWith("/repairs/")) return "work";
  if (pathname.startsWith("/monitor")) return "monitor";
  const from = state && typeof state === "object" ? (state as { tab?: unknown }).tab : null;
  if (/^\/projects\/[^/]+\/tasks\//.test(pathname)) {
    return from === "needs" || from === "chat" ? from : "work";
  }
  // A project's routines are reached from Work, and their pages keep it lit unless chat opened them.
  if (/^\/projects\/[^/]+\/agents(\/|$)/.test(pathname)) return from === "chat" ? "chat" : "work";
  // Agent chats are listed in Work.
  if (/^\/projects\/[^/]+\/chats\//.test(pathname)) return "work";
  if (/^\/projects\/[^/]+\/decisions\//.test(pathname)) {
    return from === "work" || from === "chat" ? from : "needs";
  }
  // Settings keeps lit the tab of the page that opened it (settingsOpener), as from Needs you's alert link.
  const opened = state && typeof state === "object" ? (state as { settingsTab?: unknown }).settingsTab : null;
  if (pathname.startsWith("/settings") && typeof opened === "string" && opened in ICONS) return opened as Tab;
  return new URLSearchParams(search).get("tab") === "work" ? "work" : "chat";
}

/** The phone's four tabs (SPEC.md §2.2): Chat and Work follow the selected project. */
export function TabBar({ overview, stale = false }: { overview: Overview | undefined; stale?: boolean }) {
  const location = useLocation();
  const selected = useSelectedProject();
  const projectMatch = useMatch("/projects/:name/*");
  const project = scopedProject(overview, projectMatch?.params.name ?? selected);
  const projectPath = project ? `/projects/${project}` : "/projects";
  const attention = attentionCount(overview, stale);
  // Before any project exists there is no Work, so a repair's page lights no tab.
  const current = project || !location.pathname.startsWith("/repairs/") ? activeTab(location.pathname, location.state, location.search) : null;

  const tabs: Array<{ tab: Tab; label: string; to: string }> = [
    { tab: "chat", label: "Chat", to: projectPath },
    { tab: "work", label: "Work", to: `${projectPath}?tab=work` },
    { tab: "needs", label: "Needs you", to: "/" },
    { tab: "monitor", label: "Monitor", to: "/monitor" },
  ];

  return (
    <nav className="tab-bar" aria-label="Primary">
      {tabs.map((item) => (
        // A Link, not a NavLink: Chat and Work share a path and differ by ?tab, so the router's own
        // path match would light both.
        <Link
          key={item.tab}
          to={item.to}
          className="tab-item"
          aria-label={item.tab === "needs" && attention ? `Needs you, ${attention.label}` : item.label}
          aria-current={current === item.tab ? "page" : undefined}
        >
          <span className="tab-icon">
            <svg aria-hidden viewBox="0 0 20 20" width="22" height="22">
              <path d={ICONS[item.tab]} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
            </svg>
            {item.tab === "needs" && attention ? <span className="badge tab-badge" aria-label={attention.label}>{attention.text}</span> : null}
          </span>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
