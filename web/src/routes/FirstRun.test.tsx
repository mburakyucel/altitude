import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { folderName } from "./FirstRun";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function overview(projects: unknown[]) {
  return {
    projects,
    queue: [],
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: [],
    roots: ["~/Projects"],
  };
}

const folders = [
  { name: "alpha", managed: false, path: "/home/ada/Projects/alpha" },
  { name: "beta", managed: false, path: "/home/ada/Projects/beta" },
];

/** The server after Start L3: the project is managed and its chat carries the start turn's outcome,
 * which the first poll does not see yet. */
function mockFetch(options: { addStatus?: number; outcome?: "assistant" | "error" | "none" } = {}) {
  let added = false;
  let polls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) {
      return jsonResponse(overview(added ? [{ name: "alpha", managed: true }, folders[1]] : folders));
    }
    if (url.includes("/api/project/add")) {
      if (options.addStatus && options.addStatus !== 200) {
        return jsonResponse({ error: "/home/ada/Projects/alpha is not a directory" }, options.addStatus);
      }
      added = true;
      return jsonResponse({ ok: true });
    }
    if (url.includes("/api/chat/alpha")) {
      polls += 1;
      const outcome = polls === 1 ? "none" : (options.outcome ?? "assistant");
      const history =
        outcome === "assistant"
          ? [{ at: "2026-09-06T10:00:00Z", role: "assistant", text: "Alpha is a small library.", trigger: "start" }]
          : outcome === "error"
            ? [{ at: "2026-09-06T10:00:00Z", role: "error", text: "L3 turn failed: engine hold", trigger: "start" }]
            : [];
      return jsonResponse({ history, active: null, busy: false });
    }
    if (url.includes("/api/project/alpha")) return jsonResponse({ name: "alpha", tasks: [], l3: {} });
    return jsonResponse({ error: `not found: ${init?.method ?? "GET"} ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("First run", () => {
  it("names the folder a path points at", () => {
    expect(folderName("/home/ada/work/my-project/")).toBe("my-project");
    expect(folderName("~/work/tool")).toBe("tool");
  });

  it("says it is scanning while the overview is still on its way", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const { user } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Add a folder" }));
    expect(await screen.findByText("Scanning for folders…")).toBeInTheDocument();
    // No folder rows yet; the path field is there from the start.
    expect(screen.getAllByRole("button", { name: "Start L3" })).toHaveLength(1);
  });

  it("lists the folders with Start L3 and a path field", async () => {
    mockFetch();
    renderApp({ route: "/projects" });
    await screen.findByText("Altitude found 2 folders under ~/Projects");
    expect(screen.getByText("/home/ada/Projects/alpha")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Start L3" })).toHaveLength(3);
    expect(screen.getByLabelText("A folder elsewhere")).toBeInTheDocument();
  });

  it("offers the path field alone when no folder is found", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(overview([]))));
    renderApp({ route: "/projects/anything" });
    await screen.findByText("Altitude found no folders under ~/Projects.");
    expect(screen.getAllByRole("button", { name: "Start L3" })).toHaveLength(1);
  });

  it("starts L3 for a folder and opens the project page on the first reply", async () => {
    const fetchMock = mockFetch();
    const { router, user } = renderApp({ route: "/projects" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    const row = screen.getByText("alpha").closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Start L3" }));

    await screen.findByText("L3 is starting…");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/project/add"));
    expect(JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))).toEqual({
      name: "alpha",
      path: "/home/ada/Projects/alpha",
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/alpha"), { timeout: 4000 });
    expect(localStorage.getItem("altitude.project")).toBe("alpha");
  });

  it("starts L3 for a folder elsewhere from the path field", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    await user.type(screen.getByLabelText("A folder elsewhere"), "/srv/work/alpha");
    await user.click(screen.getAllByRole("button", { name: "Start L3" }).at(-1)!);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/project/add"));
      expect(JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))).toEqual({
        name: "alpha",
        path: "/srv/work/alpha",
      });
    });
  });

  it("reports a failed start in one sentence with Retry", async () => {
    mockFetch({ outcome: "error" });
    const { user } = renderApp({ route: "/projects" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    const row = screen.getByText("alpha").closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Start L3" }));

    await screen.findByText("L3 is starting…");
    await screen.findByText("L3 could not start for alpha: engine hold", undefined, { timeout: 4000 });
    expect(within(row).getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("retries after a failure without reading the old error row as the new outcome", async () => {
    // Walkthrough 2026-09-06: Retry re-read the first attempt's error row from the conversation and
    // failed again at once, so the second attempt was never watched.
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) {
          return jsonResponse(overview(attempts ? [{ name: "alpha", managed: true }, folders[1]] : folders));
        }
        if (url.includes("/api/project/add")) {
          attempts += 1;
          return jsonResponse({ ok: true });
        }
        if (url.includes("/api/chat/alpha")) {
          const log = [{ at: "2026-09-06T10:00:00Z", role: "error", text: "L3 turn failed: engine hold", trigger: "start" }];
          if (attempts > 1) log.push({ at: "2026-09-06T10:01:00Z", role: "assistant", text: "Alpha is small.", trigger: "start" });
          return jsonResponse({ history: log, active: null, busy: false });
        }
        if (url.includes("/api/project/alpha")) return jsonResponse({ name: "alpha", tasks: [], l3: {} });
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    const { router, user } = renderApp({ route: "/projects" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    const row = screen.getByText("alpha").closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Start L3" }));
    await screen.findByText("L3 could not start for alpha: engine hold", undefined, { timeout: 4000 });

    await user.click(within(row).getByRole("button", { name: "Retry" }));
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/alpha"), { timeout: 4000 });
    expect(attempts).toBe(2);
  });

  it("reports a refused add the same way", async () => {
    mockFetch({ addStatus: 400 });
    const { user } = renderApp({ route: "/projects" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    const row = screen.getByText("alpha").closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Start L3" }));

    await screen.findByText(/L3 could not start for alpha: .* is not a directory/);
    expect(within(row).getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});
