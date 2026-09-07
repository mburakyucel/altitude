import { useSyncExternalStore } from "react";

/**
 * The scope rule (SPEC.md §2.3): the selected project is UI state persisted per browser, set by the
 * rail, the switcher, or a project route. The phone's Chat and Work tabs follow it.
 */
export const PROJECT_KEY = "altitude.project";

const listeners = new Set<() => void>();

export function readSelectedProject(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

export function setSelectedProject(name: string | null): void {
  if (readSelectedProject() === name) return;
  try {
    if (name === null) localStorage.removeItem(PROJECT_KEY);
    else localStorage.setItem(PROJECT_KEY, name);
  } catch {
    // no persistence: the route still carries the project for this page
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useSelectedProject(): string | null {
  return useSyncExternalStore(subscribe, readSelectedProject, () => null);
}
