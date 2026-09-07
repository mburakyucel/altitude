import { screen, waitFor } from "@testing-library/react";
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
    wip: { per_project: {}, machine: 0, waiting: [] },
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

  it("says what changed in words, that Altitude restarts at the next quiet moment, and offers Restart when nothing runs", async () => {
    mockFetch({ ...pending, waiting_for: [] });
    renderApp({ route: "/" });
    await screen.findByText(/Merged changes to the backend are waiting to activate\./);
    expect(screen.getByText(/2 files, landed 2h ago/)).toBeInTheDocument();
    expect(screen.getByText("Altitude restarts at the next quiet moment.")).toBeInTheDocument();
    expect(restartButton()).toBeInTheDocument();
  });

  it("names what the restart waits for and hides the button while something runs (SPEC.md §3.13)", async () => {
    mockFetch({ ...pending, waiting_for: ["altitude/fix-thing", "altitude L3"] });
    renderApp({ route: "/" });
    await screen.findByText(/Waiting for altitude\/fix-thing, altitude L3\./);
    expect(screen.getByText(/Altitude restarts at the next quiet moment\./)).toBeInTheDocument();
    expect(restartButton()).toBeNull();
  });

  it("describes web-only and combined activation truthfully", async () => {
    mockFetch({ ...pending, files: ["web/src/routes/Monitor.tsx"], waiting_for: [] });
    const first = renderApp({ route: "/" });
    await screen.findByText(/Merged changes to the web app are waiting to activate/);
    expect(screen.getByText(/1 file, landed/)).toBeInTheDocument();
    first.unmount();

    mockFetch({ ...pending, files: ["altitude/server.py", "web/src/routes/Monitor.tsx"], waiting_for: [] });
    renderApp({ route: "/" });
    await screen.findByText(/Merged changes to the backend and the web app are waiting to activate/);
  });

  it("says the restart is under way with the button gone once altd requested it", async () => {
    mockFetch({ ...pending, waiting_for: [], requested_at: new Date().toISOString(), unit: "altitude-restart-1" });
    renderApp({ route: "/" });
    await screen.findByText("Altitude is restarting…");
    expect(screen.queryByText(/next quiet moment/)).toBeNull();
    expect(restartButton()).toBeNull();
  });

  it("offers a failed activation's retry only after the system becomes idle", async () => {
    mockFetch({ ...pending, failed: new Date().toISOString(), waiting_for: ["altitude/new-work"] });
    const first = renderApp({ route: "/" });
    await screen.findByText(/Automatic activation did not complete; L3 has the fault\. Waiting for altitude\/new-work\./);
    expect(screen.queryByText(/next quiet moment/)).toBeNull();
    expect(restartButton()).toBeNull();
    first.unmount();

    mockFetch({ ...pending, failed: new Date().toISOString(), requested_at: new Date().toISOString(), waiting_for: [] });
    renderApp({ route: "/" });
    await screen.findByText(/Automatic activation did not complete; L3 has the fault\./);
    expect(restartButton()).toBeInTheDocument();
  });

  it("posts /api/restart when Restart is pressed, drops the button, and leaves when the new process answers", async () => {
    const fetchMock = mockFetch({ ...pending, waiting_for: [] }, { ...pending, waiting_for: [] }, null);
    const { user, queryClient } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Restart" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/restart"))).toBe(true);
    });
    // Under way: the button is gone and the text says so, even before altd's poll reports it.
    expect(await screen.findByText("Altitude is restarting…")).toBeInTheDocument();
    expect(restartButton()).toBeNull();
    // The new process answers with nothing pending: the banner leaves.
    await queryClient.invalidateQueries({ queryKey: ["overview"] });
    await waitFor(() => expect(banner()).toBeNull());
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
