import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChangeStream } from "./api";

class FakeEventSource extends EventTarget {
  static readonly CLOSED = 2;
  static all: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    super();
    FakeEventSource.all.push(this);
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  change(projects: string[]) {
    this.dispatchEvent(new MessageEvent("change", { data: JSON.stringify({ projects }) }));
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

function Stream() {
  useChangeStream();
  return null;
}

function mount(client: QueryClient, children: ReactNode = <Stream />) {
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

const keys = [["overview"], ["monitor"], ["project", "atlas"], ["task", "atlas", "rollout"], ["project", "beacon"], ["task", "beacon", "backup"], ["chat", "atlas"]];

function seeded() {
  const client = new QueryClient();
  for (const key of keys) client.setQueryData(key, { cached: true });
  return client;
}

const stale = (client: QueryClient) => keys.filter((key) => client.getQueryState(key)?.isInvalidated).map((key) => key.join("/"));

describe("useChangeStream", () => {
  beforeEach(() => {
    FakeEventSource.all = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("refreshes every task, decision and project view each time the stream opens, never the chat", () => {
    const client = seeded();
    mount(client);
    expect(FakeEventSource.all.map((source) => source.url)).toEqual(["/api/changes"]);
    act(() => FakeEventSource.all[0]!.open());
    expect(stale(client)).toEqual(["overview", "monitor", "project/atlas", "task/atlas/rollout", "project/beacon", "task/beacon/backup"]);
  });

  it("refreshes only the named projects' views with the overview and monitor", () => {
    const client = seeded();
    mount(client);
    act(() => FakeEventSource.all[0]!.change(["beacon"]));
    expect(stale(client)).toEqual(["overview", "monitor", "project/beacon", "task/beacon/backup"]);
  });

  it("reconnects after a refused stream and closes on unmount", () => {
    vi.useFakeTimers();
    const view = mount(seeded());
    const first = FakeEventSource.all[0]!;
    act(() => { first.readyState = 1; first.onerror?.(); vi.advanceTimersByTime(10_000); });
    expect(FakeEventSource.all).toHaveLength(1); // EventSource retries a dropped connection itself
    act(() => { first.close(); first.onerror?.(); vi.advanceTimersByTime(4_999); });
    expect(FakeEventSource.all).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(1); });
    expect(FakeEventSource.all).toHaveLength(2);
    view.unmount();
    expect(FakeEventSource.all[1]!.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("closes the stream in a hidden tab and reopens it, refreshing, when shown", () => {
    let hidden = false;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    const client = seeded();
    mount(client);
    const first = FakeEventSource.all[0]!;
    hidden = true;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(first.readyState).toBe(FakeEventSource.CLOSED);
    hidden = false;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(FakeEventSource.all).toHaveLength(2);
    expect(stale(client)).toEqual([]);
    act(() => FakeEventSource.all[1]!.open());
    expect(stale(client)).toHaveLength(6);
  });
});
