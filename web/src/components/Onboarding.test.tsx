import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import type { HostVoice } from "../data/api";

function response(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}
const overview = { projects: [{ name: "alpha", managed: false, path: "/home/ada/Projects/alpha" }], queue: [],
  wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: ["~/Projects"] };
const unmet = [
  { key: "github", label: "GitHub CLI signed in", state: "unmet", detail: "Agents push branches and open pull requests through the GitHub CLI.", command: "gh auth login" },
  { key: "engine-a", label: "Engine A signed in", state: "met", detail: null, command: null },
  { key: "engine-b", label: "Engine B not installed", state: "optional", detail: "Optional: Altitude works with any one coding agent.", command: null },
  { key: "git", label: "Git installed", state: "met", detail: null, command: null },
];

type Host = HostVoice;

function mockFetch(options: { refuseRepository?: boolean; host?: Host; backend?: "host" | "browser" } = {}) {
  const voice = { backend: options.backend ?? (options.host?.state === "unavailable" ? "browser" : "host"), selection: "fixture-1",
    host: options.host ?? { state: "absent", download_bytes: 698_435_338 } as Host };
  const machine = { operator: "Ada Fixture" as string | null, incident_repository: null as string | null, altitude_repository: "product-fixture/altitude",
    network: { computer: "Atlas host", host: "127.0.0.1", port: 8890, url: "https://127.0.0.1:8890", scope: "local", listener_scope: "local", state: "running", packaged: true } };
  let checks = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if (url.includes("/api/overview")) return response({ ...overview, operator: machine.operator });
    if (url.includes("/api/machine")) return response(machine);
    if (url.includes("/api/prerequisites")) {
      checks += 1;
      return response({ items: checks > 1 ? unmet.map((item) => ({ ...item, state: item.state === "unmet" ? "met" : item.state })) : unmet });
    }
    if (url.includes("/api/operator-name")) {
      machine.operator = body.name || null;
      return response(machine);
    }
    if (url.includes("/api/incident-reports")) {
      if (options.refuseRepository && body.repository) return response({ error: `The signed-in GitHub CLI cannot see ${body.repository}.` }, 400);
      machine.incident_repository = body.repository;
      return response(machine);
    }
    if (url.endsWith("/api/voice/host")) {
      voice.host = { state: "setting-up", download_bytes: 698_435_338 };
      return response(voice);
    }
    if (url.endsWith("/api/voice")) {
      if (body) { voice.backend = body.backend; voice.selection = `fixture-${body.backend}`; }
      return response(voice);
    }
    return response({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const posted = (fetchMock: ReturnType<typeof mockFetch>, path: string) =>
  fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith(path) && init?.method === "POST").map(([, init]) => JSON.parse(String(init?.body)));

describe("First run onboarding", () => {
  it("leads with the host and leaves preferences optional without saving", async () => {
    const fetchMock = mockFetch();
    const { user, router } = renderApp({ route: "/projects" });
    await screen.findByRole("heading", { name: "Welcome to Altitude" });
    expect(await screen.findByText("Atlas host")).toBeVisible();
    expect(screen.queryByLabelText("Your name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
    await user.click(screen.getByText("Optional settings"));
    for (const title of ["Your name", "Incident reports", "Voice input"]) expect(screen.getByRole("link", { name: title })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "What the agents need" });
    expect(router.state.location.search).toBe("?step=agents");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toEqual([]);
  });

  it("shows real unmet checks, copies fixes and checks again before continuing", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects?step=agents" });
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const check = (await screen.findByText("GitHub CLI signed in")).closest(".onboarding-check") as HTMLElement;
    expect(within(check).getByText("Needs attention")).toBeVisible();
    expect(screen.getByText("Optional")).toBeVisible();
    await user.click(within(check).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("gh auth login");
    expect(screen.getByRole("button", { name: "Choose a project anyway" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.queryByText("gh auth login")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Add a project" });
    expect(screen.getByText(/Setup may start the coordinator’s first introduction/)).toBeVisible();
  });

  it("uses inert position numbers after moving forward or back, never completion checks", async () => {
    const fetchMock = mockFetch();
    const { user, router } = renderApp({ route: "/projects" });
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Choose a project anyway" }));
    await screen.findByRole("heading", { name: "Add a project" });
    const positions = screen.getByRole("list", { name: "Step 3 of 3" });
    expect(positions).toHaveTextContent("1Your Altitude2Coding tools3Project");
    expect(within(positions).queryByRole("button")).toBeNull();
    expect(within(positions).queryByText("✓")).toBeNull();
    await user.click(screen.getByRole("button", { name: "‹ Back" }));
    expect(router.state.location.search).toBe("?step=agents");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toEqual([]);
  });

  it("never claims readiness on a failed prerequisite read, but permits project selection", async () => {
    const fetchMock = mockFetch();
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).includes("/api/prerequisites") ? response({ error: "Tools unavailable" }, 503) : answer(input, init));
    const { user } = renderApp({ route: "/projects?step=agents" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not check coding tools");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Choose a project anyway" }));
    await screen.findByRole("heading", { name: "Add a project" });
  });

  it("keeps Continue when the address cannot be read and never invents an origin", async () => {
    const fetchMock = mockFetch();
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).includes("/api/machine") ? response({ error: "unavailable" }, 503) : answer(input, init));
    const { user } = renderApp({ route: "/projects" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not read service address");
    expect(screen.queryByRole("link", { name: /^https?:/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "What the agents need" });
  });
});

describe("Settings for this machine", () => {
  it("lists the name, prerequisites and incident reports and edits each in place", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/settings" });
    const name = await screen.findByRole("link", { name: /Your name/ });
    await waitFor(() => expect(name).toHaveTextContent("Ada Fixture"));
    expect(screen.getByRole("link", { name: /Incident reports/ })).toHaveTextContent("Kept on this computer");
    expect(screen.getByRole("link", { name: /Prerequisites/ })).toHaveAttribute("href", "/settings/prerequisites");
    await user.click(name);
    const field = await screen.findByLabelText("Your name");
    await user.clear(field);
    await user.type(field, "Ada L.");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(posted(fetchMock, "/api/operator-name")).toEqual([{ name: "Ada L." }]);
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    await user.click(await screen.findByRole("link", { name: /Incident reports/ }));
    await user.click(await screen.findByRole("radio", { name: "Also publish them as GitHub issues" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    await user.click(screen.getByRole("link", { name: "‹ Settings" }));
    expect(await screen.findByRole("link", { name: /Incident reports/ })).toHaveTextContent("Published to product-fixture/altitude");
  });
});
