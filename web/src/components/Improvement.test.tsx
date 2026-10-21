import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { AgentSuggestions, ImprovementRun, ImprovementSuggestion } from "../data/api";

/*
 * Improvement suggestions in chat and Needs you (improvement-review/PROPOSAL.md, "States and exact text"): the
 * running line and Stop, endings with Show, the card's presses and what appears after each, and the Suggestions
 * section that never counts as something needing you. The fake server keeps one record the presses change.
 */

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

function suggestion(n: number, fields: Partial<ImprovementSuggestion> = {}): ImprovementSuggestion {
  return {
    ref: `0123456789ab/${n}`, n, target: n === 2 ? "altitude" : "How agents work",
    title: n === 2 ? "The phone swipe walkthrough fails the web shard about once a week" : "Owners re-ask merge approval after a routine rebase",
    explanation: "In 3 of 14 tasks the owner re-asked you to approve a merge when only a rebase had changed the PR.",
    seen: [
      { slug: "release-notes", title: "Release notes for 0.4" }, { slug: "tidy-settings", title: "Tidy the settings page" },
      { slug: "task-search", title: "Add search to the task list" }, { slug: "safari-voice", title: "Fix voice on Safari" },
    ],
    count: "Questions re-asked without your input in 3 of 14 tasks",
    state: "undecided", task: null, undo: false, opening: false, ...fields,
    waits: fields.waits ?? ((fields.state ?? "undecided") === "undecided" || fields.state === "accepted" && !fields.task),
  };
}

function posted(suggestions: ImprovementSuggestion[]): ImprovementRun {
  return {
    kind: "review", id: "0123456789ab", state: "posted", started: ago(40), ended: ago(30), posted: ago(30), tasks: 14, window: "Oct 5–12",
    reason: null, suggestions,
    details: [["Read", "14 tasks that finished Oct 5–12; their questions, reviews, landings and conversations"],
      ["L3 dropped", "Suggestion 3: already owned by an open task"], ["Model", "Claude Sonnet 5.5"],
      ["Usage", "24,800 of its 86,000-token limit"]],
  };
}

