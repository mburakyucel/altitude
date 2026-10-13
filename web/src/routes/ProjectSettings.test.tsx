import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const engines = [{ engine: "alpha", label: "Alpha", week: null, known: false, stale: false, at: null },
  { engine: "beta", label: "Beta", week: null, known: false, stale: false, at: null }];
const overview = { projects: [{ name: "example", managed: true }, { name: "sample", managed: true }], queue: [], engines,
  wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } };
const efforts = [{ value: "native", label: "Native" }, { value: "low", label: "Low" }, { value: "high", label: "High" }];

const options = {
  models: [{ engine: "alpha", model: "swift", label: "Swift" }, { engine: "alpha", model: "deep", label: "Deep" }, { engine: "beta", model: null, label: "Beta default" }],
  efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }], beta: [{ value: "low", label: "Low" }, { value: "ultra", label: "Ultra" }] },
};

/** A fictional server: settings keyed like the registry, validated and echoed as the defaults view. */
function fixture({ refuse = "", l3 = {} as Record<string, unknown>, hold = null as null | { setting: string; until: Promise<void> },
  routing = null as string | null, initial = {} as Record<string, unknown>, remove = (): Response | Promise<Response> => json({ ok: true }),
  overviewDown = { on: false } } = {}) {
  const saved: Record<string, unknown> = { ...initial };
  const posts: Record<string, unknown>[] = [];
  let managed = true;
  const field = (setting: string, fallback: string, choices: unknown) => ({ setting, value: saved[setting] ?? null, default: fallback, choices });
  const view = () => ({ l2_engine: saved.l2_engine ?? null, l3_choice: saved.l3_choice ?? null,
    l2_preference: saved.l2_preference ?? null, l3_unavailable: null, routing, ...options,
    engines: engines.map(({ engine, label }) => ({ value: engine, label, efforts: [], routed: !routing || routing.includes(engine) })),
    roles: (["l3", "l2"] as const).map((role) => ({ role, engines: engines.map(({ engine, label }) => ({
    engine, label,
    model: field(`${role}_${engine}_model`, engine === "alpha" ? "alpha-default" : "CLI default", engine === "alpha" ? ["swift", "deep"] : []),
    effort: field(`${role}_${engine}_effort`, role === "l2" && engine === "beta" ? "High" : "Native", efforts),
  })) })) });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview" && overviewDown.on) throw new TypeError("Failed to fetch");
    if (url === "/api/overview") return json({ ...overview, projects: overview.projects.filter((row) => managed || row.name !== "example") });
    if (url === "/api/project/example") return json({ name: "example", tasks: [], l3 });
    if (url === "/api/defaults/example") return json(view());
    if (url === "/api/project-model-settings") return json({ rows: [] });
    if (url === "/api/defaults" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)); posts.push(body);
      if (body.setting === refuse) return json({ error: "Alpha does not support reasoning effort low" }, 400);
      if ("expected" in body && JSON.stringify(saved[body.setting] ?? null) !== JSON.stringify(body.expected)) return json({ error: "Changed in another window.", changed: true }, 409);
      saved[body.setting] = body.value;
      const response = view();
      if (hold && body.setting === hold.setting) await hold.until;
      return json(response);
    }
    if (url === "/api/project/remove") {
      posts.push(JSON.parse(String(init?.body)));
      const response = await remove();
      if (response.ok) managed = false;
      return response;
    }
    return json({}, 404);
  }));
  return { posts, saved, removeNow: () => { managed = false; } };
}


describe("Project settings", () => {
  it("has no model editors: the coordinator's model button lives under the chat", async () => {
    fixture(); renderApp({ route: "/settings/projects/example" });
    const section = await screen.findByRole("region", { name: "L3" });
    expect(section).toHaveTextContent("Its model and effort are under the chat's message box.");
    expect(screen.queryByRole("combobox", { name: "Coordinator engine" })).toBeNull();
    expect(screen.queryByRole("link", { name: /Models and effort/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Model" })).toBeNull();
  });

  it("routes tasks Auto, Prefer or Only an engine, sending the value each page showed, and has no L3 routing", async () => {
    const { posts } = fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const tasks = await screen.findByRole("combobox", { name: "Tasks routing" });
    expect(screen.getByRole("region", { name: "Routing" })).toHaveTextContent("The order Auto tries: Alpha and Beta share work by weekly headroom.");
    expect(within(tasks).getAllByRole("option").map((o) => o.textContent)).toEqual(["Auto", "Prefer Alpha", "Prefer Beta", "Only Alpha", "Only Beta"]);
    await user.selectOptions(tasks, "prefer:beta");
    await waitFor(() => expect(tasks).toHaveDisplayValue("Prefer Beta"));
    await user.selectOptions(tasks, "only:alpha");
    await waitFor(() => expect(tasks).toHaveDisplayValue("Only Alpha"));
    await user.selectOptions(tasks, "");
    await waitFor(() => expect(tasks).toHaveDisplayValue("Auto"));
    expect(posts).toEqual([
      { project: "example", setting: "l2_preference", value: "beta", expected: null },
      { project: "example", setting: "l2_engine", value: "alpha", expected: null },
      { project: "example", setting: "l2_preference", value: null, expected: "beta" },
      { project: "example", setting: "l2_engine", value: null, expected: "alpha" },
    ]);
    expect(screen.queryByRole("combobox", { name: "L3 routing" })).toBeNull();
  });

  it("names custom routing and a preference outside it, and refuses a routing changed in another window", async () => {
    const { saved } = fixture({ routing: "beta" });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const routing = await screen.findByRole("region", { name: "Routing" });
    expect(routing).toHaveTextContent("The order Auto tries. Custom routing: beta.");
    await user.selectOptions(within(routing).getByRole("combobox", { name: "Tasks routing" }), "prefer:alpha");
    expect(await within(routing).findByText("Alpha is not in this project's custom routing, so Prefer has no effect.")).toBeVisible();
    saved.l2_engine = "beta";
    await user.selectOptions(within(routing).getByRole("combobox", { name: "Tasks routing" }), "only:alpha");
    expect(await within(routing).findByRole("alert")).toHaveTextContent("Changed in another window.");
  });

  it("shows what L3's last reply reported, and what it asked for when the engine answered with another model", async () => {
    fixture({ initial: { l3_choice: { engine: "alpha", model: "deep", effort: "high" } },
      l3: { engine_last: "alpha", engine_model: "alpha-deep-2", launch_model: "deep", launch_effort: "high", engine_reasoning_effort: "low" } });
    renderApp({ route: "/settings/projects/example" });
    const section = await screen.findByRole("region", { name: "L3" });
    expect(await within(section).findByText("Last reply reported alpha-deep-2 · Low.")).toBeVisible();
    expect(within(section).getByText("Last reply requested: deep.")).toBeVisible();
  });

  it("says when L3 has not replied and shows Retry when the settings cannot load", async () => {
    fixture();
    renderApp({ route: "/settings/projects/example" });
    expect(await screen.findByText(/L3 has not replied yet/)).toBeVisible();
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "Project is not managed." }, 404)));
    renderApp({ route: "/settings/projects/missing" });
    expect(await screen.findByText(/Could not load settings/)).toBeVisible();
  });
});

