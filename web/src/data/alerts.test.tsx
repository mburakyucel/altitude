import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { ALERTS_KEY, ALERTS_LAST_KEY, ALERTS_PUSH_KEY, ALERTS_SEEN_KEY } from "./alerts";

/** Decision alerts (issue #221) through the whole app: the shell watches, Needs you switches. */
function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const question = {
  id: "q-retention", revision: 1, anchor_id: "q-message", status: "open", audience: "operator",
  project: "altitude", slug: "choose-retention", title: "Choose backup retention", kind: "asks",
  asked_by: "l2", question: "How long should backups stay?", asked: ago(3), since: ago(3),
  alert_key: "altitude:choose-retention:q-retention",
};
const second = {
  ...question, id: "q-drill", slug: "run-restore-drill", title: "Run a restore drill", question: "Which drill?",
  alert_key: "altitude:run-restore-drill:q-drill",
};
const stopped = { project: "harbor", slug: "fix-audio", title: "Fix the audio", kind: "stopped", asked_by: "l2", question: "upload fails", asked: ago(2), since: ago(2) };

function overview(queue: unknown[]) {
  return {
    projects: [{ name: "altitude", managed: true }, { name: "harbor", managed: true }],
    queue,
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: [],
    roots: ["~/Projects"],
  };
}

/** A 65-byte application server key, as altd hands it to the page. */
const PUSH_KEY = btoa(String.fromCharCode(...new Uint8Array(65).fill(4))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** The fixture queue the next overview read returns; tests grow it like altd would. */
function mockFetch(initial: unknown[], key: string | null = PUSH_KEY, unreachable = false, refused: string | null = null) {
  let queue = initial;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/alerts/subscription")) {
      if (unreachable) throw new TypeError("Failed to fetch");
      const { endpoint, remove } = JSON.parse(String(init?.body)) as { endpoint: string; remove?: boolean };
      if (remove) return jsonResponse({ push: false });
      const known = !ended.delete(endpoint); // told once, altd holds it
      return jsonResponse({ push: true, known, refused });
    }
    if (url.includes("/api/alerts/pace")) {
      return paceRefusal ? jsonResponse({ error: paceRefusal }, 400) : jsonResponse({ every: JSON.parse(String(init?.body)).every });
    }
    if (url.includes("/api/alerts")) {
      await keyRead;
      return jsonResponse({ key });
    }
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
    if (url.includes("/api/project/")) return jsonResponse({ name: url.split("/").at(-1), tasks: [] });
    if (url.includes("/api/task/")) return jsonResponse({ slug: "x", state: "blocked", messages: [] });
    if (url.includes("/api/chat/")) return jsonResponse({ history: [], queued: [], active: null, busy: false });
    return jsonResponse({ error: "not found" }, 404);
  }));
  return (next: unknown[]) => { queue = next; };
}

const ENDPOINT = "https://push.example/wake/device-1";

/** What altd answers a change of pace with: refused with this reason, or saved. */
let paceRefusal: string | null = null;
/** Endpoints altd does not hold, because their push service ended them or they are new, until it is told one. */
const ended = new Set<string>();
/** Resolves when altd answers the key read, so a test can act while a renewal waits on it. */
let keyRead: Promise<void> = Promise.resolve();

/** The worker's store in Cache Storage, which the page shares: announced keys and this device's preferences. */
function deviceStore(preferences?: Record<string, unknown>) {
  const stored = new Map<string, string>();
  if (preferences) stored.set("/preferences", JSON.stringify(preferences));
  vi.stubGlobal("caches", {
    open: async () => ({
      match: async (key: string) => (stored.has(key) ? new Response(stored.get(key)) : undefined),
      put: async (key: string, value: Response) => { stored.set(key, await value.text()); },
    }),
  });
  return stored;
}

/** The bytes a browser stores with a subscription, as it decodes the key altd handed the page. */
function keyBytes(key: string): Uint8Array {
  const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(key.length / 4) * 4, "="));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

type FakeSubscription = {
  endpoint: string;
  options: { applicationServerKey: Uint8Array };
  unsubscribe: () => Promise<boolean>;
};

