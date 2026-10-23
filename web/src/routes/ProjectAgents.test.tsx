import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { runTime } from "../data/projectAgents";
import { clockText } from "../data/observed";

/* The routine pages against a fictional server (design/wireframes/project-agents/PROPOSAL.md, "States"). */

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const here = () => -new Date().getTimezoneOffset();

const overview = {
  projects: [{ name: "harbor", managed: true }], queue: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: null, known: false, stale: false, at: null, reserve: 90 }],
  new_tasks: { models: [{ engine: "alpha", model: "swift", label: "Swift" }], efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }] },
    value: null, only: [] },
};
const definition = { name: "Dependency check", brief: "Check whether any dependency has a security fix.", schedules: [{ days: [0, 1, 2, 3, 4], time: "07:00" }],
  events: false, tell: "found", model: null, limit: 20, may: [], commands: [] };
const summary = (over: Record<string, unknown> = {}) => ({ id: "dependency-check", name: "Dependency check", state: "on", triggers: "Weekdays at 07:00",
  news: "Found something", news_at: ago(120), cue: null, retired_line: null, may: null, ...over });
const builtin = { key: "checks", name: "Clean checks", line: "Mondays at 08:00 · Runs the project's checks on a clean copy and says what broke" };
const list = (over: Record<string, unknown> = {}) => ({ clock: { zone: "Istanbul", offset: here() },
  budget: { minutes: 120, choices: [30, 60, 120, 300, 600], used: 70, spent: false },
  agents: [summary(), summary({ id: "merge-review", name: "Review each merge", triggers: "Only when you run it", news: "Running, 2 of 20 min", news_at: null, cue: "running" }),
    summary({ id: "release-notes", name: "Release notes", triggers: "Fridays at 17:00", news: "Paused", news_at: null, cue: "paused", state: "paused" })],
  retired: [summary({ id: "old-audit", name: "Old audit", state: "retired", retired_line: "Retired Oct 19 · 23 runs" })],
  builtins: [builtin], offers: [], ...over });
const run = (n: number, over: Record<string, unknown> = {}) => ({ n, state: "found", outcome: "Found something", tone: "accent", at: ago(60 * 24 * n),
  started: ago(60 * 24 * n), late: false, ended: ago(60 * 24 * n - 6), minutes: 6, tokens: 4_100_000, line: "2 packages have security fixes.", page: true,
  trigger_line: "On its schedule", events: [], ...over });
const skipped = run(1, { state: "skipped", outcome: "Skipped", tone: "muted", minutes: null, tokens: null, line: "Didn't start within 6 hours", page: false, started: null, ended: null });
const detail = (over: Record<string, unknown> = {}) => ({ id: "dependency-check", name: "Dependency check", state: "on", builtin: null, version: 3,
  status: { line: "Next: tomorrow at 07:00", note: null, actions: ["run", "pause"], run: null, spinning: false }, definition,
  definition_line: "Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min", may_lines: [], running_commands: [], runs: [run(2), skipped], more: false, send: null, ...over });
const runDetail = (over: Record<string, unknown> = {}) => ({ ...run(2), agent: { id: "dependency-check", name: "Dependency check" }, model: "Swift",
  limit: 20, brief: "Check whether any **dependency** has a security fix.", report: "Two packages have security fixes:\n\n- `left-pad` 1.3.1\n- `is-odd` 3.0.2",
  session: { engine: "alpha", session_id: "0123456789abcdef", attempt: 0 }, suggested: null, changes: [], undo_all: false, ...over });
const kept = (n: number, title: string) => ({ ref: `agent:dependency-check/2/${n}`, n, title, explanation: "Bump it to the fixed release and run the tests.",
  seen: [], count: null, state: "undecided", task: null, undo: false, opening: false, waits: true });
const suggested = (over: Record<string, unknown> = {}) => ({ kind: "agent", id: "agent:dependency-check/2", agent: "dependency-check", n: 2,
  name: "Dependency check", line: "On its schedule", at: ago(60 * 48), posted: ago(60 * 47), checking: 0,
  suggestions: [kept(1, "Bump left-pad to 1.3.1")], set_aside: [{ n: 2, title: "Bump is-odd to 3.0.2", reason: "#214 already bumps it" }],
  unfinished: false, ...over });
const transcript = { project: "harbor", slug: "dependency-check/runs/2", engine: "alpha", session_id: "0123456789abcdef", attempt: 0, cursor: "epoch:1",
  lower: "", next: "", more: false, reset: false, has_earlier: false, has_engine_records: false, deleted: [], redaction: "",
  events: [{ seq: 0, id: "r-0", order: "00000000", version: 1, source: "alpha", kind: "message", type: "assistant", role: "assistant", at: ago(5),
    text: "Reading the lock file." }] };

