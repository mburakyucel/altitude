import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { TerminalStatus } from "../data/api";
import { requestCommand } from "../data/terminalCommand";

// The xterm screen needs a real canvas; here it only reports how its terminal ended.
const screenEnd: { current?: (status: TerminalStatus) => void } = {};
const screenThrows: { current?: Error } = {};
vi.mock("./TerminalScreen", () => ({
  default: ({ id, command, onEnd }: { id: string; command?: { text: string } | null; onEnd: (status: TerminalStatus) => void }) => {
    if (screenThrows.current) throw screenThrows.current;
    screenEnd.current = onEnd;
    return <div data-testid="terminal-screen" data-command={command?.text}>{id}</div>;
  },
}));

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const overview = { projects: [{ name: "demo", managed: true, counts: {} }], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } };
const none = (enabled = true): TerminalStatus => ({ state: "none", enabled });
const running = (busy: string | null = null): TerminalStatus => ({ state: "running", id: "t1", enabled: true, folder: "/home/fixture/demo", offset: 0, exit_code: null, reason: null, busy });

function fixture(status: TerminalStatus, answers: { open?: () => Response | Promise<Response>; status?: () => TerminalStatus | Promise<TerminalStatus> } = {}) {
  let current = status;
  const posts: [string, unknown][] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/machine") return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: current.enabled });
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push([url, body]);
      if (url === "/api/terminal/demo/open") {
        if (answers.open) return answers.open();
        current = running();
        return json(current);
      }
      if (url === "/api/terminal-access") {
        current = { ...current, enabled: body.enabled };
        return json({ operator: null, incident_repository: null, altitude_repository: "fixture/altitude", terminal: body.enabled });
      }
      return json({ ok: true });
    }
    if (url === "/api/terminal/demo") return json(await (answers.status?.() ?? current));
    return json({ error: "not in this fixture" }, 404);
  }));
  return posts;
}

