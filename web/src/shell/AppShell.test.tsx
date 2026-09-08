import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import { ToastProvider } from "../data/Toast";
import { routes } from "../routes";
import { renderApp, setViewport } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const decision = {
  project: "tutor",
  slug: "fix-audio",
  title: "Fix the audio",
  kind: "asks",
  asked_by: "l3",
  question: "Which upload limit should stand?",
  asked: ago(4),
  since: ago(4),
  recommendation: { option: "resume", why: "" },
};

const overview = {
  projects: [
    { name: "altitude", managed: true, counts: { running: 1, fault: 0 } },
    { name: "tutor", managed: true, counts: { running: 0, fault: 0 } },
    { name: "idle", managed: true, counts: { running: 0, fault: 0 } },
    { name: "broken", managed: true, counts: { running: 1, fault: 1 } },
    { name: "spare", managed: false, path: "/home/ada/Projects/spare" },
  ],
  queue: [decision],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: true, seven_day: 52 },
  engines: [
    { engine: "alpha", label: "Alpha", week: 52, known: true, stale: false, at: ago(1) },
    { engine: "beta", label: "Beta", week: 78, known: false, stale: true, at: ago(3 * 60) },
    { engine: "gamma", label: "Gamma", week: null, known: false, stale: false, at: null },
  ],
  roots: ["~/Projects"],
  operator: "Ada",
  now: new Date().toISOString(),
};

function mockFetch(data: unknown = overview) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(data);
    if (url.includes("/api/chat/")) return jsonResponse({ history: [], active: null, busy: false });
    if (url.includes("/api/project/")) return jsonResponse({ name: "altitude", tasks: [] });
    if (url.includes("/api/task/")) return jsonResponse({ slug: "fix-audio", messages: [] });
    if (url.includes("/api/monitor")) return jsonResponse({ quota: { known: false }, sessions: [] });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function dotOf(link: HTMLElement): string | undefined {
  return link.querySelector(".dot")?.getAttribute("data-state") ?? undefined;
}

/** Back uses the browser router's entry index; MemoryRouter has no browser history. */
function renderBrowserApp(route: string) {
  const previous = { url: window.location.href, state: window.history.state };
  window.history.replaceState(null, "", route);
  const router = createBrowserRouter(routes);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ToastProvider><RouterProvider router={router} /></ToastProvider>
    </QueryClientProvider>,
  );
  onTestFinished(() => {
    view.unmount();
    router.dispose();
    client.clear();
    window.history.replaceState(previous.state, "", previous.url);
  });
  return { router, user: userEvent.setup() };
}

