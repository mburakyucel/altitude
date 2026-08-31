import { NavLink, Outlet } from "react-router";
import { ToastViewport } from "../data/Toast";
import { useOverview } from "../data/api";
import type { Quota } from "../data/api";
import { ThemeToggle } from "./theme";

const NAV = [
  { to: "/", label: "Inbox", end: true, badge: true },
  { to: "/projects", label: "Projects", end: false, badge: false },
  { to: "/chat", label: "Chat", end: false, badge: false },
  { to: "/monitor", label: "Monitor", end: false, badge: false },
];

/** The persistent readout: "5h 12% · 7d 40%", or "quota unknown". */
function quotaText(quota: Quota | undefined): string {
  return quota && quota.known && quota.five_hour != null && quota.seven_day != null
    ? `5h ${Math.round(quota.five_hour)}% · 7d ${Math.round(quota.seven_day)}%`
    : "quota unknown";
}

/** Layout route: sidebar at md and up, four-tab bottom bar below md. */
export default function AppShell() {
  // Shared with every page through the ["overview"] query cache — no extra request.
  const overview = useOverview();
  const decisions = overview.data?.queue.length ?? 0;
  const quota = quotaText(overview.data?.quota);

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[var(--sidebar-w)_1fr]">
      <aside className="hidden md:flex md:flex-col md:gap-1 md:border-r md:border-border md:bg-surface md:p-4">
        <div className="mb-1 px-3 text-card-title font-semibold">Altitude</div>
        <div className="mb-4 px-3 text-meta text-muted" aria-label="Usage quota">
          {quota}
        </div>
        <nav className="flex flex-col gap-1" aria-label="Primary">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
              {item.label}
              {item.badge && decisions > 0 ? (
                <span className="pill ml-2" aria-label={`${decisions} waiting`}>
                  {decisions}
                </span>
              ) : null}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-1 pt-4">
          <ThemeToggle />
        </div>
      </aside>
      <main className="p-4 pb-24 md:p-8 md:pb-8">
        <div className="mb-4 md:hidden">
          <div className="flex items-center justify-between text-meta text-muted" aria-label="Usage quota">
            <span className="font-semibold text-ink-2">Altitude</span>
            <span>{quota}</span>
          </div>
          <div className="mt-2">
            <ThemeToggle />
          </div>
        </div>
        <Outlet />
      </main>
      <nav
        aria-label="Primary, bottom bar"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t border-border bg-surface md:hidden"
      >
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className="flex min-h-[var(--target-min)] items-center justify-center gap-1 text-meta font-medium text-ink-2 aria-[current=page]:text-accent-ink"
          >
            {item.label}
            {item.badge && decisions > 0 ? (
              <span className="pill" aria-label={`${decisions} waiting`}>
                {decisions}
              </span>
            ) : null}
          </NavLink>
        ))}
      </nav>
      <ToastViewport className="bottom-20 left-1/2 -translate-x-1/2 md:bottom-6 md:left-[calc(var(--sidebar-w)+24px)] md:translate-x-0" />
    </div>
  );
}
