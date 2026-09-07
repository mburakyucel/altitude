import { useSyncExternalStore } from "react";

/**
 * Light by default, dark on request, persisted per browser under "altitude.theme". The tokens flip on
 * <html data-theme="dark">; index.html applies the stored value before first paint, which is why boot
 * stores the default too.
 */
export type Theme = "light" | "dark";

export const THEME_KEY = "altitude.theme";

const listeners = new Set<() => void>();

export function readTheme(): Theme {
  try {
    if (localStorage.getItem(THEME_KEY) === "dark") return "dark";
  } catch {
    // preference storage unavailable: light
  }
  return "light";
}

export function applyTheme(theme: Theme): void {
  if (theme === "dark") document.documentElement.dataset.theme = "dark";
  else delete document.documentElement.dataset.theme;

  let themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!themeColor) {
    themeColor = document.createElement("meta");
    themeColor.name = "theme-color";
    document.head.append(themeColor);
  }
  themeColor.content = theme === "dark" ? "#0f172a" : "#f8fafc";
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // no persistence: still applies for this page
  }
  applyTheme(theme);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  return [useSyncExternalStore(subscribe, readTheme, () => "light"), setTheme];
}

/** One button in the operator row: pressed while dark. */
export function ThemeToggle() {
  const [theme, set] = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label="Dark theme"
      aria-pressed={dark}
      title={dark ? "Switch to the light theme" : "Switch to the dark theme"}
      onClick={() => set(dark ? "light" : "dark")}
    >
      <svg aria-hidden viewBox="0 0 20 20" width="18" height="18">
        {dark ? (
          <path d="M11.5 2.5a7.5 7.5 0 1 0 6 12 6.5 6.5 0 0 1-6-12Z" fill="currentColor" />
        ) : (
          <>
            <circle cx="10" cy="10" r="3.5" fill="currentColor" />
            <path
              d="M10 2v2.2M10 15.8V18M2 10h2.2M15.8 10H18M4.3 4.3l1.6 1.6M14.1 14.1l1.6 1.6M4.3 15.7l1.6-1.6M14.1 5.9l1.6-1.6"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </>
        )}
      </svg>
    </button>
  );
}
