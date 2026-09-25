import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { TerminalStatus } from "../data/api";

// The xterm screen needs a real canvas; here it only reports how its terminal ended.
const screenEnd: { current?: (status: TerminalStatus) => void } = {};
vi.mock("./TerminalScreen", () => ({
  default: ({ id, onEnd }: { id: string; onEnd: (status: TerminalStatus) => void }) => {
    screenEnd.current = onEnd;
    return <div data-testid="terminal-screen">{id}</div>;
  },
}));

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const overview = { projects: [{ name: "demo", managed: true, counts: {} }], queue: [], engines: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } };
const none = (enabled = true, boot = "boot-1"): TerminalStatus => ({ state: "none", boot, enabled });
const running = (busy: string | null = null): TerminalStatus => ({ state: "running", id: "t1", boot: "boot-1", enabled: true, folder: "/home/fixture/demo", offset: 0, exit_code: null, reason: null, busy });

function fixture(status: TerminalStatus, answers: { open?: () => Response } = {}) {
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
    if (url === "/api/terminal/demo") return json(current);
    return json({ error: "not in this fixture" }, 404);
  }));
  return posts;
}

describe("Project terminal", () => {
  it("says the terminal is off and links to Settings", async () => {
    fixture(none(false));
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Terminal is off")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("button", { name: "Close terminal" })).toBeNull();
  });

  it("opens in the project folder, warns about main and closes back to the project", async () => {
    const posts = fixture(none());
    const { user, router } = renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText("Open a terminal in the project folder")).toBeVisible();
    expect(screen.getByText(/lands PRs from this folder's clean main/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Open terminal" }));
    expect(await screen.findByTestId("terminal-screen")).toHaveTextContent("t1");
    expect(screen.getByText("/home/fixture/demo")).toBeVisible();
    expect(posts).toContainEqual(["/api/terminal/demo/open", {}]);
    await user.click(screen.getByRole("button", { name: "Close terminal" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/demo"));
    expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/close");
  });

  it("returns to Ready when its shell was closed elsewhere", async () => {
    const posts = fixture(running());
    renderApp({ route: "/projects/demo/terminal" });
    await screen.findByTestId("terminal-screen");
    act(() => screenEnd.current!({ ...running(), state: "exited", reason: "closed", exit_code: 129 }));
    expect(await screen.findByText("Open a terminal in the project folder")).toBeVisible();
    expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/forget");
  });

  it("asks before closing a running command", async () => {
    const posts = fixture(running("pnpm"));
    const { user } = renderApp({ route: "/projects/demo/terminal" });
    await user.click(await screen.findByRole("button", { name: "Close terminal" }));
    expect(await screen.findByText("pnpm is still running and will be stopped.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Close the terminal?")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Close terminal" }));
    const confirm = (await screen.findByText("Close the terminal?")).parentElement!;
    await user.click(within(confirm).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/close"));
  });

  it("keeps the last output after the shell exits and opens a new one", async () => {
    const posts = fixture(running());
    const { user } = renderApp({ route: "/projects/demo/terminal" });
    await screen.findByTestId("terminal-screen");
    act(() => screenEnd.current!({ ...running(), state: "exited", reason: "exited", exit_code: 0 }));
    expect(await screen.findByText("Terminal closed · exit code 0")).toBeVisible();
    expect(screen.getByTestId("terminal-screen")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Open a new terminal" }));
    await waitFor(() => expect(posts.map(([url]) => url)).toContain("/api/terminal/demo/open"));
  });

  it("says a restart ended the terminal this page had open", async () => {
    sessionStorage.setItem("altitude.terminal.boot:demo:", "boot-0");
    fixture(none(true, "boot-1"));
    renderApp({ route: "/projects/demo/terminal" });
    expect(await screen.findByText(/Altitude restarted, which ends open terminals/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Open a new terminal" })).toBeVisible();
  });

  it("explains a terminal that could not start and retries", async () => {
    let attempts = 0;
    const posts = fixture(none(), { open: () => (++attempts === 1 ? json({ error: "The folder is missing." }, 409) : json(running())) });
    const { user } = renderApp({ route: "/projects/demo/terminal" });
    await user.click(await screen.findByRole("button", { name: "Open terminal" }));
    expect(await screen.findByText("Couldn't open a terminal")).toBeVisible();
    expect(screen.getByText("The folder is missing.")).toBeVisible();
    await user.click(within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("terminal-screen")).toBeVisible();
    expect(posts.filter(([url]) => url.endsWith("/open"))).toHaveLength(2);
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
