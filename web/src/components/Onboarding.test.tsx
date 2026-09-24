import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

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

function mockFetch(options: { refuseRepository?: boolean } = {}) {
  const machine = { operator: "Ada Fixture" as string | null, incident_repository: null as string | null, altitude_repository: "product-fixture/altitude" };
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
    return response({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const posted = (fetchMock: ReturnType<typeof mockFetch>, path: string) =>
  fetchMock.mock.calls.filter(([url]) => String(url).includes(path)).map(([, init]) => JSON.parse(String(init?.body)));

describe("First run onboarding", () => {
  it("starts with the name filled in, saves it on Continue and moves to the prerequisites", async () => {
    const fetchMock = mockFetch();
    const { user, router } = renderApp({ route: "/projects" });
    await screen.findByRole("heading", { name: "Welcome to Altitude" });
    const field = await screen.findByLabelText("Your name");
    expect(field).toHaveValue("Ada Fixture");
    expect(screen.queryByRole("button", { name: "‹ Back" })).toBeNull();
    await user.clear(field);
    await user.type(field, "Ada Lovelace");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "What the agents need" });
    expect(router.state.location.search).toBe("?step=agents");
    expect(posted(fetchMock, "/api/operator-name")).toEqual([{ name: "Ada Lovelace" }]);
  });

  it("shows an unmet check with its terminal command, checks again and continues", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects?step=agents" });
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const check = (await screen.findByText("GitHub CLI signed in")).closest(".onboarding-check") as HTMLElement;
    expect(within(check).getByText("gh auth login")).toBeInTheDocument();
    expect(within(check).getByText(/needs attention/)).toBeInTheDocument();
    expect(screen.getByText("Engine B not installed")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(within(check).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("gh auth login");
    expect(await within(check).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue anyway" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.queryByText("gh auth login")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Report Altitude’s own faults?" });
  });

  it("keeps incidents local by default and skipping publishes nothing", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects?step=incidents" });
    expect(await screen.findByRole("radio", { name: "Keep incidents on this computer" })).toBeChecked();
    expect(screen.queryByLabelText("Repository")).toBeNull();
    expect(screen.getByText(/Only the system-level cause and a fictional or redacted/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Add your projects" });
    expect(posted(fetchMock, "/api/incident-reports")).toEqual([]);
  });

  it("fills in Altitude's repository when publishing is turned on and saves a fork", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects?step=incidents" });
    await user.click(await screen.findByRole("radio", { name: "Also publish them as GitHub issues" }));
    const repository = screen.getByLabelText("Repository");
    expect(repository).toHaveValue("product-fixture/altitude");
    expect(screen.getByText(/Altitude’s repository is public/)).toBeInTheDocument();
    await user.clear(repository);
    await user.type(repository, "fork-fixture/altitude");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Add your projects" });
    expect(posted(fetchMock, "/api/incident-reports")).toEqual([{ repository: "fork-fixture/altitude" }]);
  });

  it("keeps a refused repository on the step with the server's reason", async () => {
    mockFetch({ refuseRepository: true });
    const { user } = renderApp({ route: "/projects?step=incidents" });
    await user.click(await screen.findByRole("radio", { name: "Also publish them as GitHub issues" }));
    await user.click(screen.getByRole("button", { name: "Save and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot see product-fixture/altitude");
    expect(screen.getByRole("heading", { name: "Report Altitude’s own faults?" })).toBeInTheDocument();
  });

  it("skips each step and goes back without saving", async () => {
    const fetchMock = mockFetch();
    const { user, router } = renderApp({ route: "/projects" });
    await user.click(await screen.findByRole("button", { name: "Skip" }));
    await user.click(await screen.findByRole("button", { name: "Continue anyway" }));
    await user.click(await screen.findByRole("button", { name: "Skip" }));
    await screen.findByRole("heading", { name: "Add your projects" });
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "‹ Back" }));
    expect(router.state.location.search).toBe("?step=incidents");
    expect(posted(fetchMock, "/api/operator-name")).toEqual([]);
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