type Reply = Response | Promise<Response>;
/** The server: reads from mutable state; every press is recorded and answered by `press`. */
function serve(options: { agents?: () => Reply; agent?: (runs: number) => Reply; run?: () => Reply; press?: (body: Record<string, unknown>) => Reply;
  improve?: () => Reply } = {}) {
  const state = { agents: list(), agent: detail(), run: runDetail() };
  const presses: Record<string, unknown>[] = [];
  const reads: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    reads.push(url);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/project/harbor") return json({ name: "harbor", tasks: [] });
    if (url === "/api/improve") {
      presses.push(JSON.parse(String(init!.body)));
      return options.improve ? options.improve() : json({ ok: true });
    }
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

describe.each([390, 1440])("routines at %ipx", (width) => {
  it("lists routines with their triggers and news, the budget, New routine, the retired count and built-ins", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents" });
    const row = await screen.findByRole("link", { name: /^Dependency check/ });
    expect(row).toHaveAttribute("href", "/projects/harbor/agents/dependency-check");
    expect(row).toHaveTextContent("Weekdays at 07:00 · Found something 2 h ago");
    expect(screen.getByRole("link", { name: /^Review each merge/ })).toHaveTextContent("Only when you run it · Running, 2 of 20 min");
    expect(screen.getByRole("link", { name: /^Release notes/ })).toHaveTextContent("Fridays at 17:00 · Paused");
    expect(back()).toHaveTextContent("harbor");
    expect(back()).toHaveAttribute("href", "/projects/harbor?tab=work");
    expect(screen.getByRole("heading", { name: "Routines" })).toBeInTheDocument();
    expect(screen.getByText("Each one runs a brief when its trigger fires, and tells you what it found.")).toBeInTheDocument();
    const budget = screen.getByRole("combobox", { name: /Routines may use up to/ });
    expect(within(budget).getAllByRole("option").map((o) => o.textContent)).toEqual(["30 min a week", "1 h a week", "2 h a week", "5 h a week", "10 h a week"]);
    expect(budget).toHaveDisplayValue("2 h a week");
    expect(screen.getByText("1 h 10 min used this week")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New routine" })).toHaveAttribute("href", "/projects/harbor/agents/new");
    expect(screen.getByText("Mondays at 08:00 · Runs the project's checks on a clean copy and says what broke")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Old audit/ })).toBeNull();
  });
});

