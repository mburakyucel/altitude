import { describe, expect, it, vi } from "vitest";
import { ApiError, ChatViewSchema, OverviewSchema, api, isChatStreaming, streamChat } from "./api";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api()", () => {
  it("resolves parsed JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true })));
    await expect(api("/api/overview")).resolves.toEqual({ ok: true });
  });

  it("throws ApiError carrying the server's error message and status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: "L3 is busy; try again in a moment" }, 409)),
    );
    const err = await api("/api/chat").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("L3 is busy; try again in a moment");
  });

  it("falls back to HTTP <status> when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    const err = await api("/api/overview").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("HTTP 500");
  });
});

describe("schemas", () => {
  it("OverviewSchema tolerates unknown keys, unknown kinds and missing optionals", () => {
    const parsed = OverviewSchema.parse({
      projects: [{ name: "altitude", managed: true, brand_new_field: 1 }],
      queue: [
        {
          project: "altitude",
          slug: "x",
          kind: "some-future-kind",
          options: null,
          extra: "ignored",
        },
      ],
      wip: { per_project: {}, machine: 0, waiting: [] },
      quota: { known: false },
      now: "2026-08-29T12:00:00",
      extra_top_level: true,
    });
    expect(parsed.queue[0]?.kind).toBe("some-future-kind");
    expect(parsed.queue[0]?.options).toBeNull();
  });

  it("ChatViewSchema parses history and passes unknown keys through", () => {
    const parsed = ChatViewSchema.parse({
      history: [{ at: "2026-08-29T12:00:00", role: "user", text: "hi", extra: 1 }],
      busy: false,
      l3: null,
      future_field: "ok",
    });
    expect(parsed.history[0]?.text).toBe("hi");
  });
});

describe("streamChat", () => {
  function streamResponse(chunks: string[]): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  }

  it("reassembles NDJSON lines split across chunks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse([
          '{"t":"Hel',
          'lo"}\n{"t":" world"}\n',
          '{"done":{"turn_id":"t1","session_id":"s1","error":null}}\n',
        ]),
      ),
    );
    const seen: string[] = [];
    const done = await streamChat("altitude", "hi", { onText: (t) => seen.push(t) });
    expect(seen.join("")).toBe("Hello world");
    expect(done.session_id).toBe("s1");
  });

  it("names the turn before the first text and reports it on done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse([
          '{"turn":{"id":"t9","started_at":"2026-09-07T09:14:00+00:00","trigger":"chat"}}\n',
          '{"t":"Hi"}\n{"done":{"turn_id":"t9"}}\n',
        ]),
      ),
    );
    const order: string[] = [];
    const done = await streamChat("altitude", "hi", {
      onAccepted: () => order.push("accepted"),
      onTurn: (turn) => order.push(`turn:${turn.id}`),
      onText: (t) => order.push(`text:${t}`),
    });
    expect(order).toEqual(["accepted", "turn:t9", "text:Hi"]);
    expect(done.turn?.id).toBe("t9");
    expect(done.turn_id).toBe("t9");
  });

  it("throws ApiError on 409 (L3 busy)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: "L3 is busy; try again in a moment" }, 409)),
    );
    const err = await streamChat("altitude", "hi", { onText: () => {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
  });

  it.each([
    { turn: { id: "t1", started_at: "2026-09-09T12:00:00Z", trigger: "chat" } },
    { queued: { id: "q1", at: "2026-09-09T12:00:00Z", text: "hi", trigger: "chat", role: "user", position: 1 } },
    { done: { turn_id: "t1", error: "The assistant failed" } },
  ])("retains the authoritative receipt on interrupted transport: %j", async (receipt) => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }));
    vi.stubGlobal("fetch", vi.fn(async () => response));
    const accepted = vi.fn(() => { controller.error(new TypeError("Connection lost")); });
    const result = streamChat("altitude", "hi", { onText: () => {}, onAccepted: accepted });
    controller.enqueue(new TextEncoder().encode(`${JSON.stringify(receipt)}\n`));
    await expect(result).resolves.toEqual("done" in receipt ? receipt.done : receipt);
    expect(accepted).toHaveBeenCalledOnce();
    expect(isChatStreaming()).toBe(false);
  });

  it.each([null, "", '{"t":"Not a receipt"}\n'])("does not infer acceptance from HTTP 200 with body %j", async (body) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body)));
    const accepted = vi.fn();
    const error = await streamChat("altitude", "hi", { onText: () => {}, onAccepted: accepted }).catch((error: unknown) => error);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(ApiError);
    expect(accepted).not.toHaveBeenCalled();
    expect(isChatStreaming()).toBe(false);
  });
});
