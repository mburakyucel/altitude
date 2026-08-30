import { useSyncExternalStore } from "react";

/**
 * Light / dark / system theme. The preference persists under "altitude.theme"; the tokens flip
 * on <html data-theme="dark">, and index.html applies the same rule before first paint.
 */
export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "altitude.theme";

const listeners = new Set<() => void>();

export function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // preference storage unavailable — fall through
  }
  return "system";
}

function systemPrefersDark(): boolean {
  // jsdom may not implement matchMedia — guard every use.
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(theme: Theme): void {
  const dark = theme === "dark" || (theme === "system" && systemPrefersDark());
  if (dark) document.documentElement.dataset.theme = "dark";
  else delete document.documentElement.dataset.theme;

  let themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!themeColor) {
    themeColor = document.createElement("meta");
    themeColor.name = "theme-color";
    document.head.append(themeColor);
  }
  themeColor.content = dark ? "#0f172a" : "#f8fafc";
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // no persistence — still applies for this page
  }
  applyTheme(theme);
  listeners.forEach((listener) => listener());
}

/** Re-apply when the OS scheme changes while the preference is "system". Call once at boot. */
export function watchSystemTheme(): void {
  if (typeof matchMedia !== "function") return;
  const query = matchMedia("(prefers-color-scheme: dark)");
  if (typeof query.addEventListener !== "function") return;
  query.addEventListener("change", () => {
    if (readTheme() === "system") applyTheme("system");
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  return [useSyncExternalStore(subscribe, readTheme, () => "system"), setTheme];
}

const LABELS: Record<Theme, string> = { light: "Light", system: "System", dark: "Dark" };

export function ThemeToggle() {
  const [theme, set] = useTheme();
  return (
    <div role="group" aria-label="Theme" className="inline-flex rounded-card border border-border p-0.5 text-meta">
      {(["light", "system", "dark"] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={theme === option}
          onClick={() => set(option)}
          className="min-h-[var(--target-min)] rounded-[6px] px-3 font-medium text-ink-2 aria-pressed:bg-accent-tint aria-pressed:text-accent-ink"
        >
          {LABELS[option]}
        </button>
      ))}
    </div>
  );
}
