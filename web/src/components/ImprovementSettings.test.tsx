import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { ImprovementSettings } from "../data/api";

/*
 * Improvement suggestions in a project's settings (improvement-review/PROPOSAL.md, "Turning it on"): the Weekly
 * switch and its lines, Get suggestions now with its line, its reason when unavailable and the running review in its
 * place, and the sheet that comes first without agreement. The fake server keeps one record the presses change.
 */

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const terms = {
  version: "0a1b2c3d4e5f", title: "Turn on improvement suggestions for altitude?",
  body: "Claude Sonnet 5.5 reads counts from this project's tasks and short quotes from its chats (up to 300 characters each).",
  sent: "Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check",
};
const off = "Every Monday morning, when at least 5 tasks have finished.";
const reads = { line: "Reads the 6 tasks finished since Oct 12.", unavailable: null };

function serve(initial: Partial<ImprovementSettings>, press: (body: Record<string, unknown>, state: ImprovementSettings) => Response | void = () => undefined) {
  const state: ImprovementSettings = { available: true, terms, agreed: false, weekly: false, line: off, now: reads, ...initial };
  const posts: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return json({ projects: [{ name: "altitude", managed: true }], queue: [], engines: [],
      wip: { machine: 0, per_project: {}, waiting: [] }, quota: { known: false } });
    if (url === "/api/project/altitude") return json({ name: "altitude", tasks: [], l3: {}, improvements: [] });
    if (url === "/api/defaults/altitude") return json({ l2_engine: null, l3_choice: null, l2_preference: null,
      l3_unavailable: null, routing: null, models: [], efforts: {}, engines: [], roles: [] });
    if (url === "/api/improve/altitude") return json(state);
    if (url === "/api/improve" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      posts.push(body);
      const refused = press(body, state);
      if (refused) return refused;
      if (body.terms) state.agreed = true;
      if (body.action === "weekly") Object.assign(state, { weekly: body.on, line: body.on ? "Next: Monday, Oct 19." : off });
      if (body.action === "run") Object.assign(state, { now: undefined, running: { id: "0123456789ab", line: "Reading 6 tasks" } });
      if (body.action === "stop") Object.assign(state, { now: reads, running: undefined });
      return json({ ok: true });
    }
    return json({}, 404);
  }));
  return { state, posts };
}

const section = () => screen.findByRole("region", { name: "Improvement suggestions" });
const weekly = () => screen.findByRole("switch", { name: /^Weekly/ });

