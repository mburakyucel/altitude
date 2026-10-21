import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

/* Project agents in the project's chat and Work (design/wireframes/project-agents/PROPOSAL.md, "Work" and "In chat"). */

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const overview = { projects: [{ name: "harbor", managed: true }], queue: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: null, known: false, stale: false, at: null, reserve: 90 }] };
const definition = { name: "Dependency check", brief: "Check whether any dependency has a security fix.", schedules: [{ days: [0, 1, 2, 3, 4], time: "07:00" }],
  events: false, tell: "found", model: null, limit: 20 };
const offer = (over: Record<string, unknown> = {}) => ({ id: "o1", turn_id: "t1", kind: "new", agent: null, agent_name: null, definition,
  line: "Weekdays at 07:00 · Tells you when it finds something", changes: [], on: true, state: "open", reason: null, result_agent: null, ...over });
const runs = [{ agent: "dependency-check", name: "Dependency check", n: 3, state: "running" as const, line: "Agent run · 2 of 20 min" },
  { agent: "merge-review", name: "Review each merge", n: 7, state: "waiting" as const, line: "Agent run · waiting to start" }];
const project = (agents: Record<string, unknown> | null = { runs, count: 4, offers: [] }) => ({ name: "harbor", l3: {}, busy: false,
  tasks: [{ slug: "fix-timer", state: "running", title: "Fix the timer", updated: ago(2) }], archive: [], decisions: [], incidents: [],
  ...(agents ? { project_agents: agents } : {}) });
const agentRows = [
  { at: ago(30), role: "assistant", trigger: "project-agent", turn_id: "a1", text: "**Dependency check** found something: 2 packages have security fixes.",
    project_agent: { agent: "dependency-check", n: 3, tone: "accent" } },
  { at: ago(20), role: "assistant", trigger: "project-agent", turn_id: "a2", text: "**Dependency check** paused itself: 3 runs in a row couldn't finish.",
    project_agent: { agent: "dependency-check", n: null, tone: "danger" } },
];
const ask = (turn: string, minutes: number) => [
  { at: ago(minutes), role: "user", text: `Ask ${turn}`, turn_id: turn },
  { at: ago(minutes - 1), role: "assistant", text: `Reply ${turn}`, trigger: "chat", engine: "alpha", turn_id: turn },
];

function serve(options: { project?: () => unknown; history?: unknown[]; press?: () => Response | Promise<Response> } = {}) {
  const presses: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/project/harbor") return json(options.project ? options.project() : project());
    if (url.startsWith("/api/chat/harbor")) return json({ history: options.history ?? [...ask("t0", 60), ...agentRows], active: null, busy: false, l3: {} });
    if (url === "/api/agents/harbor") return json({ clock: { zone: "Istanbul", offset: -new Date().getTimezoneOffset() },
      budget: { minutes: 120, choices: [120], used: 0, spent: false }, agents: [], retired: [], builtins: [], offers: [] });
    if (url === "/api/agents" && init?.method === "POST") {
      const { request_id: _, ...body } = JSON.parse(String(init.body));
      presses.push(body);
      return options.press ? options.press() : json({ ok: true, agent: "dependency-check" });
    }
    return json({ error: "not found" }, 404);
  }));
  return { presses };
}

afterEach(() => setViewport(1024));

