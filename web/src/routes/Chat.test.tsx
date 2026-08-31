import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** NDJSON over a ReadableStream, one enqueue per chunk — lines may straddle chunks. */
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

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

const chatView = {
  history: [
    { at: new Date(Date.now() - 5 * 60_000).toISOString(), role: "user", text: "how is it going?" },
    {
      at: new Date(Date.now() - 4 * 60_000).toISOString(),
      role: "assistant",
      text: "two tasks running.",
      trigger: "chat",
      context_percent: 12,
    },
  ],
  busy: false,
  l3: { session_id: "abcdef1234567890", context_percent: 12, turns: 3 },
};

function postsToChat(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls.filter(
    ([u, init]) =>
      String(u).includes("/api/chat") && (init as RequestInit | undefined)?.method === "POST",
  );
}

const route = "/chat/altitude";

describe("Chat", () => {
  it("renders the L3 history", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route });

    await screen.findByText("how is it going?");
    expect(screen.getByText("two tasks running.")).toBeInTheDocument();
    expect(screen.getByText("L3 · chat · 4m · ctx 12%")).toBeInTheDocument();
  });

  it("streams the reply into the transcript across chunk boundaries", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return streamResponse([
          '{"t":"Two tasks are ',
          'run',
          'ning."}\n{"t":" One is blo',
          'cked."}\n',
          // the terminal line arrives with no trailing newline
          '{"done":{"session_id":"s1","context_percent":13,"error":null}}',
        ]);
      }
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    await user.type(screen.getByLabelText("Message L3"), "status?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Two tasks are running. One is blocked.");
    expect(screen.getByText("status?")).toBeInTheDocument();
    expect(postsToChat(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String(postsToChat(fetchMock)[0]?.[1]?.body))).toEqual({
      project: "altitude",
      text: "status?",
    });
  });

  it("toasts 'L3 is busy' on 409 and does not retry", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return jsonResponse({ error: "L3 is busy; try again in a moment" }, 409);
      }
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    await user.type(screen.getByLabelText("Message L3"), "status?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("L3 is busy");
    // one attempt, no automatic retry, and the text is handed back to the composer
    await waitFor(() => {
      expect(postsToChat(fetchMock)).toHaveLength(1);
    });
    expect(screen.getByLabelText("Message L3")).toHaveValue("status?");
    expect(postsToChat(fetchMock)).toHaveLength(1);
  });

  // Matching is by turn identity, so repeating an earlier question keeps the new user bubble and reply.
  it("keeps the new turn when the same words are already in the history", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/chat") && init?.method === "POST") {
        return streamResponse(['{"t":"still two."}\n', '{"done":{"error":null}}']);
      }
      // The refetch after the stream returns the *same* transcript — the L3 write has not landed
      // yet — so the pending turn must survive it.
      if (url.includes("/api/chat")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route });
    await screen.findByText("how is it going?");

    // exactly the text of the first message already in the transcript
    await user.type(screen.getByLabelText("Message L3"), "how is it going?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("still two.");
    await waitFor(() => {
      expect(postsToChat(fetchMock)).toHaveLength(1);
    });
    // the history copy and the new optimistic bubble, both on screen
    expect(screen.getAllByText("how is it going?")).toHaveLength(2);
    expect(screen.getByText("still two.")).toBeInTheDocument();
  });

  it("offers a link to every managed project's chat and marks the current one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) {
          return jsonResponse({
            ...overview,
            projects: [
              { name: "altitude", managed: true },
              { name: "sibling", managed: true },
              { name: "unmanaged", managed: false },
            ],
          });
        }
        if (url.includes("/api/chat")) return jsonResponse(chatView);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route });

    await screen.findByText("how is it going?");
    const strip = await screen.findByRole("navigation", { name: "Projects" });

    expect(within(strip).getByRole("link", { name: "sibling" })).toHaveAttribute(
      "href",
      "/chat/sibling",
    );
    expect(within(strip).getByRole("link", { name: "altitude" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(strip).getByRole("link", { name: "sibling" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(within(strip).queryByRole("link", { name: "unmanaged" })).toBeNull();
  });
});
