import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import type { Attention, Overview } from "./api";
import { attentionPath, isStep } from "./decisions";

/**
 * Decision alerts (issue #221): one notification per newly escalated operator question, carrying the
 * project and task name at most, as this device's preferences allow. The switch is per device, because each browser grants its own
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
export const ALERTS_LAST_KEY = "altitude.alerts.last";

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

/** Tell Altitude about this device's subscription, or to forget it; telling it about one answers its refusal. */
async function tell(endpoint: string, remove = false): Promise<Told> {
  const response = await fetch("/api/alerts/subscription", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(remove ? { endpoint, remove } : { endpoint, every: (await loadPreferences())?.every ?? 0 }),
  });
  if (!response.ok) throw new Error("Altitude did not take this device's subscription.");
  return (await response.json()) as Told;
}

/** The worker's store (web/public/sw.js): what it announced, and this device's alert preferences. */
const WORKER_STORE = "altitude-alerts";

/** What a banner reveals: the project and task, the project alone, or only that a decision waits. */
export type Reveal = "task" | "project" | "none";
/** Seconds that must pass between two alerts on this device; decisions in between arrive together. */
export const PACES = [0, 900, 3600] as const;
export type Preferences = { reveal: Reveal; every: (typeof PACES)[number]; sound: boolean };
export const DEFAULT_PREFERENCES: Preferences = { reveal: "task", every: 0, sound: true };

/**
 * Each device keeps its own preferences, in the worker's Cache Storage beside its announced keys: the one
 * store the page and the worker both read, so a phone can stay discreet while a desktop names everything.
 * Altitude itself learns only how often it may wake this device (`every`), because it sends the wakes, and
 * its copy follows this one each time Altitude opens here. A device that never chose has today's alerts; one
 * whose store cannot be read is `null` here and names nothing, as the worker does, so a failed read never
 * reveals more than was chosen.
 */
let preferences: Preferences | null | undefined;
let loading: Promise<Preferences | null> | undefined;
const UNREAD: Preferences = { ...DEFAULT_PREFERENCES, reveal: "none" };

function parsePreferences(raw: unknown): Preferences {
  const given = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof Preferences, unknown>>;
  return {
    reveal: given.reveal === "project" || given.reveal === "none" ? given.reveal : "task",
    every: PACES.find((pace) => pace === given.every) ?? 0,
    sound: given.sound !== false,
  };
}

/** Read once and kept; `fresh` reads again, as each mount, each return to the screen and each alert do, so a
 * choice made in another tab of this device is the one that holds. */
function loadPreferences(fresh = false): Promise<Preferences | null> {
  if (fresh) loading = undefined;
  loading ??= (async () => {
    const stored = await (await caches.open(WORKER_STORE)).match("/preferences");
    return parsePreferences(stored ? await stored.json() : null);
  })().catch(() => null)
    .then((loaded) => {
      preferences = loaded;
      announce();
      return loaded;
    });
  return loading;
}

/** This device's preferences: undefined while read, null when its store cannot be read. */
export function usePreferences(): Preferences | null | undefined {
  useEffect(() => {
    const read = () => { if (!document.hidden) void loadPreferences(true); };
    read();
    document.addEventListener("visibilitychange", read);
    return () => document.removeEventListener("visibilitychange", read);
  }, []);
  return useSyncExternalStore(watch, () => preferences, () => undefined);
}

/** Tell Altitude how often it may wake this device, when push wakes it; refused, it says why. */
async function pace(every: number): Promise<void> {
  if (!readPushState()) return;
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  const subscription = await registration?.pushManager?.getSubscription();
  if (!subscription) return;
  const response = await fetch("/api/alerts/pace", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: subscription.endpoint, every }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error || "Altitude did not save how often to alert this device.");
  }
}

/** Altitude first: a refused pace leaves the device's choice, and so the screen, as it was. */
export async function savePreferences(next: Preferences): Promise<void> {
  const before = await loadPreferences();
  if (next.every !== before?.every) await pace(next.every);
  await (await caches.open(WORKER_STORE)).put("/preferences", new Response(JSON.stringify(next)));
  preferences = next;
  loading = Promise.resolve(next);
  announce();
}

/**
 * A subscription carries the key it was made with; a key regenerated on the machine cannot wake it. A browser that
 * does not say which key keeps its subscription: if Altitude's key cannot wake it, altd ends it and the next renewal
 * replaces it.
 */