describe("Remove project", () => {
  const open = async (user: ReturnType<typeof renderApp>["user"]) => {
    await user.click(await screen.findByRole("button", { name: "Remove…" }));
    return screen.getByRole("dialog", { name: "Remove example from Altitude?" });
  };

  it("explains what stays, focuses Cancel, closes on Escape and names a refusal", async () => {
    const { posts } = fixture({ remove: () => json({ error: "Finish or reject fix-timer before removing this project." }, 409) });
    const { router, user } = renderApp({ route: "/settings/projects/example" });
    let dialog = await open(user);
    expect(dialog).toHaveTextContent("Stays on disk: the repository, worktrees, history and queued messages.");
    expect(dialog).toHaveTextContent("Not kept: its settings here, such as models and routing.");
    expect(dialog).toHaveTextContent("Undo: add the same folder as example again to reattach L3 with its history.");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Remove example"]);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posts).toEqual([]);
    dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Remove example" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Finish or reject fix-timer");
    expect(router.state.location.pathname).toBe("/settings/projects/example");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("disables both buttons while removing, can close meanwhile, and leaves when removal completes", async () => {
    let finish!: (response: Response) => void;
    fixture({ remove: () => new Promise<Response>((resolve) => { finish = resolve; }) });
    const { router, user } = renderApp({ route: "/settings/projects/example" });
    const dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Remove example" }));
    expect(within(dialog).getByRole("button", { name: "Removing…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(dialog).toHaveTextContent("Closing doesn't cancel removal.");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(async () => finish(json({ ok: true })));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("rereads the project list after a lost response: gone continues, still there offers Retry", async () => {
    const lost = { gone: false };
    const server = fixture({ remove: () => { if (lost.gone) server.removeNow(); throw new TypeError("Failed to fetch"); } });
    const { router, user } = renderApp({ route: "/settings/projects/example" });
    const dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Remove example" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Couldn't confirm removal; example is still in Altitude.");
    lost.gone = true;
    await user.click(within(dialog).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("never removes again while presence is unknown: Check again rereads, and only a present project offers Retry", async () => {
    const overviewDown = { on: false };
    const { posts } = fixture({ overviewDown, remove: () => { overviewDown.on = true; throw new TypeError("Failed to fetch"); } });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Remove example" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Couldn't confirm removal, and the project list could not be read.");
    expect(within(dialog).queryByRole("button", { name: /Remove example|Retry/ })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Check again" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Couldn't confirm removal, and the project list could not be read.");
    overviewDown.on = false;
    await user.click(within(dialog).getByRole("button", { name: "Check again" }));
    expect(await within(dialog).findByText("Couldn't confirm removal; example is still in Altitude.")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(posts.filter((p) => "name" in p)).toHaveLength(1);
  });
});

describe("Settings overview", () => {
  it("groups settings by destination and shows This project only when opened from one", async () => {
    fixture();
    const { router, user } = renderApp({ route: "/settings" });
    for (const name of ["Projects", "Voice", "Devices and access", "Coding agents"])
      expect(await screen.findByRole("region", { name })).toBeVisible();
    for (const name of ["Models and effort · All projects", "Engines", "Project settings"]) expect(screen.queryByRole("region", { name })).toBeNull();
    expect(screen.queryByRole("region", { name: "This project" })).toBeNull();
    await user.click(within(screen.getByRole("region", { name: "Projects" })).getByRole("link", { name: /All projects/ }));
    const links = await screen.findAllByRole("link", { name: "sample" });
    expect(links.map((link) => link.getAttribute("href"))).toContain("/settings/projects/sample");
    expect(screen.getByRole("link", { name: /Projects folder/ })).toHaveAttribute("href", "/settings/projects-folder");
    await act(() => router.navigate("/settings", { state: { settingsFrom: "/projects/example?tab=work" } }));
    const current = await screen.findByRole("region", { name: "This project" });
    expect(await within(current).findByRole("link", { name: /example routing Auto/ })).toBeVisible();
    await user.click(within(current).getByRole("link", { name: /example/ }));
    await user.click(await screen.findByRole("link", { name: "‹ Settings" }));
    expect(await screen.findByRole("region", { name: "This project" })).toBeVisible();
  });
});
