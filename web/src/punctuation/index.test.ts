import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Request } from "./worker";

type Loader = typeof import("./index");
// The suite replaces this module with a fixture (vitest.setup.ts); these tests need the real loader.
const load = async () => (await vi.importActual<Loader>("./index")).loadPunctuator;

/** A worker that answers the load and echoes words, unless told to stay silent or throw. */
class StubWorker {
  static created = 0;
  static throws = false;
  static silent = false;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  terminated = false;
  constructor() {
    if (StubWorker.throws) throw new Error("Workers are blocked");
    StubWorker.created++;
  }
  postMessage({ id, words }: Request) {
    if (StubWorker.silent && words) return;
    queueMicrotask(() => this.onmessage?.({ data: { id, words: words?.map((w) => w.toUpperCase()) } }));
  }
  terminate() { this.terminated = true; }
}

describe("loadPunctuator", () => {
  beforeEach(() => {
    vi.resetModules();
    StubWorker.created = 0;
    StubWorker.throws = StubWorker.silent = false;
    vi.stubGlobal("Worker", StubWorker);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("loads once per page and punctuates through the worker", async () => {
    const loadPunctuator = await load();
    const model = await loadPunctuator();
    expect(await loadPunctuator()).toBe(model);
    expect(await model.punctuate(["so"])).toEqual(["SO"]);
    expect(StubWorker.created).toBe(1);
  });

  it("rejects when the worker cannot start, and the next call tries again", async () => {
    const loadPunctuator = await load();
    StubWorker.throws = true;
    await expect(loadPunctuator()).rejects.toThrow("Workers are blocked");
    StubWorker.throws = false;
    await expect(loadPunctuator()).resolves.toBeTruthy();
  });

  it("drops a worker that does not answer in time, and the next call starts a fresh one", async () => {
    vi.useFakeTimers();
    const loadPunctuator = await load();
    const model = await loadPunctuator();
    StubWorker.silent = true;
    const stuck = expect(model.punctuate(["so"])).rejects.toThrow("did not answer in time");
    await vi.advanceTimersByTimeAsync(10000);
    await stuck;
    StubWorker.silent = false;
    expect(await loadPunctuator()).not.toBe(model);
    expect(StubWorker.created).toBe(2);
  });
});
