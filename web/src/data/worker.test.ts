import { beforeEach, describe, expect, it, vi } from "vitest";
import SOURCE from "../../public/sw.js?raw"; // the worker the app ships, run in a fabricated scope

/**
 * The service worker's side of decision alerts (issue #221): a push carries nothing, so the worker
 * asks Altitude what is waiting. This drives the real `web/public/sw.js` in a fabricated worker scope.
 */
type Shown = { title: string; options: NotificationOptions };
type Waiting = { waitUntil: (work: Promise<unknown>) => void };
type Banner = { title: string; body?: string; tag: string; data?: unknown; close: () => void };

function worker(options: { visible?: boolean; focus?: () => Promise<unknown> } = {}) {
  const handlers: Record<string, (event: Waiting) => void> = {};
  const shown: Shown[] = [];
  const displayed = new Map<string, Banner>(); // what the device shows now, one banner per tag
  const stored = new Map<string, string>();
  const caches = {
    open: async () => ({
      match: async (key: string) => (stored.has(key) ? new Response(stored.get(key)) : undefined),
      put: async (key: string, value: Response) => { stored.set(key, await value.text()); },
    }),
  };
  const self = {
    addEventListener: (type: string, handler: (event: Waiting) => void) => { handlers[type] = handler; },
    registration: {
      showNotification: async (title: string, given: NotificationOptions) => {
        shown.push({ title, options: given });
        const tag = given.tag ?? "";
        displayed.set(tag, { title, body: given.body, tag, data: given.data, close: () => displayed.delete(tag) });
      },
      getNotifications: async (filter: { tag?: string } = {}) =>
        [...displayed.values()].filter((banner) => filter.tag === undefined || banner.tag === filter.tag),
    },
    clients: {
      matchAll: async () => (options.visible === undefined ? [] : [{
        url: "https://altitude.test/", visibilityState: options.visible ? "visible" : "hidden",
        focus: options.focus ?? (async () => undefined),
        postMessage: (message: unknown) => { posted.push(message); },
      }]),
      openWindow: async (url: string) => { opened.push(url); },
    },
    location: { origin: "https://altitude.test" },
  };
  const fetched = vi.fn();
  const posted: unknown[] = [];
  const opened: string[] = [];
  new Function("self", "fetch", "caches", SOURCE)(self, fetched, caches);
  return {
    shown, fetched, posted, opened,
    showing: () => [...displayed.keys()],
    /** The operator taps the banner shown under `tag`. */
    tap: async (tag: string) => {
      let work: Promise<unknown> = Promise.resolve();
      const banner = displayed.get(tag)!;
      handlers.notificationclick!({ notification: banner, waitUntil: (given: Promise<unknown>) => { work = given; } } as unknown as Waiting);
      await work;
    },
    push: async () => {
      let work: Promise<unknown> = Promise.resolve();
      handlers.push!({ waitUntil: (given) => { work = given; } });
      await work;
    },
  };
}

const queue = (rows: unknown[]) => async () =>
  new Response(JSON.stringify({ queue: rows }), { status: 200, headers: { "Content-Type": "application/json" } });

const question = {
  id: "q-drill", revision: 2, project: "atlas", slug: "run-restore-drill", title: "Run a restore drill",
  kind: "asks", question: "Which drill first?",
};

beforeEach(() => {
  if (!("timeout" in AbortSignal)) {
    Object.defineProperty(AbortSignal, "timeout", { configurable: true, value: () => new AbortController().signal });
  }
});

