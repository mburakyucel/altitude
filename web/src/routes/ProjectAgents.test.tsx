import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { runTime } from "../data/projectAgents";

/* The project agents pages against a fictional server (design/wireframes/project-agents/PROPOSAL.md, "States"). */

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const here = () => -new Date().getTimezoneOffset();

const overview = {
  projects: [{ name: "harbor", managed: true }], queue: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: null, known: false, stale: false, at: null }],
  new_tasks: { models: [{ engine: "alpha", model: "swift", label: "Swift" }], efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }] },
    value: null, only: [] },
};
const definition = { name: "Dependency check", brief: "Check whether any dependency has a security fix.", schedules: [{ days: [0, 1, 2, 3, 4], time: "07:00" }],
  tell: "found", model: null, limit: 20 };
const summary = (over: Record<string, unknown> = {}) => ({ id: "dependency-check", name: "Dependency check", state: "on", triggers: "Weekdays at 07:00",
  news: "Found something", news_at: ago(120), cue: null, retired_line: null, ...over });
const builtin = { key: "checks", name: "Clean checks", line: "Mondays at 08:00 · Runs the project's checks on a clean copy and says what broke" };
const list = (over: Record<string, unknown> = {}) => ({ clock: { zone: "Istanbul", offset: here() },
  budget: { minutes: 120, choices: [30, 60, 120, 300, 600], used: 70, spent: false },
  agents: [summary(), summary({ id: "merge-review", name: "Review each merge", triggers: "When you run it", news: "Running, 2 of 20 min", news_at: null, cue: "running" }),
    summary({ id: "release-notes", name: "Release notes", triggers: "Fridays at 17:00", news: "Paused", news_at: null, cue: "paused", state: "paused" })],
  retired: [summary({ id: "old-audit", name: "Old audit", state: "retired", retired_line: "Retired Oct 19 · 23 runs" })],
  builtins: [builtin], offers: [], ...over });
const run = (n: number, over: Record<string, unknown> = {}) => ({ n, state: "found", outcome: "Found something", tone: "accent", at: ago(60 * 24 * n),
  started: ago(60 * 24 * n), late: false, ended: ago(60 * 24 * n - 6), minutes: 6, tokens: 4_100_000, line: "2 packages have security fixes.", page: true,
  trigger_line: "On its schedule", ...over });
const skipped = run(1, { state: "skipped", outcome: "Skipped", tone: "muted", minutes: null, tokens: null, line: "No free slot for 6 hours", page: false, started: null, ended: null });
const detail = (over: Record<string, unknown> = {}) => ({ id: "dependency-check", name: "Dependency check", state: "on", builtin: null, version: 3,
  status: { line: "Next: tomorrow at 07:00", actions: ["run", "pause"], run: null, spinning: false }, definition,
  definition_line: "Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min", runs: [run(2), skipped], more: false, ...over });
const runDetail = (over: Record<string, unknown> = {}) => ({ ...run(2), agent: { id: "dependency-check", name: "Dependency check" }, model: "Swift",
  limit: 20, brief: "Check whether any **dependency** has a security fix.", report: "Two packages have security fixes:\n\n- `left-pad` 1.3.1\n- `is-odd` 3.0.2",
  session: { engine: "alpha", session_id: "0123456789abcdef", attempt: 0 }, ...over });
const transcript = { project: "harbor", slug: "dependency-check/runs/2", engine: "alpha", session_id: "0123456789abcdef", attempt: 0, cursor: "epoch:1",
  lower: "", next: "", more: false, reset: false, has_earlier: false, has_engine_records: false, deleted: [], redaction: "",
  events: [{ seq: 0, id: "r-0", order: "00000000", version: 1, source: "alpha", kind: "message", type: "assistant", role: "assistant", at: ago(5),
    text: "Reading the lock file." }] };