function madeWith(subscription: PushSubscription, key: Uint8Array): boolean {
  const applied = subscription.options?.applicationServerKey;
  if (!applied) return true;
  const bytes = new Uint8Array(applied);
  return bytes.length === key.length && bytes.every((byte, index) => byte === key[index]);
}

type Refusal = { host: string; reason: string };
type Told = { known?: boolean; refused?: string | null };

/** What this device's push service last answered Altitude, as Altitude told this device: null while it takes alerts. */
let refusal: Refusal | null = null;
/** Subscribing and unsubscribing run one after another, so the switch and Altitude opening here never overtake each other. */
let queue: Promise<unknown> = Promise.resolve();
function inTurn<T>(work: () => Promise<T>): Promise<T> {
  const turn = queue.then(work);
  queue = turn.catch(() => undefined);
  return turn;
}

/** The push service first, then Altitude: an endpoint Altitude fails to forget is gone, and its next wake ends it. */
async function replace(registration: ServiceWorkerRegistration, old: PushSubscription | null, key: Uint8Array<ArrayBuffer>) {
  if (old) {
    await old.unsubscribe();
    await tell(old.endpoint, true).catch(() => undefined);
  }
  return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
}

/**
 * Ask the push service to wake this device for Altitude, and tell Altitude. True once Altitude holds a subscription
 * that can wake this device; false when the device cannot be woken — no key on the machine, no push service, or an
 * iPhone not added to the Home Screen — and alerts then arrive only while Altitude is open, which the note says;
 * undefined when Altitude could not be told or alerts went off meanwhile, which changes nothing. A subscription made
 * with a key Altitude no longer signs with is replaced, and so, when `renewing`, is one Altitude no longer held because
 * its push service ended it; Altitude forgets the endpoint replaced, so no record outlives it.
 */
function subscribePush(renewing: boolean): Promise<boolean | undefined> {
  return inTurn(async () => {
    if (readAlertState() !== "on") return undefined;
    const key = await fetch("/api/alerts").then((response) => response.json() as Promise<{ key?: string | null }>)
      .then((body) => body.key ?? null, () => undefined);
    if (key === undefined) return undefined;
    if (!key) return false;
    try {
      // Registering first is idempotent and keeps `ready` from waiting forever when site data was cleared;
      // a subscription attaches to an active worker, and a freshly registered one is still installing.
      await navigator.serviceWorker.register("/sw.js");
      const registration = await navigator.serviceWorker.ready;
      if (!registration.pushManager) return false;
      const wanted = keyBytes(key);
      const held = await registration.pushManager.getSubscription();
      let subscription = held && madeWith(held, wanted) ? held : await replace(registration, held, wanted);
      let told: Told | undefined = await tell(subscription.endpoint).catch(() => undefined);
      if (told && !told.known && renewing && subscription === held) {
        subscription = await replace(registration, subscription, wanted);
        told = await tell(subscription.endpoint).catch(() => undefined);
      }
      if (!told) return undefined;
      refusal = told.refused ? { host: new URL(subscription.endpoint).host, reason: told.refused } : null;
      return true;
    } catch {
      return false; // the push service or the browser refused a subscription
    }
  });
}

/** Whether Altitude can wake this device follows each definite answer while alerts are on; the note says which. */
async function renewPush(renewing: boolean): Promise<void> {
  const pushed = await subscribePush(renewing);
  if (pushed !== undefined && readAlertState() === "on") store(ALERTS_PUSH_KEY, pushed ? "on" : null);
  announce();
}

const unsubscribePush = () => inTurn(async () => {
  try {
    const registration = await navigator.serviceWorker.getRegistration("/sw.js");
    const subscription = await registration?.pushManager?.getSubscription();
    if (!subscription) return;
    // The push service first: an endpoint Altitude fails to drop is then gone, and its next wake ends it.
    await subscription.unsubscribe();
    await tell(subscription.endpoint, true);
  } catch {
    // this device stops alerting either way, and altd drops an endpoint its push service rejects
  }
});

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
    await (await caches.open(WORKER_STORE)).put("/announced", new Response(JSON.stringify(keys)));
  } catch {
    // no Cache Storage: the first wake may name a decision that was already waiting
  }
}

/**
 * Turning alerts on records what is already waiting, for the page and for the worker, so neither the switch
 * nor the first wake announces the backlog.
 */