describe("Routines page", () => {
  it("shows Loading routines… then, when the read fails, Could not load routines. with Retry", async () => {
    let fail = true;
    const hold = deferred();
    let first = true;
    serve({ agents: () => { if (first) { first = false; return hold.promise; } return fail ? json({ error: "down" }, 500) : json(list()); } });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("status", { name: "Loading routines…" })).toBeInTheDocument();
    hold.resolve(json({ error: "down" }, 500));
    expect(await screen.findByText("Could not load routines.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: /^Dependency check/ })).toBeInTheDocument();
  });

  it("with no routines of its own holds only the budget and New routine, then every built-in", async () => {
    const { state } = serve();
    state.agents = list({ agents: [], retired: [] });
    renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("link", { name: "New routine" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Dependency check/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /retired/ })).toBeNull();
    expect(screen.getByRole("heading", { name: "Built in" })).toBeInTheDocument();
  });

  it("opens the retired routines behind their count", async () => {
    serve();
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const toggle = await screen.findByRole("button", { name: "1 retired" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("heading", { name: "Retired routines" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Old audit/ })).toHaveTextContent("Retired Oct 19 · 23 runs");
  });

  it("budget: the choice shows at once with Saving…, a refusal returns to the saved value with Retry", async () => {
    const hold = deferred();
    const { presses } = serve({ press: () => hold.promise });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const budget = await screen.findByRole("combobox", { name: /Routines may use up to/ });
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

  it("a spent budget says so in amber under the intro, and its used line turns amber", async () => {
    const { state } = serve();
    state.agents = list({ budget: { minutes: 120, choices: [30, 60, 120, 300, 600], used: 120, spent: true } });
    renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByText("This week’s budget is used: no new runs until Monday unless you raise it.")).toHaveClass("agents-spent");
    expect(screen.getByText("2 h used this week")).toHaveClass("agents-spent");
  });

  it("Turn on spins until the routine reads as on and its row leaves Built in; a refusal returns the button with why", async () => {
    let refuse = true;
    const { state, presses } = serve({ press: async () => {
      if (refuse) return json({ error: "Not turned on: the routines changed in another window." }, 409);
      state.agents = list({ builtins: [], agents: [...list().agents, summary({ id: "clean-checks", name: "Clean checks", triggers: "Mondays at 08:00", news: "Never run", news_at: null })] });
      return json({ ok: true, agent: "clean-checks" });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    await user.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not turned on: the routines changed in another window.");
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

describe.each([390, 1440])("one routine at %ipx", (width) => {
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
    const skip = screen.getByText("Didn't start within 6 hours");
    expect(skip.closest("a")).toBeNull();
    expect(skip.closest(".agent-run")!.querySelector(".agents-chevron")).toBeNull();
    expect(screen.getByText("Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/projects/harbor/agents/dependency-check/edit");
    expect(screen.getByRole("button", { name: "Retire…" })).toHaveClass("btn-outline-danger");
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();
  });
});

describe("One routine", () => {
  it("shows Loading this routine… then Could not load this routine. with Retry", async () => {
    let fail = true;
    const hold = deferred();
    let first = true;
    serve({ agent: () => { if (first) { first = false; return hold.promise; } return fail ? json({ error: "down" }, 500) : json(detail()); } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByRole("status", { name: "Loading this routine…" })).toBeInTheDocument();
    hold.resolve(json({ error: "down" }, 500));
    expect(await screen.findByText("Could not load this routine.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Next: tomorrow at 07:00")).toBeInTheDocument();
  });

  it("new: Not on yet with Run now and Turn on, which answers at once", async () => {
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.agent = detail({ state: "new", status: { line: "Not on yet", note: null, actions: ["run", "turn_on"], run: null, spinning: false }, runs: [] });
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
    state.agent = detail({ status: { line: "Running · 0 of 20 min", note: null, actions: ["open", "stop", "pause"], run: 3, spinning: true },
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
    state.agent = detail({ status: { line: "Waiting to start", note: null, actions: ["stop", "pause"], run: 3, spinning: false } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    expect(screen.getByText("Waiting to start")).toBeInTheDocument();
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
      state.agent = detail({ state: "paused", status: { line: "Paused", note: null, actions: ["resume", "run"], run: null, spinning: false } });
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

  it("Pause during a run keeps its line, Open and Stop, held until the server's answer is read", async () => {
    const hold = deferred();
    const { state } = serve({ press: () => hold.promise });
    state.agent = detail({ status: { line: "Running · 4 of 20 min", note: null, actions: ["open", "stop", "pause"], run: 3, spinning: true } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(screen.getByText(/Running · 4 of 20 min/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    state.agent = detail({ state: "paused", status: { line: "Running · 4 of 20 min · Paused after this run", note: null, actions: ["open", "stop", "resume"], run: 3, spinning: true } });
    hold.resolve(json({ ok: true, agent: "dependency-check", state: "paused" }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("paused itself: its reason in the line", async () => {
    const { state } = serve();
    state.agent = detail({ state: "paused", status: { line: "Paused itself after 3 runs in a row couldn't finish", note: null, actions: ["resume", "run"], run: null, spinning: false } });
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
    state.agent = detail({ state: "retired", status: { line: "Retired Oct 19", note: null, actions: [], run: null, spinning: false } });
    answer.resolve(json({ ok: true, agent: "dependency-check", state: "retired" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Retired Oct 19")).toBeInTheDocument();
    expect(presses.map(without)).toEqual([{ project: "harbor", action: "retire", agent: "dependency-check" }, { project: "harbor", action: "retire", agent: "dependency-check" }]);
  });

  it("retired: title, Retired Oct 19, runs and the definition as text, with no actions, Edit or Retire", async () => {
    const { state } = serve();
    state.agent = detail({ state: "retired", status: { line: "Retired Oct 19", note: null, actions: [], run: null, spinning: false } });
    renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Retired Oct 19")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Found something/ })).toBeInTheDocument();
    expect(screen.getByText("Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after 20 min")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run now" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retire…" })).toBeNull();
  });
  it("events: the status says what Run now reads and what waits; Send it an event shows the command, how to try it and folded examples", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const { state } = serve();
    const command = "alt --project harbor agent send 'Dependency check' \"what happened\"";
    const merges = "# crontab: */10 * * * * sh ~/bin/harbor-merges.sh\nfor n in $(gh pr list -R acme/harbor --state merged -L 5 --json number -q '.[].number'); do\n  alt --project harbor agent send dependency-check \"PR #$n merged\" --key \"pr-$n\" >/dev/null\ndone";
    state.agent = detail({ definition: { ...definition, schedules: [], events: true },
      status: { line: "Runs when it's sent an event", note: "No event yet, so Run now runs without one.", actions: ["run", "pause"], run: null, spinning: false },
      send: { command, id: "dependency-check", recipes: [{ what: "Each merged pull request: a script for cron every 10 minutes", text: merges },
        { what: "When a command of yours fails", text: "make bench >/dev/null 2>&1 || alt --project harbor agent send dependency-check \"Benchmarks failed\"" }] } });
    const { user, queryClient } = renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("Runs when it's sent an event")).toBeInTheDocument();
    expect(screen.getByText("No event yet, so Run now runs without one.")).toBeInTheDocument();
    const send = screen.getByRole("group", { name: "Send it an event" });
    expect(within(send).getByText("From a script or your terminal on this computer:")).toBeInTheDocument();
    expect(within(send).getByText(command)).toBeInTheDocument();
    expect(send).toHaveTextContent("The examples use its id, dependency-check, so they keep working after a rename.");
    expect(send).toHaveTextContent("To try it first, use agent run in place of agent send: it starts a run now, even before the routine is on.");
    expect(within(send).getByText("Send numbers and your own words, not text anyone else can write: a run has your code and the network.")).toBeInTheDocument();
    const examples = within(send).getByText("Examples").closest("details")!;
    expect(examples).not.toHaveAttribute("open");
    await user.click(within(send).getByText("Examples"));
    expect(examples).toHaveAttribute("open");
    const recipe = within(examples).getByText("Each merged pull request: a script for cron every 10 minutes").closest(".agent-recipe")!;
    expect(recipe.querySelector("pre")!.textContent).toBe(merges);
    await user.click(within(recipe as HTMLElement).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(merges);
    expect(screen.getByRole("link", { name: "Edit" }).compareDocumentPosition(send) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    state.agent = detail({ ...state.agent, status: { line: "Running · 4 of 20 min", note: "3 events wait for the next run.", actions: ["open", "stop", "pause"], run: 3, spinning: true } });
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "harbor", "agents"] }));
    expect(await screen.findByText("3 events wait for the next run.")).toBeInTheDocument();
    state.agent = detail({ ...state.agent, state: "retired", send: null, status: { line: "Retired Oct 19", note: null, actions: [], run: null, spinning: false } });
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "harbor", "agents"] }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Send it an event" })).toBeNull());
  });
});

describe.each([390, 1440])("one run at %ipx", (width) => {
  it("ended: the outcome and report, then Brief it ran and Session folded, the session read from under its routine", async () => {
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

  it("waiting: its line and Stop with no model before a worker started, and no live session", async () => {
    const { state } = serve();
    state.run = runDetail({ state: "waiting", outcome: "Waiting", tone: "muted", started: null, ended: null, minutes: null, tokens: null,
      line: "Waiting to start", report: null, model: null, session: null });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("Waiting to start")).toBeInTheDocument();
    expect(document.querySelector(".agent-run-meta")).toHaveTextContent(/^On its schedule$/);
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.queryByText("Reading the lock file.")).toBeNull();
  });

  it("couldn't finish: the reason in the report's place", async () => {
    const { state } = serve();
    state.run = runDetail({ state: "failed", outcome: "Couldn't finish", tone: "danger", report: null, line: "`make audit` is not a target." });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("Couldn't finish")).toBeInTheDocument();
    expect(screen.getByText("make audit")).toBeInTheDocument();
    expect(document.querySelector(".agent-report .dot")).toHaveAttribute("data-state", "danger");
  });

  it("events: one muted line per event, its time and first line, under what fired it", async () => {
    const { state } = serve();
    const at = new Date(Date.now() - 60 * 60_000);
    const minute = (n: number) => new Date(at.getTime() + n * 60_000).toISOString();
    state.run = runDetail({ at: minute(0), trigger_line: "For “PR #810 merged” and 2 more",
      events: [{ at: minute(0), line: "PR #810 merged" }, { at: minute(0), line: "PR #811 merged" }, { at: minute(2), line: "PR #812 merged" }] });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("For “PR #810 merged” and 2 more · Swift · 6 min · 4.1M tokens")).toBeInTheDocument();
    expect([...screen.getByLabelText("Events").querySelectorAll("p")].map((p) => p.textContent)).toEqual([
      `${clockText(minute(0))} · PR #810 merged`, `${clockText(minute(0))} · PR #811 merged`, `${clockText(minute(2))} · PR #812 merged`]);
  });

  it("suggested tasks: L3 is checking with a spinner and no buttons while it checks", async () => {
    const { state } = serve();
    state.run = runDetail({ suggested: suggested({ checking: 2, suggestions: [], set_aside: [], posted: null }) });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const section = await screen.findByRole("region", { name: "Suggested tasks" });
    expect(within(section).getByText("L3 is checking 2 suggestions")).toBeVisible();
    expect(section.querySelector(".spinner")).not.toBeNull();
    expect(within(section).queryByRole("button")).toBeNull();
  });

  it("suggested tasks: what L3 kept with its buttons, what it set aside folded with each reason, and Dismiss folding at the press", async () => {
    const { state, presses } = serve();
    state.run = runDetail({ suggested: suggested() });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const section = await screen.findByRole("region", { name: "Suggested tasks" });
    expect(within(section).getByRole("heading", { name: "1. Bump left-pad to 1.3.1" })).toBeVisible();
    const fold = within(section).getByText("Not offered · 1").closest("details")!;
    expect(fold).not.toHaveAttribute("open");
    await user.click(within(section).getByText("Not offered · 1"));
    expect(within(fold).getByText("2. Bump is-odd to 3.0.2")).toBeVisible();
    expect(within(fold).getByText("#214 already bumps it")).toBeVisible();
    state.run = runDetail({ suggested: suggested({ suggestions: [{ ...kept(1, "Bump left-pad to 1.3.1"), state: "dismissed", undo: true, waits: false }] }) });
    await user.click(within(section).getByRole("button", { name: "Dismiss" }));
    expect(within(section).getByText(/^Dismissed(?! by L3)/)).toBeVisible();
    expect(without(presses.at(-1)!)).toEqual({ project: "harbor", action: "dismiss", ref: "agent:dependency-check/2/1" });
    expect(await within(section).findByRole("button", { name: "Undo" })).toBeEnabled();
  });

  it("suggested tasks: Undo on the run page brings its card back to Needs you at the press", async () => {
    const save = deferred();
    const { state } = serve({ improve: () => save.promise });
    state.run = runDetail({ suggested: suggested({ suggestions: [{ ...kept(1, "Bump left-pad to 1.3.1"), state: "dismissed", undo: true, waits: false }] }) });
    const { user, router } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    await user.click(within(await screen.findByRole("region", { name: "Suggested tasks" })).getByRole("button", { name: "Undo" }));
    await act(async () => { await router.navigate("/"); });
    const back = within(await screen.findByRole("region", { name: "Suggestions" })).getByRole("article", { name: "Suggested tasks from Dependency check" });
    expect(within(back).getByRole("button", { name: "Dismiss" })).toBeDisabled();
    await act(async () => save.resolve(json({ error: "Altitude is busy." }, 503)));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Suggestions" })).toBeNull());
  });

  it("suggested tasks: a check that missed its deadline says why once on the fold, opening to the titles", async () => {
    const { state } = serve();
    state.run = runDetail({ suggested: suggested({ suggestions: [], unfinished: true,
      set_aside: [{ n: 1, title: "Bump left-pad to 1.3.1", reason: null }, { n: 2, title: "Bump is-odd to 3.0.2", reason: null }] }) });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const section = await screen.findByRole("region", { name: "Suggested tasks" });
    await user.click(within(section).getByText("Not offered · 2 · not checked in time"));
    expect(within(section).getByText("1. Bump left-pad to 1.3.1")).toBeVisible();
    expect(within(section).getByText("2. Bump is-odd to 3.0.2")).toBeVisible();
    expect(within(section).queryByRole("button")).toBeNull();
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
  it("a new routine starts on weekdays at 07:00, telling when it finds something, Default model, 20 min", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents/new" });
    expect(await screen.findByRole("heading", { level: 1, name: "New routine" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(screen.getByText("Write it as you would brief a task. It can use anything in the repository.")).toBeInTheDocument();
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    expect(within(schedule).getAllByRole("button", { pressed: true }).map((b) => b.textContent)).toEqual(["M", "T", "W", "T", "F"]);
    expect(within(schedule).getAllByRole("button", { pressed: false }).map((b) => b.getAttribute("aria-label"))).toEqual(["Saturday", "Sunday"]);
    expect(within(schedule).getByLabelText("Time")).toHaveValue("07:00");
    expect(within(schedule).queryByText("Istanbul")).toBeNull();
    expect(screen.getByRole("radio", { name: "When it finds something" })).toBeChecked();
    expect(screen.getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["When it finds something", "Every run", "Never"]);
    expect(screen.getByText("Suggested tasks wait in Needs you whatever you choose.")).toBeVisible();
    expect(screen.getByRole("button", { name: /^Model and effort for this routine: Default/ })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Each run stops after" })).toHaveDisplayValue("20 min");
    expect(screen.getByRole("button", { name: "Create routine" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });
});

describe("The form", () => {
  it("incomplete: Create routine marks each empty field under it and focuses the first", async () => {
    const { presses } = serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/new" });
    expect(await screen.findByText("It starts off: try it with Run now, then turn it on.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveAccessibleDescription("Give it a name.");
    expect(screen.getByText("Say what it does.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Build time");
    expect(screen.queryByText("Give it a name.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    expect(screen.getByRole("textbox", { name: "What it does" })).toHaveFocus();
    expect(screen.getByText("Say what it does.")).toBeInTheDocument();
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
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    expect(within(schedule).getByRole("button", { name: "Monday" })).toHaveFocus();
    expect(presses).toEqual([]);
    await user.click(within(schedule).getByRole("button", { name: "Remove this trigger" }));
    expect(screen.getByText("Only when you run it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ Add a trigger" }));
    await user.click(screen.getByRole("menuitem", { name: "On a schedule" }));
    expect(within(screen.getByRole("group", { name: "Schedule 1" })).getAllByRole("button", { pressed: true })).toHaveLength(5);
  });

  it("+ Add a trigger: a menu until the event trigger is in the list, Escape returning focus; the event row and its hint", async () => {
    const { presses } = serve();
    const { user } = renderApp({ route: "/projects/harbor/agents/new" });
    await user.type(await screen.findByRole("textbox", { name: "Name" }), "Review each merge");
    await user.type(screen.getByRole("textbox", { name: "What it does" }), "Check each merged pull request has tests.");
    const add = screen.getByRole("button", { name: "+ Add a trigger" });
    await user.click(add);
    expect(screen.getByRole("menuitem", { name: "On a schedule" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "When it’s sent an event" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(add).toHaveFocus();
    expect(screen.queryByText("Events that arrive during a run go to its next run together.")).toBeNull();
    await user.click(add);
    await user.click(screen.getByRole("menuitem", { name: "When it’s sent an event" }));
    const row = screen.getByRole("group", { name: "When it’s sent an event" });
    expect(row).toHaveTextContent("When it’s sent an eventYou send it from this computer; its page shows how.");
    expect(within(row).getByRole("button", { name: "Remove this trigger" })).toHaveFocus();
    expect(screen.getByText("Events that arrive during a run go to its next run together.")).toBeInTheDocument();
    // With the event row in the list, the button adds a schedule at once.
    expect(add).not.toHaveAttribute("aria-haspopup");
    await user.click(within(screen.getByRole("group", { name: "Schedule 1" })).getByRole("button", { name: "Remove this trigger" }));
    expect(screen.queryByText("Only when you run it")).toBeNull();
    await user.click(add);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("group", { name: "Schedule 1" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    expect(without(presses[0]!)).toMatchObject({ action: "create", definition: { events: true, schedules: [{ days: [0, 1, 2, 3, 4], time: "07:00" }] } });
  });

  it("creates the routine with the fields set and opens its page", async () => {
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
    await user.click(screen.getByRole("button", { name: /^Model and effort for this routine/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Swift" }));
    // What it may change: a new routine only reports until its box is ticked, and the muted line goes with the tick.
    const may = screen.getByRole("checkbox", { name: "Worktrees and branches" });
    expect(may).not.toBeChecked();
    expect(may).toHaveAccessibleDescription(/^Sets aside worktrees and deletes branches nothing needs/);
    expect(screen.getByText("Nothing ticked or listed: it only reports.")).toBeInTheDocument();
    await user.click(may);
    expect(screen.queryByText("Nothing ticked or listed: it only reports.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "create", definition: { name: "Build time",
      brief: "Say whether the build got slower.", schedules: [{ days: [0, 1, 2, 3, 4, 5], time: "06:30" }], events: false, tell: "every",
      model: { engine: "alpha", model: "swift" }, limit: 60, may: ["worktrees"], commands: [] } });
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
    // Retry repeats the same press, so a save whose answer was lost is not made twice.
    expect(presses[1]!.request_id).toBe(presses[0]!.request_id);
  });

  it("edit: once the routine changed in another window, the refusal offers no Retry", async () => {
    const { state, presses } = serve({ press: () => {
      state.agent = detail({ version: 4 });
      return json({ error: "Dependency check changed in another window." }, 409);
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/edit" });
    await user.type(await screen.findByRole("textbox", { name: "What it does" }), " Name the packages.");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Dependency check changed in another window.");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry" })).toBeNull());
    expect(presses).toHaveLength(1);
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
      changes: [], may: null, on: false, state: "open", reason: null, result_agent: null };
    const { state, presses } = serve();
    state.agents = list({ offers: [offer], clock: { zone: "Istanbul", offset: here() + 60 } });
    const { user } = renderApp({ route: "/projects/harbor/agents/new?offer=offer-1" });
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue("Build time");
    const schedule = screen.getByRole("group", { name: "Schedule 1" });
    expect(within(schedule).getByLabelText("Time")).toHaveValue("06:00");
    expect(within(schedule).getByText("Istanbul")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Every run" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Create routine" }));
    await waitFor(() => expect(presses).toHaveLength(1));
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "create", definition: offer.definition, offer: "offer-1" });
  });
});

/* A routine that may change worktrees and branches (design/wireframes/project-agents/ACTING.md, "Increment 1 in detail"). */
const change = (n: number, over: Record<string, unknown> = {}) => ({ n, kind: "worktree", outcome: "done", head: "Set aside worktree",
  name: `agent-7c41e2d${n}`, note: "Removed for good Nov 9 unless you undo it.", undone: false, undo: true, refused: null, ...over });
const changes = [
  change(1),
  change(2, { kind: "branch", head: "Deleted branch", name: "upload-retry-fix", note: "Merged by #214." }),
  change(3, { outcome: "kept", head: "Kept worktree", name: "add-offline-sync-for-the-mobile-app-and-tablets", note: "It has uncommitted changes.", undo: false }),
  change(4, { kind: "branch", outcome: "refused", head: "Not allowed: delete branch", name: "docs-typo-fix", note: "This run used its 20 requests.", undo: false }),
  change(5, { outcome: "failed", head: "Couldn't set aside worktree", name: "agent-5b2e8f4c", note: "git: cannot move a locked working tree", undo: false }),
];
const busy = (button: HTMLElement) => button.querySelector("[aria-busy]") !== null;

describe.each([390, 1440])("a routine that may change things at %ipx", (width) => {
  it("the list adds what it may change after its triggers, the routine page says so, and one that only reports says that", async () => {
    setViewport(width);
    const { state } = serve();
    state.agents = list({ agents: [summary({ name: "Tidy worktrees and branches", id: "tidy", triggers: "Mondays at 09:00", may: "Changes worktrees and branches" }), summary()] });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    expect(await screen.findByRole("link", { name: /^Tidy worktrees and branches/ }))
      .toHaveTextContent("Mondays at 09:00 · Changes worktrees and branches · Found something 2 h ago");
    expect(screen.getByRole("link", { name: /^Dependency check/ })).toHaveTextContent("Weekdays at 07:00 · Found something 2 h ago");
    await user.click(screen.getByRole("link", { name: /^Dependency check/ }));
    expect(await screen.findByText("Only reports")).toHaveClass("text-muted");
    state.agent = detail({ may_lines: ["May change worktrees and branches, with Undo"] });
    await user.click(back());
    await user.click(await screen.findByRole("link", { name: /^Dependency check/ }));
    expect(await screen.findByText("May change worktrees and branches, with Undo")).not.toHaveClass("text-muted");
    expect(screen.queryByText("Only reports")).toBeNull();
  });

  it("ended: What it changed under the report, one row per request; Undo spins until the row reads as undone", async () => {
    setViewport(width);
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.run = runDetail({ changes, undo_all: true });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const card = (await screen.findByRole("heading", { name: "What it changed" })).closest("section")!;
    expect(card.compareDocumentPosition(screen.getByText("Two packages have security fixes:")) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    const rows = [...card.querySelectorAll(".agent-change")].map((row) => row.textContent);
    expect(rows).toEqual([
      "Set aside worktree agent-7c41e2d1Removed for good Nov 9 unless you undo it.Undo",
      "Deleted branch upload-retry-fixMerged by #214.Undo",
      "Kept worktree add-offline-sync-for…ile-app-and-tabletsadd-offline-sync-for-the-mobile-app-and-tabletsIt has uncommitted changes.",
      "Not allowed: delete branch docs-typo-fixThis run used its 20 requests.",
      "Couldn't set aside worktree agent-5b2e8f4cgit: cannot move a locked working tree",
    ]);
    expect(within(card).getByTitle("add-offline-sync-for-the-mobile-app-and-tablets")).toBeInTheDocument();
    const undo = within(card).getByRole("button", { name: "Undo: Set aside worktree agent-7c41e2d1" });
    await user.click(undo);
    expect(busy(undo)).toBe(true);
    expect(undo).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Undo all" })).toBeDisabled();
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "undo", agent: "dependency-check", n: 2, change: 1 });
    state.run = runDetail({ changes: [change(1, { undone: true, undo: false, note: "Put back Oct 26. This routine leaves it alone from now on." }),
      ...changes.slice(1)], undo_all: false });
    hold.resolve(json({ ok: true, agent: "dependency-check", n: 2, undone: [1], refused: [] }));
    expect(await within(card).findByText("Put back Oct 26. This routine leaves it alone from now on.")).toBeInTheDocument();
    expect(within(card).getByText(/^Set aside worktree.* · Undone$/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Undo all" })).toBeNull();
    expect(within(card).getAllByRole("button")).toHaveLength(1);
  });

  it("Undo all spins every row it puts back, then each says on its own whether it was put back", async () => {
    setViewport(width);
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.run = runDetail({ changes: changes.slice(0, 2), undo_all: true });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    await user.click(await screen.findByRole("button", { name: "Undo all" }));
    const card = screen.getByRole("heading", { name: "What it changed" }).closest("section")!;
    expect(within(card).getAllByRole("button", { name: /^Undo: / }).map(busy)).toEqual([true, true]);
    expect(busy(within(card).getByRole("button", { name: "Undo all" }))).toBe(true);
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "undo", agent: "dependency-check", n: 2, change: "all" });
    state.run = runDetail({ changes: [change(1, { undone: true, undo: false, note: "Put back Oct 26. This routine leaves it alone from now on." }),
      { ...changes[1]!, refused: "a branch with that name is already there." }], undo_all: false });
    hold.resolve(json({ ok: true, agent: "dependency-check", n: 2, undone: [1], refused: [{ n: 2, reason: "a branch with that name is already there." }] }));
    expect(await within(card).findByText("Not undone: a branch with that name is already there.")).toHaveClass("text-danger");
    const again = within(card).getByRole("button", { name: "Undo: Deleted branch upload-retry-fix" });
    expect(again).toBeEnabled();
    expect(busy(again)).toBe(false);
    expect(within(card).getByText("Put back Oct 26. This routine leaves it alone from now on.")).toBeInTheDocument();
  });
});

describe("a routine that may change things", () => {
  it("running: the card sits above the live session; a run with no requests has no card", async () => {
    const { state } = serve();
    state.run = runDetail({ state: "running", outcome: "Running", ended: null, minutes: 3, tokens: null, line: null, report: null, changes: [changes[0]] });
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const heading = await screen.findByRole("heading", { name: "What it changed" });
    const session = await screen.findByText("Reading the lock file.");
    expect(heading.compareDocumentPosition(session) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Running · 3 of 20 min")).toBeInTheDocument();
    cleanup();
    state.run = runDetail();
    renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    expect(await screen.findByText("Two packages have security fixes:")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What it changed" })).toBeNull();
  });

  it("a refused Undo says Not undone: under the card and returns every button", async () => {
    const { state } = serve({ press: () => json({ error: "That change has nothing to undo." }, 409) });
    state.run = runDetail({ changes: [changes[0]] });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    await user.click(await screen.findByRole("button", { name: "Undo: Set aside worktree agent-7c41e2d1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not undone: That change has nothing to undo.");
    expect(screen.getByRole("button", { name: "Undo: Set aside worktree agent-7c41e2d1" })).toBeEnabled();
  });

  it("Built in: Turn on shows what it may change, Cancel focused; Cancel changes nothing; Turn on turns it on; a refusal says why under the row", async () => {
    let refuse = true;
    const tidy = { key: "tidy", name: "Tidy worktrees and branches", line: "Mondays at 09:00 · Clears worktrees and branches nothing needs, with Undo",
      terms: "Mondays at 09:00 it sets aside worktrees and deletes local branches of harbor that nothing needs: no uncommitted change, nothing changed in seven days, every commit kept on a branch or merged, and nothing open in them. Each is listed on its run's page with Undo. It never touches main, open tasks or GitHub." };
    const { state, presses } = serve({ press: async () => {
      if (refuse) return json({ error: "Only one Tidy worktrees and branches." }, 409);
      state.agents = list({ builtins: [] });
      return json({ ok: true, agent: "tidy" });
    } });
    state.agents = list({ builtins: [tidy] });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const turnOn = await screen.findByRole("button", { name: "Turn on" });
    expect(turnOn).toHaveAttribute("aria-haspopup", "dialog");
    await user.click(turnOn);
    const sheet = screen.getByRole("dialog", { name: "Turn on Tidy worktrees and branches for harbor?" });
    expect(sheet).toHaveTextContent(tidy.terms);
    expect(within(sheet).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(sheet).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(presses).toHaveLength(0);
    await user.click(turnOn);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not turned on: Only one Tidy worktrees and branches.");
    expect(screen.queryByRole("dialog")).toBeNull();
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Built in" })).toBeNull());
    expect(presses.map(without)).toEqual([{ project: "harbor", action: "builtin", key: "tidy" }, { project: "harbor", action: "builtin", key: "tidy" }]);
  });
});

/* Commands you list (design/wireframes/project-agents/COMMANDS.md, "What you see"). */
const prune = { name: "prune-images", words: "podman image prune --all --force" };
const cache = { name: "clear-cache", words: "rm -r ~/.cache/harbor-build" };
const command = (n: number, over: Record<string, unknown> = {}) => ({ n, kind: "command", outcome: "done", head: "Ran", name: "prune-images",
  note: "Finished in 12 s.", tone: null, undone: false, undo: false, refused: null, stop: false, stopping: false, output: "Deleted: 3 images", ...over });
const NOTE = "These run as you, outside Altitude’s sandbox, whenever the routine decides. Altitude doesn’t check what they do, and its model reads them and what they print.";

describe.each([390, 1440])("commands you list at %ipx", (width) => {
  it("the form adds, checks, edits in place and removes commands, one draft at a time, and saves them with the routine", async () => {
    setViewport(width);
    const { state, presses } = serve();
    state.agent = detail({ definition: { ...definition, commands: [prune] } });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/edit" });
    const group = await screen.findByRole("group", { name: "Commands" });
    expect(within(group).getByText("prune-images")).toBeInTheDocument();
    expect(group).toHaveTextContent("prune-imagespodman image prune --all --force");
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(screen.queryByText("Nothing ticked or listed: it only reports.")).toBeNull();
    // A new draft hides Add a command and the other rows' buttons; Done shows what is missing.
    await user.click(within(group).getByRole("button", { name: "Add a command" }));
    const draft = within(group).getByRole("group", { name: "New command" });
    expect(within(group).queryByRole("button", { name: "Add a command" })).toBeNull();
    expect(within(group).queryByRole("button", { name: "Edit prune-images" })).toBeNull();
    expect(within(draft).getByText("Starts in harbor’s folder, with its files as they are then. Up to 10 minutes, once a run at most.")).toBeInTheDocument();
    expect(within(draft).getByRole("textbox", { name: "Name" })).toHaveFocus();
    await user.click(within(draft).getByRole("button", { name: "Done" }));
    expect(within(draft).getByRole("textbox", { name: "Name" })).toHaveAccessibleDescription("Give it a name.");
    expect(within(draft).getByRole("textbox", { name: "Command" })).toHaveAccessibleDescription("Write the command.");
    await user.type(within(draft).getByRole("textbox", { name: "Name" }), "Clear cache");
    await user.click(within(draft).getByRole("button", { name: "Done" }));
    expect(within(draft).getByRole("textbox", { name: "Name" })).toHaveAccessibleDescription("Use lowercase letters, digits and dashes.");
    await user.clear(within(draft).getByRole("textbox", { name: "Name" }));
    await user.type(within(draft).getByRole("textbox", { name: "Name" }), "prune-images");
    await user.click(within(draft).getByRole("button", { name: "Done" }));
    expect(within(draft).getByRole("textbox", { name: "Name" })).toHaveAccessibleDescription("Another command has that name.");
    // Save with a draft open saves nothing and says why.
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Finish or cancel the command first.")).toHaveClass("text-danger");
    expect(presses).toHaveLength(0);
    await user.clear(within(draft).getByRole("textbox", { name: "Name" }));
    await user.type(within(draft).getByRole("textbox", { name: "Name" }), cache.name);
    fireEvent.change(within(draft).getByRole("textbox", { name: "Command" }), { target: { value: `  ${cache.words}\n` } });
    await user.click(within(draft).getByRole("button", { name: "Done" }));
    expect(within(group).queryByRole("group", { name: "New command" })).toBeNull();
    expect(screen.queryByText("Finish or cancel the command first.")).toBeNull();
    expect(within(group).getByRole("button", { name: "Edit clear-cache" })).toHaveFocus();
    // Edit opens the row in place; Cancel puts it back as it was.
    await user.click(within(group).getByRole("button", { name: "Edit prune-images" }));
    const edit = within(group).getByRole("group", { name: "Edit prune-images" });
    expect(within(edit).getByRole("textbox", { name: "Command" })).toHaveValue(prune.words);
    expect(within(group).queryByRole("button", { name: "Remove clear-cache" })).toBeNull();
    fireEvent.change(within(edit).getByRole("textbox", { name: "Command" }), { target: { value: "podman system prune" } });
    await user.click(within(edit).getByRole("button", { name: "Cancel" }));
    expect(within(group).getByRole("button", { name: "Edit prune-images" })).toHaveFocus();
    expect(group).toHaveTextContent("podman image prune --all --force");
    await user.click(within(group).getByRole("button", { name: "Remove prune-images" }));
    expect(within(group).queryByText("prune-images")).toBeNull();
    expect(within(group).getByRole("button", { name: "Add a command" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(presses).toHaveLength(1));
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "set", agent: "dependency-check", version: 3,
      definition: { ...definition, commands: [cache] } });
  });

  it("saving says which running command it stops; at ten commands Add a command gives way to the limit", async () => {
    setViewport(width);
    const { state } = serve();
    const ten = Array.from({ length: 10 }, (_, i) => ({ name: `step-${i}`, words: `echo ${i}` }));
    state.agent = detail({ definition: { ...definition, commands: [prune, ...ten.slice(1)] }, running_commands: ["prune-images"] });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/edit" });
    const group = await screen.findByRole("group", { name: "Commands" });
    expect(within(group).queryByRole("button", { name: "Add a command" })).toBeNull();
    expect(screen.getByText("Ten commands at most.")).toBeInTheDocument();
    expect(screen.queryByText(/^Saving stops/)).toBeNull();
    await user.click(within(group).getByRole("button", { name: "Edit prune-images" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Command" }), { target: { value: "podman system prune --force" } });
    await user.click(within(group).getByRole("button", { name: "Done" }));
    expect(screen.getByText("Saving stops prune-images, which is running.")).toBeInTheDocument();
    await user.click(within(group).getByRole("button", { name: "Remove prune-images" }));
    expect(screen.getByText("Saving stops prune-images, which is running.")).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Add a command" })).toBeInTheDocument();
    expect(screen.queryByText("Ten commands at most.")).toBeNull();
  });

  it("the routine page says what it may run as you", async () => {
    setViewport(width);
    const { state } = serve();
    state.agent = detail({ may_lines: ["May change worktrees and branches, with Undo", "May run as you: prune-images and clear-cache"] });
    renderApp({ route: "/projects/harbor/agents/dependency-check" });
    expect(await screen.findByText("May run as you: prune-images and clear-cache")).not.toHaveClass("text-muted");
    expect(screen.getByText("May change worktrees and branches, with Undo")).toBeInTheDocument();
    expect(screen.queryByText("Only reports")).toBeNull();
  });

  it("a run's page lists each command request: Stop reads Stopping… until the row ends; output folds once it ended", async () => {
    setViewport(width);
    const hold = deferred();
    const { state, presses } = serve({ press: () => hold.promise });
    state.run = runDetail({ state: "running", outcome: "Running", ended: null, minutes: 3, tokens: null, line: null, report: null, changes: [
      command(1, { outcome: "running", head: "Running", note: "For 2 min.", stop: true, output: null }),
      command(2, { outcome: "failed", name: "clear-cache", note: "Exited with 1 after 3 s.", tone: "danger", output: "rm: cannot remove" }),
      command(3, { outcome: "refused", head: "Not allowed: a command", name: null, note: "The run asked for something that is not a command name.", output: null }),
    ] });
    const { user } = renderApp({ route: "/projects/harbor/agents/dependency-check/runs/2" });
    const card = (await screen.findByRole("heading", { name: "What it changed" })).closest("section")!;
    expect([...card.querySelectorAll(".agent-change")].map((row) => row.textContent)).toEqual([
      "Running prune-imagesFor 2 min.Stop",
      "Ran clear-cacheExited with 1 after 3 s.Outputrm: cannot remove",
      "Not allowed: a commandThe run asked for something that is not a command name.",
    ]);
    expect(within(card).getByText("Exited with 1 after 3 s.")).toHaveClass("text-danger");
    expect(within(card).queryByRole("button", { name: /^Undo/ })).toBeNull();
    const output = within(card).getByText("Output").closest("details")!;
    expect(output).not.toHaveAttribute("open");
    await user.click(within(output).getByText("Output"));
    expect(output).toHaveAttribute("open");
    const stop = within(card).getByRole("button", { name: "Stop prune-images" });
    await user.click(stop);
    expect(stop).toBeDisabled();
    expect(stop).toHaveTextContent("Stopping…");
    expect(without(presses[0]!)).toEqual({ project: "harbor", action: "stop_command", agent: "dependency-check", n: 2, change: 1 });
    state.run = runDetail({ changes: [command(1, { outcome: "stopped", note: "You stopped it after 2 min." })] });
    hold.resolve(json({ ok: true }));
    expect(await screen.findByText("You stopped it after 2 min.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop prune-images" })).toBeNull();
  });
});
