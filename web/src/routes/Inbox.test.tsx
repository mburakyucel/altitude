import { screen, waitFor } from "@testing-library/react";
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
  queue: [
    {
      project: "altitude",
      slug: "fix-timer",
      kind: "proposed",
      class: "M",
      title: "Fix the timer",
      question: "The toast timer drifts; proposal attached.",
      asked: new Date(Date.now() - 5 * 60_000).toISOString(),
      options: ["Approve", "Revise", "Reject"],
    },
  ],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

describe("Inbox", () => {
  it("renders the decision queue and posts a decision with the option index", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/decide")) return jsonResponse({ ok: true, state: "approved" });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route: "/" });
    await screen.findByText("Fix the timer");
    expect(screen.getByText("Decisions (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const decide = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
      expect(decide).toBeDefined();
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
    const init = call?.[1];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      option: 0,
    });
  });

  it("sends the typed note with the decision", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/decide")) return jsonResponse({ ok: true, state: "approved" });
      return jsonResponse({ error: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { user } = renderApp({ route: "/" });
    await screen.findByText("Fix the timer");

    await user.type(screen.getByLabelText("Note for Fix the timer"), "ship it");
    await user.click(screen.getByRole("button", { name: "Revise" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/decide"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      option: 1,
      note: "ship it",
    });
  });

  // A task Altitude is holding (blocked + resume_after) leaves the Decisions queue and joins the
  // waiting list; "(resume)" is what tells you nobody has to dispatch it by hand.
  it("marks a waiting entry Altitude will resume itself", async () => {
    const waiting = {
      ...overview,
      queue: [],
      wip: {
        per_project: { altitude: 1 },
        machine: 1,
        waiting: [
          { project: "altitude", slug: "held-task", why: "resume" },
          { project: "altitude", slug: "next-up", why: "dispatch" },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/api/overview")
          ? jsonResponse(waiting)
          : jsonResponse({ error: "not found" }, 404),
      ),
    );
    renderApp({ route: "/" });

    expect(
      await screen.findByText("Waiting: altitude/held-task (resume), altitude/next-up"),
    ).toBeInTheDocument();
  });

  it("shows the empty state when nothing is queued", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/api/overview")
          ? jsonResponse({ ...overview, queue: [] })
          : jsonResponse({ error: "not found" }, 404),
      ),
    );
    renderApp({ route: "/" });
    await screen.findByText("Nothing needs you.");
    expect(screen.getByText("No FYIs yet.")).toBeInTheDocument();
  });
});