describe.each([390, 1440])("improvement suggestions settings at %ipx", (width) => {
  it("opens the sheet first and saves nothing on Cancel or Escape", async () => {
    setViewport(width);
    const { posts } = serve({});
    const { user } = renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    expect(within(area).getByText("Finds friction that keeps coming back in this project's tasks and suggests fixes.")).toBeInTheDocument();
    expect(within(area).getByText(off)).toBeInTheDocument();
    expect(within(area).getByText(reads.line)).toBeInTheDocument();
    expect(within(area).getByText(terms.sent)).toBeInTheDocument();
    await user.click(await weekly());
    const sheet = await screen.findByRole("dialog", { name: terms.title });
    expect(sheet.closest(".overlay")).toHaveAttribute("data-side", width === 390 ? "bottom" : "center");
    expect(within(sheet).getByText(terms.body)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(await weekly()).not.toBeChecked();
    await user.click(within(sheet).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(within(area).getByRole("button", { name: "Get suggestions now" }));
    await screen.findByRole("dialog", { name: terms.title });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts).toEqual([]);
  });

  it("agrees and turns Weekly on with Turn on, then turns it off without the sheet", async () => {
    setViewport(width);
    const { posts } = serve({});
    const { user } = renderApp({ route: "/settings/projects/altitude" });
    await user.click(await weekly());
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(screen.getByText("Next: Monday, Oct 19.")).toBeInTheDocument());
    expect(await weekly()).toBeChecked();
    expect(posts).toEqual([expect.objectContaining({ action: "weekly", on: true, terms: terms.version })]);
    await user.click(await weekly());
    await waitFor(() => expect(screen.getByText(off)).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts[1]).toEqual(expect.objectContaining({ action: "weekly", on: false }));
    expect(posts[1]).not.toHaveProperty("terms");
  });

  it("keeps the shown terms in an open sheet when they change behind it, and sends their version", async () => {
    setViewport(width);
    const { state, posts } = serve({});
    const { user, queryClient } = renderApp({ route: "/settings/projects/altitude" });
    await user.click(await weekly());
    const sheet = await screen.findByRole("dialog", { name: terms.title });
    state.terms = { ...terms, version: "ffffffffffff", body: "Claude Opus 5.5 reads counts from this project's tasks.",
      sent: "Counts and short chat quotes · Claude Opus 5.5 on your plan · capped per run, plus one L3 check" };
    await act(() => queryClient.refetchQueries({ queryKey: ["project", "altitude", "improvement"] }));
    await waitFor(() => expect(screen.getByText(state.terms!.sent)).toBeInTheDocument());
    expect(within(sheet).getByText(terms.body)).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(posts).toEqual([expect.objectContaining({ action: "weekly", on: true, terms: terms.version })]));
  });

  it("runs now without the sheet once agreed, and the running line with Stop replaces the button", async () => {
    setViewport(width);
    const { posts } = serve({ agreed: true });
    const { user } = renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    await user.click(within(area).getByRole("button", { name: "Get suggestions now" }));
    await within(area).findByText("Reading 6 tasks");
    expect(within(area).queryByRole("button", { name: "Get suggestions now" })).not.toBeInTheDocument();
    expect(posts).toEqual([expect.objectContaining({ action: "run" })]);
    expect(posts[0]).not.toHaveProperty("terms");
    await user.click(within(area).getByRole("button", { name: "Stop" }));
    await within(area).findByRole("button", { name: "Get suggestions now" });
    expect(posts[1]).toEqual(expect.objectContaining({ action: "stop", ref: "0123456789ab" }));
  });

  it("explains an unavailable Get suggestions now on press and sends nothing", async () => {
    setViewport(width);
    const reason = "Needs 5 finished tasks since Oct 12; 3 so far.";
    const { posts } = serve({ agreed: true, weekly: true, now: { line: "Reads the 3 tasks finished since Oct 12.", unavailable: reason },
      line: "Skipped Oct 12: 3 tasks had finished. Next: Monday, Oct 19, if 2 more tasks finish." });
    const { user } = renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    expect(within(area).getByText(/^Skipped Oct 12: 3 tasks had finished\./)).toBeInTheDocument();
    const button = within(area).getByRole("button", { name: "Get suggestions now" });
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAccessibleDescription(reason);
    expect(within(area).queryByText("Reads the 3 tasks finished since Oct 12.")).not.toBeInTheDocument();
    await user.click(button);
    expect(within(area).getByText(reason)).toBeVisible();
    expect(posts).toEqual([]);
  });

  it("shows a refusal in red and the switch at its saved value", async () => {
    setViewport(width);
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    serve({ agreed: true }, () => undefined);
    const fetcher = vi.mocked(fetch);
    const real = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (input, init) => {
      if (String(input) === "/api/improve") {
        await held;
        return json({ error: "Improvement suggestions cover only the Altitude project for now." }, 409);
      }
      return real(input, init);
    });
    const { user } = renderApp({ route: "/settings/projects/altitude" });
    await user.click(await weekly());
    const area = await section();
    expect(within(area).getByText("Saving…")).toBeInTheDocument();
    expect(await weekly()).toBeDisabled();
    await act(async () => release());
    expect(await within(area).findByRole("alert")).toHaveTextContent("only the Altitude project");
    expect(await weekly()).not.toBeChecked();
    expect(within(area).queryByText("Saving…")).not.toBeInTheDocument();
  });

  it("sums past suggestions once there is a run, with Latest opening the latest card in chat", async () => {
    setViewport(width);
    serve({ agreed: true, past: { line: "4 runs · 7 suggestions · 3 became tasks · 2 dismissed", latest: "0123456789ab" } });
    const { user, router } = renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    expect(await within(area).findByText(/^4 runs · 7 suggestions · 3 became tasks · 2 dismissed/)).toBeVisible();
    await user.click(within(area).getByRole("link", { name: "Latest" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/altitude"));
    expect(router.state.location.hash).toBe("#improvement-0123456789ab");
  });

  it("has no Past suggestions line before the first run", async () => {
    setViewport(width);
    serve({});
    renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    expect(within(area).getByText(terms.sent)).toBeInTheDocument();
    expect(within(area).queryByText(/ runs? · /)).toBeNull();
  });

  it("has no Latest when no review has had a card", async () => {
    setViewport(width);
    serve({ past: { line: "1 run · 0 suggestions · 0 became tasks · 0 dismissed", latest: null } });
    renderApp({ route: "/settings/projects/altitude" });
    const area = await section();
    expect(within(area).getByText("1 run · 0 suggestions · 0 became tasks · 0 dismissed")).toBeVisible();
    expect(within(area).queryByRole("link", { name: "Latest" })).toBeNull();
  });

  it("has no section where reviews are not available", async () => {
    setViewport(width);
    serve({ available: false });
    renderApp({ route: "/settings/projects/altitude" });
    await screen.findByRole("heading", { name: "Routing" });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => url === "/api/improve/altitude")).toBe(true));
    expect(screen.queryByRole("region", { name: "Improvement suggestions" })).not.toBeInTheDocument();
  });
});
