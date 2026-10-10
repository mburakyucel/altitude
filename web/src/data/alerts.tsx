import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import type { Decision, Overview } from "./api";
import { questionPath } from "./decisions";

/**
 * Decision alerts (issue #221): one notification per newly escalated operator question, carrying the
 * project and task name only. The switch is per device, because each browser grants its own
 * permission, and lives under "altitude.alerts" beside the theme. A device that push can wake is alerted
 * by the service worker, open page or not; any other device is alerted by an open Altitude page — a
 * background desktop tab still alerts; a phone stops the page shortly after it leaves the screen.
 * `altitude.alerts.seen` keeps the keys the page already alerted, so a refresh, a reconnection or
 * activity on other tasks never repeats one, and a device that has never recorded a key starts from
 * what is already waiting instead of announcing the backlog.
 */
export const ALERTS_KEY = "altitude.alerts";
export const ALERTS_SEEN_KEY = "altitude.alerts.seen";
export const ALERTS_PUSH_KEY = "altitude.alerts.push";

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

function store(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // no persistence: the switch still reflects this page's permission
  }
}

/** Whether this device also gets a push while Altitude is closed; the note says which it is. */
export function readPushState(): boolean {
  try {
    return supported() && localStorage.getItem(ALERTS_PUSH_KEY) === "on";
  } catch {
    return false;
  }
}

function keyBytes(key: string): Uint8Array<ArrayBuffer> {
  const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(key.length / 4) * 4, "="));
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  raw.split("").forEach((character, index) => { bytes[index] = character.charCodeAt(0); });
  return bytes;
}

async function tell(endpoint: string, remove = false): Promise<boolean> {
  const response = await fetch("/api/alerts/subscription", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(remove ? { endpoint, remove } : { endpoint }),
  });
  return response.ok;
}

/** A subscription carries the key it was made with; a key regenerated on the machine cannot wake it. */
function madeWith(subscription: PushSubscription, key: Uint8Array): boolean {
  const applied = subscription.options?.applicationServerKey;
  if (!applied) return false;
  const bytes = new Uint8Array(applied);
  return bytes.length === key.length && bytes.every((byte, index) => byte === key[index]);
}

/**
 * Ask the push service to wake this device for Altitude. It returns false whenever the device cannot
 * be woken — no key on the machine, no push service reachable, or an iPhone not added to the Home
 * Screen — and alerts then arrive only while Altitude is open, which the note says.
 */
async function subscribePush(): Promise<boolean> {
  try {
    const { key } = (await (await fetch("/api/alerts")).json()) as { key?: string | null };
    if (!key) return false;
    // A subscription attaches to an active worker: a freshly registered one is still installing.
    const registration = await navigator.serviceWorker.ready;
    if (!registration.pushManager) return false;
    const wanted = keyBytes(key);
    let subscription = await registration.pushManager.getSubscription();
    if (subscription && !madeWith(subscription, wanted)) {
      await subscription.unsubscribe(); // this endpoint is deaf to the key Altitude now signs with
      subscription = null;
    }
    subscription ??= await registration.pushManager.subscribe(
      { userVisibleOnly: true, applicationServerKey: wanted });
    return await tell(subscription.endpoint);
  } catch {
    return false;
  }
}

