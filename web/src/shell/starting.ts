import { useSyncExternalStore } from "react";

/**
 * The one project First run is starting (SPEC.md §3.12). It lives outside the component because the
 * project routes decide between First run and the project page from the overview, and the overview
 * lists the project as managed the moment it is added: the page must stay on First run until L3's
 * first reply, wherever the start was pressed.
 */
export interface Starting {
  name: string;
  path: string;
  /** The sentence that stands in for the reply when the start failed; null while it runs. */
  failed: string | null;
  /**
   * How many conversation rows existed when Start was pressed. Only rows after them count as the
   * outcome, so a Retry does not read the previous attempt's error row as its own.
   */
  seen: number | null; // null until registration is accepted; retained history is not a start result
}

let current: Starting | null = null;
const listeners = new Set<() => void>();

export function readStarting(): Starting | null {
  return current;
}

export function setStarting(next: Starting | null): void {
  current = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useStarting(): Starting | null {
  return useSyncExternalStore(subscribe, readStarting, () => null);
}
