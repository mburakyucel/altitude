import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import type { Decision, Overview } from "./api";
import { questionPath } from "./decisions";

/**
 * Decision alerts (issue #221): one notification per newly escalated operator question, carrying the
 * project and task name only. The switch is per device, because each browser grants its own
 * permission, and lives under "altitude.alerts" beside the theme. Alerts arrive only while an
 * Altitude page is open — a background desktop tab still alerts; a phone stops the page shortly after
 * it leaves the screen. `altitude.alerts.seen` keeps the keys already alerted, so a refresh,
 * a reconnection or activity on other tasks never repeats one, and a device that has never
 * recorded a key starts from what is already waiting instead of announcing the backlog.
 */
export const ALERTS_KEY = "altitude.alerts";
export const ALERTS_SEEN_KEY = "altitude.alerts.seen";

/** "off" also covers a browser that has neither granted nor refused permission yet. */
export type AlertState = "unsupported" | "off" | "on" | "blocked";

const listeners = new Set<() => void>();

function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    Boolean(navigator.serviceWorker) &&
    typeof Notification !== "undefined" &&
    typeof ServiceWorkerRegistration !== "undefined" &&
    "showNotification" in ServiceWorkerRegistration.prototype
  );
}

export function readAlertState(): AlertState {
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  try {
    if (localStorage.getItem(ALERTS_KEY) !== "on") return "off";
  } catch {
    return "off"; // preference storage unavailable: alerts stay off
  }
  return Notification.permission === "granted" ? "on" : "off";
}

function announce() {
  listeners.forEach((listener) => listener());
}

function store(value: string | null) {
  try {
    if (value === null) localStorage.removeItem(ALERTS_KEY);
    else localStorage.setItem(ALERTS_KEY, value);
  } catch {
    // no persistence: the switch still reflects this page's permission
  }
}

function readSeen(): string[] | null {
  try {
    const raw = localStorage.getItem(ALERTS_SEEN_KEY);
    const parsed: unknown = raw === null ? null : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === "string") : null;
  } catch {
    return null;
  }
}

function writeSeen(keys: string[] | null) {
  try {
    if (keys === null) localStorage.removeItem(ALERTS_SEEN_KEY);
    else localStorage.setItem(ALERTS_SEEN_KEY, JSON.stringify(keys));
  } catch {
    // without storage every open alerts once, which is the honest limit of this device
  }
}

/** Turning alerts on records what is already waiting, so the switch never announces the backlog. */
export async function enableAlerts(pending: Decision[]): Promise<AlertState> {
  if (!supported()) return "unsupported";
  const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (permission !== "granted") {
    announce();
    return readAlertState();
  }
  await navigator.serviceWorker.register("/sw.js");
  writeSeen(pending.filter((decision) => decision.id).map(alertKey));
  store("on");
  announce();
  return "on";
}

export function disableAlerts(): void {
  store(null);
  announce();
}

export function useAlertState(): AlertState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      // Permission can also change in browser settings, which shows on the next return to the page.
      document.addEventListener("visibilitychange", listener);
      return () => {
        listeners.delete(listener);
        document.removeEventListener("visibilitychange", listener);
      };
    },
    readAlertState,
    () => "unsupported",
  );
}

/**
 * One key per waiting ask: the members of a grouped question share one card and one alert, and a
 * republished question keeps its key. A block and the escalation that follows it are two
 * publications of the same waiting decision, and keying on the revision alerted twice for one card.
 */
export function alertKey(decision: Decision): string {
  return `${decision.project}:${decision.slug}:${decision.group_id || decision.id}`;
}

/** Needs you shows every decision; a task or decision page shows its own. */
export function showsDecision(pathname: string, decision: Decision): boolean {
  if (pathname === "/") return true;
  const owner = `/projects/${decision.project}/tasks/${decision.slug}`;
  return pathname === owner || pathname.startsWith(`${owner}/`)
    || pathname === `/projects/${decision.project}/decisions/${decision.slug}`;
}

async function deliver(decision: Decision, key: string): Promise<void> {
  // Registering first is idempotent and keeps `ready` from waiting forever when site data was cleared.
  await navigator.serviceWorker.register("/sw.js");
  const registration = await navigator.serviceWorker.ready;
  await registration.showNotification(`${decision.project} needs a decision`, {
    // The task name only: question, conversation and incident text never leave the page.
    body: decision.title || decision.slug,
    tag: key,
    data: { url: questionPath(decision) },
  });
}

/**
 * Mounted once by the shell. Published operator questions alert; faults, stopped tasks and finished
 * work stay in Needs you without one. A decision already on screen is recorded without alerting.
 */
export function useDecisionAlerts(overview: Overview | undefined, pathname: string): void {
  const state = useAlertState();
  const navigate = useNavigate();
  const asks = (overview?.queue ?? []).filter((decision) => decision.id);
  const signature = asks.map(alertKey).join("\n");
  const latest = useRef(asks);
  latest.current = asks;

  useEffect(() => {
    if (state !== "on" || !overview) return;
    const current = new Map(latest.current.map((decision) => [alertKey(decision), decision]));
    const keys = [...current.keys()];
    const seen = readSeen();
    writeSeen(keys); // resolved decisions drop out, so the record stays the size of the queue
    if (seen === null) return; // storage lost its record: start again from what is waiting now
    for (const [key, decision] of current) {
      if (seen.includes(key)) continue;
      if (!document.hidden && showsDecision(pathname, decision)) continue;
      // A permission revoked since the last read leaves the decision in Needs you and nothing else.
      void deliver(decision, key).catch(() => announce());
    }
  }, [state, signature, pathname]);

  useEffect(() => {
    if (!supported()) return;
    const open = (event: MessageEvent) => {
      const data = event.data as { type?: string; url?: string } | null;
      if (data?.type === "alert-open" && data.url) navigate(data.url);
    };
    const worker = navigator.serviceWorker;
    worker.addEventListener("message", open);
    return () => worker.removeEventListener("message", open);
  }, [navigate]);
}

const NOTE: Record<AlertState, string> = {
  unsupported: "This browser cannot show alerts.",
  off: "Altitude can alert you when a new decision arrives.",
  on: "Alerts arrive only while Altitude is open. On a phone that means while it is on screen.",
  blocked: "Alerts are blocked in this browser's settings. Allow notifications for this site, then turn them on again.",
};

/** The switch on Needs you (SPEC.md §2.1), set on each device that should alert. */
export function DecisionAlertToggle({ pending }: { pending: Decision[] }) {
  const state = useAlertState();
  const [asking, setAsking] = useState(false);
  const on = state === "on";
  const settled = state === "unsupported" || state === "blocked";

  return (
    <p className="needs-alerts text-meta text-muted">
      <button
        type="button"
        className="link"
        aria-pressed={settled ? undefined : on}
        disabled={settled || asking}
        onClick={() => {
          if (on) return disableAlerts();
          setAsking(true);
          // A worker that fails to register leaves the switch off, ready to try again.
          void enableAlerts(pending).catch(disableAlerts).finally(() => setAsking(false));
        }}
      >
        {on ? "Alerts on" : "Alert me about new decisions"}
      </button>{" "}
      <span>{NOTE[state]}</span>
    </p>
  );
}
