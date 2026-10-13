import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const pair = () => ({ model: null as string | null, effort: null as string | null });
const key = (engine: string, model: string) => JSON.stringify([engine, model]);
function server({ status = 200, hold = null as Promise<void> | null, overrides = [] as Record<string, unknown>[] } = {}) {
  const values = { alpha: pair(), beta: pair() };
  const posts: Record<string, unknown>[] = [];
  const failure = { status };
  const options = { models: [], efforts: {} };
  const engines = ["alpha", "beta"].map((engine) => ({ engine, label: engine === "alpha" ? "Alpha" : "Beta", known: false }));
  let newTasks: Record<string, string> | null = { engine: "alpha", model: "old", effort: "high" };
  const view = () => ({ engines: engines.map(({ engine, label }) => ({ engine, label, value: values[engine as keyof typeof values], models: [{ value: "deep", label: "Deep" }],
    efforts: [{ value: "native", label: "Native" }, { value: "high", label: "High" }, { value: "ultra", label: "Ultra" }], default_model: "Provider default", default_effort: "Tasks/reviews: High · Chat: Native", native_effort: "Provider chooses effort", unavailable: null })), overrides });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json({ projects: [{ name: "example", managed: true }], queue: [], engines, wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false }, new_tasks: { ...options, value: newTasks, only: [] } });
    if (url === "/api/model-defaults" && init?.method !== "POST") return json(view());
    if (url === "/api/model-defaults" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)); posts.push(body);
      if (failure.status !== 200) return json({ error: "Save refused" }, failure.status);
      const engine = body.engine as keyof typeof values;
      if (JSON.stringify(values[engine]) !== JSON.stringify(body.expected)) return json({ error: "Changed" }, 409);
      values[engine] = body.value;
      const snapshot = JSON.parse(JSON.stringify(view()));
      if (engine === "alpha" && hold) await hold;
      return json(snapshot);
    }
    if (url === "/api/model-defaults/reset") {
      const body = JSON.parse(String(init?.body)); posts.push(body);
      overrides = overrides.filter((row) => row.id !== body.id); return json(view());
    }
    if (url === "/api/new-tasks") { const body = JSON.parse(String(init?.body)); posts.push(body); newTasks = body.value; return json({ ...options, value: newTasks, only: [] }); }
    return json({}, 404);
  }));
  return { values, posts, failure };
}
beforeEach(() => {
  setViewport(1440);
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
const model = (name = "Alpha") => screen.getByRole("combobox", { name: `${name} model` });
const effort = (name = "Alpha") => screen.getByRole("combobox", { name: `${name} effort` });
const ready = () => screen.findByRole("combobox", { name: "Alpha model" });

describe("Altitude-wide model and effort pairs", () => {
  it("saves independent fields as a pair, retaining Native distinctly from inheritance", async () => {
    const { posts, values } = server(); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(effort(), "native");
    await waitFor(() => expect(effort()).toHaveValue("native"));
    await user.selectOptions(model(), key("alpha", "deep"));
    await waitFor(() => expect(values.alpha).toEqual({ model: "deep", effort: "native" }));
    expect(posts[1]).toEqual({ engine: "alpha", value: { model: "deep", effort: "native" }, expected: { model: null, effort: "native" } });
    expect(values.beta).toEqual(pair());
    await user.selectOptions(effort(), "");
    await waitFor(() => expect(values.alpha).toEqual({ model: "deep", effort: null }));
    expect(screen.getAllByText("Tasks/reviews: High · Chat: Native")).toHaveLength(2);
  });
  it("locks both controls of the saving pair, keeps focus, and merges reversed cross-engine responses", async () => {
    let release!: () => void;
    const { posts } = server({ hold: new Promise<void>((resolve) => { release = resolve; }) });
    const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(effort(), "high");
    expect(effort()).toHaveFocus(); expect(model()).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(model(), { target: { value: key("alpha", "deep") } });
    expect(posts).toHaveLength(1);
    await user.selectOptions(model("Beta"), key("beta", "deep"));
    await waitFor(() => expect(model("Beta")).toHaveDisplayValue("Deep"));
    await act(async () => release());
    await waitFor(() => expect(effort()).toHaveAttribute("aria-disabled", "false"));
    expect(model("Beta")).toHaveDisplayValue("Deep"); expect(effort()).toHaveValue("high");
  });
  it("commits custom IDs with original pair comparison and restores dropdown focus", async () => {
    const { posts } = server(); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(model(), "other");
    await user.type(screen.getByRole("textbox", { name: "Model ID" }), "exact-model{Enter}");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Model ID" })).toBeNull());
    expect(model()).toHaveFocus(); expect(posts[0]).toEqual({ engine: "alpha", value: { model: "exact-model", effort: null }, expected: pair() });
  });
  it("blank, Escape and whitespace-invalid custom values never write", async () => {
    const { posts } = server(); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(model(), "other"); await user.tab();
    expect(screen.queryByRole("textbox", { name: "Model ID" })).toBeNull();
    await user.selectOptions(model(), "other"); await user.type(screen.getByRole("textbox", { name: "Model ID" }), "bad id{Enter}");
    expect(await screen.findByText("Use one model ID without spaces.")).toBeVisible();
    await user.keyboard("{Escape}"); expect(posts).toHaveLength(0);
  });
  it("retains a custom draft's baseline across a refresh and requires reload after conflict", async () => {
    const { values, posts } = server(); const { user, queryClient } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(model(), "other"); await user.type(screen.getByRole("textbox", { name: "Model ID" }), "custom");
    values.alpha = { model: null, effort: "high" };
    await act(() => queryClient.invalidateQueries({ queryKey: ["model-defaults"] }));
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Changed in another window.")).toBeVisible();
    expect(posts[0]).toMatchObject({ expected: pair() });
    await user.tab();
    expect(posts).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Model ID" })).toBeNull());
    expect(effort()).toHaveValue("high");
  });
  it("restores committed fields after failure and retries the same pair", async () => {
    const { failure, posts } = server({ status: 500 }); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(effort(), "ultra"); await screen.findByText("Save refused"); expect(effort()).toHaveValue("");
    failure.status = 200; await user.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(effort()).toHaveValue("ultra")); expect(posts[0]).toEqual(posts[1]);
  });
  it("offers no automatic retry after denial", async () => {
    server({ status: 403 }); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(effort(), "high"); await screen.findByText("Save refused");
    expect(screen.queryByRole("button", { name: "Retry save" })).toBeNull(); expect(effort()).toHaveValue("");
  });
  it("clears explicit overrides only on request and restores focus to the global section", async () => {
    const { posts } = server({ overrides: [{ id: "override", project: "example", label: "example · Tasks", engine: "alpha", model: "custom", effort: "high", expected: { model: "custom", effort: "high" }, editable: true }] });
    const { user } = renderApp({ route: "/settings" }); await ready();
    await user.click(screen.getByRole("button", { name: "Use global defaults" }));
    await waitFor(() => expect(screen.queryByText("Existing overrides")).toBeNull());
    expect(screen.getByRole("heading", { name: "Models and effort · All projects" })).toHaveFocus();
    expect(posts[0]).toEqual({ id: "override", expected: { model: "custom", effort: "high" } });
  });
  it("keeps engine preference separate and clears old engine-specific fields when it changes", async () => {
    const { posts } = server(); const { user } = renderApp({ route: "/settings" }); await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "New tasks engine" }), "beta");
    await waitFor(() => expect(posts).toContainEqual({ value: { engine: "beta" }, expected: { engine: "alpha", model: "old", effort: "high" } }));
    expect(model()).toHaveAttribute("aria-disabled", "false"); expect(model("Beta")).toHaveAttribute("aria-disabled", "false");
  });
  it("makes the shared shortcut navigate to global settings with its return location", async () => {
    server(); const { user, router } = renderApp({ route: "/settings/projects/example" });
    const links = await screen.findAllByRole("link", { name: "Model settings" });
    await user.click(links[0]!); expect(router.state.location.pathname).toBe("/settings"); expect(router.state.location.hash).toBe("#models");
    expect(router.state.location.state.settingsFrom).toBe("/settings/projects/example");
  });
});