describe("the service worker's push", () => {
  it("names the project and task it reads from Altitude, and repeats none of them", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question, { ...question, kind: "fault", id: undefined }]));
    await running.push();
    expect(running.fetched).toHaveBeenCalledWith("/api/overview", expect.objectContaining({ cache: "no-store" }));
    expect(running.shown).toEqual([{
      title: "atlas needs a decision",
      options: {
        body: "Run a restore drill",
        tag: "atlas:run-restore-drill:q-drill",
        data: { url: "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=2" },
        silent: false,
      },
    }]);
    // The question itself stays in Altitude, and a fault is not a decision.
    expect(JSON.stringify(running.shown)).not.toContain("Which drill first?");

    // A second wake with nothing new still shows a notification, and it says so: the standing banner is not
    // shown again, since iOS would stack a second one for the same decision.
    await running.push();
    expect(running.shown[1]).toEqual({
      title: "No new decision",
      options: { body: "Nothing new since your last alert.", tag: "altitude-nothing-new", data: { url: "/" }, silent: true },
    });

    // The same decision at a later revision is the same decision.
    running.fetched.mockImplementation(queue([{ ...question, revision: 3 }]));
    await running.push();
    expect(running.shown[2]!.title).toBe("No new decision");

    running.fetched.mockImplementation(queue([question, { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" }]));
    await running.push();
    expect(running.shown).toHaveLength(4);
    expect(running.shown[3]).toEqual({
      title: "atlas needs a decision",
      options: {
        body: "Rotate the signing key", tag: "atlas:rotate-the-signing-key:q-key",
        data: { url: "/projects/atlas/tasks/rotate-the-signing-key?question=q-key&revision=2" },
        silent: false,
      },
    });
    // The note that nothing was new has done its work once a decision is named.
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill", "atlas:rotate-the-signing-key:q-key"]);
  });

  it("never shows a bare banner: a held decision or a review leaves a plain note instead", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([{ ...question, alert_held: true },
      { project: "atlas", slug: "ship-it", title: "Ship it", kind: "review", pr: 12, question: "Review PR #12 before merge" }]));
    await running.push();
    expect(running.shown).toEqual([{
      title: "No decision needs you now",
      options: { body: "It was settled before this alert arrived.", tag: "altitude-nothing-new", data: { url: "/" }, silent: true },
    }]);
    expect(running.shown.map((shown) => shown.title)).not.toContain("Altitude");

    // The task rests with the decision open: it alerts, with a sound, once.
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
    expect(running.shown.at(-1)!.options.silent).toBe(false);
  });

  it("closes the banner of a decision answered since when the next alert wakes it", async () => {
    const running = worker();
    const key = { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" };
    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill", "atlas:rotate-the-signing-key:q-key"]);

    // The drill was settled elsewhere and a new decision wakes the device: the drill's banner goes,
    // the one still waiting stays, and the new one is named.
    const logs = { ...question, id: "q-logs", slug: "archive-logs", title: "Archive the old logs" };
    running.fetched.mockImplementation(queue([key, logs]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:rotate-the-signing-key:q-key", "atlas:archive-logs:q-logs"]);

    // A decision that returns after being answered alerts again.
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
  });

  it("says plainly that it cannot name the decision when Altitude is out of reach", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    running.fetched.mockRejectedValue(new TypeError("Failed to fetch"));
    await running.push();
    expect(running.shown.at(-1)).toEqual({
      title: "A decision needs you",
      options: {
        body: "Altitude is out of reach, so this alert can't name it.", tag: "altitude-decision",
        data: { url: "/" }, silent: false,
      },
    });
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill", "altitude-decision"]);

    // Back in reach, the next wake names what waits and the generic banner goes.
    const key = { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" };
    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill", "atlas:rotate-the-signing-key:q-key"]);
  });

  it("still alerts with Altitude on screen, without a sound", async () => {
    const visible = worker({ visible: true });
    visible.fetched.mockImplementation(queue([question]));
    await visible.push();
    expect(visible.shown).toEqual([expect.objectContaining({
      title: "atlas needs a decision", options: expect.objectContaining({ silent: true }) })]);

    const hidden = worker({ visible: false });
    hidden.fetched.mockImplementation(queue([question]));
    await hidden.push();
    expect(hidden.shown[0]!.options.silent).toBe(false);
  });
});

describe("tapping a banner", () => {
  const url = "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=2";

  it("opens the decision in the app already running, without reloading it", async () => {
    const running = worker({ visible: false });
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    await running.tap("atlas:run-restore-drill:q-drill");
    expect(running.posted).toEqual([{ type: "alert-open", url }]);
    expect(running.opened).toEqual([]);
    expect(running.showing()).toEqual([]);
  });

  it("still routes the app when the platform refuses to focus it, and opens the decision instead", async () => {
    const running = worker({ visible: false, focus: async () => { throw new DOMException("not allowed", "InvalidAccessError"); } });
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    await running.tap("atlas:run-restore-drill:q-drill");
    expect(running.posted).toEqual([{ type: "alert-open", url }]);
    expect(running.opened).toEqual([url]);
  });

  it("opens the decision when Altitude is closed", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    await running.tap("atlas:run-restore-drill:q-drill");
    expect(running.opened).toEqual([url]);
  });
});