function ended(id: string, state: ImprovementRun["state"], reason: string | null = null): ImprovementRun {
  return { ...posted([]), id, state, reason, posted: null };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** One project, one chat and the review records; `press` answers POST /api/improve and may change `runs`. */
function serve(runs: ImprovementRun[], press: (body: { action: string; ref?: string }) => Response = () => json({ ok: true }),
  queue: unknown[] = []) {
  const state = { runs };
  const rows = runs.filter((run) => !run.line).map((run, index) => ({
    at: run.ended, role: "assistant", text: "row", trigger: "improvement", turn_id: run.id, improvement: run.id, index,
  }));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/improve")) return press(JSON.parse(String(init?.body)) as { action: string; ref?: string });
    if (url.includes("/api/overview")) {
      const waiting = state.runs.filter((run) => run.suggestions.some((s) => s.state === "undecided" || (s.state === "accepted" && !s.task)));
      return json({ projects: [{ name: "altitude", managed: true, counts: {} }], queue, suggestions: waiting.map((run) => ({ ...run, project: "altitude" })),
        wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: [] });
    }
    if (url.includes("/api/project/altitude")) {
      return json({ name: "altitude", l3: { session_id: "abc" }, tasks: [{ slug: "cite-answers", state: "running", title: "Stop re-asking merge approval" }],
        archive: [], decisions: [], improvements: state.runs });
    }
    if (url.includes("/api/chat/")) return json({ history: [{ at: ago(50), role: "user", text: "Get improvement suggestions.", trigger: "chat", turn_id: "c1" },
      { at: ago(49), role: "assistant", text: "Started. The suggestions will appear here.", trigger: "chat", turn_id: "c1" }, ...rows],
    active: null, busy: false, queued: [], l3: { session_id: "abc" } });
    return json({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, fetchMock };
}

const presses = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls
  .filter(([url]) => String(url).endsWith("/api/improve")).map(([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, string>);

const card = async () => screen.findByRole("article", { name: "Improvement suggestions, 14 tasks from Oct 5–12" });

describe.each([390, 1440])("improvement suggestions at %ipx", (width) => {
  it("shows the card in chat with its places, count, actions and details", async () => {
    setViewport(width);
    serve([posted([suggestion(1), suggestion(2, { seen: [{ slug: "tidy-settings", title: "Tidy the settings page" }, { slug: null, title: "the project chat" }], count: null })])]);
    const { user, router } = renderApp({ route: "/projects/altitude" });
    const shown = await card();
    expect(within(shown).getByRole("heading", { name: "Improvement suggestions" })).toBeVisible();
    expect(within(shown).getByText("14 tasks from Oct 5–12")).toBeVisible();
    const first = within(shown).getByRole("region", { name: "1. Owners re-ask merge approval after a routine rebase" });
    expect(within(first).getByText("How agents work")).toBeVisible();
    expect(within(first).getAllByRole("link").map((link) => link.textContent)).toEqual(["Release notes for 0.4", "Tidy the settings page", "Add search to the task list"]);
    await user.click(within(first).getByRole("button", { name: "1 more" }));
    expect(within(first).getByRole("link", { name: "Fix voice on Safari" })).toHaveAttribute("href", "/projects/altitude/tasks/safari-voice");
    expect(within(first).queryByRole("button", { name: "1 more" })).toBeNull();
    expect(within(first).getByText("Questions re-asked without your input in 3 of 14 tasks")).toBeVisible();
    const second = within(shown).getByRole("region", { name: /^2\. The phone swipe/ });
    expect(within(second).getByText("altitude")).toBeVisible();
    expect(within(second).getByText("the project chat")).not.toHaveAttribute("href");
    expect(within(second).queryByText(/in \d+ of 14 tasks/)).toBeNull();
    expect(within(second).getByRole("button", { name: "Make a task" })).toBeVisible();
    expect(within(second).getByRole("button", { name: "Dismiss" })).toBeVisible();
    expect(within(shown).queryByText("Claude Sonnet 5.5")).toBeNull();
    await user.click(within(shown).getByRole("button", { name: "Details" }));
    expect(within(shown).getByText("Suggestion 3: already owned by an open task")).toBeVisible();
    expect(within(shown).getByText("24,800 of its 86,000-token limit")).toBeVisible();
    expect(within(shown).getByText("Ran")).toBeVisible();
    await user.click(within(shown).getByRole("button", { name: "Hide details" }));
    expect(within(shown).queryByText("Claude Sonnet 5.5")).toBeNull();
    await user.click(within(first).getByRole("link", { name: "Release notes for 0.4" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/altitude/tasks/release-notes"));
  });

  it("Make a task spins in place, then reads accepted, then the task card; a lost task offers Try again", async () => {
    setViewport(width);
    let release: (() => void) | null = null;
    const { state, fetchMock } = serve([posted([suggestion(1)])], (body) => {
      if (body.action === "accept") state.runs = [posted([suggestion(1, { state: "accepted", opening: true })])];
      if (body.action === "retry") state.runs = [posted([suggestion(1, { state: "accepted", task: "cite-answers" })])];
      return json({ ok: true });
    });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    const shown = await card();
    const make = within(shown).getByRole("button", { name: "Make a task" });
    fetchMock.mockImplementationOnce(async (input, init) => {
      await new Promise<void>((resolve) => { release = resolve; });
      return (fetchMock.getMockImplementation()!)(input, init);
    });
    await user.click(make);
    expect(await within(shown).findByRole("status", { name: "Saving" })).toBeVisible();
    expect(within(shown).getByRole("button", { name: "Dismiss" })).toBeDisabled();
    release!();
    expect(await within(shown).findByText("Accepted · L3 is opening a task")).toBeVisible();
    expect(within(shown).queryByRole("button", { name: "Make a task" })).toBeNull();
    expect(presses(fetchMock)[0]).toEqual({ project: "altitude", action: "accept", ref: "0123456789ab/1", request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    // L3's turn ended without a task: the record says so, and Try again queues the same turn.
    state.runs = [posted([suggestion(1, { state: "accepted" })])];
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "altitude"] }));
    expect(await within(shown).findByText(/Accepted · no task yet/)).toBeVisible();
    await user.click(within(shown).getByRole("button", { name: "Try again" }));
    expect(await within(shown).findByRole("link", { name: /Stop re-asking merge approval/ })).toHaveAttribute("href", "/projects/altitude/tasks/cite-answers");
  });

  it("Dismiss folds the suggestion with Undo, Undo opens it again, and a lapsed one reads Not decided", async () => {
    setViewport(width);
    const { state } = serve([posted([suggestion(1), suggestion(2, { state: "not decided" })])], (body) => {
      const first = body.action === "dismiss" ? suggestion(1, { state: "dismissed", undo: true }) : suggestion(1);
      state.runs = [posted([first, suggestion(2, { state: "not decided" })])];
      return json({ ok: true });
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const shown = await card();
    const lapsed = within(shown).getByRole("region", { name: /^2\. The phone swipe/ });
    expect(within(lapsed).getByText("Not decided")).toBeVisible();
    expect(within(lapsed).queryByRole("button")).toBeNull();
    expect(within(lapsed).queryByText(/In 3 of 14 tasks/)).toBeNull();
    await user.click(within(shown).getByRole("button", { name: "Dismiss" }));
    const folded = await within(shown).findByText("Dismissed", { exact: false });
    expect(folded).toBeVisible();
    expect(within(shown).queryByRole("button", { name: "Make a task" })).toBeNull();
    await user.click(within(shown).getByRole("button", { name: "Undo" }));
    expect(await within(shown).findByRole("button", { name: "Make a task" })).toBeVisible();
    expect(within(shown).getByRole("button", { name: "Dismiss" })).toBeVisible();
  });

  it("a refused Dismiss brings the buttons back with Not saved and Retry, gone on the next press", async () => {
    setViewport(width);
    let fail = true;
    serve([posted([suggestion(1)])], () => (fail ? json({ error: "down" }, 500) : json({ ok: true })));
    const { user } = renderApp({ route: "/projects/altitude" });
    const shown = await card();
    await user.click(within(shown).getByRole("button", { name: "Dismiss" }));
    expect(await within(shown).findByRole("alert")).toHaveTextContent(/^Not saved: down Retry$/);
    expect(within(shown).getByRole("button", { name: "Make a task" })).toBeEnabled();
    fail = false;
    await user.click(within(shown).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(within(shown).queryByRole("alert")).toBeNull());
  });

  it("a running review shows its line with Stop; a failed Stop says so and offers Stop again", async () => {
    setViewport(width);
    let fail = true;
    const running = { ...ended("feedfacecafe", "reading"), line: "Improvement suggestions · reading 14 tasks from Oct 5–12" };
    const { state, fetchMock } = serve([running], () => {
      if (fail) return json({ error: "down" }, 500);
      state.runs = [{ ...ended("feedfacecafe", "stopped"), ended: ago(0) }];
      return json({ ok: true });
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const line = (await screen.findByText("Improvement suggestions · reading 14 tasks from Oct 5–12")).closest<HTMLElement>(".sys-line")!;
    expect(within(line).queryByRole("button", { name: "Show" })).toBeNull();
    await user.click(within(line).getByRole("button", { name: "Stop" }));
    expect(await within(line).findByRole("alert")).toHaveTextContent("Not stopped. Try again.");
    fail = false;
    await user.click(within(line).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.queryByText("Improvement suggestions · reading 14 tasks from Oct 5–12")).toBeNull());
    expect(presses(fetchMock).map((press) => [press.action, press.ref])).toEqual([["stop", "feedfacecafe"], ["stop", "feedfacecafe"]]);
  });

  it("a change of terms says the Weekly improvement review paused, Open going to its page", async () => {
    setViewport(width);
    const { fetchMock } = serve([]);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes("/api/chat/")
      ? json({ history: [{ at: ago(5), role: "assistant", text: "Weekly improvement review paused: its terms changed.", trigger: "improvement",
        turn_id: "0123456789ab", improvement: "terms-0123456789ab" }], active: null, busy: false, queued: [], l3: { session_id: "abc" } })
      : original(input, init));
    renderApp({ route: "/projects/altitude" });
    const line = (await screen.findByText(/paused: its terms changed\./)).closest<HTMLElement>(".sys-line")!;
    expect(line).toHaveTextContent(/Weekly improvement review paused: its terms changed\. Open$/);
    expect(within(line).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/altitude/agents/weekly");
  });

  it("endings are system lines: nothing to suggest and failed open their details, stopped has none", async () => {
    setViewport(width);
    serve([ended("aaaaaaaaaaaa", "nothing"), ended("bbbbbbbbbbbb", "failed", "Claude Sonnet 5.5 is unavailable"), ended("cccccccccccc", "stopped")]);
    const { user } = renderApp({ route: "/projects/altitude" });
    const nothing = (await screen.findByText("Improvement suggestions · 14 tasks from Oct 5–12 · nothing to suggest")).closest<HTMLElement>(".irc-ending")!;
    const failed = screen.getByText("Improvement suggestions failed · Claude Sonnet 5.5 is unavailable").closest<HTMLElement>(".irc-ending")!;
    const stopped = screen.getByText("Improvement suggestions stopped by you").closest<HTMLElement>(".irc-ending")!;
    expect(failed.querySelector(".sys-dot")).toHaveAttribute("data-tone", "danger");
    expect(nothing.querySelector(".sys-dot")).not.toHaveAttribute("data-tone");
    expect(within(stopped).queryByRole("button")).toBeNull();
    // Recent runs on the Weekly improvement review's page open each ending here.
    expect(nothing).toHaveAttribute("id", "improvement-aaaaaaaaaaaa");
    await user.click(within(nothing).getByRole("button", { name: "Show" }));
    expect(within(nothing).getByText("Suggestion 3: already owned by an open task")).toBeVisible();
    await user.click(within(nothing).getByRole("button", { name: "Hide" }));
    expect(within(nothing).queryByText("Claude Sonnet 5.5")).toBeNull();
    await user.click(within(failed).getByRole("button", { name: "Show" }));
    expect(within(failed).getByText("Claude Sonnet 5.5")).toBeVisible();
  });

  it("Needs you lists waiting cards under Suggestions without counting them, the header opening the card in chat", async () => {
    setViewport(width);
    serve([posted([suggestion(1)])]);
    const { user, router } = renderApp({ route: "/" });
    expect(await screen.findByText("Nothing needs you.")).toBeVisible();
    const section = screen.getByRole("region", { name: "Suggestions" });
    expect(within(section).getByRole("heading", { name: "altitude" })).toBeVisible();
    expect(screen.queryByText(/waiting|needs you in/i)).toBeNull();
    const waiting = within(section).getByRole("article");
    expect(within(waiting).queryByRole("button", { name: "Details" })).toBeNull();
    await user.click(within(waiting).getByRole("link", { name: "Improvement suggestions" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/altitude"));
    expect(router.state.location.hash).toBe("#improvement-0123456789ab");
    expect(await card()).toHaveAttribute("id", "improvement-0123456789ab");
  });

  it("dismissing the last waiting suggestion in Needs you takes the card away at the press; a refusal brings it back saying why", async () => {
    setViewport(width);
    let answer!: (response: Response) => void;
    serve([posted([suggestion(1)])], () => new Promise<Response>((resolve) => { answer = resolve; }) as unknown as Response);
    const { user } = renderApp({ route: "/" });
    const section = await screen.findByRole("region", { name: "Suggestions" });
    await user.click(within(within(section).getByRole("article")).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Suggestions" })).toBeNull());
    await act(async () => answer(json({ error: "Altitude is busy." }, 503)));
    const back = within(await screen.findByRole("region", { name: "Suggestions" })).getByRole("article");
    expect(await within(back).findByRole("alert")).toHaveTextContent(/^Not saved: Altitude is busy\. Retry$/);
    expect(within(back).getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });

  it("a Dismiss Altitude saved despite a lost reply stays folded without Not saved", async () => {
    setViewport(width);
    const { state } = serve([posted([suggestion(1)])], () => {
      state.runs = [posted([suggestion(1, { state: "dismissed", undo: true })])];
      return json({ error: "lost" }, 503);
    });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    const shown = await card();
    await user.click(within(shown).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(queryClient.isMutating() + queryClient.isFetching()).toBe(0));
    expect(within(shown).getByText("Dismissed", { exact: false })).toBeVisible();
    expect(within(shown).queryByRole("alert")).toBeNull();
  });

  it("Undo in chat brings its card back to Needs you at the press", async () => {
    setViewport(width);
    let answer!: (response: Response) => void;
    serve([posted([suggestion(1, { state: "dismissed", undo: true })])], () => new Promise<Response>((resolve) => { answer = resolve; }) as unknown as Response);
    const { user, router } = renderApp({ route: "/projects/altitude" });
    await user.click(within(await card()).getByRole("button", { name: "Undo" }));
    await act(async () => { await router.navigate("/"); });
    const back = within(await screen.findByRole("region", { name: "Suggestions" })).getByRole("article");
    expect(within(back).getByRole("button", { name: "Dismiss" })).toBeDisabled();
    await act(async () => answer(json({ error: "Altitude is busy." }, 503)));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Suggestions" })).toBeNull());
  });

  it("a card with nothing waiting leaves Needs you", async () => {
    setViewport(width);
    serve([posted([suggestion(1, { state: "dismissed", undo: true }), suggestion(2, { state: "accepted", task: "cite-answers" })])]);
    renderApp({ route: "/" });
    expect(await screen.findByText("Nothing needs you.")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Suggestions" })).toBeNull();
  });
});

/** An agent run's card (project-agents/PROPOSAL.md, "Suggestions"): its suggestions have no target, Seen in or count. */
function agentCard(suggestions: ImprovementSuggestion[]): AgentSuggestions {
  return { kind: "agent", id: "agent:0a1b2c3d4e5f/7", agent: "0a1b2c3d4e5f", n: 7, name: "Review each merge",
    line: "For “PR #810 merged” and 2 more", at: ago(30), posted: ago(10), checking: 0, suggestions, set_aside: [], unfinished: false };
}
const agentSuggestion = (n: number, fields: Partial<ImprovementSuggestion> = {}) => suggestion(n, {
  ref: `agent:0a1b2c3d4e5f/7/${n}`, target: undefined, seen: [], count: null,
  title: "Add a test for the uploader's retry limit", explanation: "A case beside the other retry tests that checks the limit of 5.", ...fields });

describe.each([390, 1440])("an agent run's suggested tasks at %ipx", (width) => {
  it("Needs you shows the agent's card uncounted: its name, what fired it, the suggestion and Open the run", async () => {
    setViewport(width);
    const { fetchMock } = serve([]);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).includes("/api/overview")) return base(input, init);
      return json({ projects: [{ name: "altitude", managed: true, counts: {} }], queue: [],
        suggestions: [{ ...agentCard([agentSuggestion(1)]), project: "altitude" }],
        wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: [] });
    });
    const { user } = renderApp({ route: "/" });
    expect(await screen.findByText("Nothing needs you.")).toBeVisible();
    const shown = within(screen.getByRole("region", { name: "Suggestions" })).getByRole("article", { name: "Suggested tasks from Review each merge" });
    expect(within(shown).getByRole("heading", { name: "Review each merge" })).toBeVisible();
    expect(within(shown).getByText("For “PR #810 merged” and 2 more")).toBeVisible();
    expect(within(shown).getByRole("heading", { name: "1. Add a test for the uploader's retry limit" })).toBeVisible();
    expect(shown.querySelector(".chip")).toBeNull();
    expect(within(shown).queryByText(/^Seen in/)).toBeNull();
    expect(within(shown).getByRole("link", { name: "Open the run" })).toHaveAttribute("href", "/projects/altitude/agents/0a1b2c3d4e5f/runs/7");
    await user.click(within(shown).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(presses(fetchMock)).toEqual([expect.objectContaining({ action: "dismiss", ref: "agent:0a1b2c3d4e5f/7/1" })]));
  });

  it("Needs you shows the task a suggestion became with its title and status, as the run page does", async () => {
    setViewport(width);
    const { fetchMock } = serve([]);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).includes("/api/overview")) return base(input, init);
      return json({ projects: [{ name: "altitude", managed: true, counts: {} }], queue: [],
        suggestions: [{ ...agentCard([agentSuggestion(1), agentSuggestion(2, { state: "accepted", task: "cite-answers" })]), project: "altitude" }],
        wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [], roots: [] });
    });
    renderApp({ route: "/" });
    const shown = within(await screen.findByRole("region", { name: "Suggestions" })).getByRole("article");
    const task = await within(shown).findByRole("link", { name: /^Stop re-asking merge approval · / });
    expect(task).toHaveAttribute("href", "/projects/altitude/tasks/cite-answers");
    expect(within(shown).queryByText("Status unavailable")).toBeNull();
  });
});
