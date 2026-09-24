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

/** A fictional server: settings keyed like the registry, validated and echoed as the defaults view. */
function fixture({ refuse = "", l3 = {} as Record<string, unknown>, hold = null as null | { setting: string; until: Promise<void> } } = {}) {
  const saved: Record<string, string | null> = {};
  let pin: string | null = null;
  const posts: Record<string, unknown>[] = [];
  const field = (setting: string, fallback: string, choices: unknown) => ({ setting, value: saved[setting] ?? null, default: fallback, choices });
  const view = () => ({ l3_engine: pin, roles: (["l3", "l2"] as const).map((role) => ({ role, engines: engines.map(({ engine, label }) => ({
    engine, label,
    model: field(`${role}_${engine}_model`, engine === "alpha" ? "alpha-default" : "CLI default", engine === "alpha" ? ["swift", "deep"] : []),
    effort: field(`${role}_${engine}_effort`, role === "l2" && engine === "beta" ? "High" : "Native", efforts),
  })) })) });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/project/example") return json({ name: "example", tasks: [], l3 });
    if (url === "/api/defaults/example") return json(view());
    if (url === "/api/defaults" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)); posts.push(body);
      if (body.setting === refuse) return json({ error: "Alpha does not support reasoning effort low" }, 400);
      saved[body.setting] = body.value;
      const response = view();
      if (hold && body.setting === hold.setting) await hold.until;
      return json(response);
    }
    if (url === "/api/l3/engine") { const body = JSON.parse(String(init?.body)); posts.push(body); pin = body.engine; return json({ ok: true }); }
    return json({}, 404);
  }));
  return posts;
}

const group = (name: string) => screen.findByRole("group", { name });

describe("Project settings", () => {
  it("saves each role and engine default independently and restores Default", async () => {
    const posts = fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 on Alpha");
    expect(within(l3Alpha).getByLabelText("Model")).toHaveAttribute("placeholder", "Default: alpha-default");
    expect(within(await group("L2 on Beta")).getByLabelText("Effort")).toHaveDisplayValue("Default (High)");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    await within(l3Alpha).findByText("Saved.");
    expect(posts).toEqual([{ project: "example", setting: "l3_alpha_effort", value: "low" }]);
    for (const other of ["L3 on Beta", "L2 on Alpha", "L2 on Beta"]) {
      expect(within(await group(other)).getByLabelText("Effort")).toHaveValue("");
      expect(within(await group(other)).queryByText("Saved.")).toBeNull();
    }
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "");
    await waitFor(() => expect(posts.at(-1)).toEqual({ project: "example", setting: "l3_alpha_effort", value: null }));
    await waitFor(() => expect(within(l3Alpha).getByLabelText("Effort")).toHaveDisplayValue("Default (Native)"));
  });

  it("saves a typed model on Enter or leaving the field, restores on Escape, and clears to Default", async () => {
    const posts = fixture();
    const { user } = renderApp({ route: "/settings/projects/example" });
    const model = within(await group("L2 on Beta")).getByLabelText("Model");
    expect(model).toHaveAttribute("placeholder", "Default: CLI default");
    await user.type(model, "  beta-model {Enter}");
    await within(await group("L2 on Beta")).findByText("Saved.");
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
    const model = within(await group("L3 on Alpha")).getByLabelText("Model");
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
    const posts = fixture({ hold: { setting: "l3_alpha_effort", until: new Promise<void>((resolve) => { release = resolve; }) } });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 on Alpha");
    const model = within(await group("L2 on Beta")).getByLabelText("Model");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    await user.type(model, "beta-model{Enter}");
    await within(await group("L2 on Beta")).findByText("Saved.");
    release();
    await within(l3Alpha).findByText("Saved.");
    expect(posts.map((post) => post.setting)).toEqual(["l3_alpha_effort", "l2_beta_model"]);
    expect(model).toHaveValue("beta-model");
    expect(within(l3Alpha).getByLabelText("Effort")).toHaveValue("low");
  });

  it("keeps the saved choice and offers Retry save when the server refuses", async () => {
    const posts = fixture({ refuse: "l3_alpha_effort" });
    const { user } = renderApp({ route: "/settings/projects/example" });
    const l3Alpha = await group("L3 on Alpha");
    await user.selectOptions(within(l3Alpha).getByLabelText("Effort"), "low");
    expect(await within(l3Alpha).findByRole("alert")).toHaveTextContent("does not support reasoning effort low");
    expect(within(l3Alpha).getByLabelText("Effort")).toHaveValue("");
    await user.click(within(l3Alpha).getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(posts).toHaveLength(2));
  });

  it("pins the L3 engine and describes the last turn's requested and reported effort", async () => {
    const posts = fixture({ l3: { engine_last: "beta", engine_model: "beta-model", launch_effort: "high", engine_reasoning_effort: null } });
    const { user } = renderApp({ route: "/settings/projects/example" });
    expect(await screen.findByText("Last turn: Beta · beta-model · High effort requested · applied effort not reported")).toBeVisible();
    const pin = screen.getByRole("combobox", { name: "L3 engine" });
    expect(pin).toHaveDisplayValue("Auto");
    await user.selectOptions(pin, "alpha");
    await waitFor(() => expect(posts).toEqual([{ project: "example", engine: "alpha" }]));
    await waitFor(() => expect(pin).toHaveDisplayValue("Alpha"));
  });

  it("says when L3 has not started and shows Retry when the settings cannot load", async () => {
    fixture();
    renderApp({ route: "/settings/projects/example" });
    expect(await screen.findByText("L3 has not started.")).toBeVisible();
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "Project is not managed." }, 404)));
    renderApp({ route: "/settings/projects/missing" });
    expect(await screen.findByText(/Could not load settings/)).toBeVisible();
  });
});

describe("Settings overview project rows", () => {
  it("shows the project Settings was opened from, or every managed project on a direct visit", async () => {
    fixture();
    const { router, user } = renderApp({ route: "/settings" });
    const projects = await screen.findByRole("region", { name: "Projects" });
    await within(projects).findByRole("link", { name: /sample/ });
    expect(within(projects).getByRole("link", { name: /example/ })).toHaveAttribute("href", "/settings/projects/example");
    await act(() => router.navigate("/settings", { state: { settingsFrom: "/projects/example?tab=work" } }));
    const current = await screen.findByRole("region", { name: "This project" });
    expect(within(current).queryByRole("link", { name: /sample/ })).toBeNull();
    await user.click(within(current).getByRole("link", { name: /example/ }));
    await user.click(await screen.findByRole("link", { name: "‹ Settings" }));
    expect(await screen.findByRole("region", { name: "This project" })).toBeVisible();
  });
});