async function unsubscribePush(): Promise<void> {
  try {
    const registration = await navigator.serviceWorker.getRegistration("/sw.js");
    const subscription = await registration?.pushManager?.getSubscription();
    if (!subscription) return;
    // The push service first: an endpoint Altitude fails to drop is then gone, and 410 clears it.
    await subscription.unsubscribe();
    await tell(subscription.endpoint, true);
  } catch {
    // this device stops alerting either way, and altd drops an endpoint its push service rejects
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

/** The service worker's record of the decisions it announced (`altitude-alerts` in web/public/sw.js). */
async function rememberInWorker(keys: string[]): Promise<void> {
  try {
    await (await caches.open("altitude-alerts")).put("/announced", new Response(JSON.stringify(keys)));
  } catch {
    // no Cache Storage: the first wake may name a decision that was already waiting
  }
}

/**
 * Turning alerts on records what is already waiting, for the page and for the worker, so neither the switch
 * nor the first wake announces the backlog.
 */
export async function enableAlerts(pending: Decision[]): Promise<AlertState> {
  if (!supported()) return "unsupported";
  const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (permission !== "granted") {
    announce();
    return readAlertState();
  }
  await navigator.serviceWorker.register("/sw.js");
  const waiting = pending.filter((decision) => decision.id).map(alertKey);
  writeSeen(waiting);
  await rememberInWorker(waiting);
  store(ALERTS_KEY, "on");
  announce();
  store(ALERTS_PUSH_KEY, await subscribePush() ? "on" : null);
  announce();
  return "on";
}

export function disableAlerts(): void {
  store(ALERTS_KEY, null);
  store(ALERTS_PUSH_KEY, null);
  announce();
  void unsubscribePush();
}

function watch(listener: () => void) {
  listeners.add(listener);
  // Permission can also change in browser settings, which shows on the next return to the page.
  document.addEventListener("visibilitychange", listener);
  return () => {
    listeners.delete(listener);
    document.removeEventListener("visibilitychange", listener);
  };
}

export function useAlertState(): AlertState {
  return useSyncExternalStore(watch, readAlertState, () => "unsupported");
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

/** The tag the worker shows when it cannot read what is waiting; it stands for any waiting decision. */
const ANY_DECISION = "altitude-decision";

/**
 * A banner whose decision has left the queue was answered, withdrawn or superseded, and a generic one has
 * been read here: this device closes them without being touched. WebKit ignores a close within 30 seconds
 * of the banner appearing, so an open page tries again on each later read; the worker does the same when
 * the next alert wakes it with the page closed.
 */
async function closeAnswered(waiting: Set<string>): Promise<void> {
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  for (const banner of (await registration?.getNotifications()) ?? []) {
    if (banner.tag === ANY_DECISION || !waiting.has(banner.tag)) banner.close();
  }
}

/** How often an open page tries again to close what WebKit kept open in a banner's first 30 seconds. */
const CLOSE_RETRY_MS = 30_000;

/**
 * Mounted once by the shell. Published operator questions alert; faults, stopped tasks, reviews and finished
 * work stay in Needs you without one. A decision already on screen is recorded without alerting, and
 * one marked `alert_held` waits, unrecorded, until its task rests. A device push wakes leaves alerting to
 * the service worker, so one decision never shows twice there.
 */
export function useDecisionAlerts(overview: Overview | undefined, pathname: string): void {
  const state = useAlertState();
  const pushed = useSyncExternalStore(watch, readPushState, () => false);
  const navigate = useNavigate();
  const asks = (overview?.queue ?? []).filter((decision) => decision.id);
  const signature = asks.map((decision) => `${alertKey(decision)}${decision.alert_held ? " held" : ""}`).join("\n");
  const latest = useRef(asks);
  latest.current = asks;

  useEffect(() => {
    if (state !== "on" || !overview) return;
    const waiting = new Set(latest.current.map(alertKey));
    const current = new Map(latest.current.filter((decision) => !decision.alert_held)
      .map((decision) => [alertKey(decision), decision]));
    const seen = readSeen();
    // Answered decisions drop out, so the record stays the size of the queue.
    writeSeen([...new Set([...(seen ?? []).filter((key) => waiting.has(key)), ...current.keys()])]);
    void closeAnswered(waiting).catch(() => undefined); // a banner left open still opens the queue
    if (seen === null || pushed) return; // storage lost its record: start again from what is waiting now
    for (const [key, decision] of current) {
      if (seen.includes(key)) continue;
      if (!document.hidden && showsDecision(pathname, decision)) continue;
      // A permission revoked since the last read leaves the decision in Needs you and nothing else.
      void deliver(decision, key).catch(() => announce());
    }
  }, [state, signature, pathname, pushed]);

  useEffect(() => {
    if (state !== "on") return;
    const retry = () => {
      if (!document.hidden) void closeAnswered(new Set(latest.current.map(alertKey))).catch(() => undefined);
    };
    const timer = window.setInterval(retry, CLOSE_RETRY_MS);
    document.addEventListener("visibilitychange", retry);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", retry);
    };
  }, [state]);

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

/** The honest difference the operator needs: this device wakes for a decision, closed app and all. */
const PUSHED = "Alerts arrive on this device even when Altitude is closed. Away from your network the alert says a decision is waiting, without naming it.";

type Refusal = { host: string; reason: string };

/**
 * A push service that refuses Altitude's alerts leaves its device alerting only while Altitude is
 * open; altd records why. Read while alerts are on, again after this device subscribes and whenever
 * the page returns to the screen, so a refusal cleared by a later push stops showing.
 */
function useRefusals(on: boolean, pushed: boolean): Refusal[] {
  const [refused, setRefused] = useState<Refusal[]>([]);
  useEffect(() => {
    if (!on) return;
    let live = true;
    const read = () => {
      if (document.hidden) return;
      void fetch("/api/alerts")
        .then((response) => response.json() as Promise<{ refused?: Refusal[] }>)
        .then((body) => { if (live) setRefused(body.refused ?? []); })
        .catch(() => undefined); // altd unreachable: the next return to the page reads again
    };
    read();
    document.addEventListener("visibilitychange", read);
    return () => {
      live = false;
      document.removeEventListener("visibilitychange", read);
    };
  }, [on, pushed]);
  return on ? refused : [];
}

function refusedNote({ host, reason }: Refusal): string {
  return `The push service at ${host} refused Altitude's last alert${reason ? ` (${reason})` : ""}, so that device `
    + "gets no alerts. Turn alerts off and on there to subscribe it again.";
}

/** The switch on Needs you (SPEC.md §2.1), set on each device that should alert. */
export function DecisionAlertToggle({ pending }: { pending: Decision[] }) {
  const state = useAlertState();
  const pushed = useSyncExternalStore(watch, readPushState, () => false);
  const [refusal] = useRefusals(state === "on", pushed);
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
      <span>{refusal ? refusedNote(refusal) : state === "on" && pushed ? PUSHED : NOTE[state]}</span>
    </p>
  );
}
