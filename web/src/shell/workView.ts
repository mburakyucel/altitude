import { useSyncExternalStore } from "react";

/**
 * How Work shows current work on desktop (SPEC.md §3.7): the list, or the graph. A display preference
 * persisted per browser under "altitude.workView"; absent or unreadable means the list, and without
 * storage the choice lasts for the page.
 */
export type WorkView = "list" | "graph";

export const WORK_VIEW_KEY = "altitude.workView";

const listeners = new Set<() => void>();
let unstored: WorkView | null = null;

export function readWorkView(): WorkView {
  if (unstored) return unstored;
  try {
    return localStorage.getItem(WORK_VIEW_KEY) === "graph" ? "graph" : "list";
  } catch {
    return "list";
  }
}

export function setWorkView(view: WorkView): void {
  try {
    localStorage.setItem(WORK_VIEW_KEY, view);
    unstored = null;
  } catch {
    unstored = view;
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useWorkView(): [WorkView, (view: WorkView) => void] {
  return [useSyncExternalStore(subscribe, readWorkView, () => "list"), setWorkView];
}