export async function enableAlerts(pending: Attention[]): Promise<AlertState> {
  if (!supported()) return "unsupported";
  const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (permission !== "granted") {
    announce();
    return readAlertState();
  }
  await navigator.serviceWorker.register("/sw.js");
  const waiting = pending.filter(alerting).map((decision) => decision.alert_key);
  writeSeen(waiting);
  await rememberInWorker(waiting);
  store(ALERTS_KEY, "on");
  announce();
  await renewPush(false);
  return "on";
}

export function disableAlerts(): void {
  store(ALERTS_KEY, null);
  store(ALERTS_PUSH_KEY, null);
  refusal = null;
  announce();
  void unsubscribePush(); // after a renewal under way, which would otherwise subscribe this device again

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

type Ask = Attention & { alert_key: string };

/**
 * A published operator question, which altd keys for alerting (`alert_key`): one key per question group,
 * renewed only when the decision is asked again after the operator replied, so a rewording or an escalation
 * of a waiting decision never alerts twice.
 */
function alerting(decision: Attention): decision is Ask {
  return Boolean(decision.alert_key);
}

/** What a banner may name: the task, or for an operator step only that it is one, never its text. */
const alertName = (decision: Attention) => isStep(decision) ? "A command to run" : decision.title || decision.slug;

/** Needs you shows every decision; a task or decision page shows its own. */
export function showsDecision(pathname: string, decision: Attention): boolean {
  if (pathname === "/") return true;
  // Project chat shows only a count, so a step shows only in Needs you.
  if (isStep(decision)) return false;
  const owner = `/projects/${decision.project}/tasks/${decision.slug}`;
  return pathname === owner || pathname.startsWith(`${owner}/`)
    || pathname === `/projects/${decision.project}/decisions/${decision.slug}`;
}

/**
 * The banner for one decision, as web/public/sw.js composes it: the project and the task name at most, so
 * question, conversation and incident text never leave the page.
 */
function banner(decision: Ask, reveal: Reveal): [string, NotificationOptions] {
  return [reveal === "none" ? "A decision needs you" : `${decision.project} needs a decision`, {
    body: reveal === "task" ? alertName(decision) : undefined,
    tag: decision.alert_key,
    data: { url: attentionPath(decision) },
  }];
}

/** Decisions that arrived while this device waited out its pace, in one banner opening Needs you. */
function together(decisions: Ask[], reveal: Reveal): [string, NotificationOptions] {
  const lines = reveal === "task" ? decisions.map((decision) => `${decision.project} · ${alertName(decision)}`)
    : reveal === "project" ? [...new Set(decisions.map((decision) => decision.project))] : [];
  return [`${decisions.length} decisions need you`, {
    body: lines.length ? lines.join("\n") : undefined, tag: decisions[0]!.alert_key, data: { url: "/" },
  }];
}

async function deliver(decisions: Ask[]): Promise<void> {
  const chosen = (await loadPreferences(true)) ?? UNREAD;
  // Registering first is idempotent and keeps `ready` from waiting forever when site data was cleared.
  await navigator.serviceWorker.register("/sw.js");
  const registration = await navigator.serviceWorker.ready;
  const banners = chosen.every && decisions.length > 1 ? [together(decisions, chosen.reveal)]
    : decisions.map((decision) => banner(decision, chosen.reveal));
  for (const [title, options] of banners) await registration.showNotification(title, { ...options, silent: !chosen.sound });
}

function readLast(): number {
  try {
    return Number(localStorage.getItem(ALERTS_LAST_KEY)) || 0;
  } catch {
    return 0;
  }
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
 * one marked `alert_held` waits, unrecorded, until its task rests, as does one arriving before this device's
 * pace has passed since its last alert. A device push wakes leaves alerting to the service worker, so one
 * decision never shows twice there.
 */
export function useDecisionAlerts(overview: Overview | undefined, pathname: string): void {
  const state = useAlertState();
  const pushed = useSyncExternalStore(watch, readPushState, () => false);
  const chosen = usePreferences();
  const [due, setDue] = useState(0); // the end of this device's pace, which reads the queue again
  const navigate = useNavigate();
  const asks = (overview?.queue ?? []).filter(alerting);
  const signature = asks.map((decision) => `${decision.alert_key}${decision.alert_held ? " held" : ""}`).join("\n");
  const latest = useRef(asks);
  latest.current = asks;

  useEffect(() => {
    if (state !== "on" || !overview || chosen === undefined) return;
    const every = chosen?.every ?? 0;
    const waiting = new Set(latest.current.map((decision) => decision.alert_key));
    const current = new Map(latest.current.filter((decision) => !decision.alert_held)
      .map((decision) => [decision.alert_key, decision]));
    const seen = readSeen();
    void closeAnswered(waiting).catch(() => undefined); // a banner left open still opens the queue
    // Storage lost its record: start again from what is waiting now. A device push wakes is alerted by its worker.
    const fresh = seen === null || pushed ? []
      : [...current.values()].filter((decision) => !seen.includes(decision.alert_key)
        && (document.hidden || !showsDecision(pathname, decision)));
    const wait = fresh.length && every ? readLast() + every * 1000 - Date.now() : 0;
    // Answered decisions drop out, so the record stays the size of the queue; one waiting out the pace stays unrecorded.
    const deferred = new Set(wait > 0 ? fresh.map((decision) => decision.alert_key) : []);
    writeSeen([...new Set([...(seen ?? []).filter((key) => waiting.has(key)),
      ...[...current.keys()].filter((key) => !deferred.has(key))])]);
    if (wait > 0) {
      const timer = window.setTimeout(() => setDue(Date.now()), wait);
      return () => window.clearTimeout(timer);
    }
    if (!fresh.length) return;
    store(ALERTS_LAST_KEY, String(Date.now()));
    // A permission revoked since the last read leaves the decision in Needs you and nothing else.
    void deliver(fresh).catch(() => announce());
  }, [state, signature, pathname, pushed, chosen, due]);

  const loaded = chosen !== undefined;
  const every = chosen?.every;
  useEffect(() => {
    // Each time Altitude opens or returns here with alerts on, the device makes sure push can wake it: a subscription
    // made with an old key, or one Altitude ended, is made anew without the switch, one that failed is tried again,
    // Altitude's copy of the pace follows the device's own, and the note learns whether this device's push service
    // refuses Altitude's alerts.
    if (state !== "on" || !loaded) return;
    const renew = () => { if (!document.hidden) void renewPush(true); };
    renew();
    document.addEventListener("visibilitychange", renew);
    return () => document.removeEventListener("visibilitychange", renew);
  }, [state, loaded, every]);

  useEffect(() => {
    if (state !== "on") return;
    const retry = () => {
      if (!document.hidden) void closeAnswered(new Set(latest.current.map((decision) => decision.alert_key))).catch(() => undefined);
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

/**
 * A refusal Altitude cannot end by itself, on the device it concerns: a subscription its push service ended or made
 * with an old key is replaced without a word. A refused sender token (401, 403) is fixed on the computer running
 * Altitude; any other refusal, such as a busy service, passes, and Altitude tries again with each decision owed.
 */
function refusedNote({ host, reason }: Refusal): string {
  const lead = `The push service at ${host} refused Altitude's last alert (${reason}), so this device gets alerts only `
    + "while Altitude is open.";
  return /^40[13]\b/.test(reason)
    ? `${lead} Give Altitude a real contact address in ALTITUDE_PUSH_CONTACT on the computer it runs on.`
    : `${lead} Altitude keeps trying until one gets through.`;
}

/** What this device's alerts reach, or why it has none, for the switch's line on Needs you and in Settings. */
export function useAlertNote(state: AlertState): string {
  const pushed = useSyncExternalStore(watch, readPushState, () => false);
  const refused = useSyncExternalStore(watch, () => refusal, () => null);
  return state === "on" && pushed ? refused ? refusedNote(refused) : PUSHED : NOTE[state];
}

/** The switch on Needs you (SPEC.md §3.8.1), set on each device that should alert. */
export function DecisionAlertToggle({ pending }: { pending: Attention[] }) {
  const state = useAlertState();
  const note = useAlertNote(state);
  const [asking, setAsking] = useState(false);
  const [showReason, setShowReason] = useState(false);
  const reasonId = useId();
  const on = state === "on";
  const settled = state === "unsupported" || state === "blocked";

  return (
    <p className="needs-alerts text-meta text-muted">
      <button
        type="button"
        className="link"
        aria-pressed={settled ? undefined : on}
        disabled={asking}
        aria-disabled={settled || undefined}
        aria-describedby={reasonId}
        onClick={() => {
          if (settled) { setShowReason(!showReason); return; }
          if (on) return disableAlerts();
          setAsking(true);
          // A worker that fails to register leaves the switch off, ready to try again.
          void enableAlerts(pending).catch(disableAlerts).finally(() => setAsking(false));
        }}
      >
        {on ? "Alerts on" : "Alert me about new decisions"}
      </button>{" "}
      <span id={reasonId} className={settled && !showReason ? "sr-only" : undefined}>{note}</span>
    </p>
  );
}
