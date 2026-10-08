import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const options = {
  models: [{ engine: "alpha", model: "swift", label: "Swift" }, { engine: "alpha", model: "deep", label: "Deep" }, { engine: "beta", model: null, label: "Beta default" }],
  efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }], beta: [{ value: "low", label: "Low" }, { value: "ultra", label: "Ultra" }] },
};
const engines = [{ engine: "alpha", label: "Alpha", week: 40, known: true, stale: false, at: new Date().toISOString() },
  { engine: "beta", label: "Beta", week: 10, known: true, stale: false, at: new Date().toISOString() }];

type Choice = Record<string, string> | null;

/** A fictional server holding New tasks and the example project's L3 choice, refusing a stale `expected`. */
function server({ newTasks = null as Choice, l3Choice = null as Choice, l3Engine = null as string | null, unavailable = null as string | null,
  only = [] as { project: string; engine: string }[], refuse = "", hold = null as Promise<void> | null } = {}) {
  const state = { newTasks, l3Choice };
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const tasksView = () => ({ value: state.newTasks, unavailable, only, ...options });
  const defaults = () => ({ l3_engine: l3Engine, l2_engine: null, l3_choice: state.l3Choice, l2_preference: null, l3_unavailable: null, routing: null,
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

describe("Models dialog", () => {
  it("opens on Tasks from beside the quota, lists Only-engine projects and saves New tasks for every project", async () => {
    const { posts } = server({ only: [{ project: "tutor", engine: "beta" }] });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: /^New tasks · Auto/ }));
    expect(dialog()).toHaveAccessibleName("Models · Tasks · All projects");
    expect(within(dialog()).getByRole("tab", { name: /Tasks/ })).toHaveAttribute("aria-selected", "true");
    expect(dialog()).toHaveTextContent("In use: Auto · every project; tasks that start from now, including queued ones. Started tasks keep theirs.");
    expect(within(dialog()).getByRole("radio", { name: /^Auto/ })).toHaveFocus();
    expect(within(dialog()).getByRole("button", { name: "Use for all new tasks" })).toBeDisabled();
    expect(within(dialog()).queryByRole("button", { name: "Back to Auto" })).toBeNull();
    await user.click(within(dialog()).getByRole("radio", { name: /^Swift/ }));
    expect(within(dialog()).getAllByRole("radio").filter((r) => r.closest(".models-effort")).map((r) => r.parentElement?.textContent)).toEqual(["Default", "Low", "High"]);
    await user.click(within(dialog()).getByRole("radio", { name: "Low" }));
    expect(dialog()).toHaveTextContent("When Swift is unavailable, each project's Auto picks instead.");
    expect(dialog()).toHaveTextContent("tutor runs tasks only on Beta, so it keeps its Beta model.");
    expect(within(dialog()).getByRole("link", { name: "Change" })).toHaveAttribute("href", "/settings/projects/tutor#routing");
    expect(dialog()).toHaveTextContent("For one task, tell L3: “use Opus at Max for this”.");
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
    expect(dialog()).toHaveTextContent("In use: Deep · who answers you in example's chat.");
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
    expect(dialog()).toHaveTextContent("In use: Auto");
    expect(within(dialog()).getByRole("radio", { name: /^Deep/ })).toBeChecked();
    fixture.posts.length = 0;
    vi.stubGlobal("fetch", vi.fn());
    const changed = server({ newTasks: { engine: "beta" } });
    await user.click(within(dialog()).getByRole("button", { name: "Retry" }));
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("Changed in another window.");
    expect(changed.posts.at(-1)?.body).toEqual({ value: { engine: "alpha", model: "deep" }, expected: null });
    await user.click(within(dialog()).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(dialog()).toHaveTextContent("In use: Beta default"));
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

  it("names an L3 Only engine that keeps a chosen model from being used", async () => {
    server({ l3Engine: "beta" });
    const { user } = renderApp({ route: "/projects/example" });
    await user.click(await screen.findByRole("button", { name: "L3 model: Auto" }));
    await user.click(within(dialog()).getByRole("radio", { name: /^Deep/ }));
    expect(dialog()).toHaveTextContent("This project keeps L3 only on Beta, so Deep can't be used here.");
    expect(within(dialog()).getByRole("link", { name: "Change in Routing" })).toHaveAttribute("href", "/settings/projects/example#routing");
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
