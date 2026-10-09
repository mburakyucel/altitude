import { useEffect, useRef } from "react";
import { useLocation } from "react-router";

/**
 * What a page holds when the operator leaves one history entry, such as unsent answers or the question
 * whose preview they opened. Kept in this tab's memory only, under the router's entry key, so it returns
 * on Back/Forward to that same entry and never on a fresh visit. Reloading, closing the tab or pairing
 * the device again drops it; each reader decides whether a kept value is still current.
 */
const kept = new Map<string, unknown>();
const id = (entry: string, item: string) => `${entry}\n${item}`;

/** A pairing starts the app fresh, like its cached replies. */
export function forgetVisits() {
  kept.clear();
}

/** Record a value for the entry being shown now, before navigating away from it. */
export function keepForVisit(entry: string, item: string, value: unknown) {
  kept.set(id(entry, item), value);
}

/** Read once what this entry kept for `item`; it applies to one return only. */
export function useVisitReturn<T>(item: string): T | undefined {
  const { key } = useLocation();
  const value = useRef<{ kept: T | undefined } | null>(null);
  value.current ??= { kept: kept.get(id(key, item)) as T | undefined };
  useEffect(() => { kept.delete(id(key, item)); }, []);
  return value.current.kept;
}

/**
 * Restore `items` kept for this history entry once, and keep their latest values when the component
 * leaves it. `read` returns the value to keep for each item, or undefined for nothing.
 */
export function useVisitMemory<T>(items: string[], read: (item: string) => T | undefined): Record<string, T> {
  const { key } = useLocation();
  const entry = useRef(key);
  entry.current = key;
  const latest = useRef(read);
  latest.current = read;
  const list = useRef(items);
  list.current = items;
  const restored = useRef<Record<string, T> | null>(null);
  restored.current ??= Object.fromEntries(items.flatMap((item) => {
    const value = kept.get(id(key, item)) as T | undefined;
    return value === undefined ? [] : [[item, value]];
  }));
  useEffect(() => {
    for (const item of Object.keys(restored.current ?? {})) kept.delete(id(entry.current, item));
    return () => {
      for (const item of list.current) {
        const value = latest.current(item);
        if (value === undefined) kept.delete(id(entry.current, item));
        else kept.set(id(entry.current, item), value);
      }
    };
  }, []);
  return restored.current;
}
