import { act, screen, waitFor, within } from "@testing-library/react";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
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
  const view = () => ({ l3_engine: saved.l3_engine ?? null, l2_engine: saved.l2_engine ?? null, l3_choice: saved.l3_choice ?? null,
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

const group = (name: string) => screen.findByRole("group", { name });

describe("Project settings", () => {
  it("saves each role and engine default independently and restores Default", async () => {
    const { posts } = fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 · Alpha");
    expect(within(l3Alpha).getByLabelText("Model")).toHaveAttribute("placeholder", "Default: alpha-default");
    expect(within(await group("Tasks · Beta")).getByLabelText("Effort")).toHaveDisplayValue("Default (High)");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    await within(l3Alpha).findByText("Saved.");
    expect(posts).toEqual([{ project: "example", setting: "l3_alpha_effort", value: "low" }]);
    for (const other of ["L3 · Beta", "Tasks · Alpha", "Tasks · Beta"]) {
      expect(within(await group(other)).getByLabelText("Effort")).toHaveValue("");
      expect(within(await group(other)).queryByText("Saved.")).toBeNull();
    }
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "");
    await waitFor(() => expect(posts.at(-1)).toEqual({ project: "example", setting: "l3_alpha_effort", value: null }));
    await waitFor(() => expect(within(l3Alpha).getByLabelText("Effort")).toHaveDisplayValue("Default (Native)"));
  });

  it("saves a typed model on Enter or leaving the field, restores on Escape, and clears to Default", async () => {
    const { posts } = fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const model = within(await group("Tasks · Beta")).getByLabelText("Model");
    expect(model).toHaveAttribute("placeholder", "Default: CLI default");
    await user.type(model, "  beta-model {Enter}");
    await within(await group("Tasks · Beta")).findByText("Saved.");
    expect(posts).toEqual([{ project: "example", setting: "l2_beta_model", value: "beta-model" }]);
    expect(model).toHaveValue("beta-model");
    await user.type(model, "-draft{Escape}");
    expect(model).toHaveValue("beta-model");
    await user.clear(model);
    await user.tab();
    await waitFor(() => expect(posts.at(-1)).toEqual({ project: "example", setting: "l2_beta_model", value: null }));
    await waitFor(() => expect(model).toHaveValue(""));
    await user.click(model);
    await user.tab();
    expect(posts).toHaveLength(2);
  });

  it("keeps the saved model visible while cache notifications wait", async () => {
    fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const model = within(await group("L3 · Alpha")).getByLabelText("Model");
    const pending: (() => void)[] = [];
    notifyManager.setScheduler((callback) => pending.push(callback));
    try {
      await user.type(model, "deep{Enter}");
      await act(async () => { while (pending.length) pending.shift()!(); });
      expect(model).toHaveValue("deep");
    } finally {
      notifyManager.setScheduler(defaultScheduler);
      await act(async () => { while (pending.length) pending.shift()!(); });
    }
  });

  it("keeps a later save of another field when an earlier save's response arrives last", async () => {
    let release!: () => void;
    const { posts } = fixture({ hold: { setting: "l3_alpha_effort", until: new Promise<void>((resolve) => { release = resolve; }) } });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 · Alpha");
    const model = within(await group("Tasks · Beta")).getByLabelText("Model");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    await user.type(model, "beta-model{Enter}");
    await within(await group("Tasks · Beta")).findByText("Saved.");
    release();
    await within(l3Alpha).findByText("Saved.");
    expect(posts.map((post) => post.setting)).toEqual(["l3_alpha_effort", "l2_beta_model"]);
    expect(model).toHaveValue("beta-model");
    expect(within(l3Alpha).getByLabelText("Effort")).toHaveValue("low");
  });

  it("keeps the saved choice and offers Retry save when the server refuses", async () => {
    const { posts } = fixture({ refuse: "l3_alpha_effort" });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 · Alpha");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    expect(await within(l3Alpha).findByRole("alert")).toHaveTextContent("does not support reasoning effort low");
    expect(within(l3Alpha).getByLabelText("Effort")).toHaveValue("");
    await user.click(within(l3Alpha).getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(posts).toHaveLength(2));
  });

  it("routes tasks Auto, Prefer or Only an engine, and L3 Auto or Only, sending the value each page showed", async () => {
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
    const l3 = screen.getByRole("combobox", { name: "L3 routing" });
    expect(within(l3).getAllByRole("option").map((o) => o.textContent)).toEqual(["Auto", "Only Alpha", "Only Beta"]);
    await user.selectOptions(l3, "only:beta");
    await waitFor(() => expect(l3).toHaveDisplayValue("Only Beta"));
    expect(posts.at(-1)).toEqual({ project: "example", setting: "l3_engine", value: "beta", expected: null });
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

  it("shows L3's choice with the last reply, Back to Auto and Change… opening Models on L3", async () => {
    const { posts } = fixture({ initial: { l3_choice: { engine: "alpha", model: "deep", effort: "high" } },
      l3: { engine_last: "alpha", engine_model: "alpha-deep-2", launch_effort: "high", engine_reasoning_effort: "low" } });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const section = await screen.findByRole("region", { name: "L3" });
    await within(section).findByText("Deep · High");
    expect(section).toHaveTextContent("Until you choose Auto · last reply reported alpha-deep-2 · Low");
    await user.click(within(section).getByRole("button", { name: "Change…" }));
    const dialog = await screen.findByRole("dialog", { name: "Models · L3 · example only" });
    expect(within(dialog).getByRole("tab", { name: /L3/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Escape}");
    await user.click(within(section).getByRole("button", { name: "Back to Auto" }));
    await within(section).findByText("Auto");
    expect(posts).toEqual([{ project: "example", setting: "l3_choice", value: null, expected: { engine: "alpha", model: "deep", effort: "high" } }]);
    expect(within(section).queryByRole("button", { name: "Back to Auto" })).toBeNull();
    expect(section).toHaveTextContent("Project routing and defaults · last reply reported");
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
    for (const name of ["Models", "Projects", "Voice", "Devices and access", "Coding agents"])
      expect(await screen.findByRole("region", { name })).toBeVisible();
    expect(screen.queryByRole("region", { name: "This project" })).toBeNull();
    await user.click(within(screen.getByRole("region", { name: "Projects" })).getByRole("link", { name: /All projects/ }));
    const links = await screen.findAllByRole("link", { name: "sample" });
    expect(links.map((link) => link.getAttribute("href"))).toContain("/settings/projects/sample");
    expect(screen.getByRole("link", { name: /Projects folder/ })).toHaveAttribute("href", "/settings/projects-folder");
    await act(() => router.navigate("/settings", { state: { settingsFrom: "/projects/example?tab=work" } }));
    const current = await screen.findByRole("region", { name: "This project" });
    expect(await within(current).findByRole("link", { name: /example L3: Auto · routing Auto/ })).toBeVisible();
    await user.click(within(current).getByRole("link", { name: /example/ }));
    await user.click(await screen.findByRole("link", { name: "‹ Settings" }));
    expect(await screen.findByRole("region", { name: "This project" })).toBeVisible();
  });
});
