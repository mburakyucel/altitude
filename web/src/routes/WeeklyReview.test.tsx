import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

/* The Weekly improvement review as a built-in agent against a fictional server (design/wireframes/project-agents/
 * PROPOSAL.md, "The Weekly improvement review"). */

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const overview = {
  projects: [{ name: "harbor", managed: true }], queue: [], wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: null, known: false, stale: false, at: null }],
  new_tasks: { models: [{ engine: "alpha", model: "swift", label: "Swift" }], efforts: { alpha: [{ value: "low", label: "Low" }] }, value: null, only: [] },
};
const terms = { version: "0123456789ab", title: "Turn on the Weekly improvement review for harbor?",
  body: "Swift reads counts from this project's tasks and short quotes from its chats.", sent: "Counts and short chat quotes · Swift on your plan · capped per run, plus one L3 check" };
const builtin = { key: "weekly", name: "Weekly improvement review", line: "Mondays at 08:00 · Suggests fixes for friction that keeps coming back in this project's tasks" };
const row = (over: Record<string, unknown> = {}) => ({ id: "weekly", name: "Weekly improvement review", state: "on", triggers: "Mondays at 08:00",
  news: "3 suggestions", news_at: ago(60 * 48), cue: null, retired_line: null, ...over });
const list = (weekly: Record<string, unknown>) => ({ clock: { zone: "Istanbul", offset: -new Date().getTimezoneOffset() },
  budget: { minutes: 120, choices: [30, 60, 120, 300, 600], used: 0, spent: false }, agents: [] as Record<string, unknown>[], retired: [], builtins: [], offers: [],
  weekly: { terms, agent: null, builtin: null, ...weekly } });
const idle = { line: "Next: Monday, Oct 26", actions: ["run", "pause"], run: null, spinning: false, paused: false,
  now: { line: "Reads the 6 tasks finished since Oct 19.", unavailable: null } };
const runs = [
  { id: "aaaaaaaaaaaa", state: "posted", outcome: "3 suggestions", tone: "accent", at: ago(60 * 24), minutes: 4, line: "Dock paths reset; Harbor list exported twice; Tide timer drifts" },
  { id: null, state: "skipped", outcome: "Skipped", tone: "muted", at: ago(60 * 24 * 8), minutes: null, line: "3 tasks had finished; it needs 5." },
  { id: "bbbbbbbbbbbb", state: "failed", outcome: "Couldn't finish", tone: "danger", at: ago(60 * 24 * 15), minutes: 2, line: "It was interrupted by a restart." },
];
const page = (over: Record<string, unknown> = {}) => ({ name: "Weekly improvement review", terms, agreed: true, status: idle, runs,
  what: { text: "Finds friction that keeps coming back in this project's tasks and suggests fixes, as cards in chat you can make into tasks.",
    sent: terms.sent, past: { line: "4 runs · 7 suggestions · 3 became tasks · 2 dismissed", latest: "aaaaaaaaaaaa" } }, ...over });

type Reply = Response | Promise<Response>;
function serve(options: { page?: () => Reply; press?: (body: Record<string, unknown>) => Reply } = {}) {
  const state = { agents: list({ builtin }), page: page() };
  const presses: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json(overview);
    if (url === "/api/project/harbor") return json({ name: "harbor", tasks: [] });
    if (url === "/api/improve" && init?.method === "POST") {
      const { request_id: _, ...body } = JSON.parse(String(init.body));
      presses.push(body);
      return options.press ? options.press(body) : json({ ok: true });
    }
    if (url === "/api/agents/harbor") return json(state.agents);
    if (url === "/api/agents/harbor/weekly") return options.page ? options.page() : json(state.page);
    return json({ error: "not found" }, 404);
  }));
  return { state, presses };
}

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

const actions = () => screen.getAllByRole("button").filter((b) => b.closest(".agent-status-actions")).map((b) => b.textContent);

afterEach(() => setViewport(1024));

