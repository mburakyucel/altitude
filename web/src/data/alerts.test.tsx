import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { ALERTS_KEY, ALERTS_SEEN_KEY } from "./alerts";

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
const stopped = { project: "tutor", slug: "fix-audio", title: "Fix the audio", kind: "stopped", asked_by: "l2", question: "upload fails", asked: ago(2), since: ago(2) };

function overview(queue: unknown[]) {
  return {
    projects: [{ name: "altitude", managed: true }, { name: "tutor", managed: true }],
    queue,
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: [],
    roots: ["~/Projects"],
  };
}

/** The fixture queue the next overview read returns; tests grow it like altd would. */
function mockFetch(initial: unknown[]) {
  let queue = initial;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
    if (url.includes("/api/project/")) return jsonResponse({ name: url.split("/").at(-1), tasks: [] });
    if (url.includes("/api/task/")) return jsonResponse({ slug: "x", state: "blocked", messages: [] });
    if (url.includes("/api/chat/")) return jsonResponse({ history: [], queued: [], active: null, busy: false });
    return jsonResponse({ error: "not found" }, 404);
  }));
  return (next: unknown[]) => { queue = next; };
}

class FakeRegistration {
  async showNotification(_title: string, _options?: NotificationOptions) {}
}

/** A browser that can alert: a service worker registration and a permission prompt that sticks. */
function alertingBrowser(permission: NotificationPermission = "default", answer: NotificationPermission = "granted") {
  const shown = vi.spyOn(FakeRegistration.prototype, "showNotification").mockResolvedValue(undefined);
  const registration = new FakeRegistration();
  const register = vi.fn(async () => registration);
  const listeners = new Set<(event: MessageEvent) => void>();
  const worker = {
    register,
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
    shown, register, request: notification.requestPermission,
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

const KEY = "altitude:choose-retention:q-retention:1";

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
    expect(screen.getByText(/only while Altitude is open/)).toBeVisible();
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
      tag: "altitude:run-restore-drill:q-drill:1",
      data: { url: "/projects/altitude/tasks/run-restore-drill?question=q-drill&revision=1" },
    }));
    // The question text never leaves the page, and a stopped task is not a question.
    expect(JSON.stringify(browser.shown.mock.calls)).not.toContain("Which drill?");
    expect(JSON.stringify(browser.shown.mock.calls)).not.toContain("fix-audio");

    // A refetch of the same queue, as reconnection and polling produce, repeats nothing.
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(browser.shown).toHaveBeenCalledTimes(1);

    browser.click("/projects/altitude/tasks/run-restore-drill?question=q-drill&revision=1");
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/altitude/tasks/run-restore-drill"));
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

  it("stops alerting when the switch goes off", async () => {
    const browser = alreadyOn([]);
    const setQueue = mockFetch([]);
    const { queryClient, user } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Alerts on" }));
    expect(localStorage.getItem(ALERTS_KEY)).toBeNull();

    setQueue([question]);
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await screen.findByRole("article", { name: "Choose backup retention" });
    expect(browser.shown).not.toHaveBeenCalled();
  });
});