describe("Rail", () => {
  it("lists Needs you with its count, one row per managed project with dot and badge, and the rest", async () => {
    mockFetch();
    renderApp({ route: "/" });

    const rail = await screen.findByRole("navigation", { name: "Rail" });
    const needs = within(rail).getByRole("link", { name: /^Needs you/ });
    expect(needs).toHaveAttribute("aria-current", "page");
    expect(await within(needs).findByText("1")).toHaveClass("badge");

    expect(dotOf(within(rail).getByRole("link", { name: "altitude" }))).toBe("running");
    const tutor = within(rail).getByRole("link", { name: /^tutor/ });
    expect(dotOf(tutor)).toBe("waiting");
    expect(within(tutor).getByText("1")).toHaveClass("badge");
    expect(dotOf(within(rail).getByRole("link", { name: "idle" }))).toBe("idle");
    expect(dotOf(within(rail).getByRole("link", { name: "broken" }))).toBe("danger");
    expect(within(rail).queryByRole("link", { name: "spare" })).toBeNull();
    expect(within(rail).getByRole("button", { name: "1 folder not managed" })).toBeInTheDocument();
    expect(within(rail).getByRole("link", { name: "Monitor" })).toBeInTheDocument();
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dark theme" })).toBeInTheDocument();
  });

  it("reads each engine's week from the seam's rows, with no reading and stale spelled out", async () => {
    mockFetch();
    renderApp({ route: "/" });

    const engines = await screen.findByRole("list", { name: "Engines" });
    const rows = within(engines).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Alpha52% of week");
    expect(rows[0]?.querySelector(".meter-fill")).toHaveAttribute("data-danger", "false");
    expect(rows[1]).toHaveTextContent("Beta78% of week · reading 3h old");
    expect(rows[1]?.querySelector(".meter-fill")).toHaveAttribute("data-danger", "true");
    expect(rows[2]).toHaveTextContent("Gammano reading");
  });

  it("hides badges at zero and the unmanaged line with nothing to add", async () => {
    mockFetch({
      ...overview,
      queue: [],
      projects: [{ name: "altitude", managed: true, counts: { running: 0 } }],
      engines: [],
    });
    renderApp({ route: "/" });

    const rail = await screen.findByRole("navigation", { name: "Rail" });
    expect(within(rail).queryByText("1")).toBeNull();
    expect(within(rail).queryByRole("button", { name: /not managed/ })).toBeNull();
    expect(screen.queryByRole("list", { name: "Engines" })).toBeNull();
  });

  it("opens First run from the plus and closes it with the scrim", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/" });

    await user.click(await screen.findByRole("button", { name: "Add a folder" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a folder" });
    expect(within(dialog).getByText("Altitude found 1 folder under ~/Projects")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("selects the project a route names and marks its row", async () => {
    mockFetch();
    renderApp({ route: "/projects/tutor" });

    const rail = await screen.findByRole("navigation", { name: "Rail" });
    await waitFor(() => expect(localStorage.getItem("altitude.project")).toBe("tutor"));
    expect(within(rail).getByRole("link", { name: /^tutor/ })).toHaveAttribute("aria-current", "page");
  });
});

describe("Routes", () => {
  it.each(["/projects/removed/tasks/old", "/projects/removed/tasks/old/report"])(
    "a removed project's stale route %s shows no task actions or history", async (route) => {
      mockFetch();
      renderApp({ route });
      await screen.findByRole("heading", { name: "Project not managed" });
      expect(screen.getByRole("link", { name: "Open projects" })).toHaveAttribute("href", "/projects");
      expect(screen.queryByRole("textbox")).toBeNull();
      expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    },
  );

  it("a stale task route after the last project is removed shows First run and clears selection", async () => {
    mockFetch({ ...overview, queue: [], projects: [{ name: "removed", managed: false, path: "/tmp/removed" }] });
    localStorage.setItem("altitude.project", "removed");
    renderApp({ route: "/projects/removed/tasks/old/report" });
    await screen.findByText("Altitude found 1 folder under ~/Projects");
    await waitFor(() => expect(localStorage.getItem("altitude.project")).toBeNull());
    expect(screen.queryByRole("heading", { name: "Report" })).toBeNull();
  });

  it("sends /projects and /chat/:name to the project page", async () => {
    mockFetch();
    const first = renderApp({ route: "/projects" });
    await waitFor(() => expect(first.router.state.location.pathname).toBe("/projects/altitude"));
    first.unmount();

    const second = renderApp({ route: "/chat/tutor" });
    await waitFor(() => expect(second.router.state.location.pathname).toBe("/projects/tutor"));
  });

  it("sends an unknown path to Needs you", async () => {
    mockFetch();
    const { router } = renderApp({ route: "/nowhere/at/all" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });
});

describe("Phone", () => {
  it("shows Altitude over the global tabs and the tab bar with the Needs you count", async () => {
    mockFetch();
    setViewport(390);
    renderApp({ route: "/" });

    expect(await screen.findByRole("heading", { name: "Altitude", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Rail" })).toBeNull();
    const bar = screen.getByRole("navigation", { name: "Primary" });
    expect(within(bar).getByRole("link", { name: /^Needs you/ })).toHaveAttribute("aria-current", "page");
    expect(await within(bar).findByText("1")).toHaveClass("badge");
    // Chat and Work follow the selected project; nothing selected yet means the first managed one.
    expect(within(bar).getByRole("link", { name: "Chat" })).toHaveAttribute("href", "/projects/altitude");
    expect(within(bar).getByRole("link", { name: "Work" })).toHaveAttribute("href", "/projects/altitude?tab=work");
    expect(within(bar).getByRole("link", { name: "Monitor" })).toHaveAttribute("href", "/monitor");
  });

  it("names the project with a chevron that opens the switcher, and follows the pick", async () => {
    mockFetch();
    setViewport(390);
    const { router, user } = renderApp({ route: "/projects/altitude" });

    const title = await screen.findByRole("button", { name: "altitude" });
    expect(title).toHaveAttribute("aria-haspopup", "dialog");
    await user.click(title);
    const sheet = await screen.findByRole("dialog", { name: "Switch project" });
    const tutor = within(sheet).getByRole("link", { name: /^tutor/ });
    expect(dotOf(tutor)).toBe("waiting");
    expect(within(sheet).getByText("Add a folder")).toBeInTheDocument();
    expect(within(sheet).getByText("spare")).toBeInTheDocument();
    await user.click(tutor);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(router.state.location.pathname).toBe("/projects/tutor");
    expect(localStorage.getItem("altitude.project")).toBe("tutor");
    const bar = screen.getByRole("navigation", { name: "Primary" });
    expect(within(bar).getByRole("link", { name: "Work" })).toHaveAttribute("href", "/projects/tutor?tab=work");
  });

  it("hides the chevron with one managed project and nothing to add", async () => {
    mockFetch({ ...overview, queue: [], projects: [{ name: "altitude", managed: true }] });
    setViewport(390);
    renderApp({ route: "/projects/altitude" });

    expect(await screen.findByRole("heading", { name: "altitude", level: 1 })).toHaveClass("phone-title");
    expect(screen.queryByRole("button", { name: "altitude" })).toBeNull();
  });

  it("pushes a task page over its tab with a back control and keeps the tab bar", async () => {
    mockFetch();
    setViewport(390);
    const { router, user } = renderBrowserApp("/projects/tutor?tab=work");

    await act(() => router.navigate("/projects/tutor/tasks/fix-audio"));
    expect(await screen.findByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "fix-audio", level: 1 })).toBeInTheDocument();
    const bar = screen.getByRole("navigation", { name: "Primary" });
    expect(within(bar).getByRole("link", { name: "Work" })).toHaveAttribute("aria-current", "page");
    // Chat and Work share a path; only the tab the page belongs to is current (walkthrough 2026-09-06:
    // both lit on the phone).
    expect(within(bar).getByRole("link", { name: "Chat" })).not.toHaveAttribute("aria-current");

    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/tutor"));
    expect(router.state.location.search).toBe("?tab=work");
  });

  it.each(["", "/live"])("direct task entry %s falls back to its owning L3 after local switches", async (suffix) => {
    mockFetch();
    setViewport(390);
    localStorage.setItem("altitude.project", "altitude");
    const { router, user } = renderBrowserApp(`/projects/tutor/tasks/fix-audio${suffix}`);
    const tabs = await screen.findByRole("navigation", { name: "Task views" });
    await user.click(within(tabs).getByRole("link", { name: "Live session" }));
    await user.click(within(tabs).getByRole("link", { name: "Conversation" }));
    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/tutor"));
    expect(router.state.location.search).toBe("");
    expect(router.state.historyAction).toBe("REPLACE");
    expect(screen.getByRole("textbox", { name: "Message L3 about tutor" })).toBeInTheDocument();
  });

  it.each(["/live/", "/LIVE"])("direct live route variant %s uses the same app Back fallback", async (suffix) => {
    mockFetch();
    setViewport(390);
    const { router, user } = renderBrowserApp(`/projects/tutor/tasks/fix-audio${suffix}`);
    await screen.findByRole("region", { name: "Live session" });
    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/tutor"));
    expect(router.state.location.search).toBe("");
    expect(router.state.historyAction).toBe("REPLACE");
  });
});