type Reply = Response | Promise<Response>;
/** The server: reads from mutable state; every press is recorded and answered by `press`. */
function serve(options: { agents?: () => Reply; agent?: (runs: number) => Reply; run?: () => Reply; press?: (body: Record<string, unknown>) => Reply } = {}) {
  const state = { agents: list(), agent: detail(), run: runDetail() };
  const presses: Record<string, unknown>[] = [];
  const reads: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    reads.push(url);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/project/harbor") return json({ name: "harbor", tasks: [] });
    if (url === "/api/agents" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      presses.push(body);
      return options.press ? options.press(body) : json({ ok: true, agent: "dependency-check" });
    }
    if (url === "/api/agents/harbor") return options.agents ? options.agents() : json(state.agents);
    const one = url.match(/^\/api\/agents\/harbor\/dependency-check\?runs=(\d+)$/);
    if (one) return options.agent ? options.agent(Number(one[1])) : json(state.agent);
    if (url.startsWith("/api/agents/harbor/dependency-check/runs/2/transcript?")) return json(transcript);
    if (url === "/api/agents/harbor/dependency-check/runs/2") return options.run ? options.run() : json(state.run);
    return json({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, presses, reads };
}

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

const back = () => document.querySelector<HTMLAnchorElement>("a.agents-back")!;
const without = ({ request_id: _, ...rest }: Record<string, unknown>) => rest;

afterEach(() => setViewport(1024));

describe.each([390, 1440])("project agents at %ipx", (width) => {
  it("lists agents with their triggers and news, the budget, New agent, the retired count and built-ins", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents" });
    const row = await screen.findByRole("link", { name: /^Dependency check/ });
    expect(row).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    expect(row).toHaveTextContent("Weekdays at 07:00 · Found something 2 h ago");
    expect(screen.getByRole("link", { name: /^Review each merge/ })).toHaveTextContent("When you run it · Running, 2 of 20 min");
    expect(screen.getByRole("link", { name: /^Release notes/ })).toHaveTextContent("Fridays at 17:00 · Paused");
    expect(back()).toHaveTextContent("harbor");
    expect(back()).toHaveAttribute("href", "/projects/harbor?tab=work");
    expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByText("Each one runs a brief when its trigger fires, and tells you what it found.")).toBeInTheDocument();
    const budget = screen.getByRole("combobox", { name: /Agents may use up to/ });
    expect(within(budget).getAllByRole("option").map((o) => o.textContent)).toEqual(["30 min a week", "1 h a week", "2 h a week", "5 h a week", "10 h a week"]);
    expect(budget).toHaveDisplayValue("2 h a week");
    expect(screen.getByText("1 h 10 min used this week")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New agent" })).toHaveAttribute("href", "/projects/harbor/agents/new");
    expect(screen.getByText("Mondays at 08:00 · Runs the project's checks on a clean copy and says what broke")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Old audit/ })).toBeNull();
  });
});

