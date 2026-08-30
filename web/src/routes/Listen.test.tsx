import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const overview = {
  projects: [],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
};

function mockFetch(digest: unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/digest/speak")) return jsonResponse({ ok: true });
    if (url.includes("/api/digest")) return jsonResponse(digest);
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Listen", () => {
  it("renders the digest text and the rendered audio", async () => {
    mockFetch({ text: "Two tasks landed today.", audio: true });
    renderApp({ route: "/listen" });

    expect(await screen.findByText("Two tasks landed today.")).toBeInTheDocument();
    const audio = screen.getByLabelText("Digest audio");
    expect(audio.getAttribute("src")).toMatch(/^\/digest\.wav\?/);
  });

  it("degrades to a note when there is no digest and no audio", async () => {
    mockFetch({ text: null, audio: false });
    renderApp({ route: "/listen" });

    expect(await screen.findByText("no rendered digest yet")).toBeInTheDocument();
    expect(screen.getByText(/No digest yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Digest audio")).toBeNull();
  });

  it("asks Kokoro for a render", async () => {
    const fetchMock = mockFetch({ text: "digest", audio: false });
    const { user } = renderApp({ route: "/listen" });

    await user.click(await screen.findByRole("button", { name: "Render with Kokoro" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/digest/speak"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/digest/speak"));
    expect(call?.[1]?.method).toBe("POST");
  });

  // Regression: the cache-buster used to be re-stamped in the speak mutation's onSuccess. That POST
  // returns the moment Kokoro is spawned — seconds before the wav is written — so the src was busted
  // against the *old* file and then never moved again. It must turn over on every digest refresh.
  it("re-busts the audio src when the digest query refreshes, not when the POST returns", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/digest/speak")) return jsonResponse({ ok: true });
        if (url.includes("/api/digest")) {
          // guarantee the two resolutions land in different milliseconds
          await new Promise((r) => setTimeout(r, 5));
          return jsonResponse({ text: "digest", audio: true });
        }
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    const { queryClient } = renderApp({ route: "/listen" });

    const first = (await screen.findByLabelText("Digest audio")).getAttribute("src");
    expect(first).toMatch(/^\/digest\.wav\?/);

    // the poll that eventually observes the finished render
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["digest"] });
    });

    await waitFor(() => {
      expect(screen.getByLabelText("Digest audio").getAttribute("src")).not.toBe(first);
    });
  });
});