class FakeRegistration {
  /** A freshly registered worker is still installing: a real one rejects a subscription until active. */
  constructor(public active = true) {}
  subscribed: FakeSubscription | null = null;
  subscription: FakeSubscription = {
    endpoint: ENDPOINT, options: { applicationServerKey: keyBytes(PUSH_KEY) },
    unsubscribe: vi.fn(async () => true),
  };
  pushManager = {
    subscribe: vi.fn(async (_options: PushSubscriptionOptionsInit) => {
      if (!this.active) throw new DOMException("no active worker", "InvalidStateError");
      this.subscribed = this.subscription;
      return this.subscription;
    }),
    getSubscription: vi.fn(async () => this.subscribed),
  };
  async showNotification(_title: string, _options?: NotificationOptions) {}
  /** The banners this device shows now; closing one removes it. */
  banners: { tag: string; close: () => void }[] = [];
  async getNotifications() { return [...this.banners]; }
  show(...tags: string[]) {
    this.banners.push(...tags.map((tag) => {
      const banner = { tag, close: vi.fn(() => { this.banners = this.banners.filter((shown) => shown !== banner); }) };
      return banner;
    }));
  }
}

/** A browser that can alert: a service worker registration and a permission prompt that sticks. */
function alertingBrowser(permission: NotificationPermission = "default", answer: NotificationPermission = "granted") {
  const shown = vi.spyOn(FakeRegistration.prototype, "showNotification").mockResolvedValue(undefined);
  const registration = new FakeRegistration();
  const installing = new FakeRegistration(false); // what register() resolves with the first time
  const register = vi.fn(async () => installing);
  const listeners = new Set<(event: MessageEvent) => void>();
  const worker = {
    register,
    getRegistration: vi.fn(async () => registration),
    ready: Promise.resolve(registration),
    addEventListener: (_type: string, listener: (event: MessageEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MessageEvent) => void) => listeners.delete(listener),
  };
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: worker });
  deviceStore(); // Cache Storage, empty: this device never chose
  vi.stubGlobal("ServiceWorkerRegistration", FakeRegistration);
  const notification = {
    permission,
    requestPermission: vi.fn(async () => {
      notification.permission = answer;
      return answer;
    }),
  };
  vi.stubGlobal("Notification", notification);
  return {
    shown, register, registration, installing, request: notification.requestPermission,
    click: (url: string) => act(() => {
      listeners.forEach((listener) => listener(new MessageEvent("message", { data: { type: "alert-open", url } })));
    }),
  };
}

/** The permission the browser already holds, with alerts already switched on for this device. */
function alreadyOn(seen: string[]) {
  const browser = alertingBrowser("granted");
  localStorage.setItem(ALERTS_KEY, "on");
  localStorage.setItem(ALERTS_SEEN_KEY, JSON.stringify(seen));
  return browser;
}

const KEY = "altitude:choose-retention:q-retention";

