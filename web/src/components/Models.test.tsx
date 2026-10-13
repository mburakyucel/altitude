import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const options = {
  task_example: "use Deep at High for this",
  models: [{ engine: "alpha", model: "swift", label: "Swift" }, { engine: "alpha", model: "deep", label: "Deep" }, { engine: "beta", model: null, label: "Beta default" }],
  efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }], beta: [{ value: "low", label: "Low" }, { value: "ultra", label: "Ultra" }] },
};
const engines = [{ engine: "alpha", label: "Alpha", week: 40, known: true, stale: false, at: new Date().toISOString() },
  { engine: "beta", label: "Beta", week: 10, known: true, stale: false, at: new Date().toISOString() }];

type Choice = Record<string, string> | null;

/** A fictional server holding New tasks and the example project's L3 choice, refusing a stale `expected`. */
function server({ newTasks = null as Choice, l3Choice = null as Choice, unavailable = null as string | null,
  only = [] as { project: string; engine: string }[], refuse = "", hold = null as Promise<void> | null } = {}) {
  const state = { newTasks, l3Choice };
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const tasksView = () => ({ value: state.newTasks, unavailable, only, ...options });
  const defaults = () => ({ l2_engine: null, l3_choice: state.l3Choice, l2_preference: null, l3_unavailable: null, routing: null,
    engines: engines.map(({ engine, label }) => ({ value: engine, label, efforts: [], routed: true })), roles: [], ...options });
  const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json({ projects: [{ name: "example", managed: true }], queue: [], engines, operator: "Ada",
      wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false }, new_tasks: tasksView() });
    if (url === "/api/project/example") return json({ name: "example", tasks: [], archive: [], l3: {} });
    if (url.startsWith("/api/chat/example")) return json({ history: [], active: null, busy: false, l3: {} });
    if (url === "/api/defaults/example") return json(defaults());
    if (url === "/api/monitor") return json({ seats: [], routing: [], sessions: [] });
    if (init?.method === "POST" && (url === "/api/new-tasks" || url === "/api/defaults")) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      posts.push({ path: url, body });
      if (hold) await hold;
      if (refuse) return json({ error: refuse }, 400);
      const key = url === "/api/new-tasks" ? "newTasks" : "l3Choice";
      if (!same(state[key], body.expected)) return json({ error: "Changed in another window.", changed: true }, 409);
      state[key] = body.value as Choice;
      return json(key === "newTasks" ? tasksView() : defaults());
    }
    return json({}, 404);
  }));
  return { posts, state };
}

const dialog = () => screen.getByRole("dialog", { name: /^Models/ });

beforeEach(() => setViewport(1440));

