import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const tasks = { id: "tasks", project: "example", label: "example · Tasks", detail: "Alpha · Deep · High", expected: { model: "deep", effort: "high" }, editable: true };
const routing = { id: "routing", project: "example", label: "example · Routing", detail: "Alpha · tier-model", expected: null, editable: false };

function server({ rows = [tasks, routing] as Record<string, unknown>[], status = 200, read = 200 } = {}) {
  const posts: Record<string, unknown>[] = [];
  const state = { rows, status, read };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json({ projects: [{ name: "example", managed: true }], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    if (url === "/api/project-model-settings") return state.read === 200 ? json({ rows: state.rows }) : json({ error: "Unavailable" }, state.read);
    if (url === "/api/project-model-settings/reset") {
      const body = JSON.parse(String(init?.body)); posts.push(body);
      if (state.status !== 200) return json({ error: "Reset refused" }, state.status);
      state.rows = state.rows.filter((row) => row.id !== body.id);
      return json({ rows: state.rows });
    }
    return json({}, 404);
  }));
  return { posts, state };
}
beforeEach(() => setViewport(1440));
const section = () => screen.findByRole("region", { name: "Project settings" });

describe("Project settings in Settings", () => {
  it("is absent while no project has its own model settings", async () => {
    server({ rows: [] }); renderApp({ route: "/settings" });
    expect(await screen.findByRole("region", { name: "Voice" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "Project settings" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
  it("lists each setting with Reset; routing models link to routing; Reset clears one and keeps focus in place", async () => {
    const { posts } = server(); const { user } = renderApp({ route: "/settings" });
    const region = await section();
    const row = within(region).getByRole("group", { name: "example · Tasks" });
    expect(row).toHaveTextContent("Alpha · Deep · High");
    expect(within(region).getByRole("group", { name: "example · Routing" })).toHaveTextContent("Alpha · tier-model");
    expect(within(region).getByRole("link", { name: "Routing details" })).toHaveAttribute("href", "/settings/projects/example#routing");
    await user.click(within(row).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(within(region).queryByRole("group", { name: "example · Tasks" })).toBeNull());
    expect(posts).toEqual([{ id: "tasks", expected: { model: "deep", effort: "high" } }]);
    expect(screen.getByRole("heading", { name: "Project settings" })).toHaveFocus();
  });
  it("resetting the last setting removes the section and moves focus to the page heading", async () => {
    server({ rows: [tasks] }); const { user } = renderApp({ route: "/settings" });
    await user.click(within(await section()).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Project settings" })).toBeNull());
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toHaveFocus();
  });
  it("a successful Retry moves focus like the first press", async () => {
    const { state } = server({ rows: [tasks], status: 500 }); const { user } = renderApp({ route: "/settings" });
    const row = within(await section()).getByRole("group", { name: "example · Tasks" });
    await user.click(within(row).getByRole("button", { name: "Reset" }));
    await within(row).findByRole("alert");
    state.status = 200;
    await user.click(within(row).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Project settings" })).toBeNull());
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toHaveFocus();
  });
  it("keeps the row after a failed reset, retries it, and offers Reload after a change elsewhere", async () => {
    const { posts, state } = server({ status: 500 }); const { user } = renderApp({ route: "/settings" });
    const row = within(await section()).getByRole("group", { name: "example · Tasks" });
    await user.click(within(row).getByRole("button", { name: "Reset" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("Reset refused");
    state.status = 409;
    await user.click(within(row).getByRole("button", { name: "Retry" }));
    expect(await within(row).findByText(/Changed in another window\./)).toBeVisible();
    expect(posts).toHaveLength(2);
    state.status = 200;
    state.rows = [routing];
    await user.click(within(row).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "example · Tasks" })).toBeNull());
  });
  it("offers no retry after denial", async () => {
    server({ status: 403 }); const { user } = renderApp({ route: "/settings" });
    const row = within(await section()).getByRole("group", { name: "example · Tasks" });
    await user.click(within(row).getByRole("button", { name: "Reset" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("Reset refused");
    expect(within(row).queryByRole("button", { name: "Retry" })).toBeNull();
  });
  it("says when the list cannot load, with Retry", async () => {
    const { state } = server({ read: 503 }); const { user } = renderApp({ route: "/settings" });
    const alert = (await screen.findByText(/Could not load project model settings/)).closest("p")!;
    state.read = 200;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await section()).toBeVisible();
  });
});