afterEach(() => {
  paceRefusal = null;
  ended.clear();
  keyRead = Promise.resolve();
  Reflect.deleteProperty(navigator, "serviceWorker");
  Reflect.deleteProperty(globalThis, "caches");
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("decision alerts", () => {
  it("asks for permission once, registers the worker and starts from what is already waiting", async () => {
    const browser = alertingBrowser("default", "granted");
    const stored = new Map<string, string>(); // the worker's own record, in Cache Storage
    vi.stubGlobal("caches", { open: async () => ({ put: async (key: string, value: Response) => { stored.set(key, await value.text()); } }) });
    mockFetch([question]);
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alert me about new decisions" }));
    await waitFor(() => expect(browser.register).toHaveBeenCalledWith("/sw.js"));
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([KEY]);
    // The first wake must not announce the backlog either.
    await waitFor(() => expect(stored.get("/announced")).toBe(JSON.stringify([KEY])));
    expect(browser.shown).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Alerts on" })).toHaveAttribute("aria-pressed", "true");

    // This device also subscribes for a push, so a closed phone still learns a decision is waiting.
    await waitFor(() => expect(browser.registration.pushManager.subscribe).toHaveBeenCalled());
    const [options] = browser.registration.pushManager.subscribe.mock.calls[0]!;
    expect(options.userVisibleOnly).toBe(true);
    expect((options.applicationServerKey as Uint8Array).length).toBe(65);
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      method: "POST", body: JSON.stringify({ endpoint: "https://push.example/wake/device-1", every: 0 }),
    }));
    expect(localStorage.getItem(ALERTS_PUSH_KEY)).toBe("on");
    expect(await screen.findByText(/even when Altitude is closed/)).toBeVisible();
    // The subscription waits for the active worker; the registration just returned is still installing.
    expect(browser.installing.pushManager.subscribe).not.toHaveBeenCalled();
  });

  it("replaces a subscription made with a key this machine no longer signs with", async () => {
    const browser = alertingBrowser("default", "granted");
    const stale = {
      endpoint: "https://push.example/wake/old", options: { applicationServerKey: new Uint8Array(65).fill(9) },
      unsubscribe: vi.fn(async () => true),
    };
    browser.registration.subscribed = stale;
    mockFetch([question]);
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alert me about new decisions" }));
    await waitFor(() => expect(stale.unsubscribe).toHaveBeenCalled());
    // Altitude forgets the endpoint it replaces, so no record outlives its subscription.
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: "https://push.example/wake/old", remove: true }),
    }));
    expect(browser.registration.pushManager.subscribe).toHaveBeenCalled();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT, every: 0 }),
    })));
  });

  it("renews a subscription made with an old key each time Altitude opens, without the switch", async () => {
    const browser = alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    const stale = {
      endpoint: "https://push.example/wake/old", options: { applicationServerKey: new Uint8Array(65).fill(9) },
      unsubscribe: vi.fn(async () => true),
    };
    browser.registration.subscribed = stale;
    ended.add(ENDPOINT); // the new subscription is news to altd, and is not replaced again for that
    mockFetch([question]);
    renderApp({ route: "/" });

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT, every: 0 }),
    })));
    expect(stale.unsubscribe).toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: "https://push.example/wake/old", remove: true }),
    }));
    expect(browser.registration.pushManager.subscribe).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Alerts on" })).toBeVisible();
    expect(screen.getByText(/even when Altitude is closed/)).toBeVisible();

    // Back on screen, the renewed subscription is told again and left as it is.
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => url === "/api/alerts/subscription"
      && init?.body === JSON.stringify({ endpoint: ENDPOINT, every: 0 }))).toHaveLength(2));
    expect(browser.registration.pushManager.subscribe).toHaveBeenCalledTimes(1);
  });

  it("renews a subscription its push service ended, which altd no longer holds", async () => {
    const browser = alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    const dead = { ...browser.registration.subscription, endpoint: "https://push.example/wake/dead", unsubscribe: vi.fn(async () => true) };
    browser.registration.subscribed = dead;
    ended.add(dead.endpoint);
    mockFetch([question]);
    renderApp({ route: "/" });

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT, every: 0 }),
    })));
    expect(dead.unsubscribe).toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: dead.endpoint, remove: true }),
    }));
    expect(screen.getByText(/even when Altitude is closed/)).toBeVisible();
  });

  it("says alerts arrive only while open when a renewal cannot subscribe again", async () => {
    const browser = alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    browser.registration.subscribed = {
      endpoint: "https://push.example/wake/old", options: { applicationServerKey: new Uint8Array(65).fill(9) },
      unsubscribe: vi.fn(async () => true),
    };
    browser.registration.pushManager.subscribe.mockRejectedValue(new DOMException("refused", "NotAllowedError"));
    mockFetch([question]);
    renderApp({ route: "/" });

    expect(await screen.findByText(/only while Altitude is open/)).toBeVisible();
    expect(localStorage.getItem(ALERTS_PUSH_KEY)).toBeNull();
  });

  it("keeps the switch off when it is turned off while a renewal is under way", async () => {
    const browser = alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    let answer = () => {};
    keyRead = new Promise((resolve) => { answer = resolve; });
    mockFetch([question]);
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alerts on" }));
    act(() => answer());
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT, remove: true }),
    })));
    // The renewal finished first, and turning alerts off had the last word with altd and the push service.
    const told = vi.mocked(fetch).mock.calls.filter(([url]) => url === "/api/alerts/subscription").map(([, init]) => init?.body);
    expect(told.at(-1)).toBe(JSON.stringify({ endpoint: ENDPOINT, remove: true }));
    expect(browser.registration.subscription.unsubscribe).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Alert me about new decisions" })).toBeVisible();
  });

  it("keeps alerting while Altitude is open, and says so, when this device cannot be woken", async () => {
    const browser = alertingBrowser("default", "granted");
    const setQueue = mockFetch([question], null); // altd has no key: nothing can wake this device
    const { user, queryClient, router } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alert me about new decisions" }));
    expect(await screen.findByRole("button", { name: "Alerts on" })).toBeVisible();
    expect(browser.registration.pushManager.subscribe).not.toHaveBeenCalled();
    expect(localStorage.getItem(ALERTS_PUSH_KEY)).toBeNull();
    expect(screen.getByText(/only while Altitude is open/)).toBeVisible();

    // The open page still alerts for a decision that is not on screen.
    await act(async () => { await router.navigate("/projects/altitude"); });
    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledTimes(1));
  });

  it.each([
    ["403 BadJwtToken", "Give Altitude a real contact address in ALTITUDE_PUSH_CONTACT on the computer it runs on."],
    ["429 TooManyRequests", "Altitude keeps trying until one gets through."],
  ])("names this device's push service refusing alerts (%s), with what to do", async (reason, todo) => {
    const browser = alreadyOn([KEY]);
    browser.registration.subscribed = browser.registration.subscription;
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    mockFetch([question], PUSH_KEY, false, reason);
    renderApp({ route: "/" });

    expect(await screen.findByText(`The push service at push.example refused Altitude's last alert (${reason}), so this `
      + `device gets alerts only while Altitude is open. ${todo}`)).toBeVisible();
    expect(screen.queryByText(/even when Altitude is closed/)).toBeNull();
  });

  it("shows no refusal on a device push does not wake", async () => {
    alreadyOn([KEY]);
    mockFetch([question], PUSH_KEY, false, "403 BadJwtToken");
    renderApp({ route: "/" });

    expect(await screen.findByText(/only while Altitude is open\. On a phone/)).toBeVisible();
    expect(screen.queryByText(/refused/)).toBeNull();
    expect(fetch).not.toHaveBeenCalledWith("/api/alerts/subscription", expect.anything());
  });

  it("alerts once for a new question, with the task name only, and never again for the same one", async () => {
    const browser = alreadyOn([KEY]);
    const setQueue = mockFetch([question]);
    const { queryClient, router } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    setQueue([question, second, stopped]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledTimes(1));
    expect(browser.shown).toHaveBeenCalledWith("altitude needs a decision", expect.objectContaining({
      body: "Run a restore drill",
      tag: "altitude:run-restore-drill:q-drill",
      data: { url: "/projects/altitude/tasks/run-restore-drill?question=q-drill&revision=1" },
    }));
    // The question text never leaves the page, and a stopped task is not a question.
    expect(JSON.stringify(browser.shown.mock.calls)).not.toContain("Which drill?");
    expect(JSON.stringify(browser.shown.mock.calls)).not.toContain("fix-audio");

    // A refetch of the same queue, as reconnection and polling produce, repeats nothing.
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).toHaveBeenCalledTimes(1);

    // The same decision republished — a block that L3 then escalates — is one decision, alerted once.
    setQueue([question, { ...second, revision: 2 }, stopped]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).toHaveBeenCalledTimes(1);

    browser.click("/projects/altitude/tasks/run-restore-drill?question=q-drill&revision=1");
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/altitude/tasks/run-restore-drill"));
  });

  it("waits to alert while the decision's task is still moving, and alerts once it rests", async () => {
    const browser = alreadyOn([KEY]);
    const setQueue = mockFetch([question]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    // L3 is reading the block that published it: Needs you lists it, and nothing alerts or records it.
    setQueue([question, { ...second, alert_held: true }]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).not.toHaveBeenCalled();
    expect(localStorage.getItem(ALERTS_SEEN_KEY)).not.toContain("q-drill");

    // L3's turn ended with it still open: it alerts once.
    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledTimes(1));
    expect(browser.shown).toHaveBeenCalledWith("altitude needs a decision", expect.objectContaining({
      tag: "altitude:run-restore-drill:q-drill",
    }));

    // Held again for a later revision, it was already announced and does not alert a second time.
    setQueue([question, { ...second, revision: 2, alert_held: true }]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    setQueue([question, { ...second, revision: 2 }]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).toHaveBeenCalledTimes(1);
  });

  it("closes the banner of a decision answered since, keeps the ones still waiting, and retries a kept one", async () => {
    const browser = alreadyOn([KEY, "altitude:run-restore-drill:q-drill"]);
    browser.registration.show(KEY, "altitude:run-restore-drill:q-drill", "altitude-decision");
    const setQueue = mockFetch([question, { ...second, alert_held: true }]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-drill"));
    // The page read the queue, so the banner that said only "a decision needs you" has done its work.
    await waitFor(() => expect(browser.registration.banners.map((banner) => banner.tag))
      .toEqual([KEY, "altitude:run-restore-drill:q-drill"]));

    // The drill was settled within the banner's first 30 seconds: WebKit keeps it open once.
    const [, drill] = browser.registration.banners;
    const close = drill!.close;
    drill!.close = vi.fn(() => { drill!.close = close; });
    setQueue([question]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(drill!.close).toBe(close));
    expect(browser.registration.banners.map((banner) => banner.tag)).toEqual([KEY, "altitude:run-restore-drill:q-drill"]);

    // The page tries again, here on its return to the screen, and the answered banner goes; the other stays.
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(browser.registration.banners.map((banner) => banner.tag)).toEqual([KEY]));

    setQueue([]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.registration.banners).toEqual([]));
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([]);
    expect(browser.shown).not.toHaveBeenCalled();
  });

  it("leaves alerting to the service worker on a device push wakes", async () => {
    const browser = alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    const setQueue = mockFetch([question]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    // The worker announces it on this device, with or without the page: one decision, one banner.
    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-drill"));
    expect(browser.shown).not.toHaveBeenCalled();
    localStorage.removeItem(ALERTS_PUSH_KEY);
  });

  it("records a decision the operator is already looking at without alerting, and alerts elsewhere", async () => {
    const browser = alreadyOn([]);
    const setQueue = mockFetch([]);
    const { queryClient } = renderApp({ route: "/" });
    await screen.findByRole("button", { name: "Alerts on" });

    setQueue([question]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));
    expect(browser.shown).not.toHaveBeenCalled();

    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    // Needs you shows both, so neither alerts; the record still grows.
    await waitFor(() => expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toHaveLength(2));
    expect(browser.shown).not.toHaveBeenCalled();
  });

  it("keeps Needs you usable when permission is denied or the browser cannot alert", async () => {
    const browser = alertingBrowser("denied", "denied");
    mockFetch([question]);
    const { unmount, user } = renderApp({ route: "/" });
    expect(await screen.findByRole("article", { name: "Choose backup retention" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Alert me about new decisions" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(/blocked in this browser's settings/)).toHaveClass("sr-only");
    await user.click(screen.getByRole("button", { name: "Alert me about new decisions" }));
    expect(screen.getByText(/blocked in this browser's settings/)).not.toHaveClass("sr-only");
    expect(browser.request).not.toHaveBeenCalled();
    unmount();

    Reflect.deleteProperty(navigator, "serviceWorker");
    renderApp({ route: "/" });
    expect(await screen.findByRole("article", { name: "Choose backup retention" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Alert me about new decisions" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("This browser cannot show alerts.")).toHaveClass("sr-only");
    await user.click(screen.getByRole("button", { name: "Alert me about new decisions" }));
    expect(screen.getByText("This browser cannot show alerts.")).not.toHaveClass("sr-only");
    expect(browser.request).not.toHaveBeenCalled();
  });

  it("stops alerting and gives up its push subscription when the switch goes off", async () => {
    const browser = alreadyOn([]);
    browser.registration.subscribed = browser.registration.subscription;
    const setQueue = mockFetch([]);
    const { queryClient, user } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Alerts on" }));
    expect(localStorage.getItem(ALERTS_KEY)).toBeNull();
    await waitFor(() => expect(browser.registration.subscription.unsubscribe).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: "https://push.example/wake/device-1", remove: true }),
    }));

    setQueue([question]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await screen.findByRole("article", { name: "Choose backup retention" });
    expect(browser.shown).not.toHaveBeenCalled();
  });

  it("gives up the push subscription even while altd cannot be told", async () => {
    const browser = alreadyOn([]);
    browser.registration.subscribed = browser.registration.subscription;
    mockFetch([], PUSH_KEY, true);
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alerts on" }));
    // The push service is told first, so nothing wakes this device again; altd drops the endpoint
    // the next time its push service answers that it is gone.
    await waitFor(() => expect(browser.registration.subscription.unsubscribe).toHaveBeenCalled());
    expect(localStorage.getItem(ALERTS_PUSH_KEY)).toBeNull();
  });
});

describe("this device's alert preferences", () => {
  it("names nothing and stays silent when this device chose so", async () => {
    const browser = alreadyOn([KEY]);
    deviceStore({ reveal: "none", sound: false });
    const setQueue = mockFetch([question]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledTimes(1));
    expect(browser.shown).toHaveBeenCalledWith("A decision needs you", {
      body: undefined, tag: second.alert_key,
      data: { url: "/projects/altitude/tasks/run-restore-drill?question=q-drill&revision=1" }, silent: true,
    });
  });

  it("names nothing when this device's choices cannot be read, and says so in Settings", async () => {
    const browser = alreadyOn([KEY]);
    deviceStore().set("/preferences", "{not json");
    const setQueue = mockFetch([question]);
    const { queryClient, router } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledWith("A decision needs you", expect.objectContaining({ body: undefined })));

    await act(async () => { await router.navigate("/settings"); });
    const notifications = within(await screen.findByRole("region", { name: "Notifications" }));
    expect(await notifications.findByRole("alert")).toHaveTextContent("This browser cannot keep alert choices here, so alerts name nothing.");
    expect(notifications.queryByRole("combobox", { name: "Show" })).toBeNull();
  });

  it("brings Altitude's copy of this device's pace back to the device's own each time it opens", async () => {
    const browser = alreadyOn([KEY]);
    browser.registration.subscribed = browser.registration.subscription;
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    deviceStore({ every: 3600 });
    mockFetch([question]);
    renderApp({ route: "/" });
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT, every: 3600 }),
    })));
  });

  it("names the project alone when this device chose so", async () => {
    const browser = alreadyOn([KEY]);
    deviceStore({ reveal: "project" });
    const setQueue = mockFetch([question]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-retention"));

    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.shown).toHaveBeenCalledWith("altitude needs a decision",
      expect.objectContaining({ body: undefined, silent: false })));
  });

  it("with a pace, holds what arrives before it has passed and then shows it in one banner", async () => {
    // The clock stands still until the pace has passed, however long rendering takes; timers stay real.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 12, 9, 0));
    const browser = alreadyOn([]);
    deviceStore({ every: 900 });
    localStorage.setItem(ALERTS_LAST_KEY, String(Date.now() - 900_000 + 1_000)); // the pace ends in a second
    const setQueue = mockFetch([]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await screen.findByRole("heading", { name: /altitude/i });

    setQueue([question, second]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([]); // held, not recorded

    vi.setSystemTime(Date.now() + 1_000);
    await waitFor(() => expect(browser.shown).toHaveBeenCalledTimes(1));
    expect(browser.shown).toHaveBeenCalledWith("2 decisions need you", {
      body: "altitude · Choose backup retention\naltitude · Run a restore drill",
      tag: question.alert_key, data: { url: "/" }, silent: false,
    });
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([question.alert_key, second.alert_key]);
  });
});

