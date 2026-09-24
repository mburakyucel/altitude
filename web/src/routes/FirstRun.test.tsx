import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { folderName } from "../components/FolderBrowser";

function response(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}
const folders = [
  { name: "alpha", managed: false, path: "/home/ada/Projects/alpha" },
  { name: "beta", managed: false, path: "/home/ada/Projects/beta" },
];
const overview = (projects: unknown[]) => ({ projects, queue: [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: ["~/Projects"] });

const listings: Record<string, unknown> = {
  "": { path: "/home/ada", parts: [], readable: true, folders: [
    { name: "locked", path: "/home/ada/locked", project: null, git: false },
    { name: "work", path: "/home/ada/work", project: null, git: false }] },
  "/home/ada/work": { path: "/home/ada/work", parts: ["work"], readable: true, folders: [
    { name: "alpha", path: "/home/ada/work/alpha", project: null, git: true },
    { name: "gamma", path: "/home/ada/work/gamma", project: "gamma", git: true }] },
  "/home/ada/work/alpha": { path: "/home/ada/work/alpha", parts: ["work", "alpha"], readable: true, folders: [] },
  "/home/ada/locked": { path: "/home/ada/locked", parts: ["locked"], readable: false, folders: [] },
};

function mockFetch(options: { addStatus?: number; restored?: boolean; empty?: boolean } = {}) {
  let added = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/folders")) {
      const path = new URL(url, "http://x").searchParams.get("path") ?? "";
      return path in listings ? response(listings[path]) : response({ error: "Browsing stays inside your home folder." }, 403);
    }
    if (options.empty && url.includes("/api/overview")) return response(overview([]));
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

  it("says it is looking and offers a folder elsewhere while the projects folder loads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { user } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Add a folder" }));
    expect(await screen.findByText("Looking for folders…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose a folder elsewhere…" })).toBeInTheDocument();
  });

  it("lists the folders in the projects folder and keeps the browser closed", async () => {
    mockFetch();
    renderApp({ route: "/projects" });
    await screen.findByText("Altitude found 2 folders in ~/Projects");
    expect(screen.getAllByRole("button", { name: "Add project" })).toHaveLength(2);
    expect(screen.getByText("/home/ada/Projects/alpha")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Choose a folder" })).toBeNull();
  });

  it("opens the browser and points at Settings when the projects folder is empty", async () => {
    mockFetch({ empty: true });
    renderApp({ route: "/projects/anything" });
    await screen.findByText("No folders in ~/Projects yet");
    expect(screen.getByRole("link", { name: "change the projects folder in Settings" })).toHaveAttribute("href", "/settings/projects-folder");
    const browser = screen.getByRole("region", { name: "Choose a folder" });
    expect(await within(browser).findByRole("button", { name: /work/ })).toBeInTheDocument();
    expect(within(browser).getByRole("button", { name: "Add “Home”" })).toBeDisabled();
    expect(within(browser).queryByRole("button", { name: "Cancel" })).toBeNull();
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

  it("browses into a folder and adds it through the existing flow", async () => {
    const fetchMock = mockFetch();
    const { user, router } = renderApp({ route: "/projects" });
    await screen.findByText("Altitude found 2 folders in ~/Projects");
    await user.click(screen.getByRole("button", { name: "Choose a folder elsewhere…" }));
    const browser = screen.getByRole("region", { name: "Choose a folder" });
    await user.click(await within(browser).findByRole("button", { name: /work/ }));
    const gamma = await within(browser).findByRole("button", { name: /gamma/ });
    expect(within(gamma).getByText("Project")).toBeInTheDocument();
    await user.click(within(browser).getByRole("button", { name: /alpha/ }));
    await within(browser).findByText(/No folders inside/);
    expect(within(browser).getByRole("navigation", { name: "Folder path" })).toHaveTextContent("Home›work›alpha");
    await user.click(within(browser).getByRole("button", { name: "Add “alpha”" }));
    await screen.findByRole("dialog", { name: "Project setup" });
    expect(router.state.location.pathname).toBe("/projects/alpha");
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/project/add"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ name: "alpha", path: "/home/ada/work/alpha" });
  });

  it("shows an unreadable folder without an add action and goes back by the path", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects" });
    await user.click(await screen.findByRole("button", { name: "Choose a folder elsewhere…" }));
    const browser = screen.getByRole("region", { name: "Choose a folder" });
    await user.click(await within(browser).findByRole("button", { name: /locked/ }));
    await within(browser).findByText(/your account can’t read it/);
    expect(within(browser).getByRole("button", { name: "Add “locked”" })).toBeDisabled();
    await user.click(within(browser).getByRole("button", { name: "Home" }));
    expect(await within(browser).findByRole("button", { name: /work/ })).toBeInTheDocument();
    await user.click(within(browser).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "Choose a folder" })).toBeNull();
  });

  it("adds a typed folder elsewhere", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });
    await user.click(await screen.findByRole("button", { name: "Choose a folder elsewhere…" }));
    await user.click(screen.getByRole("button", { name: "Type a path instead" }));
    await user.type(screen.getByLabelText("A folder on the computer running Altitude"), "/srv/work/alpha");
    await user.click(screen.getByRole("button", { name: "Add" }));
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