describe("Settings model dropdowns", () => {
  const group = () => screen.getByRole("group", { name: "New tasks · All projects" });
  const model = () => within(group()).getByRole("combobox", { name: "Model" });
  const key = (engine: string, model: string | null) => JSON.stringify([engine, model]);

  it("returns focus to the model dropdown after saving a custom ID with Enter", async () => {
    server();
    const { user } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    await user.selectOptions(model(), "other");
    await user.type(within(group()).getByRole("textbox", { name: "Model ID" }), "exact-model{Enter}");
    await waitFor(() => expect(within(group()).queryByRole("textbox")).toBeNull());
    expect(model()).toHaveFocus();
  });

  it("saves named choices immediately for all new tasks, with project L3 kept separate", async () => {
    const { posts, state } = server();
    const { user, router } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    expect(model()).toHaveValue("");
    expect(group()).toHaveTextContent("Started tasks keep theirs.");
    await user.selectOptions(model(), key("alpha", "deep"));
    await waitFor(() => expect(state.newTasks).toEqual({ engine: "alpha", model: "deep" }));
    expect(group()).toHaveTextContent("deep · Access unverified");
    await act(() => router.navigate("/settings/projects/example"));
    const l3 = await screen.findByRole("group", { name: "L3 · example" });
    expect(l3).toHaveTextContent("Applies from the next reply.");
    await user.selectOptions(within(l3).getByRole("combobox", { name: "Model" }), key("alpha", "swift"));
    await waitFor(() => expect(state.l3Choice).toEqual({ engine: "alpha", model: "swift" }));
    expect(state.newTasks).toEqual({ engine: "alpha", model: "deep" });
    expect(posts.at(-1)).toEqual({ path: "/api/defaults", body: { project: "example", setting: "l3_choice", value: { engine: "alpha", model: "swift" }, expected: null } });
  });

  it("commits custom engine and ID together on group exit, while blank, invalid and Escape never save", async () => {
    const { posts } = server();
    const { user } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    await user.selectOptions(model(), "other");
    await user.tab();
    expect(posts).toHaveLength(0);
    expect(within(group()).queryByRole("textbox")).toBeNull();
    await user.selectOptions(model(), "other");
    const id = within(group()).getByRole("textbox", { name: "Model ID" });
    await user.type(id, "next model");
    await user.keyboard("{Enter}");
    expect(within(group()).getByRole("alert")).toHaveTextContent("without spaces");
    expect(posts).toHaveLength(0);
    await user.clear(id);
    await user.type(id, "next-model");
    await user.selectOptions(within(group()).getByRole("combobox", { name: "Engine" }), "beta");
    expect(posts).toHaveLength(0);
    await user.click(id);
    await user.tab();
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toEqual({ value: { engine: "beta", model: "next-model" }, expected: null });
    await waitFor(() => expect(within(group()).queryByRole("textbox")).toBeNull());
    await user.selectOptions(model(), "other");
    await user.keyboard("{Escape}");
    expect(within(group()).queryByRole("textbox")).toBeNull();
    expect(posts).toHaveLength(1);
  });

  it("keeps the custom draft's original comparison value across refresh, then reloads a conflict", async () => {
    const { posts, state } = server();
    const { user, queryClient } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    await user.selectOptions(model(), "other");
    await user.type(within(group()).getByRole("textbox", { name: "Model ID" }), "next-model");
    state.newTasks = { engine: "beta" };
    await act(() => queryClient.refetchQueries({ queryKey: ["overview"] }));
    await user.keyboard("{Enter}");
    expect(await within(group()).findByRole("alert")).toHaveTextContent("Changed in another window.");
    expect(posts[0]?.body.expected).toBeNull();
    await user.click(within(group()).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(model()).toHaveValue(key("beta", null)));
    expect(within(group()).queryByRole("textbox")).toBeNull();
  });

  it("retains focus while saving, ignores further changes, and retries a rejected request", async () => {
    let release!: () => void;
    const { posts } = server({ refuse: "Model access denied.", hold: new Promise<void>((resolve) => { release = resolve; }) });
    const { user } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    await user.selectOptions(model(), key("alpha", "deep"));
    expect(model()).toHaveAttribute("aria-disabled", "true");
    expect(model()).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(posts).toHaveLength(1);
    await user.tab();
    expect(within(group()).getByRole("combobox", { name: "Effort" })).toHaveFocus();
    release();
    expect(await within(group()).findByRole("alert")).toHaveTextContent("Model access denied.");
    expect(model()).toHaveValue("");
    const retry = server();
    await user.click(within(group()).getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(retry.state.newTasks).toEqual({ engine: "alpha", model: "deep" }));
    expect(retry.posts[0]?.body).toEqual(posts[0]?.body);
  });

  it("resets unsupported effort when changing engine and explains inherited versus explicit requests", async () => {
    const { state } = server({ newTasks: { engine: "alpha", model: "deep", effort: "high" }, unavailable: "Account limit reached" });
    const { user } = renderApp({ route: "/settings" });
    await screen.findByRole("group", { name: "New tasks · All projects" });
    expect(group()).toHaveTextContent("Unavailable · Auto meanwhile: Account limit reached");
    await user.selectOptions(model(), key("beta", null));
    await waitFor(() => expect(state.newTasks).toEqual({ engine: "beta" }));
    expect(await within(group()).findByText("Effort reset to Default for this engine.")).toBeVisible();
    expect(within(group()).getByRole("combobox", { name: "Effort" })).toHaveValue("");
    expect(group()).toHaveTextContent("Uses each project's default, then the engine's own default.");
  });
});