describe("Project terminal", () => {
  it("leaves an error inside a loaded terminal screen to the route instead of calling it an update", async () => {
    fixture(running());
    screenThrows.current = new Error("xterm could not start");
    vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderApp({ route: "/projects/demo/terminal" });
      expect((await screen.findAllByText(/xterm could not start/)).length).toBeGreaterThan(0);
      expect(screen.queryByText("Altitude was updated")).toBeNull();
      expect(screen.queryByText("Couldn't load the terminal")).toBeNull();
      expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).not.toContain("/");
    } finally {
      screenThrows.current = undefined;
    }
  });

  it("reopens after it ended even when a status read from before the open answers after it", async () => {
    let answer!: (status: TerminalStatus) => void;
    const posts = fixture(none(), { status: () => new Promise((resolve) => { answer = resolve; }) });
    const { router, queryClient } = renderApp({ route: "/projects/demo" });
    queryClient.setQueryData(["terminal", "demo", null], none());
    await act(() => router.navigate("/projects/demo/terminal"));
    await waitFor(() => expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/open"));
    expect(await screen.findByTestId("terminal-screen")).toHaveTextContent("t1");
    await act(async () => {
      answer(none());
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(queryClient.getQueryData(["terminal", "demo", null])).toMatchObject({ id: "t1" });
    expect(screen.getByTestId("terminal-screen")).toHaveTextContent("t1");
    expect(router.state.location.pathname).toBe("/projects/demo/terminal");
  });

  it("ignores an open answered after its view went away", async () => {
    let answerOpen!: (response: Response) => void;
    let status: TerminalStatus = none();
    let opens = 0;
    fixture(none(), {
      open: () => (opens++ ? json(status) : new Promise((resolve) => { answerOpen = resolve; })),
      status: () => status,
    });
    const { router } = renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Starting the terminal…")).toBeVisible();
    await act(() => router.navigate("/projects/demo"));
    status = { ...running(), id: "t2" };
    await act(() => router.navigate("/projects/demo/terminal"));
    expect(await screen.findByTestId("terminal-screen")).toHaveTextContent("t2");
    await act(async () => {
      answerOpen(json(running()));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByTestId("terminal-screen")).toHaveTextContent("t2");
    expect(router.state.location.pathname).toBe("/projects/demo/terminal");
  });

  it("says the terminal is off and links to Settings", async () => {
    const posts = fixture(none(false));
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Terminal is off")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open Settings" })).toHaveAttribute("href", "/settings");
    expect(within(screen.getByRole("region", { name: "Terminal" })).queryByRole("button", { name: "Close" })).toBeNull();
    expect(posts).toEqual([]);
  });

  it("opens when shown and closes back to the project without a notice", async () => {
    const posts = fixture(none());
    const { user, router } = renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByTestId("terminal-screen")).toHaveTextContent("t1");
    expect(posts).toContainEqual(["/api/terminal/demo/open", {}]);
    expect(screen.queryByText(/clean main/)).toBeNull();
    await user.click(within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
    expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/close");
    expect(screen.queryByText(/terminal closed|Terminal closed/)).toBeNull();
  });

  it("leaves silently after a clean exit and names a failing exit code", async () => {
    for (const [code, notice] of [[0, null], [2, "Terminal closed · exit code 2"]] as const) {
      fixture(running());
      const { router, unmount } = renderApp({ route: "/projects/demo/terminal" });
      await screen.findByTestId("terminal-screen");
      act(() => screenEnd.current!({ ...running(), state: "exited", reason: "exited", exit_code: code }));
      await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
      if (notice) expect(await screen.findByText(notice)).toBeVisible();
      else expect(screen.queryByText(/Terminal closed/)).toBeNull();
      unmount();
    }
  });

  it("says when the terminal ended elsewhere or out of reach", async () => {
    for (const [ended, notice] of [
      [{ ...running(), state: "exited", reason: "closed", exit_code: 129 }, "The terminal was closed elsewhere."],
      [{ ...running(), state: "exited", reason: "project-removed", exit_code: 129 }, "The project is no longer managed, so its terminal closed."],
      [none(), "The terminal closed while the connection was lost."],
    ] as const) {
      const posts = fixture(running());
      const { router, unmount } = renderApp({ route: "/projects/demo/terminal" });
      await screen.findByTestId("terminal-screen");
      act(() => screenEnd.current!(ended));
      await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
      expect(await screen.findByText(notice)).toBeVisible();
      expect(posts.map(([url]) => url)).not.toContain("/api/terminal/demo/open");
      unmount();
    }
  });

  it("leaves when a later status or the open answer says the shell ended", async () => {
    for (const [later, notice] of [
      [none(), "The terminal closed while the connection was lost."],
      [{ ...running(), id: "t2" }, "The terminal was closed elsewhere."],
    ] as const) {
      let answer: TerminalStatus = running();
      const posts = fixture(running(), { status: () => answer });
      const { router, queryClient, unmount } = renderApp({ route: "/projects/demo/terminal" });
      expect(await screen.findByTestId("terminal-screen")).toHaveTextContent("t1");
      answer = later;
      await act(() => queryClient.refetchQueries({ queryKey: ["terminal", "demo", null] }));
      await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
      expect(await screen.findByText(notice)).toBeVisible();
      expect(screen.queryByText("t2")).toBeNull();
      expect(posts.map(([url]) => url)).not.toContain("/api/terminal/demo/open");
      unmount();
    }
    fixture(none(), { open: () => json({ ...running(), state: "exited", reason: "exited", exit_code: 1 }) });
    const { router } = renderApp({ route: "/projects/demo/terminal" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
    expect(await screen.findByText("Terminal closed · exit code 1")).toBeVisible();
  });

  it("asks before closing a running command", async () => {
    const posts = fixture(running("pnpm"));
    const { user } = renderApp({ route: "/projects/demo/terminal" });
    await user.click(await within(await screen.findByRole("region", { name: "Terminal" })).findByRole("button", { name: "Close" }));
    expect(await screen.findByText("pnpm is still running and will be stopped.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Close the terminal?")).toBeNull();
    await user.click(within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", { name: "Close" }));
    const confirm = (await screen.findByText("Close the terminal?")).parentElement!;
    await user.click(within(confirm).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/close"));
  });

  it("explains a terminal that could not start and retries", async () => {
    let attempts = 0;
    const posts = fixture(none(), { open: () => (++attempts === 1 ? json({ error: "The folder is missing." }, 409) : json(running())) });
    const { user } = renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Couldn't open a terminal")).toBeVisible();
    expect(screen.getByText("The folder is missing.")).toBeVisible();
    await user.click(within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("terminal-screen")).toBeVisible();
    expect(posts.filter(([url]) => url.endsWith("/open"))).toHaveLength(2);
  });

  it("hands its own chat command to the screen, taken once", async () => {
    fixture(running());
    requestCommand("demo", "other-task", "echo not-mine");
    requestCommand("demo", undefined, "echo mine");
    const { unmount } = renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByTestId("terminal-screen")).toHaveAttribute("data-command", "echo mine");
    unmount();
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByTestId("terminal-screen")).not.toHaveAttribute("data-command");
  });

  it("drops a chat command when the terminal is off", async () => {
    fixture(none(false));
    requestCommand("demo", undefined, "echo off");
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Terminal is off")).toBeVisible();
    fixture(running());
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByTestId("terminal-screen")).not.toHaveAttribute("data-command");
  });

  it("opens full screen on phone with Back and Close", async () => {
    setViewport(390);
    fixture(running());
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByRole("button", { name: "Close" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Back" })).toBeVisible();
    expect(screen.getByText("demo · project folder")).toBeVisible();
  });
});

describe("Terminal setting", () => {
  it("turns the terminal on for this computer", async () => {
    const posts = fixture(none(false));
    const { user } = renderApp({ route: "/settings" });
    const toggle = await screen.findByRole("switch", { name: /Terminal/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(posts).toContainEqual(["/api/terminal-access", { enabled: true }]);
  });
});