describe.each([390, 1440])("agents in the project at %ipx", (width) => {
  it("shows each run's result as a system line with its dot and Open, not as L3's answer", async () => {
    setViewport(width);
    serve();
    const { user, router } = renderApp({ route: "/projects/harbor" });
    const found = (await screen.findByText("found something: 2 packages have security fixes.", { exact: false })).closest(".agent-line")!;
    expect(found).toHaveTextContent("Dependency check found something: 2 packages have security fixes. Open");
    expect(within(found as HTMLElement).getByText("Dependency check").tagName).toBe("STRONG");
    expect(found.querySelector(".sys-dot")).toHaveAttribute("data-tone", "accent");
    const open = within(found as HTMLElement).getByRole("link", { name: "Open" });
    expect(open).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/runs/3");
    const paused = screen.getByText("paused itself: 3 runs in a row couldn't finish.", { exact: false }).closest(".agent-line")!;
    expect(paused.querySelector(".sys-dot")).toHaveAttribute("data-tone", "danger");
    expect(within(paused as HTMLElement).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    // The status line, on desktop, names L3's last answer, not the result lines after it.
    if (width === 1440) expect(screen.getByText(/^L3 answered 59 min ago on Alpha · 1 task in flight/)).toBeInTheDocument();
    await user.click(open);
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check/runs/3"));
    if (width === 390) expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Chat" })).toHaveAttribute("aria-current", "page");
  });

  it("Work lists waiting and running runs in Current, and Agents · N after Done this week", async () => {
    setViewport(width);
    serve();
    const { user, router } = renderApp({ route: width === 390 ? "/projects/harbor?tab=work" : "/projects/harbor" });
    const panel = await screen.findByRole("region", { name: "Work" });
    const current = await within(panel).findByRole("region", { name: "Current" });
    const running = within(current).getByRole("link", { name: "Dependency check · Agent run · 2 of 20 min" });
    expect(running).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/runs/3");
    expect(running.querySelector(".dot")).toHaveAttribute("data-state", "running");
    const waiting = within(current).getByRole("link", { name: "Review each merge · Agent run · waiting to start" });
    expect(waiting.querySelector(".dot")).toHaveAttribute("data-state", "idle");
    expect(within(panel).getByRole("heading", { name: "Current (3)" })).toBeInTheDocument();
    const agents = within(panel).getByRole("link", { name: "Agents · 4" });
    expect(agents).toHaveAttribute("href", "/projects/harbor/agents");
    await user.click(running);
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check/runs/3"));
    if (width === 390) expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Work" })).toHaveAttribute("aria-current", "page");
  });
});

describe("Agents in the project", () => {
  it("with no agents and no runs, Work has no Agents row while the menu keeps Agents…", async () => {
    serve({ project: () => project({ runs: [], count: 0, offers: [] }) });
    setViewport(1440);
    const { user, router } = renderApp({ route: "/projects/harbor" });
    const panel = await screen.findByRole("region", { name: "Work" });
    await within(panel).findByRole("link", { name: /^Fix the timer/ });
    expect(within(panel).queryByRole("link", { name: /^Agents/ })).toBeNull();
    expect(within(panel).getByRole("heading", { name: "Current (1)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Agents…" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents"));
  });

  it("shows each offer card state under the L3 reply it belongs to", async () => {
    const offers = [
      offer(),
      offer({ id: "o2", turn_id: "t2", kind: "change", agent: "dependency-check", agent_name: "Dependency check", on: false,
        line: "Weekdays at 06:00 · Tells you when it finds something", changes: ["Runs at 06:00 instead of 07:00", "New brief"],
        definition: { ...definition, brief: "Check the lock file for security fixes and name each package." } }),
      offer({ id: "o3", turn_id: "t3", state: "created", result_agent: "dependency-check" }),
      offer({ id: "o4", turn_id: "t4", kind: "change", agent: "dependency-check", agent_name: "Dependency check", state: "refused",
        reason: "Dependency check changed since this offer." }),
      offer({ id: "o5", turn_id: "t5", state: "lapsed" }),
    ];
    serve({ project: () => project({ runs: [], count: 1, offers }), history: ["t1", "t2", "t3", "t4", "t5"].flatMap((t, i) => ask(t, 50 - i * 5)) });
    renderApp({ route: "/projects/harbor" });
    const card = (id: string) => document.querySelector<HTMLElement>(`[data-offer="${id}"]`)!;
    await waitFor(() => expect(card("o1")).not.toBeNull());
    expect(card("o1").closest(".turn")).toHaveTextContent("Reply t1");
    expect(card("o1")).toHaveTextContent("New agentDependency checkWeekdays at 07:00 · Tells you when it finds something · On when created");
    expect(within(card("o1")).getByText("Check whether any dependency has a security fix.")).toBeInTheDocument();
    expect(within(card("o1")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Create agent"]);
    expect(within(card("o1")).getByRole("link", { name: "Edit first" })).toHaveAttribute("href", "/projects/harbor/agents/new?offer=o1");
    expect(card("o2")).toHaveTextContent("Change to Dependency check");
    expect(card("o2")).not.toHaveTextContent("On when created");
    expect(within(card("o2")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Runs at 06:00 instead of 07:00", "New brief"]);
    expect(within(card("o2")).getByText("Check the lock file for security fixes and name each package.")).toBeInTheDocument();
    expect(within(card("o2")).getByRole("button", { name: "Save change" })).toBeInTheDocument();
    expect(within(card("o2")).getByRole("link", { name: "Edit first" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/edit?offer=o2");
    expect(card("o3")).toHaveTextContent(/Created · Open$/);
    expect(within(card("o3")).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    expect(within(card("o3")).queryByRole("button")).toBeNull();
    expect(card("o4")).toHaveTextContent(/Not saved: Dependency check changed since this offer\.$/);
    expect(within(card("o4")).queryByRole("button")).toBeNull();
    expect(within(card("o4")).queryByRole("link")).toBeNull();
    expect(card("o5")).toHaveTextContent(/This offer lapsed\.$/);
    expect(within(card("o5")).queryByRole("button")).toBeNull();
  });

  it("Create agent spins until the project reads the offer as created; a failed press says Not saved:", async () => {
    let state = "open";
    let answer: () => Response = () => json({ error: "The name is taken." }, 409);
    const { presses } = serve({ project: () => project({ runs: [], count: 0, offers: [offer({ state, result_agent: state === "created" ? "dependency-check" : null })] }),
      history: ask("t1", 10), press: () => answer() });
    const { user } = renderApp({ route: "/projects/harbor" });
    const create = await screen.findByRole("button", { name: "Create agent" });
    await user.click(create);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: The name is taken.");
    answer = () => { state = "created"; return json({ ok: true, agent: "dependency-check" }); };
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(document.querySelector("[data-offer]")).toHaveTextContent(/Created · Open$/));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: /Create agent/ })).toBeNull();
    expect(presses).toEqual([{ project: "harbor", action: "offer", offer: "o1" }, { project: "harbor", action: "offer", offer: "o1" }]);
  });

  it("while saving, the button spins and Edit first is unavailable", async () => {
    let finish!: (response: Response) => void;
    serve({ project: () => project({ runs: [], count: 0, offers: [offer()] }), history: ask("t1", 10),
      press: () => new Promise<Response>((done) => { finish = done; }) });
    const { user } = renderApp({ route: "/projects/harbor" });
    await user.click(await screen.findByRole("button", { name: "Create agent" }));
    expect(screen.getByRole("button", { name: "Creating the agent…" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Edit first" })).toHaveAttribute("aria-disabled", "true");
    finish(json({ ok: true, agent: "dependency-check" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create agent" })).toBeEnabled());
  });
});
