import { NavLink, Outlet } from "react-router";
import { ToastViewport } from "../data/Toast";
import { ThemeToggle } from "./theme";

const NAV = [
  { to: "/", label: "Inbox", end: true },
  { to: "/projects", label: "Projects", end: false },
  { to: "/chat", label: "Chat", end: false },
  { to: "/monitor", label: "Monitor", end: false },
  { to: "/listen", label: "Listen", end: false },
];

/** Layout route: sidebar at md and up, five-tab bottom bar below md. */
export default function AppShell() {
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[var(--sidebar-w)_1fr]">
      <aside className="hidden md:flex md:flex-col md:gap-1 md:border-r md:border-border md:bg-surface md:p-4">
        <div className="mb-4 px-3 text-card-title font-semibold">Altitude</div>
        <nav className="flex flex-col gap-1" aria-label="Primary">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-1 pt-4">
          <ThemeToggle />
        </div>
      </aside>
      <main className="p-4 pb-24 md:p-8 md:pb-8">
        <Outlet />
      </main>
      <nav
        aria-label="Primary, bottom bar"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-surface md:hidden"
      >
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className="flex min-h-[var(--target-min)] items-center justify-center text-meta font-medium text-ink-2 aria-[current=page]:text-accent-ink"
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <ToastViewport className="bottom-20 left-1/2 -translate-x-1/2 md:bottom-6 md:left-[calc(var(--sidebar-w)+24px)] md:translate-x-0" />
    </div>
  );
}
