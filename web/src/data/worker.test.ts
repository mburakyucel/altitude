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
    /** What `altitude-alerts` already holds, as a page turning alerts on or an earlier worker left it. */
    remember: (tags: string[]) => stored.set("/announced", JSON.stringify(tags)),
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
  kind: "asks", question: "Which drill first?", alert_key: "atlas:run-restore-drill:g-drill",
};
const key = {
  ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key",
  alert_key: "atlas:rotate-the-signing-key:g-key",
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
        tag: "atlas:run-restore-drill:g-drill",
        data: { url: "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=2" },
        silent: false,
      },
    }]);
    // The question itself stays in Altitude, and a fault is not a decision.
    expect(JSON.stringify(running.shown)).not.toContain("Which drill first?");

    // A rewording while it waits keeps Altitude's key: nothing is new, and the wake says what still waits,
    // silently, in that decision's own banner.
    running.fetched.mockImplementation(queue([{ ...question, revision: 3 }]));
    await running.push();
    expect(running.shown[1]).toEqual({
      title: "atlas still needs a decision",
      options: {
        body: "Run a restore drill", tag: "atlas:run-restore-drill:g-drill",
        data: { url: "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=3" }, silent: true,
      },
    });

    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.shown).toHaveLength(3);
    expect(running.shown[2]).toEqual({
      title: "atlas needs a decision",
      options: {
        body: "Rotate the signing key", tag: "atlas:rotate-the-signing-key:g-key",
        data: { url: "/projects/atlas/tasks/rotate-the-signing-key?question=q-key&revision=2" },
        silent: false,
      },
    });
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill", "atlas:rotate-the-signing-key:g-key"]);
  });

  it("names a question asked again after the operator replied, as the reported night needed", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question]));
    await running.push();

    // The operator replies, the decision leaves Needs you with no push to tell this device, and the owner asks
    // again: Altitude gives it a new key, so the wake names it like a first ask and the old banner goes.
    const again = { ...question, revision: 3, alert_key: "atlas:run-restore-drill:g-drill:2026-10-11T00:12:00+00:00" };
    running.fetched.mockImplementation(queue([again]));
    await running.push();
    expect(running.shown.at(-1)).toEqual({
      title: "atlas needs a decision",
      options: {
        body: "Run a restore drill", tag: again.alert_key,
        data: { url: "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=3" }, silent: false,
      },
    });
    expect(running.showing()).toEqual([again.alert_key]);
  });

  it("shows one banner for a question group, linking its first question", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question, { ...question, id: "q-drill-day", revision: 1 }]));
    await running.push();
    expect(running.shown).toEqual([expect.objectContaining({
      title: "atlas needs a decision",
      options: expect.objectContaining({ data: { url: "/projects/atlas/tasks/run-restore-drill?question=q-drill&revision=2" } }),
    })]);
  });

  it("with nothing new, names each decision still on the operator's turn and leaves held ones out", async () => {
    const running = worker();
    const logs = {
      ...question, id: "q-logs", project: "beacon", slug: "archive-logs", title: "Archive the old logs",
      alert_key: "beacon:archive-logs:g-logs",
    };
    running.fetched.mockImplementation(queue([question, key, logs]));
    await running.push();
    await running.push(); // a second wake delivered with the first, after the phone was offline
    expect(running.shown.at(-1)).toEqual({
      title: "Still waiting for you",
      options: {
        body: "atlas · Run a restore drill\natlas · Rotate the signing key\nbeacon · Archive the old logs",
        tag: "altitude-nothing-new", data: { url: "/" }, silent: true,
      },
    });

    // Two handed back to L3 or their owners since: the one still on the operator's turn is named alone.
    running.fetched.mockImplementation(queue([{ ...question, alert_held: true }, key, { ...logs, alert_held: true }]));
    await running.push();
    expect(running.shown.at(-1)).toEqual({
      title: "atlas still needs a decision",
      options: {
        body: "Rotate the signing key", tag: "atlas:rotate-the-signing-key:g-key",
        data: { url: "/projects/atlas/tasks/rotate-the-signing-key?question=q-key&revision=2" }, silent: true,
      },
    });
  });

  it("keeps matching the record an earlier worker kept for a decision nobody has replied to since", async () => {
    const running = worker();
    running.remember([question.alert_key]); // the group key the earlier worker built, which altd still gives it
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.shown.map((shown) => shown.title)).toEqual(["atlas still needs a decision"]);
  });

  it("never shows a bare banner: a settled, held or review-only queue leaves a plain, true note", async () => {
    const running = worker();
    const review = { project: "atlas", slug: "ship-it", title: "Ship it", kind: "review", pr: 12, question: "Review PR #12 before merge" };
    running.fetched.mockImplementation(queue([review]));
    await running.push();
    running.fetched.mockImplementation(queue([{ ...question, alert_held: true }, review]));
    await running.push();
    expect(running.shown).toEqual([{
      title: "No decision needs you now",
      options: { body: "It was settled before this alert arrived.", tag: "altitude-nothing-new", data: { url: "/" }, silent: true },
    }, {
      // Handed back to L3 or its owner since the wake: still open, so not called settled.
      title: "No decision needs you now",
      options: { body: "L3 or the task's owner is handling it first.", tag: "altitude-nothing-new", data: { url: "/" }, silent: true },
    }]);
    expect(running.shown.map((shown) => shown.title)).not.toContain("Altitude");

    // The task rests with the decision open: it alerts, with a sound, once.
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill"]);
    expect(running.shown.at(-1)!.options.silent).toBe(false);
  });

  it("closes the banner of a decision answered since when the next alert wakes it", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill", "atlas:rotate-the-signing-key:g-key"]);

    // The drill was settled elsewhere and a new decision wakes the device: the drill's banner goes,
    // the one still waiting stays, and the new one is named.
    const logs = { ...question, id: "q-logs", slug: "archive-logs", title: "Archive the old logs", alert_key: "atlas:archive-logs:g-logs" };
    running.fetched.mockImplementation(queue([key, logs]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:rotate-the-signing-key:g-key", "atlas:archive-logs:g-logs"]);

    // A decision that returns after being answered alerts again.
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill"]);
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
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill", "altitude-decision"]);

    // Back in reach, the next wake names what waits and the generic banner goes.
    running.fetched.mockImplementation(queue([question, key]));
    await running.push();
    expect(running.showing()).toEqual(["atlas:run-restore-drill:g-drill", "atlas:rotate-the-signing-key:g-key"]);
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
    await running.tap("atlas:run-restore-drill:g-drill");
    expect(running.posted).toEqual([{ type: "alert-open", url }]);
    expect(running.opened).toEqual([]);
    expect(running.showing()).toEqual([]);
  });

  it("still routes the app when the platform refuses to focus it, and opens the decision instead", async () => {
    const running = worker({ visible: false, focus: async () => { throw new DOMException("not allowed", "InvalidAccessError"); } });
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    await running.tap("atlas:run-restore-drill:g-drill");
    expect(running.posted).toEqual([{ type: "alert-open", url }]);
    expect(running.opened).toEqual([url]);
  });

  it("opens the decision when Altitude is closed", async () => {
    const running = worker();
    running.fetched.mockImplementation(queue([question]));
    await running.push();
    await running.tap("atlas:run-restore-drill:g-drill");
    expect(running.opened).toEqual([url]);
  });
});
