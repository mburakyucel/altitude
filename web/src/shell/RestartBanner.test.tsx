import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function overview(restart: unknown) {
  return {
    projects: [],
    queue: [],
    fyis: [],
    wip: { per_project: { altitude: 1 }, machine: 1, waiting: [] },
    quota: { known: false },
    restart,
    now: new Date().toISOString(),
  };
}

/** Each overview read answers with the next entry; the last one repeats (the new process answering). */
function mockFetch(...restarts: unknown[]) {
  let reads = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/restart")) return jsonResponse({ ok: true, unit: "altitude-restart-1" });
    if (url.includes("/api/monitor")) return jsonResponse({ seats: [], routing: [], sessions: [] });
    if (url.includes("/api/overview")) {
      const restart = restarts[Math.min(reads, restarts.length - 1)];
      reads += 1;
      return jsonResponse(overview(restart));
    }
    return jsonResponse({ queue: [], fyis: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const pending = {
  since: new Date(Date.now() - 2 * 3600_000).toISOString(),
  head: "97e1197",
  files: ["altitude/tasks.py", "bin/alt"],
};

const banner = () => screen.queryByRole("status", { name: "Restart pending" });
const restartButton = () => screen.queryByRole("button", { name: "Restart" });

describe("RestartBanner", () => {
  it("is absent when no restart is pending", async () => {
    mockFetch(null);
    renderApp({ route: "/" });
    await screen.findByText("Nothing needs you.");
    expect(banner()).toBeNull();
  });

  it("says what changed in words, that Altitude restarts at the next quiet moment, and offers Restart while a worker runs", async () => {
    mockFetch({ ...pending, waiting_for: [] });
    renderApp({ route: "/monitor" });
    await screen.findByText(/Merged changes to the backend are waiting to activate\./);
    expect(screen.getByText(/2 files, landed 2h ago/)).toBeInTheDocument();
    expect(screen.queryByText("Altitude restarts at the next quiet moment.")).toBeNull();
    expect(restartButton()).toBeInTheDocument();
  });

  it("names what the restart waits for and hides the button during dispatch or L3 work (SPEC.md §3.13)", async () => {
    mockFetch({ ...pending, waiting_for: ["altitude/fix-thing", "altitude L3"] });
    renderApp({ route: "/monitor" });
    await screen.findByText(/Waiting for altitude\/fix-thing, altitude L3\./);
    expect(screen.getByText(/Altitude restarts at the next quiet moment\./)).toBeInTheDocument();
    expect(restartButton()).toHaveAttribute("aria-disabled", "true");
  });

  it("describes web-only and combined activation truthfully", async () => {
    mockFetch({ ...pending, files: ["web/src/routes/Monitor.tsx"], waiting_for: [] });
    const first = renderApp({ route: "/monitor" });
    await screen.findByText(/Merged changes to the web app are waiting to activate/);
    expect(screen.getByText(/1 file, landed/)).toBeInTheDocument();
    first.unmount();

    mockFetch({ ...pending, files: ["altitude/server.py", "web/src/routes/Monitor.tsx"], waiting_for: [] });
    renderApp({ route: "/monitor" });
    await screen.findByText(/Merged changes to the backend and the web app are waiting to activate/);
  });

  it("says the restart is under way with the button gone once altd requested it", async () => {
    mockFetch({ ...pending, waiting_for: [], requested_at: new Date().toISOString(), unit: "altitude-restart-1" });
    renderApp({ route: "/monitor" });
    await screen.findByText("Altitude is restarting…");
    expect(screen.queryByText(/next quiet moment/)).toBeNull();
    expect(restartButton()).toBeNull();
  });

  it("offers a failed activation's retry only at the narrow quiet point", async () => {
    mockFetch({ ...pending, failed: new Date().toISOString(), waiting_for: ["altitude/new-work"] });
    const first = renderApp({ route: "/monitor" });
    await screen.findByText(/Automatic activation did not complete; L3 has the fault\. Waiting for altitude\/new-work\./);
    expect(screen.queryByText(/next quiet moment/)).toBeNull();
    expect(restartButton()).toBeNull();
    first.unmount();

    mockFetch({ ...pending, failed: new Date().toISOString(), requested_at: new Date().toISOString(), waiting_for: [] });
    renderApp({ route: "/monitor" });
    await screen.findByText(/Automatic activation did not complete; L3 has the fault\./);
    expect(restartButton()).toBeInTheDocument();
  });

  it("posts /api/restart when Restart is pressed, drops the button, and leaves when the new process answers", async () => {
    const fetchMock = mockFetch({ ...pending, waiting_for: [] }, { ...pending, waiting_for: [] }, null);
    const { user, queryClient } = renderApp({ route: "/monitor" });
    await user.click(await screen.findByRole("button", { name: "Restart" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/restart"))).toBe(true);
    });
    // Under way: the button is gone and the text says so, even before altd's poll reports it.
    expect(await screen.findByText("Altitude is restarting…")).toBeInTheDocument();
    expect(restartButton()).toBeNull();
    // The new process answers with nothing pending: the banner leaves.
    await queryClient.invalidateQueries({ queryKey: ["overview"] });
    await screen.findByText("No update pending.");
    expect(screen.queryByRole("status", { name: "Update status" })).toBeNull();
  });

  it("keeps dismissal through polling, navigation and remount while Monitor retains the action", async () => {
    const fetchMock = mockFetch({ ...pending, waiting_for: [] });
    const view = renderApp();
    await view.user.click(await screen.findByRole("button", { name: "Dismiss update notice" }));
    expect(banner()).toBeNull();
    await act(() => view.queryClient.invalidateQueries({ queryKey: ["overview"] }));
    expect(banner()).toBeNull();
    await act(() => view.router.navigate("/monitor"));
    expect(await screen.findByRole("button", { name: "Restart" })).toBeInTheDocument();
    await act(() => view.router.navigate("/"));
    expect(banner()).toBeNull();
    view.unmount();
    renderApp();
    await screen.findByText("Nothing needs you.");
    expect(banner()).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/restart"))).toBe(false);
  });

  it("announces new updates and failures without repeating quiet-point changes or resolved failures", async () => {
    mockFetch({ ...pending, waiting_for: [] });
    const { user, queryClient } = renderApp();
    const close = () => user.click(screen.getByRole("button", { name: "Dismiss update notice" }));
    const publish = (restart: unknown) => act(() => {
      queryClient.setQueryData(["overview"], (previous: Record<string, unknown> | undefined) => ({ ...previous, restart }));
    });
    await screen.findByText("Update ready");
    await close();
    await publish({ ...pending, waiting_for: ["L3"], requested_at: "requested" });
    expect(banner()).toBeNull();
    await publish({ ...pending, waiting_for: [], failed: "failure-1" });
    expect(await screen.findByText("Activation failed")).toBeInTheDocument();
    await close();
    await publish({ ...pending, waiting_for: ["L3"], failed: "failure-1" });
    expect(banner()).toBeNull();
    await publish({ ...pending, waiting_for: [], failed: "failure-2" });
    await waitFor(() => expect(banner()).toBeInTheDocument());
    await close();
    await publish({ ...pending, waiting_for: [] });
    expect(banner()).toBeNull();
    await publish({ ...pending, head: "new-head", waiting_for: [] });
    expect(await screen.findByText("Update ready")).toBeInTheDocument();
  });

  it("sits above the phone header and at the top of the main pane on the desktop (SPEC.md §3.13)", async () => {
    mockFetch({ ...pending, waiting_for: [] });
    setViewport(390);
    const phone = renderApp({ route: "/" });
    const phoneBanner = await screen.findByRole("status", { name: "Restart pending" });
    const header = screen.getByRole("banner");
    expect(phoneBanner.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("main")).not.toContainElement(phoneBanner);
    phone.unmount();

    setViewport(1440);
    renderApp({ route: "/" });
    const desktopBanner = await screen.findByRole("status", { name: "Restart pending" });
    const main = screen.getByRole("main");
    expect(main).toContainElement(desktopBanner);
    expect(main.firstElementChild).toBe(desktopBanner);
  });
});