describe("Agents page", () => {
  it("shows Loading agents… then, when the read fails, Could not load agents. with Retry", async () => {
    let fail = true;
    const hold = deferred();
    let first = true;
    serve({ agents: () => { if (first) { first = false; return hold.promise; } return fail ? json({ error: "down" }, 500) : json(list()); } });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("status", { name: "Loading agents…" })).toBeInTheDocument();
    hold.resolve(json({ error: "down" }, 500));
    expect(await screen.findByText("Could not load agents.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: /^Dependency check/ })).toBeInTheDocument();
  });

  it("with no agents of its own holds only the budget and New agent, then every built-in", async () => {
    const { state } = serve();
    state.agents = list({ agents: [], retired: [] });
    renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("link", { name: "New agent" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Dependency check/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /retired/ })).toBeNull();
    expect(screen.getByRole("heading", { name: "Built in" })).toBeInTheDocument();
  });

  it("opens the retired agents behind their count", async () => {
    serve();
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const toggle = await screen.findByRole("button", { name: "1 retired" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("heading", { name: "Retired agents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Old audit/ })).toHaveTextContent("Retired Oct 19 · 23 runs");
  });

  it("budget: the choice shows at once with Saving…, a refusal returns to the saved value with Retry", async () => {
    const hold = deferred();
    const { presses } = serve({ press: () => hold.promise });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const budget = await screen.findByRole("combobox", { name: /Agents may use up to/ });
    await user.selectOptions(budget, "300");
    expect(budget).toHaveDisplayValue("5 h a week");
    expect(budget).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "budget", minutes: 300 });
    hold.resolve(json({ error: "The budget changed in another window." }, 409));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: The budget changed in another window. Retry");
    expect(budget).toHaveDisplayValue("2 h a week");
    expect(budget).toBeEnabled();
  });

  it("budget spent reads in amber that agents wait until Monday", async () => {
    const { state } = serve();
    state.agents = list({ budget: { minutes: 120, choices: [30, 60, 120, 300, 600], used: 120, spent: true } });
    renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByText("2 h used this week · agents wait until Monday")).toHaveClass("agents-spent");
  });

  it("Turn on spins until the agent reads as on and its row leaves Built in; a refusal returns the button with why", async () => {
    let refuse = true;
    const { state, presses } = serve({ press: async () => {
      if (refuse) return json({ error: "Not turned on: the agents changed in another window." }, 409);
      state.agents = list({ builtins: [], agents: [...list().agents, summary({ id: "clean-checks", name: "Clean checks", triggers: "Mondays at 08:00", news: "Never run", news_at: null })] });
      return json({ ok: true, agent: "clean-checks" });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    await user.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not turned on: the agents changed in another window.");
    expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled();
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("link", { name: /^Clean checks/ })).toHaveTextContent("Mondays at 08:00 · Never run");
    expect(screen.queryByRole("heading", { name: "Built in" })).toBeNull();
    expect(presses.map(without)).toEqual([{ project: "harbor", action: "builtin", key: "checks" }, { project: "harbor", action: "builtin", key: "checks" }]);
  });

  it("adds the machine's zone after schedule times when the browser's clock differs", async () => {
    const { state } = serve();
    state.agents = list({ clock: { zone: "Istanbul", offset: here() + 60 } });
    renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("link", { name: /^Dependency check/ })).toHaveTextContent("Weekdays at 07:00 Istanbul · Found something 2 h ago");
    expect(screen.getByText("Mondays at 08:00 Istanbul · Runs the project's checks on a clean copy and says what broke")).toBeInTheDocument();
  });
});

describe.each([390, 1440])("one agent at %ipx", (width) => {
  it("shows its status, recent runs, what it does with Edit, and Retire", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Next: tomorrow at 07:00")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Dependency check" })).toBeInTheDocument();
    expect(back()).toHaveTextContent("harbor");
    expect(back()).toHaveAttribute("href", "/projects/harbor/agents");
    expect(screen.getByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    const found = screen.getByRole("link", { name: /^Found something/ });
    expect(found).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/runs/2");
    expect(found).toHaveTextContent(`Found something${runTime(run(2).at)}6 min · 4.1M tokens2 packages have security fixes.`);
    const skip = screen.getByText("No free slot for 6 hours");
    expect(skip.closest("a")).toBeNull();
    expect(skip.closest(".agent-run")!.querySelector(".agents-chevron")).toBeNull();
    expect(screen.getByText("Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/edit");
    expect(screen.getByText("It won’t run again. Past runs stay readable.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retire…" })).toHaveClass("btn-outline-danger");
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();
  });
});

