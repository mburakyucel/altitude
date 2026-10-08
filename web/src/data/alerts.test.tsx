import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { ALERTS_KEY, ALERTS_PUSH_KEY, ALERTS_SEEN_KEY } from "./alerts";

/** Decision alerts (issue #221) through the whole app: the shell watches, Needs you switches. */
function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const question = {
  id: "q-retention", revision: 1, anchor_id: "q-message", status: "open", audience: "operator",
  project: "altitude", slug: "choose-retention", title: "Choose backup retention", kind: "asks",
  asked_by: "l2", question: "How long should backups stay?", asked: ago(3), since: ago(3),
};
const second = { ...question, id: "q-drill", slug: "run-restore-drill", title: "Run a restore drill", question: "Which drill?" };
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
function mockFetch(initial: unknown[], key: string | null = PUSH_KEY, unreachable = false,
  refused: { host: string; reason: string }[] = []) {
  let queue = initial;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/alerts/subscription")) {
      if (unreachable) throw new TypeError("Failed to fetch");
      return jsonResponse({ push: true });
    }
    if (url.includes("/api/alerts")) return jsonResponse({ key, refused });
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
    if (url.includes("/api/project/")) return jsonResponse({ name: url.split("/").at(-1), tasks: [] });
    if (url.includes("/api/task/")) return jsonResponse({ slug: "x", state: "blocked", messages: [] });
    if (url.includes("/api/chat/")) return jsonResponse({ history: [], queued: [], active: null, busy: false });
    return jsonResponse({ error: "not found" }, 404);
  }));
  return (next: unknown[]) => { queue = next; };
}

const ENDPOINT = "https://push.example/wake/device-1";

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
  Reflect.deleteProperty(navigator, "serviceWorker");
  vi.restoreAllMocks();
});

describe("decision alerts", () => {
  it("asks for permission once, registers the worker and starts from what is already waiting", async () => {
    const browser = alertingBrowser("default", "granted");
    mockFetch([question]);
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Alert me about new decisions" }));
    await waitFor(() => expect(browser.register).toHaveBeenCalledWith("/sw.js"));
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([KEY]);
    expect(browser.shown).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Alerts on" })).toHaveAttribute("aria-pressed", "true");

    // This device also subscribes for a push, so a closed phone still learns a decision is waiting.
    await waitFor(() => expect(browser.registration.pushManager.subscribe).toHaveBeenCalled());
    const [options] = browser.registration.pushManager.subscribe.mock.calls[0]!;
    expect(options.userVisibleOnly).toBe(true);
    expect((options.applicationServerKey as Uint8Array).length).toBe(65);
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      method: "POST", body: JSON.stringify({ endpoint: "https://push.example/wake/device-1" }),
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
    expect(browser.registration.pushManager.subscribe).toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith("/api/alerts/subscription", expect.objectContaining({
      body: JSON.stringify({ endpoint: ENDPOINT }),
    }));
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

  it("names a push service that refuses alerts, with its reason and what to do", async () => {
    alreadyOn([KEY]);
    localStorage.setItem(ALERTS_PUSH_KEY, "on");
    mockFetch([question], PUSH_KEY, false, [{ host: "push.example", reason: "403 BadJwtToken" }]);
    renderApp({ route: "/" });

    expect(await screen.findByText(
      "The push service at push.example refused Altitude's last alert (403 BadJwtToken), so that device "
      + "alerts only while Altitude is open. Turn alerts off and on there to subscribe it again.",
    )).toBeVisible();
    expect(screen.queryByText(/even when Altitude is closed/)).toBeNull();
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

  it("closes the banner of a decision answered since, and keeps the ones still waiting", async () => {
    const browser = alreadyOn([KEY, "altitude:run-restore-drill:q-drill"]);
    browser.registration.show(KEY, "altitude:run-restore-drill:q-drill", "altitude-decision");
    const setQueue = mockFetch([question, { ...second, alert_held: true }]);
    const { queryClient } = renderApp({ route: "/projects/altitude" });
    await waitFor(() => expect(localStorage.getItem(ALERTS_SEEN_KEY)).toContain("q-drill"));
    expect(browser.registration.banners.map((banner) => banner.tag))
      .toEqual([KEY, "altitude:run-restore-drill:q-drill", "altitude-decision"]);

    // The drill was settled: its banner closes without being touched; the other stays.
    setQueue([question]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.registration.banners.map((banner) => banner.tag))
      .toEqual([KEY, "altitude-decision"]));

    // Nothing waits: the banner that said only "a decision is waiting" goes too.
    setQueue([]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(browser.registration.banners).toEqual([]));
    expect(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY)!)).toEqual([]);
    expect(browser.shown).not.toHaveBeenCalled();
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
    alertingBrowser("denied", "denied");
    mockFetch([question]);
    const { unmount } = renderApp({ route: "/" });
    expect(await screen.findByRole("article", { name: "Choose backup retention" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Alert me about new decisions" })).toBeDisabled();
    expect(screen.getByText(/blocked in this browser's settings/)).toBeVisible();
    unmount();

    Reflect.deleteProperty(navigator, "serviceWorker");
    renderApp({ route: "/" });
    expect(await screen.findByRole("article", { name: "Choose backup retention" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Alert me about new decisions" })).toBeDisabled();
    expect(screen.getByText("This browser cannot show alerts.")).toBeVisible();
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
