import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatView } from "./api";
import { readOf, retired, settleOutgoing, startOutgoing, updateOutgoing } from "./outbox";
import type { Outgoing } from "./outbox";

/*
 * The project outbox (SPEC.md §3.6 Sending): what it keeps, for how long, and which server read may
 * retire an entry. Reads are numbered by a cache listener whether or not a conversation is on screen.
 */

const empty: ChatView = { history: [], active: null, busy: false, queued: [] } as unknown as ChatView;
const entry = (id: string, change: Partial<Outgoing> = {}): Outgoing =>
  ({ id, text: "Sample", reply: "", accepted: false, turnId: null, error: null, done: false, ...change });
const entries = (client: QueryClient) => client.getQueryData<Outgoing[]>(["chat-outbox", "sample"]) ?? [];

/** A server read of the sample conversation, as the chat query's fetch delivers it. */
async function fetchView(client: QueryClient, view: ChatView) {
  await client.fetchQuery({ queryKey: ["chat", "sample"], queryFn: () => structuredClone(view), staleTime: 0 });
  return client.getQueryData<ChatView>(["chat", "sample"]);
}

afterEach(() => vi.useRealTimers());

describe("project outbox", () => {
  it("keeps an entry past the cache's normal collection until the server's record retires it", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    startOutgoing(client, "sample", entry("a"));
    await vi.advanceTimersByTimeAsync(60 * 60_000);
    expect(entries(client).map((row) => row.id)).toEqual(["a"]);
  });

  it("updates only an entry that still exists; a late callback cannot recreate a removed one", () => {
    const client = new QueryClient();
    startOutgoing(client, "sample", entry("a"));
    updateOutgoing(client, "sample", "a", (current) => ({ ...current, reply: "Partial" }));
    expect(entries(client)[0]?.reply).toBe("Partial");
    // Pairing clears every query; a project's removal drops its outbox with its reads.
    client.removeQueries({ queryKey: ["chat-outbox", "sample"] });
    updateOutgoing(client, "sample", "a", (current) => ({ ...current, reply: "Late" }));
    expect(entries(client)).toEqual([]);
  });

  it("keeps entries per project and per send, and a Retry with the same send id replaces its entry", () => {
    const client = new QueryClient();
    startOutgoing(client, "sample", entry("a"));
    startOutgoing(client, "sample", entry("b"));
    startOutgoing(client, "other", entry("c"));
    startOutgoing(client, "sample", entry("a", { text: "Retried" }));
    expect(entries(client).map((row) => [row.id, row.text])).toEqual([["b", "Sample"], ["a", "Retried"]]);
    expect(client.getQueryData<Outgoing[]>(["chat-outbox", "other"])?.map((row) => row.id)).toEqual(["c"]);
  });

  it("retires a finished entry only at a server read started after it finished", async () => {
    const client = new QueryClient();
    startOutgoing(client, "sample", entry("a", { accepted: true }));
    const before = await fetchView(client, empty);
    // A read already in flight when the receipt lands is cancelled, so its older snapshot cannot count.
    let release!: (view: ChatView) => void;
    const inFlight = client.fetchQuery({ queryKey: ["chat", "sample"], queryFn: () => new Promise<ChatView>((resolve) => { release = resolve; }), staleTime: 0 });
    await settleOutgoing(client, "sample", "a", (current) => ({ ...current, queueId: "a" }));
    release(empty);
    await inFlight.catch(() => undefined);
    const finished = entries(client)[0]!;
    expect(retired(finished, client.getQueryData(["chat", "sample"]), readOf(client, client.getQueryData(["chat", "sample"])))).toBe(false);
    // The page's own cache edit (engine selection, a rollback) is not a read.
    client.setQueryData<ChatView>(["chat", "sample"], (cached) => cached && { ...cached, engine: "alpha" });
    const edited = client.getQueryData<ChatView>(["chat", "sample"]);
    expect(readOf(client, edited)).toBe(readOf(client, before));
    expect(retired(finished, edited, readOf(client, edited))).toBe(false);
    // A failed read is not a read either.
    await client.fetchQuery({ queryKey: ["chat", "sample"], queryFn: () => Promise.reject(new Error("Read unavailable")), staleTime: 0 }).catch(() => undefined);
    expect(retired(finished, edited, readOf(client, client.getQueryData(["chat", "sample"])))).toBe(false);
    const after = await fetchView(client, empty);
    expect(retired(finished, after, readOf(client, after))).toBe(true);
  });

  it("retires an unfinished entry when its queued row or its turn's answer is in the snapshot", () => {
    const pending = entry("a1", { accepted: true, turnId: "t1" });
    const view = (change: Partial<ChatView>) => ({ ...empty, ...change }) as ChatView;
    expect(retired(pending, view({ history: [{ role: "user", text: "Sample", turn_id: "t1", request_id: "a1" }] }), 0)).toBe(false);
    expect(retired(pending, view({ queued: [{ id: "a1", text: "Sample" }] }), 0)).toBe(true);
    // A stored answer for the turn the stream named, or for the queue rows that carried this send.
    expect(retired(entry("a1"), view({ history: [
      { role: "user", text: "Sample", turn_id: "t2", queue_ids: ["a1"] },
      { role: "assistant", text: "Done", turn_id: "t2" },
    ] }), 0)).toBe(true);
    expect(retired(pending, view({ history: [
      { role: "user", text: "Sample", turn_id: "t1" },
      { role: "error", text: "Failed", turn_id: "t1" },
    ] }), 0)).toBe(true);
    // The same words from another send are someone else's message.
    expect(retired(entry("a1"), view({ history: [
      { role: "user", text: "Sample", turn_id: "t3", request_id: "b2" },
      { role: "assistant", text: "Done", turn_id: "t3" },
    ] }), 0)).toBe(false);
  });
});
