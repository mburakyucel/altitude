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
  projects: [
    {
      name: "altitude",
      path: "/home/b/altitude",
      managed: true,
      git: true,
      counts: { running: 2, queued: 1 },
      l3: { session_id: "abcdef123456", context_percent: 41, last_turn: new Date(Date.now() - 3 * 60_000).toISOString() },
    },
    { name: "quiet", path: "/home/b/quiet", managed: true, git: true, counts: {} },
    { name: "sidecar", path: "/home/b/sidecar", managed: false, git: false },
  ],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: true, five_hour: 12.4, seven_day: 40 },
  now: new Date().toISOString(),
};

function mockFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/project/add")) return jsonResponse({ ok: true });
    if (url.includes("/api/project/remove")) return jsonResponse({ ok: true });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Projects", () => {
  it("lists managed projects with their L3 summary and count pills", async () => {
    mockFetch();
    renderApp({ route: "/projects" });

    await screen.findByRole("link", { name: "altitude" });
    expect(screen.getByText("Managed (2)")).toBeInTheDocument();
    expect(screen.getByText("L3 41% · 3m")).toBeInTheDocument();
    expect(screen.getByText("running 2")).toBeInTheDocument();
    expect(screen.getByText("queued 1")).toBeInTheDocument();
    expect(screen.getByText("no tasks")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "altitude" })).toHaveAttribute(
      "href",
      "/projects/altitude",
    );
  });

  it("shows unmanaged projects and starts L3", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });

    await screen.findByText("Not managed (1)");
    expect(screen.getByText("sidecar")).toBeInTheDocument();
    expect(screen.getByText("no git")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start L3" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/project/add"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/project/add"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      name: "sidecar",
      path: "/home/b/sidecar",
    });
  });

  it("posts the add-project form to /api/project/add", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });

    await screen.findByText("Add a project");
    await user.type(screen.getByLabelText("Project name"), "newthing");
    await user.type(screen.getByLabelText("Project path"), "/home/b/newthing");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/project/add"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/project/add"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      name: "newthing",
      path: "/home/b/newthing",
    });
  });

  it("removes a project only after the confirm step", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });

    await screen.findByRole("button", { name: "Remove altitude" });
    await user.click(screen.getByRole("button", { name: "Remove altitude" }));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/project/remove"))).toBe(
      false,
    );

    await user.click(screen.getByRole("button", { name: "Confirm remove" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/project/remove"))).toBe(
        true,
      );
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/project/remove"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ name: "altitude" });
  });
});
