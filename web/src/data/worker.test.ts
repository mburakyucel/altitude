import { beforeEach, describe, expect, it, vi } from "vitest";
import SOURCE from "../../public/sw.js?raw"; // the worker the app ships, run in a fabricated scope

/**
 * The service worker's side of decision alerts (issue #221): a push carries nothing, so the worker
 * asks Altitude what is waiting. This drives the real `web/public/sw.js` in a fabricated worker scope.
 */
type Shown = { title: string; options: NotificationOptions };
type Waiting = { waitUntil: (work: Promise<unknown>) => void };
type Banner = { title: string; body?: string; tag: string; data?: unknown; close: () => void };

function worker(options: { visible?: boolean } = {}) {
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
      matchAll: async () => (options.visible === undefined ? [] : [{ visibilityState: options.visible ? "visible" : "hidden" }]),
    },
    location: { origin: "https://altitude.test" },
  };
  const fetched = vi.fn();
  new Function("self", "fetch", "caches", SOURCE)(self, fetched, caches);
  return {
    shown, fetched,
    showing: () => [...displayed.keys()],
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
      },
    }]);
    // The question itself stays in Altitude, and a fault is not a decision.
    expect(JSON.stringify(running.shown)).not.toContain("Which drill first?");

    // A push must show something, so with nothing new the standing banner is shown again in place,
    // without a sound: the phone keeps one banner for the decision and is not alerted twice.
    await running.push();
    expect(running.shown[1]).toEqual({
      title: "atlas needs a decision",
      options: { ...running.shown[0]!.options, silent: true },
    });
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);

    // The same decision at a later revision is the same decision, and the same banner.
    running.fetched.mockImplementation(queue([{ ...question, revision: 3 }]));
    await running.push();
    expect(running.shown[2]!.options).toMatchObject({ tag: "atlas:run-restore-drill:q-drill", silent: true });

    running.fetched.mockImplementation(queue([question, { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" }]));
    await running.push();
    expect(running.shown).toHaveLength(4);
    expect(running.shown[3]).toEqual({
      title: "atlas needs a decision",
      options: {
        body: "Rotate the signing key", tag: "atlas:rotate-the-signing-key:q-key",
        data: { url: "/projects/atlas/tasks/rotate-the-signing-key?question=q-key&revision=2" },
      },
    });
  });

  it("waits to alert for a decision whose task is still moving, then alerts once it rests", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([{ ...question, alert_held: true }]));
    await running.push();
    expect(running.showing()).toEqual([]); // acknowledged silently, and nothing stays on the device

    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
    expect(running.shown.at(-1)!.options.silent).toBeUndefined();
  });

  it("closes the banner of a decision answered since, and invents none in its place", async () => {
    const running = worker();
    const key = { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" };
    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill", "atlas:rotate-the-signing-key:q-key"]);

    // L3 or the owner settled the drill: its banner goes, and the one still waiting stays as it was.
    running.fetched.mockImplementation(queue([key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:rotate-the-signing-key:q-key"]);
    expect(running.shown.at(-1)).toEqual({ title: "atlas needs a decision", options: expect.objectContaining({
      tag: "atlas:rotate-the-signing-key:q-key", silent: true }) });

    // The last one answered too: the device shows nothing, though the push still showed a notification.
    const before = running.shown.length;
    running.fetched.mockImplementation(queue([]));
    await running.push();
    expect(running.showing()).toEqual([]);
    expect(running.shown.slice(before)).toEqual([{ title: "Altitude", options: { tag: "altitude-quiet", silent: true } }]);

    // A decision that returns after being answered alerts again.
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
  });

  it("says a decision is waiting, and nothing more, when it cannot reach Altitude", async () => {
    const running = worker();
    running.fetched.mockRejectedValue(new TypeError("Failed to fetch"));
    await running.push();
    expect(running.shown).toEqual([{
      title: "A decision needs you",
      options: { body: "Open Altitude to read it.", tag: "altitude-decision", data: { url: "/" } },
    }]);

    // Back in reach, the generic banner stands for a decision while one waits and goes when none does.
    running.fetched.mockImplementation(queue([{ ...question, alert_held: true }]));
    await running.push();
    expect(running.showing()).toEqual(["altitude-decision"]);
    running.fetched.mockImplementation(queue([]));
    await running.push();
    expect(running.showing()).toEqual([]);
  });

  it("adds no banner out of reach while one already shows, since the push may clear rather than announce", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    running.fetched.mockRejectedValue(new TypeError("Failed to fetch"));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
    expect(running.shown.at(-1)!.options.silent).toBe(true);
  });

  it("leaves alerting to a page on screen, and still shows a notification for the push", async () => {
    const visible = worker({ visible: true });
    visible.fetched.mockImplementation(queue([question]));
    await visible.push();
    expect(visible.fetched).not.toHaveBeenCalled();
    expect(visible.shown).toEqual([{ title: "Altitude", options: { tag: "altitude-quiet", silent: true } }]);
    expect(visible.showing()).toEqual([]);

    const hidden = worker({ visible: false });
    hidden.fetched.mockImplementation(queue([question]));
    await hidden.push();
    expect(hidden.showing()).toEqual(["atlas:run-restore-drill:q-drill"]);
  });
});
