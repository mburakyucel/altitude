import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { folderName } from "./FirstRun";

function response(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}
const folders = [
  { name: "alpha", managed: false, path: "/home/ada/Projects/alpha" },
  { name: "beta", managed: false, path: "/home/ada/Projects/beta" },
];
const overview = (projects: unknown[]) => ({ projects, queue: [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: ["~/Projects"] });

function mockFetch(options: { addStatus?: number; restored?: boolean } = {}) {
  let added = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return response(overview(added ? [{ name: "alpha", managed: true }, folders[1]] : folders));
    if (url.includes("/api/project/add")) {
      if (options.addStatus) return response({ error: "The folder is not accessible" }, options.addStatus);
      added = true;
      return response({ ok: true, restored: options.restored });
    }
    if (url.includes("/api/chat/alpha")) return response({ history: [], active: null, busy: false });
    if (url.includes("/api/project/alpha")) return response({ name: "alpha", tasks: [], l3: {} });
    if (url.includes("/api/setup/alpha")) return response({ project: "alpha", status: "checking", steps: [{ id: "coordinator", label: "Coordinator", status: "pending", detail: "Waiting to start the first conversation" }] });
    return response({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("First run", () => {
  it("names the folder a path points at", () => {
    expect(folderName("/home/ada/work/my-project/")).toBe("my-project");
    expect(folderName("~/work/tool")).toBe("tool");
  });

  it("shows scanning and the path field while folder discovery loads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { user } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Add a folder" }));
    expect(await screen.findByText("Scanning for folders…")).toBeInTheDocument();
    expect(screen.getByLabelText("A folder elsewhere")).toBeInTheDocument();
  });

  it("lists discoverable folders and a manual path", async () => {
    mockFetch();
    renderApp({ route: "/projects" });
    await screen.findByText("Altitude found 2 folders under ~/Projects");
    expect(screen.getAllByRole("button", { name: "Add project" })).toHaveLength(3);
    expect(screen.getByText("/home/ada/Projects/alpha")).toBeInTheDocument();
  });

  it("offers the path field when discovery is empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(overview([]))));
    renderApp({ route: "/projects/anything" });
    await screen.findByText("Altitude found no folders under ~/Projects.");
    expect(screen.getAllByRole("button", { name: "Add project" })).toHaveLength(1);
  });

  it.each([false, true])("opens setup immediately after accepted registration (restored=%s), without waiting for chat", async (restored) => {
    const fetchMock = mockFetch({ restored });
    const { router, user } = renderApp({ route: "/projects" });
    const row = (await screen.findByText("alpha")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Add project" }));
    await screen.findByRole("dialog", { name: "Project setup" });
    expect(router.state.location.pathname).toBe("/projects/alpha");
    expect(router.state.location.search).toBe("?setup=1");
    expect(localStorage.getItem("altitude.project")).toBe("alpha");
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/project/add"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ name: "alpha", path: "/home/ada/Projects/alpha" });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("limit=10"))).toBe(false);
  });

  it("adds a folder elsewhere", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });
    await screen.findByText("Altitude found 2 folders under ~/Projects");
    await user.type(screen.getByLabelText("A folder elsewhere"), "/srv/work/alpha");
    await user.click(screen.getAllByRole("button", { name: "Add project" }).at(-1)!);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/project/add"));
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ name: "alpha", path: "/srv/work/alpha" });
    });
  });

  it("keeps a refused registration actionable and does not open setup", async () => {
    mockFetch({ addStatus: 403 });
    const { user } = renderApp({ route: "/projects" });
    const row = (await screen.findByText("alpha")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Add project" }));
    await screen.findByText("Could not add alpha: The folder is not accessible");
    expect(within(row).getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByRole("dialog", { name: "Project setup" })).toBeNull();
  });
});