describe("Notifications in Settings", () => {
  const group = async () => within(await screen.findByRole("region", { name: "Notifications" }));

  it("offers the switch alone, then this device's choices once alerts are on, and saves them on the device", async () => {
    alertingBrowser("default", "granted");
    const stored = deviceStore();
    mockFetch([question], null);
    const { user } = renderApp({ route: "/settings" });
    const notifications = await group();
    const alerts = notifications.getByRole("switch", { name: "Decision alerts" });
    expect(alerts).not.toBeChecked();
    expect(notifications.getByText("Altitude can alert you when a new decision arrives.")).toBeVisible();
    expect(notifications.queryByRole("combobox", { name: "Show" })).toBeNull();
    expect(notifications.queryByRole("switch", { name: "Sound" })).toBeNull();

    await user.click(alerts);
    await waitFor(() => expect(alerts).toBeChecked());
    expect(await notifications.findByText(/only while Altitude is open/)).toBeVisible();
    const show = notifications.getByRole("combobox", { name: "Show" });
    const often = notifications.getByRole("combobox", { name: "How often" });
    const sound = notifications.getByRole("switch", { name: "Sound" });
    await waitFor(() => expect(show).toBeEnabled());
    expect(show).toHaveValue("task");
    expect(notifications.getByText("“altitude needs a decision” and the task name")).toBeVisible();
    expect(often).toHaveValue("0");
    expect(notifications.queryByText(/arrive together/)).toBeNull();
    expect(sound).toBeChecked();

    await user.selectOptions(show, "Project only");
    expect(await notifications.findByText("“altitude needs a decision”")).toBeVisible();
    await user.selectOptions(show, "Only that a decision waits");
    expect(await notifications.findByText("“A decision needs you”")).toBeVisible();
    await user.selectOptions(often, "At most once an hour");
    expect(await notifications.findByText("Decisions in between arrive together in the next alert.")).toBeVisible();
    await user.click(sound);
    await waitFor(() => expect(sound).not.toBeChecked());
    expect(JSON.parse(stored.get("/preferences")!)).toEqual({ reveal: "none", every: 3600, sound: false });
    // A device push cannot wake keeps its pace to itself.
    expect(fetch).not.toHaveBeenCalledWith("/api/alerts/pace", expect.anything());

    await user.click(alerts);
    await waitFor(() => expect(alerts).not.toBeChecked());
    expect(notifications.queryByRole("combobox", { name: "Show" })).toBeNull();
  });

  it("tells Altitude how often it may wake this device, and keeps the earlier pace when refused", async () => {
    const browser = alreadyOn([KEY]);
    browser.registration.subscribed = browser.registration.subscription;
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    const stored = deviceStore();
    mockFetch([question]);
    const { user } = renderApp({ route: "/settings" });
    const notifications = await group();
    const often = await notifications.findByRole("combobox", { name: "How often" });
    await waitFor(() => expect(often).toBeEnabled());

    await user.selectOptions(often, "At most every 15 minutes");
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/alerts/pace", expect.objectContaining({
      method: "POST", body: JSON.stringify({ endpoint: ENDPOINT, every: 900 }),
    })));
    await waitFor(() => expect(JSON.parse(stored.get("/preferences")!)).toMatchObject({ every: 900 }));

    paceRefusal = "This device is not subscribed to alerts. Turn alerts off and on to subscribe it again.";
    await user.selectOptions(often, "At most once an hour");
    expect(await notifications.findByRole("alert")).toHaveTextContent(paceRefusal);
    expect(often).toHaveValue("900");
    expect(JSON.parse(stored.get("/preferences")!)).toMatchObject({ every: 900 });
  });

  it("shows only the switch, explaining itself on press, where alerts cannot be on", async () => {
    alertingBrowser("denied", "denied");
    deviceStore();
    mockFetch([]);
    const { user } = renderApp({ route: "/settings" });
    const notifications = await group();
    const alerts = notifications.getByRole("switch", { name: "Decision alerts" });
    expect(alerts).toHaveAttribute("aria-disabled", "true");
    expect(notifications.getByText(/blocked in this browser's settings/)).toHaveClass("sr-only");
    await user.click(alerts);
    expect(notifications.getByText(/blocked in this browser's settings/)).not.toHaveClass("sr-only");
    expect(notifications.queryByRole("combobox")).toBeNull();
  });
});
