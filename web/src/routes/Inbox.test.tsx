import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("Inbox", () => {
  it("shows only blocked task decisions and resumes with a direct note", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes("/api/decide")) return response({ ok: true });
      return response({
        projects: [],
        queue: [{
          project: "altitude",
          slug: "fix-timer",
          kind: "blocked",
          title: "Fix timer",
          question: "Stopped mid-task: Which timeout should I use?",
          options: ["Resume", "Park", "Reject"],
          detail: "The L2 needs a timeout value.",
        }],
        fyis: [],
        wip: { per_project: {}, machine: 0, waiting: [] },
        quota: { known: false },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { user } = renderApp({ route: "/inbox" });

    expect(await screen.findByText("Stopped mid-task: Which timeout should I use?")).toBeVisible();
    expect(screen.queryByText(/proposal/i)).toBeNull();
    await user.type(screen.getByLabelText("Note for Fix timer"), "Use 30 seconds.");
    await user.click(screen.getByRole("button", { name: "Resume" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/decide"))).toBe(true));
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/decide"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      option: 0,
      note: "Use 30 seconds.",
    });
  });
});
