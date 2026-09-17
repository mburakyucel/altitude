import { beforeEach, describe, expect, it, vi } from "vitest";
import SOURCE from "../../public/sw.js?raw"; // the worker the app ships, run in a fabricated scope

/**
 * The service worker's side of decision alerts (issue #221): a push carries nothing, so the worker
 * asks Altitude what is waiting. This drives the real `web/public/sw.js` in a fabricated worker scope.
 */
type Shown = { title: string; options: NotificationOptions };
type Waiting = { waitUntil: (work: Promise<unknown>) => void };

function worker(options: { visible?: boolean } = {}) {
  const handlers: Record<string, (event: Waiting) => void> = {};
  const shown: Shown[] = [];
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
      showNotification: async (title: string, given: NotificationOptions) => { shown.push({ title, options: given }); },
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

    await running.push();
    expect(running.shown).toHaveLength(1);

    // The same decision at a later revision is the same decision.
    running.fetched.mockImplementation(queue([{ ...question, revision: 3 }]));
    await running.push();
    expect(running.shown).toHaveLength(1);

    running.fetched.mockImplementation(queue([question, { ...question, id: "q-key", slug: "rotate-the-signing-key", title: "Rotate the signing key" }]));
    await running.push();
    expect(running.shown).toHaveLength(2);
    expect(running.shown[1]!.title).toBe("atlas needs a decision");
    expect(running.shown[1]!.options.body).toBe("Rotate the signing key");
  });

  it("says a decision is waiting, and nothing more, when it cannot reach Altitude", async () => {
    const running = worker();
    running.fetched.mockRejectedValue(new TypeError("Failed to fetch"));
    await running.push();
    expect(running.shown).toEqual([{
      title: "A decision needs you",
      options: { body: "Open Altitude to read it.", tag: "altitude-decision", data: { url: "/" } },
    }]);
  });

  it("stays quiet while an Altitude page is on screen, because the page alerts for itself", async () => {
    const visible = worker({ visible: true });
    visible.fetched.mockImplementation(queue([question]));
    await visible.push();
    expect(visible.shown).toEqual([]);
    expect(visible.fetched).not.toHaveBeenCalled();

    const hidden = worker({ visible: false });
    hidden.fetched.mockImplementation(queue([question]));
    await hidden.push();
    expect(hidden.shown).toHaveLength(1);
  });
});