describe.each([390, 1440])("the Weekly improvement review at %ipx", (width) => {
  it("Built in: Turn on shows its terms; Cancel changes nothing; Turn on agrees and the row becomes the first agent", async () => {
    setViewport(width);
    const { state, presses } = serve({ press: async () => {
      state.agents = list({ agent: row({ news: "Never run", news_at: null }) });
      return json({ ok: true, weekly: true });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    const built = await screen.findByText(builtin.line);
    await user.click(within(built.closest(".agents-builtin-row")!).getByRole("button", { name: "Turn on" }));
    const sheet = await screen.findByRole("dialog", { name: terms.title });
    expect(within(sheet).getByText(terms.body)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(sheet).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(presses).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn on" }));
    const yours = await screen.findByRole("link", { name: /^Weekly improvement review/ });
    expect(yours).toHaveAttribute("href", "/projects/harbor/agents/weekly");
    expect(yours).toHaveTextContent("Mondays at 08:00 · Never run");
    expect(screen.queryByRole("heading", { name: "Built in" })).toBeNull();
    expect(presses).toEqual([{ project: "harbor", action: "weekly", on: true, terms: terms.version }]);
  });

  it("its page: status with Run now's line, recent runs opening chat, a skip without a link, and what it does", async () => {
    setViewport(width);
    serve();
    renderApp({ route: "/projects/harbor/agents/weekly" });
    expect(await screen.findByText("Next: Monday, Oct 26")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Weekly improvement review" })).not.toHaveLength(0);
    expect(document.querySelector("a.agents-back")).toHaveAttribute("href", "/projects/harbor/agents");
    expect(actions()).toEqual(["Run now", "Pause"]);
    expect(screen.getByText("Reads the 6 tasks finished since Oct 19.")).toBeInTheDocument();
    const found = screen.getByRole("link", { name: /^3 suggestions/ });
    expect(found).toHaveAttribute("href", "/projects/harbor#improvement-aaaaaaaaaaaa");
    expect(found).toHaveTextContent("4 min");
    expect(found).toHaveTextContent("Dock paths reset; Harbor list exported twice; Tide timer drifts");
    expect(screen.getByRole("link", { name: /^Couldn't finish/ })).toHaveTextContent("It was interrupted by a restart.");
    expect(screen.getByText("3 tasks had finished; it needs 5.").closest("a")).toBeNull();
    expect(screen.getByText(terms.sent)).toBeInTheDocument();
    expect(screen.getByText(/4 runs · 7 suggestions · 3 became tasks · 2 dismissed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Latest" })).toHaveAttribute("href", "/projects/harbor#improvement-aaaaaaaaaaaa");
    expect(screen.queryByRole("button", { name: /Edit|Retire/ })).toBeNull();
  });
});

describe("Weekly improvement review", () => {
  it("is the first of your agents with its news", async () => {
    const { state } = serve();
    state.agents = { ...list({ agent: row({ news: "Paused: its terms changed", news_at: null, cue: "paused", state: "paused" }) }),
      agents: [{ id: "dependency-check", name: "Dependency check", state: "on", triggers: "Weekdays at 07:00", news: "Never run", news_at: null, cue: null, retired_line: null }] };
    renderApp({ route: "/projects/harbor/agents" });
    const rows = await screen.findAllByRole("link", { name: /Weekly improvement review|Dependency check/ });
    expect(rows.map((r) => r.querySelector("strong")?.textContent)).toEqual(["Weekly improvement review", "Dependency check"]);
    expect(rows[0]).toHaveTextContent("Mondays at 08:00 · Paused: its terms changed");
  });

  it("a refused Turn on returns the button with Not turned on: and its reason", async () => {
    serve({ press: () => json({ error: "The terms changed while the sheet was open; read them again." }, 409) });
    const { user } = renderApp({ route: "/projects/harbor/agents" });
    await user.click(await screen.findByRole("button", { name: "Turn on" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not turned on: The terms changed while the sheet was open; read them again.");
    expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled();
  });

  it("shows Loading… then Could not load the Weekly improvement review. with Retry", async () => {
    const hold = deferred();
    let fail = true;
    serve({ page: () => fail ? hold.promise : json(page()) });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    expect(await screen.findByRole("status", { name: "Loading Weekly improvement review…" })).toBeInTheDocument();
    hold.resolve(json({ error: "broken" }, 500));
    expect(await screen.findByText("Could not load the Weekly improvement review.", {}, { timeout: 5000 })).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Next: Monday, Oct 26")).toBeInTheDocument();
  });

  it("an unavailable Run now looks unavailable and says why only when pressed", async () => {
    const { state, presses } = serve();
    const reason = "Run now needs 5 finished tasks since Oct 19; 3 so far.";
    state.page = page({ status: { ...idle, line: "Next: Monday, Oct 26, if 2 more tasks finish", now: { line: null, unavailable: reason } } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    const runNow = await screen.findByRole("button", { name: "Run now" });
    expect(runNow).toHaveAttribute("aria-disabled", "true");
    expect(runNow).toHaveAccessibleDescription(reason);
    expect(screen.getByText(reason)).toHaveClass("sr-only");
    await user.click(runNow);
    expect(screen.getByText(reason)).not.toHaveClass("sr-only");
    expect(presses).toEqual([]);
  });

  it("Run now says Starting… with Pause unavailable until the run is read, then its line with Stop only", async () => {
    const { state, presses } = serve({ press: () => json({ ok: true, run: "cccccccccccc", state: "reading" }) });
    const { user, queryClient } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Starting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(presses).toEqual([{ project: "harbor", action: "run" }]);
    state.page = page({ status: { line: "Reading 6 tasks", actions: ["stop"], run: "cccccccccccc", spinning: true, paused: false, now: null } });
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "harbor", "agents"] }));
    expect(await screen.findByText("Reading 6 tasks")).toBeInTheDocument();
    await waitFor(() => expect(actions()).toEqual(["Stop"]));
    expect(screen.queryByText("Reads the 6 tasks finished since Oct 19.")).toBeNull();
  });

  it("a refused Run now returns the buttons with its reason in red", async () => {
    serve({ press: () => json({ error: "Another review just ended; ask again." }, 409) });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/^Another review just ended; ask again\.$/);
    expect(screen.getByRole("alert")).toHaveClass("text-danger");
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("Stop says Stopping… until the end is read, or Not stopped. Try again.", async () => {
    let refuse = true;
    const hold = deferred();
    const { state, presses } = serve({ press: () => refuse ? json({ error: "broken" }, 500) : hold.promise });
    state.page = page({ status: { line: "Reading 6 tasks", actions: ["stop"], run: "cccccccccccc", spinning: true, paused: false, now: null } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not stopped. Try again.");
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("button", { name: "Stopping…" })).toBeDisabled();
    expect(screen.getByText("Reading 6 tasks")).toBeInTheDocument();
    state.page = page({ runs: [{ id: "cccccccccccc", state: "stopped", outcome: "Stopped by you", tone: "muted", at: ago(1), minutes: 1, line: "It was reading 6 tasks." }, ...runs] });
    hold.resolve(json({ ok: true }));
    expect(await screen.findByRole("link", { name: /^Stopped by you/ })).toBeInTheDocument();
    expect(presses.at(-1)).toEqual({ project: "harbor", action: "stop", ref: "cccccccccccc" });
    expect(actions()).toEqual(["Run now", "Pause"]);
  });

  it("Pause changes the line and buttons at once; a refusal returns them with Not saved: and Retry", async () => {
    let refuse = true;
    const { state, presses } = serve({ press: async () => {
      if (refuse) return json({ error: "Altitude could not save it." }, 409);
      state.page = page({ status: { ...idle, line: "Paused", actions: ["resume", "run"], paused: true } });
      return json({ ok: true, weekly: false });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Altitude could not save it. Retry");
    expect(screen.getByText("Next: Monday, Oct 26")).toBeInTheDocument();
    refuse = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(actions()).toEqual(["Resume", "Run now"]);
    expect(presses).toEqual([{ project: "harbor", action: "weekly", on: false }, { project: "harbor", action: "weekly", on: false }]);
  });

  it("terms changed: Resume and Run now show the terms first, and Turn on agrees and completes the press", async () => {
    const { state, presses } = serve({ press: async () => {
      state.page = page();
      return json({ ok: true, weekly: true });
    } });
    state.page = page({ agreed: false, status: { ...idle, line: "Paused: its terms changed", actions: ["resume", "run"], paused: true } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("dialog", { name: terms.title })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Resume" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn on" }));
    expect(await screen.findByText("Next: Monday, Oct 26")).toBeInTheDocument();
    expect(presses).toEqual([{ project: "harbor", action: "weekly", on: true, terms: terms.version }]);
  });

  it("terms changed while the sheet was open: the refusal reads the new terms, and Retry shows them before turning on", async () => {
    const newer = { ...terms, version: "fedcba987654", title: "Turn on the Weekly improvement review for harbor? (revised)" };
    let refuse = true;
    const { state, presses } = serve({ press: async () => {
      if (refuse) {
        refuse = false;
        state.page = page({ terms: newer, agreed: false, status: { ...idle, line: "Paused: its terms changed", actions: ["resume", "run"], paused: true } });
        return json({ error: "The terms changed while the sheet was open; read them again." }, 409);
      }
      state.page = page({ terms: newer });
      return json({ ok: true, weekly: true });
    } });
    state.page = page({ agreed: false, status: { ...idle, line: "Paused: its terms changed", actions: ["resume", "run"], paused: true } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Resume" }));
    await user.click(within(await screen.findByRole("dialog", { name: terms.title })).getByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: The terms changed while the sheet was open; read them again. Retry");
    await waitFor(() => expect(presses).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await user.click(within(await screen.findByRole("dialog", { name: newer.title })).getByRole("button", { name: "Turn on" }));
    expect(await screen.findByText("Next: Monday, Oct 26")).toBeInTheDocument();
    expect(presses).toEqual([{ project: "harbor", action: "weekly", on: true, terms: terms.version },
      { project: "harbor", action: "weekly", on: true, terms: newer.version }]);
  });

  it("Pause settles on a run that started meanwhile: its line with Stop only, letting it finish", async () => {
    const { state } = serve({ press: async () => {
      state.page = page({ status: { line: "Reading 6 tasks", actions: ["stop"], run: "cccccccccccc", spinning: true, paused: false, now: null } });
      return json({ ok: true, weekly: false });
    } });
    const { user } = renderApp({ route: "/projects/harbor/agents/weekly" });
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByText("Reading 6 tasks")).toBeInTheDocument();
    expect(actions()).toEqual(["Stop"]);
  });

  it("no runs: No runs yet., and no totals before a run ends", async () => {
    const { state } = serve();
    state.page = page({ runs: [], what: { ...page().what, past: null } });
    renderApp({ route: "/projects/harbor/agents/weekly" });
    expect(await screen.findByText("No runs yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Latest" })).toBeNull();
  });
});