describe("Models dialog", () => {
  it("opens on Tasks from beside the quota, lists Only-engine projects and saves New tasks for every project", async () => {
    const { posts } = server({ only: [{ project: "harbor", engine: "beta" }] });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    expect(dialog()).toHaveAccessibleName("Models · Tasks · All projects");
    expect(within(dialog()).getByRole("tab", { name: /Tasks/ })).toHaveAttribute("aria-selected", "true");
    expect(dialog()).toHaveTextContent("Selected: Auto · every project; tasks that start from now, including queued ones. Started tasks keep theirs.");
    expect(within(dialog()).getByRole("radio", { name: /^Auto/ })).toHaveFocus();
    expect(within(dialog()).getByRole("button", { name: "Use for all new tasks" })).toBeDisabled();
    expect(within(dialog()).queryByRole("button", { name: "Back to Auto" })).toBeNull();
    await user.click(within(dialog()).getByRole("radio", { name: /^Swift/ }));
    expect(within(dialog()).getAllByRole("radio").filter((r) => r.closest(".models-effort")).map((r) => r.parentElement?.textContent)).toEqual(["Default (from project)", "Low", "High"]);
    await user.click(within(dialog()).getByRole("radio", { name: "Low" }));
    expect(dialog()).toHaveTextContent("When Swift is unavailable, each project's Auto picks instead.");
    expect(dialog()).toHaveTextContent("harbor runs tasks only on Beta, so it keeps its Beta model.");
    expect(within(dialog()).getByRole("link", { name: "Change" })).toHaveAttribute("href", "/settings/projects/harbor#routing");
    expect(dialog()).toHaveTextContent("For one task, tell L3: “use Deep at High for this”");
    await user.click(within(dialog()).getByRole("button", { name: "Use for all new tasks" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts).toEqual([{ path: "/api/new-tasks", body: { value: { engine: "alpha", model: "swift", effort: "low" }, expected: null } }]);
    expect(screen.getByRole("button", { name: /^New tasks · Swift · Low/ })).toHaveFocus();
  });

  it("saves against the value the draft started from, so a refresh while editing still meets Changed in another window", async () => {
    const { posts, state } = server();
    const { queryClient, user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    await user.click(within(dialog()).getByRole("radio", { name: /^Swift/ }));
    state.newTasks = { engine: "beta" };
    await act(() => queryClient.refetchQueries({ queryKey: ["overview"] }));
    expect(within(dialog()).getByRole("radio", { name: /^Swift/ })).toBeChecked();
    await user.click(within(dialog()).getByRole("button", { name: "Use for all new tasks" }));
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("Changed in another window.");
    expect(posts.map((p) => p.body.expected)).toEqual([null]);
    expect(state.newTasks).toEqual({ engine: "beta" });
  });

  it("keeps one draft owned by the open tab: switching tabs or closing drops it", async () => {
    server({ l3Choice: { engine: "alpha", model: "deep" } });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    await user.click(within(dialog()).getByRole("radio", { name: /^Swift/ }));
    within(dialog()).getByRole("tab", { name: /Tasks/ }).focus();
    await user.keyboard("{ArrowLeft}");
    expect(dialog()).toHaveAccessibleName("Models · L3 · example only");
    expect(within(dialog()).getByRole("tab", { name: /L3/ })).toHaveFocus();
    expect(dialog()).toHaveTextContent("Selected: Deep · who answers you in example's chat.");
    expect(within(dialog()).getByRole("radio", { name: /^Deep/ })).toBeChecked();
    expect(within(dialog()).getByRole("button", { name: "Back to Auto" })).toBeEnabled();
    await user.keyboard("{ArrowRight}");
    expect(within(dialog()).getByRole("radio", { name: /^Auto/ })).toBeChecked();
    await user.click(within(dialog()).getByRole("radio", { name: /^Swift/ }));
    await user.click(within(dialog()).getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: /^New tasks · Auto/ }));
    expect(within(dialog()).getByRole("radio", { name: /^Auto/ })).toBeChecked();
  });

  it("locks the tabs while saving, keeps a failure on its tab with Retry, and reloads a change made elsewhere", async () => {
    let release!: () => void;
    const fixture = server({ refuse: "Alpha does not support reasoning effort ultra", hold: new Promise<void>((resolve) => { release = resolve; }) });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    await user.click(within(dialog()).getByRole("radio", { name: /^Deep/ }));
    await user.click(within(dialog()).getByRole("button", { name: "Use for all new tasks" }));
    expect(within(dialog()).getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(dialog()).getByRole("tab", { name: /L3/ })).toBeDisabled();
    expect(within(dialog()).getByRole("radio", { name: /^Swift/ })).toBeDisabled();
    release();
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("Alpha does not support reasoning effort ultra");
    expect(dialog()).toHaveTextContent("Selected: Auto");
    expect(within(dialog()).getByRole("radio", { name: /^Deep/ })).toBeChecked();
    fixture.posts.length = 0;
    vi.stubGlobal("fetch", vi.fn());
    const changed = server({ newTasks: { engine: "beta" } });
    await user.click(within(dialog()).getByRole("button", { name: "Retry" }));
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("Changed in another window.");
    expect(changed.posts.at(-1)?.body).toEqual({ value: { engine: "alpha", model: "deep" }, expected: null });
    await user.click(within(dialog()).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(dialog()).toHaveTextContent("Selected: Beta default"));
    expect(within(dialog()).getByRole("radio", { name: /^Beta default/ })).toBeChecked();
  });

  it("saves a typed model on a named engine, and Back to Auto ends a choice", async () => {
    const { posts } = server({ newTasks: { engine: "alpha", model: "deep", effort: "high" } });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Deep · High/ }));
    await user.click(within(dialog()).getByRole("radio", { name: "Other model…" }));
    const use = within(dialog()).getByRole("button", { name: "Use for all new tasks" });
    expect(use).toBeDisabled();
    await user.selectOptions(within(dialog()).getByRole("combobox", { name: "Engine" }), "beta");
    await user.type(within(dialog()).getByRole("textbox", { name: "Model id" }), "beta next");
    expect(use).toBeDisabled();
    await user.clear(within(dialog()).getByRole("textbox", { name: "Model id" }));
    await user.type(within(dialog()).getByRole("textbox", { name: "Model id" }), "beta-next");
    await user.click(within(dialog()).getByRole("radio", { name: "Ultra" }));
    await user.click(use);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts.at(-1)?.body).toEqual({ value: { engine: "beta", model: "beta-next", effort: "ultra" }, expected: { engine: "alpha", model: "deep", effort: "high" } });
    await user.click(screen.getByRole("button", { name: /^New tasks · beta-next · Beta · Ultra/ }));
    expect(within(dialog()).getByRole("radio", { name: "Other model…" })).toBeChecked();
    await user.click(within(dialog()).getByRole("button", { name: "Back to Auto" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts.at(-1)?.body).toEqual({ value: null, expected: { engine: "beta", model: "beta-next", effort: "ultra" } });
    expect(screen.getByRole("button", { name: /^New tasks · Auto/ })).toBeVisible();
  });

  it("says when the chosen model is unavailable and Auto runs meanwhile", async () => {
    server({ newTasks: { engine: "alpha", model: "deep" }, unavailable: "Deep's weekly limit is used up until Friday" });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Deep unavailable · Auto meanwhile/ }));
    expect(dialog()).toHaveTextContent("Deep unavailable · Auto meanwhile: Deep's weekly limit is used up until Friday");
  });

  it("says Auto answers while L3's chosen model is unavailable", async () => {
    server({ l3Choice: { engine: "alpha", model: "deep" } });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^L3 model: Deep/ }));
    expect(dialog()).toHaveTextContent("Applies from L3's next reply. When Deep is unavailable, Auto answers instead.");
    expect(within(dialog()).queryByRole("link", { name: "Change in Routing" })).toBeNull();
  });

  it("shows only the Tasks tab outside a project, and the control on the phone's Monitor and Work", async () => {
    server();
    const { user, unmount } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    expect(dialog()).toHaveAccessibleName("Models · Tasks · All projects");
    expect(within(dialog()).queryByRole("tablist")).toBeNull();
    unmount();
    setViewport(390);
    const monitor = renderApp({ route: "/monitor" });
    expect(await screen.findByRole("button", { name: /^New tasks · Auto/ })).toBeVisible();
    monitor.unmount();
    renderApp({ route: "/projects/example?tab=work" });
    const work = await screen.findByRole("region", { name: "Work" });
    expect(await within(work).findByRole("button", { name: /^New tasks · Auto/ })).toBeVisible();
  });
});