describe("One agent", () => {
  it("shows Loading this agent… then Could not load this agent. with Retry", async () => {
    let fail = true;
    const hold = deferred();
    let first = true;
    serve({ agent: () => { if (first) { first = false; return hold.promise; } return fail ? json({ error: "down" }, 500) : json(detail()); } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByRole("status", { name: "Loading this agent…" })).toBeInTheDocument();
    hold.resolve(json({ error: "down" }, 500));
    expect(await screen.findByText("Could not load this agent.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Next: tomorrow at 07:00")).toBeInTheDocument();
  });

  it("new: Not on yet with Run now and Turn on, which answers at once", async () => {
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.agent = detail({ state: "new", status: { line: "Not on yet", actions: ["run", "turn_on"], run: null, spinning: false }, runs: [] });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Not on yet")).toBeInTheDocument();
    expect(screen.getByText("No runs yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    expect(screen.getByText("Turning on…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "turn_on", agent: "dependency-check" });
    state.agent = detail();
    hold.resolve(json({ ok: true, agent: "dependency-check", state: "on" }));
    expect(await screen.findByText("Next: tomorrow at 07:00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
  });

  it("Run now says Starting… until the run is read, then Running with Open, Stop and Pause", async () => {
    const { state, presses } = serve({ press: () => json({ ok: true, agent: "dependency-check", n: 3 }) });
    const { user, queryClient } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Starting…" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "run", agent: "dependency-check" });
    state.agent = detail({ status: { line: "Running · 0 of 20 min", actions: ["open", "stop", "pause"], run: 3, spinning: true },
      runs: [run(3, { state: "running", outcome: "Running", ended: null, minutes: 0, tokens: null, line: null }), run(2), skipped] });
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "harbor", "agents"] }));
    expect(await screen.findByText(/Running · 0 of 20 min/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/runs/3");
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
  });

  it("a refused Run now returns with its reason", async () => {
    serve({ press: () => json({ error: "Only 12 min of this week's budget is left." }, 409) });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/^Only 12 min of this week's budget is left\.$/);
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("waiting: its line with Stop, which says Stopping… until the end is read while Pause stays available", async () => {
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.agent = detail({ status: { line: "Waiting for a free task slot", actions: ["stop", "pause"], run: 3, spinning: false } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    expect(screen.getByText("Waiting for a free task slot")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "stop", agent: "dependency-check", n: 3 });
    state.agent = detail({ runs: [run(3, { state: "stopped", outcome: "Stopped by you", tone: "muted", line: null }), run(2), skipped] });
    hold.resolve(json({ ok: true, agent: "dependency-check", n: 3 }));
    expect(await screen.findByText("Stopped by you")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("Pause changes the line and buttons at once; a refusal returns them with Not saved: and Retry", async () => {
    let refuse = true;
    const { state, presses } = serve({ press: async () => {
      if (refuse) return json({ error: "Dependency check changed in another window." }, 409);
      state.agent = detail({ state: "paused", status: { line: "Paused", actions: ["resume", "run"], run: null, spinning: false } });
      return json({ ok: true, agent: "dependency-check", state: "paused" });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Dependency check changed in another window. Retry");
    expect(screen.getByText("Next: tomorrow at 07:00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getAllByRole("button").filter((b) => b.closest(".agent-status-actions")).map((b) => b.textContent)).toEqual(["Resume", "Run now"]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled());
    expect(presses.map((p) => p.action)).toEqual(["pause", "pause"]);
  });

  it("paused itself: its reason in the line", async () => {
    const { state } = serve();
    state.agent = detail({ state: "paused", status: { line: "Paused itself after 3 runs in a row couldn't finish", actions: ["resume", "run"], run: null, spinning: false } });
    renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Paused itself after 3 runs in a row couldn't finish")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("a late run says when it ran; Show older reads ten more, keeping the loaded runs", async () => {
    const older = deferred();
    const late = run(2, { late: true, started: ago(60 * 48 - 134) });
    serve({ agent: (runs) => runs === 10 ? json(detail({ runs: [late], more: true }))
      : older.promise });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    const row = await screen.findByRole("link", { name: /^Found something/ });
    expect(row).toHaveTextContent(`${runTime(late.at)}, ran at`);
    await user.click(screen.getByRole("button", { name: "Show older" }));
    expect(screen.getByRole("status", { name: "Loading older runs…" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Found something/ })).toBeInTheDocument();
    older.resolve(json(detail({ runs: [late, ...Array.from({ length: 10 }, (_, i) => run(i + 3, { outcome: "Nothing found", state: "nothing", tone: "muted", line: null }))], more: false })));
    await waitFor(() => expect(screen.getAllByText("Nothing found")).toHaveLength(10));
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();
  });

  it("clamps a long brief with Show all", async () => {
    const tall = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(200);
    serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Show all" }));
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
    expect(screen.getByText("Check whether any dependency has a security fix.").closest(".agent-brief")).not.toHaveAttribute("data-clamped");
    tall.mockRestore();
  });

  it("Retire: Cancel first and focused; Retiring… holds both buttons; a refusal shows under them and Cancel works", async () => {
    let answer = deferred();
    const { state, presses } = serve({ press: () => answer.promise });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Retire…" }));
    const dialog = screen.getByRole("dialog", { name: "Retire Dependency check?" });
    expect(dialog).toHaveTextContent("It won’t run again, from its triggers or Run now. Its past runs stay readable under Retired.");
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Retire"]);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus());
    await user.click(within(dialog).getByRole("button", { name: "Retire" }));
    expect(within(dialog).getByRole("button", { name: "Retiring…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    answer.resolve(json({ error: "Dependency check is running." }, 409));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Not retired: Dependency check is running.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    answer = deferred();
    await user.click(screen.getByRole("button", { name: "Retire…" }));
    await user.click(screen.getByRole("button", { name: "Retire" }));
    state.agent = detail({ state: "retired", status: { line: "Retired Oct 19", actions: [], run: null, spinning: false } });
    answer.resolve(json({ ok: true, agent: "dependency-check", state: "retired" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Retired Oct 19")).toBeInTheDocument();
    expect(presses.map(without)).toEqual([{ project: "harbor", action: "retire", agent: "dependency-check" }, { project: "harbor", action: "retire", agent: "dependency-check" }]);
  });

  it("retired: title, Retired Oct 19, runs and the definition as text, with no actions, Edit or Retire", async () => {
    const { state } = serve();
    state.agent = detail({ state: "retired", status: { line: "Retired Oct 19", actions: [], run: null, spinning: false } });
    renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Retired Oct 19")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Found something/ })).toBeInTheDocument();
    expect(screen.getByText("Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run now" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retire…" })).toBeNull();
  });
});

describe.each([390, 1440])("one run at %ipx", (width) => {
  it("ended: the outcome and report, then Brief it ran and Session folded, the session read from under its agent", async () => {
    setViewport(width);
    const { reads } = serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByRole("heading", { level: 1, name: `Run · ${runTime(run(2).at)}` })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dependency check" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    expect(screen.getByText("On its schedule · Swift · 6 min · 4.1M tokens")).toBeInTheDocument();
    expect(screen.getByText("Found something")).toBeInTheDocument();
    expect(screen.getByText("Two packages have security fixes:")).toBeInTheDocument();
    expect(screen.getByText("left-pad").tagName).toBe("CODE");
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("Brief it ran").closest("details")).not.toHaveAttribute("open");
    expect(reads.some((url) => url.includes("/transcript"))).toBe(false);
    await user.click(screen.getByText("Session"));
    expect(await screen.findByText("Reading the lock file.")).toBeInTheDocument();
    const read = reads.find((url) => url.includes("/transcript"))!;
    expect(read).toMatch(/^\/api\/agents\/harbor\/dependency-check\/runs\/2\/transcript\?/);
    expect(new URL(read, "http://x").searchParams.get("session_id")).toBe("0123456789abcdef");
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

describe("One run", () => {
  it("running: Running · 4 of 20 min with Stop, then the live session; Stopping… until the end is read", async () => {
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.run = runDetail({ state: "running", outcome: "Running", ended: null, minutes: 4, tokens: null, line: null, report: null });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("Running · 4 of 20 min")).toBeInTheDocument();
    expect(screen.getByText("On its schedule · Swift")).toBeInTheDocument();
    expect(await screen.findByText("Reading the lock file.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "stop", agent: "dependency-check", n: 2 });
    state.run = runDetail({ state: "stopped", outcome: "Stopped by you", tone: "muted", report: null, line: null });
    hold.resolve(json({ ok: true, agent: "dependency-check", n: 2 }));
    expect(await screen.findByText("Stopped by you")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();
  });

  it("couldn't finish: the reason in the report's place", async () => {
    const { state } = serve();
    state.run = runDetail({ state: "failed", outcome: "Couldn't finish", tone: "danger", report: null, line: "`make audit` is not a target." });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("Couldn't finish")).toBeInTheDocument();
    expect(screen.getByText("make audit")).toBeInTheDocument();
    expect(document.querySelector(".agent-report .dot")).toHaveAttribute("data-state", "danger");
  });

  it("shows Loading this run… then Could not load this run. with Retry", async () => {
    let fail = true;
    const hold = deferred();
    let first = true;
    serve({ run: () => { if (first) { first = false; return hold.promise; } return fail ? json({ error: "down" }, 500) : json(runDetail()); } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByRole("status", { name: "Loading this run…" })).toBeInTheDocument();
    hold.resolve(json({ error: "down" }, 500));
    expect(await screen.findByText("Could not load this run.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Two packages have security fixes:")).toBeInTheDocument();
  });
});

describe.each([390, 1440])("the form at %ipx", (width) => {
  it("a new agent starts on weekdays at 07:00, telling when it finds something, Default model, 20 min", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents/new" });
    expect(await screen.findByRole("heading", { level: 1, name: "New agent" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(screen.getByText("Write it as you would brief a task. It can use anything in the repository.")).toBeInTheDocument();
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    expect(within(schedule).getAllByRole("button", { pressed: true }).map((b) => b.textContent)).toEqual(["M", "T", "W", "T", "F"]);
    expect(within(schedule).getAllByRole("button", { pressed: false }).map((b) => b.getAttribute("aria-label"))).toEqual(["Saturday", "Sunday"]);
    expect(within(schedule).getByLabelText("Time")).toHaveValue("07:00");
    expect(within(schedule).queryByText("Istanbul")).toBeNull();
    expect(screen.getByRole("radio", { name: "When it finds something" })).toBeChecked();
    expect(screen.getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["When it finds something", "Every run", "Never"]);
    expect(screen.getByRole("button", { name: /^Model and effort for this agent: Default/ })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Each run stops after" })).toHaveDisplayValue("20 min");
    expect(screen.getByRole("button", { name: "Create agent" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });
});

describe("The form", () => {
  it("incomplete: Create agent focuses the first empty field and says Give it a name and a brief.", async () => {
    const { presses } = serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/new" });
    await user.click(await screen.findByRole("button", { name: "Create agent" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give it a name and a brief.");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Build time");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(screen.getByRole("textbox", { name: "What it does" })).toHaveFocus();
    expect(presses).toEqual([]);
  });

  it("no day: says Pick at least one day. and Save focuses its first day; removing the last schedule leaves Only when you run it", async () => {
    const { presses } = serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/new" });
    await user.type(await screen.findByRole("textbox", { name: "Name" }), "Build time");
    await user.type(screen.getByRole("textbox", { name: "What it does" }), "Say whether the build got slower.");
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) await user.click(within(schedule).getByRole("button", { name: day }));
    expect(within(schedule).getByText("Pick at least one day.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(within(schedule).getByRole("button", { name: "Monday" })).toHaveFocus();
    expect(presses).toEqual([]);
    await user.click(within(schedule).getByRole("button", { name: "Remove this trigger" }));
    expect(screen.getByText("Only when you run it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ Add a schedule" }));
    expect(within(screen.getByRole("group", { name: "Schedule 1" })).getAllByRole("button", { pressed: true })).toHaveLength(5);
  });

  it("creates the agent with the fields set and opens its page", async () => {
    const hold = deferred();
    const { presses } = serve({ press: () => hold.promise });
    const { user, router } = renderApp({ route: "/projects/harbor/agents/new" });
    await user.type(await screen.findByRole("textbox", { name: "Name" }), " Build time ");
    await user.type(screen.getByRole("textbox", { name: "What it does" }), "Say whether the build got slower.");
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    await user.click(within(schedule).getByRole("button", { name: "Saturday" }));
    fireEvent.change(within(schedule).getByLabelText("Time"), { target: { value: "06:30" } });
    await user.click(screen.getByRole("radio", { name: "Every run" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Each run stops after" }), "60");
    await user.click(screen.getByRole("button", { name: /^Model and effort for this agent/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Swift" }));
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "create", definition: { name: "Build time",
      brief: "Say whether the build got slower.", schedules: [{ days: [0, 1, 2, 3, 4, 5], time: "06:30" }], tell: "every",
      model: { engine: "alpha", model: "swift" }, limit: 60 } });
    hold.resolve(json({ ok: true, agent: "dependency-check" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("edit: saves with the version it opened on; a refusal keeps the edit with Not saved: and Retry", async () => {
    let refuse = true;
    const { presses } = serve({ press: () => refuse ? json({ error: "Not saved: Dependency check changed in another window." }, 409)
      : json({ ok: true, agent: "dependency-check" }) });
    const { user, router } = renderApp({ route: "/projects/harbor/agents/dependency-check/edit" });
    const brief = await screen.findByRole("textbox", { name: "What it does" });
    expect(screen.getByRole("heading", { level: 1, name: "Edit" })).toBeInTheDocument();
    expect(back()).toHaveTextContent("Dependency check");
    expect(back()).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    expect(brief).toHaveValue(definition.brief);
    await user.type(brief, " Name the packages.");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Dependency check changed in another window. Retry");
    expect(brief).toHaveValue(`${definition.brief} Name the packages.`);
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "set", agent: "dependency-check", version: 3,
      definition: { ...definition, brief: `${definition.brief} Name the packages.` } });
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check"));
  });

  it("unsaved: leaving asks Discard your changes? with Keep editing focused", async () => {
    serve();
    const { user, router } = renderApp({ route: "/projects/harbor/agents/dependency-check/edit" });
    await user.type(await screen.findByRole("textbox", { name: "Name" }), "!");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    const dialog = screen.getByRole("dialog", { name: "Discard your changes?" });
    expect(dialog).toHaveTextContent("Your edits to Dependency check haven’t been saved.");
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["Keep editing", "Discard"]);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Keep editing" })).toHaveFocus());
    await user.click(within(dialog).getByRole("button", { name: "Keep editing" }));
    expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check/edit");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Dependency check!");
    await user.click(screen.getByRole("link", { name: "Dependency check" }));
    await user.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents/dependency-check"));
  });

  it("leaves without asking when nothing changed", async () => {
    serve();
    const { user, router } = renderApp({ route: "/projects/harbor/agents/new" });
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/harbor/agents"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("?offer fills the form from L3's offer and the save names it; the zone follows the time when the clocks differ", async () => {
    const offer = { id: "offer-1", turn_id: "turn-1", kind: "new", agent: null, agent_name: null, line: "Weekdays at 06:00 · Tells you every run",
      definition: { ...definition, name: "Build time", brief: "Say whether the build got slower.", schedules: [{ days: [0], time: "06:00" }], tell: "every" },
      changes: [], on: false, state: "open", reason: null, result_agent: null };
    const { state, presses } = serve();
    state.agents = list({ offers: [offer], clock: { zone: "Istanbul", offset: here() + 60 } });
    const { user } = renderApp({ route: "/projects/harbor/agents/new?offer=offer-1" });
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue("Build time");
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    expect(within(schedule).getByLabelText("Time")).toHaveValue("06:00");
    expect(within(schedule).getByText("Istanbul")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Every run" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(presses).toHaveLength(1));
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "create", definition: offer.definition, offer: "offer-1" });
  });
});
