import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTranscript } from "./api";

const row = (id: string, order: string, version = 1, text = id) => ({ id, order, version,
  source: "fixture", kind: "message", type: "assistant", role: "assistant", text });
const page = (extra: Record<string, unknown> = {}) => ({ project: "atlas", slug: "work", engine: "fixture",
  session_id: "session", attempt: 0, cursor: "epoch:1", lower: "20", next: "", more: false, reset: false,
  has_earlier: true, has_engine_records: true, deleted: [], events: [row("recent", "20")], redaction: "redacted", ...extra });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const focus = () => act(() => { window.dispatchEvent(new Event("focus")); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("mounted transcript reader", () => {
  it("preserves the update watermark across history and merges late inserts, changed rows and deletions", async () => {
    const requests: URL[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const query = new URL(url, "http://local");
      requests.push(query);
      switch (query.searchParams.get("mode")) {
        case "history": return response(page({ cursor: "epoch:5", lower: "", has_earlier: false,
          events: [row("old", "10", 2)] }));
        case "delta": return response(page({ cursor: "epoch:6", lower: "", has_earlier: false,
          events: [row("earliest", "00", 4), row("recent", "20", 5, "finished")], deleted: ["old"] }));
        default: return response(page());
      }
    }));
    const { result } = renderHook(() => useTranscript("atlas", "work", "fixture", "session", false, true, true, 0, false));
    await waitFor(() => expect(result.current.data?.events).toHaveLength(1));
    act(() => result.current.loadOlder());
    await waitFor(() => expect(result.current.data?.events.map(e => e.id)).toEqual(["old", "recent"]));
    focus();
    await waitFor(() => expect(result.current.data?.events.map(e => e.id)).toEqual(["earliest", "recent"]));
    const delta = requests.find(r => r.searchParams.get("mode") === "delta")!;
    expect(delta.searchParams.get("cursor")).toBe("epoch:1");
    expect(delta.searchParams.get("lower")).toBe("");
    expect(result.current.data?.events[1]?.text).toBe("finished");
  });

  it("reconciles an expired epoch across bounded pages before applying writes since its first page", async () => {
    const deltas: string[] = [];
    let release!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = new URL(url, "http://local").searchParams;
      if (q.get("mode") === "initial") return response(page());
      if (q.get("mode") === "reconcile") {
        if (!q.get("after")) return response(page({ cursor: "new:7", next: "20", more: true,
          events: [row("recent", "20", 7, "revalidated")] }));
        return new Promise<Response>(resolve => { release = resolve; });
      }
      deltas.push(q.get("cursor")!);
      if (q.get("cursor") === "epoch:1") return response(page({ reset: true, events: [] }));
      return response(page({ cursor: "new:9", events: [row("recent", "20", 9, "racing update")] }));
    }));
    const { result } = renderHook(() => useTranscript("atlas", "work", "fixture", "session", false, true, true, 0, false));
    await waitFor(() => expect(result.current.data).toBeDefined());
    focus();
    await waitFor(() => expect(release).toBeDefined());
    expect(result.current.reconnecting).toBe(true);
    expect(result.current.data?.events[0]?.text).toBe("recent");
    await act(async () => release(response(page({ cursor: "new:8", events: [row("tail", "30", 8)] }))));
    await waitFor(() => expect(result.current.data?.events[0]?.text).toBe("racing update"));
    expect(deltas).toEqual(["epoch:1", "new:7"]);
    expect(result.current.data?.events.map(e => e.id)).toEqual(["recent", "tail"]);
    expect(result.current.reconnecting).toBe(false);
  });

  it("cancels and discards old-scope reads, retaining no rows when another attempt opens", async () => {
    let signal: AbortSignal | undefined;
    let release!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      return new Promise<Response>(resolve => { release = resolve; });
    }));
    const { result, rerender, unmount } = renderHook(({ attempt }) =>
      useTranscript("atlas", "work", "fixture", "session", false, true, true, attempt), { initialProps: { attempt: 1 } });
    const firstSignal = signal;
    const oldReply = release;
    rerender({ attempt: 2 });
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => oldReply(response(page())));
    expect(result.current.data).toBeUndefined();
    await act(async () => release(response(page({ attempt: 2, events: [row("new-attempt", "50")] }))));
    expect(result.current.data?.events[0]?.id).toBe("new-attempt");
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("keeps read-only history on transport failure but clears it on generation refusal", async () => {
    let status = 200;
    vi.stubGlobal("fetch", vi.fn(async () => response(status === 200 ? page() : { error: "unavailable" }, status)));
    const { result } = renderHook(() => useTranscript("atlas", "work", "fixture", "session", false));
    await waitFor(() => expect(result.current.data).toBeDefined());
    status = 503;
    focus();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data?.events[0]?.id).toBe("recent");
    status = 404;
    focus();
    await waitFor(() => expect(result.current.data).toBeUndefined());
    expect(result.current.isError).toBe(true);
  });

  it("suspends an inactive phone pane and resumes its same cursor without discarding loaded history", async () => {
    let release!: (response: Response) => void;
    let suspendedSignal: AbortSignal | undefined;
    let reads = 0;
    const cursors: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      reads += 1;
      const q = new URL(url, "http://local").searchParams;
      cursors.push(q.get("cursor") ?? "");
      if (reads === 1) return response(page());
      if (reads === 2) {
        suspendedSignal = init.signal as AbortSignal;
        return new Promise<Response>(resolve => { release = resolve; });
      }
      return response(page({ cursor: "epoch:2", events: [row("new", "30", 2)] }));
    }));
    const { result, rerender } = renderHook(({ active }) =>
      useTranscript("atlas", "work", "fixture", "session", false, true, active), { initialProps: { active: true } });
    await waitFor(() => expect(result.current.data).toBeDefined());
    focus();
    rerender({ active: false });
    expect(suspendedSignal?.aborted).toBe(true);
    await act(async () => release(response(page({ events: [row("stale", "40")] }))));
    expect(result.current.data?.events.map(e => e.id)).toEqual(["recent"]);
    focus();
    expect(reads).toBe(2);
    rerender({ active: true });
    await waitFor(() => expect(result.current.data?.events.map(e => e.id)).toEqual(["recent", "new"]));
    expect(cursors).toEqual(["", "epoch:1", "epoch:1"]);
  });

  it("suspends reads in a hidden document and refreshes when visible", async () => {
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const fetch = vi.fn(async () => response(page()));
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useTranscript("atlas", "work", "fixture", "session", false));
    expect(fetch).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(result.current.data).toBeDefined());
    visibility.mockReturnValue("hidden");
    focus();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["expired index", "long absence"])("reopens a following reader at the recent tail after %s", async (cause) => {
    const modes: string[] = [];
    let initial = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = new URL(url, "http://local").searchParams;
      modes.push(q.get("mode")!);
      expect(q.get("attempt")).toBe("0");
      if (q.get("mode") === "initial") {
        initial += 1;
        return response(page(initial === 1 ? {} : { cursor: "new:900", lower: "80", events: [row("latest", "90", 900)] }));
      }
      return response(page({ reset: true, events: [] }));
    }));
    const { result } = renderHook(() => useTranscript("atlas", "work", "fixture", "session", false));
    await waitFor(() => expect(result.current.data?.events[0]?.id).toBe("recent"));
    if (cause === "long absence") vi.spyOn(Date, "now").mockReturnValue(Date.now() + 61_000);
    focus();
    await waitFor(() => expect(result.current.data?.events.map(e => e.id)).toEqual(["latest"]));
    expect(modes).toEqual(cause === "long absence" ? ["initial", "initial"] : ["initial", "delta", "initial"]);
    expect(result.current.data?.has_earlier).toBe(true);
  });

  it("keeps the reading window when Pause arrives during a recent-tail refresh", async () => {
    let initial = 0;
    let releaseTail!: (value: Response) => void;
    let releaseHistory!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const mode = new URL(url, "http://local").searchParams.get("mode");
      if (mode === "initial") {
        if (++initial === 1) return response(page());
        return new Promise<Response>(resolve => { releaseTail = resolve; });
      }
      if (mode === "reconcile") return new Promise<Response>(resolve => { releaseHistory = resolve; });
      return response(page({ cursor: "new:901", events: [] }));
    }));
    const { result, rerender } = renderHook(({ following }) =>
      useTranscript("atlas", "work", "fixture", "session", false, true, true, 0, following),
    { initialProps: { following: true } });
    await waitFor(() => expect(result.current.data?.events[0]?.id).toBe("recent"));
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 61_000);
    focus();
    await waitFor(() => expect(releaseTail).toBeDefined());
    rerender({ following: false });
    await act(async () => releaseTail(response(page({ cursor: "new:900", lower: "80", events: [row("latest", "90", 900)] }))));
    expect(result.current.data?.events.map(e => e.id)).toEqual(["recent"]);
    await waitFor(() => expect(releaseHistory).toBeDefined());
    await act(async () => releaseHistory(response(page({ cursor: "new:901", events: [row("recent", "20", 1), row("latest", "90", 900)] }))));
    await waitFor(() => expect(result.current.reconnecting).toBe(false));
    expect(result.current.data?.events.map(e => e.id)).toEqual(["recent", "latest"]);
  });
});
