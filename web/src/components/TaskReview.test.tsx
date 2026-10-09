import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewSchema, TaskViewSchema } from "../data/api";
import type { Review } from "../data/api";
import { renderApp, setViewport } from "../test/render";

const route = "/projects/atlas/tasks/index";
const snapshot = { head: "abc123", base: "base123", tree: "tree123", context_hash: "context123", captured_context_hash: "captured123", selected_owner_evidence: true, context_ids: ["evidence"], limitations: ["Original image bytes are not reviewed."] };
const completed = ReviewSchema.parse({ id: "review-one", requested_at: "2026-09-22T12:00:00Z", requested_by: "l2", state: "completed", engine_label: "Engine B", model: "Default", same_engine: true, focus: "Pagination correctness", coverage: "assessed", snapshot,
  reconciled: { ...snapshot, head: "def456", reason: "The added test covers the fix." },
  result: { text: "Pagination is sound once expired cursors are handled.", findings: [{ id: "one", severity: "high", title: "Expired cursor", body: "Expired cursors restart pagination." }, { id: "two", severity: "low", title: "Empty page", body: "The last page has no cursor." }] },
  dispositions: [{ finding_id: "one", disposition: "fixed", reason: "Added the expiration response." }, { finding_id: "two", disposition: "dismissed", reason: "The storage contract ends pagination." }],
  can_withdraw: true, can_cancel: false, can_again: true });
const requested = ReviewSchema.parse({ ...completed, id: "review-two", requested_by: "operator", state: "requested", result: null, reconciled: null, snapshot: null, dispositions: [], coverage: "unknown", waiting: "owner", can_again: false });
const empty = TaskViewSchema.parse({ slug: "index", title: "Keep pagination stable", state: "running", messages: [{ id: "intro", role: "l2", text: "Checking pagination." }],
  review: { subjects: { proposal: { available: true, why: "", latest: null }, changes: { available: true, why: "", latest: null } }, history: [] } });
function withReviews(...reviews: Review[]) {
  const subjects = { ...empty.review!.subjects };
  for (const review of reviews) subjects[review.subject] = { available: review.can_again, why: "", latest: review,
    open: reviews.filter((entry) => entry.subject === review.subject && entry.state !== "withdrawn").reduce((sum, entry) => sum + entry.unresolved.length, 0) };
  return { ...empty, review: { subjects, history: reviews },
    messages: [...empty.messages!, ...reviews.map((review) => ({ id: `anchor-${review.id}`, role: "system" as const, text: "Review requested", review_id: review.id }))] };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function setup(initial = empty, post?: () => Response | Promise<Response>) {
  let task = initial;
  let failedRead = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/task/review")) return post ? post() : json({ ok: true, review: requested });
    if (url.includes("/api/task/")) return failedRead ? json({ error: "read failed" }, 503) : json(task);
    if (url.includes("/api/overview")) return json({ projects: [{ name: "atlas", managed: true }], queue: [], fyis: [], wip: { machine: 1, per_project: {}, waiting: [] }, quota: { known: false }, engines: [] });
    if (url.includes("/api/project/")) return json({ name: "atlas", tasks: [] });
    if (url.includes("/api/transcript/")) return json({ project: "atlas", slug: "index", events: [], cursor: 0 });
    return json({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  const app = renderApp({ route });
  const posts = () => fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review")).map(([, init]) => JSON.parse(init!.body as string));
  const settled = () => waitFor(() => expect(app.queryClient.isMutating() + app.queryClient.isFetching()).toBe(0));
  return { ...app, fetcher, posts, settled, failRead: (value: boolean) => { failedRead = value; }, update: async (next: typeof initial) => {
    task = next;
    // A refetch started by an earlier receipt carries the old saved state; let it land first.
    await waitFor(() => expect(app.queryClient.isFetching()).toBe(0));
    await act(async () => { app.queryClient.setQueryData(["task", "atlas", "index"], task); });
  } };
}
const openDetails = async (user: ReturnType<typeof setup>["user"]) => {
  await user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
  return screen.getByRole("dialog", { name: "Task details" });
};
const card = (id: string) => within(document.getElementById(`review-${id}`)!);
afterEach(() => setViewport(1024));

describe("Adversarial review", () => {
  it.each([390, 1440])("task details shows a box per kind and a request queues once at %i", async (width) => {
    setViewport(width);
    let accept!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { accept = resolve; });
    const app = setup(empty, () => pending);
    const details = await openDetails(app.user);
    for (const name of ["Proposal review", "Implementation review"]) {
      const box = within(within(details).getByRole("region", { name }));
      expect(box.getByText("Not reviewed yet")).toBeVisible();
      expect(box.getByRole("button", { name: "Request" })).toHaveClass("btn");
    }
    const request = within(within(details).getByRole("region", { name: "Implementation review" })).getByRole("button", { name: "Request" });
    await app.user.click(request);
    expect(request).toBeDisabled();
    expect(app.posts()).toEqual([expect.objectContaining({ action: "request", subject: "changes" })]);
    expect(app.posts()[0]).not.toHaveProperty("review_id");
    await app.update(withReviews(requested));
    await act(async () => { accept(json({ ok: true, review: requested })); });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Task details" })).toBeNull());
    expect(card("review-two").getByText("· requested by you")).toBeVisible();
    expect(card("review-two").getByText("Queued. L2 starts it after its current step.")).toBeVisible();
    expect(app.posts()).toHaveLength(1);
  });

  it.each([
    ["queued while L2 works", { waiting: "owner" }, "· requested by you", "Queued. L2 starts it after its current step."],
    ["queued until L2 resumes", { waiting: "resume" }, "· requested by you", "Queued. L2 starts it when it resumes."],
    ["requested by L2", { requested_by: "l2", subject: "proposal" }, "· requested by L2", "L2 asked for a review of its proposal and starts it shortly."],
    ["waiting for the reviewer", { waiting: "reviewer" }, "· requested by you", "Waiting for the reviewer: another review is running on this machine."],
    ["in progress", { state: "running", requested_by: "l2", can_cancel: true }, "· in progress", "Reviewing the implementation…"],
    ["stopping", { state: "running", cancel_requested: true }, "· stopping", "Stopping the reviewer…"],
    ["didn’t finish", { state: "failed", error: "The reviewer stopped without reading the captured changes.", can_again: true }, "· didn’t finish", "The reviewer stopped without reading the captured changes."],
    ["skipped", { state: "withdrawn", withdrawn_by: "operator", can_withdraw: false }, "· skipped", "Skipped by you"],
  ] as const)("a card shows %s with who asked and what it waits for", async (_, change, state, text) => {
    const review = ReviewSchema.parse({ ...requested, ...change });
    setup(withReviews(review));
    expect(await screen.findByText(state)).toBeVisible();
    expect(card(review.id).getByText(text)).toBeVisible();
  });

  it("stop and try again act on the exact review", async () => {
    const running = ReviewSchema.parse({ ...requested, state: "running", can_cancel: true });
    const app = setup(withReviews(running));
    await screen.findByText("· in progress");
    await app.user.click(card(running.id).getByRole("button", { name: "Stop" }));
    await app.settled();
    const failed = ReviewSchema.parse({ ...requested, state: "cancelled", can_again: true });
    await app.update(withReviews(failed));
    await app.user.click(await card(failed.id).findByRole("button", { name: "Try again" }));
    expect(app.posts()).toEqual([expect.objectContaining({ action: "cancel", review_id: running.id }),
      expect.objectContaining({ action: "request", review_id: failed.id, subject: "changes" })]);
  });

  it.each([390, 1440])("a done review shows the verdict and counts, and View opens its findings at %i", async (width) => {
    setViewport(width);
    const app = setup(withReviews(completed));
    const field = await screen.findByLabelText("Message the L2");
    await app.user.type(field, "Keep my draft");
    expect(card("review-one").getByText("Pagination is sound once expired cursors are handled.")).toBeVisible();
    expect(card("review-one").getByText("2 findings, both resolved")).toBeVisible();
    expect(screen.queryByText("Expired cursor")).toBeNull();
    const details = await openDetails(app.user);
    const box = within(within(details).getByRole("region", { name: "Implementation review" }));
    expect(box.getByText("Pagination is sound once expired cursors are handled.")).toBeVisible();
    expect(box.getByRole("button", { name: "Review again" })).toBeVisible();
    await app.user.click(box.getByRole("button", { name: "View" }));
    expect(app.router.state.location.search).toBe("?review=review-one");
    expect(document.getElementById("review-review-one")).toHaveFocus();
    const opened = card("review-one");
    expect(opened.getByText("Expired cursor")).toBeVisible();
    expect(opened.getByText("Fixed")).toBeVisible();
    expect(opened.getByText("Dismissed")).toBeVisible();
    expect(opened.getByText("Added the expiration response.")).toBeVisible();
    expect(opened.getByText("Engine B · same engine as the task")).toBeVisible();
    await app.user.click(opened.getByRole("button", { name: "Technical details" }));
    expect(opened.getByText(/Reviewed head abc123/)).toBeVisible();
    expect(opened.getByText(/Selected L2 evidence: evidence/)).toBeVisible();
    expect(opened.getByText(/Original image bytes are not reviewed/)).toBeVisible();
    await app.user.click(opened.getByRole("button", { name: "Hide implementation review details" }));
    expect(screen.queryByText("Expired cursor")).toBeNull();
    expect(field).toHaveValue("Keep my draft");
    expect(app.posts()).toHaveLength(0);
  });

  it("review again names the latest review, which stays in task details while open findings block merge", async () => {
    const app = setup(withReviews(completed));
    await app.user.click(within(within(await openDetails(app.user)).getByRole("region", { name: "Implementation review" })).getByRole("button", { name: "Review again" }));
    expect(app.posts()).toEqual([expect.objectContaining({ action: "request", subject: "changes", review_id: "review-one" })]);
    await app.settled();
    const open = ReviewSchema.parse({ ...completed, unresolved: ["one"], can_again: false, earlier: true,
      dispositions: [{ finding_id: "one", disposition: "open", reason: "Needs a systemd experiment first." }, completed.dispositions[1]] });
    await app.update(withReviews(open));
    expect(await card("review-one").findByText("1 open, blocks merge")).toBeVisible();
    expect(card("review-one").getByText(/earlier version/)).toBeVisible();
    const box = within(within(await openDetails(app.user)).getByRole("region", { name: "Implementation review" }));
    expect(box.getByText("1 open, blocks merge")).toBeVisible();
    expect(box.queryByRole("button", { name: "Review again" })).toBeNull();
  });

  it("shows only the latest iteration of a kind, with earlier ones inside it", async () => {
    const first = ReviewSchema.parse({ ...completed, id: "first", result: { ...completed.result!, text: "The change misses the landing gate." }, can_withdraw: false, can_again: false });
    const second = ReviewSchema.parse({ ...completed, id: "second", previous: "first" });
    const task = withReviews(first, second);
    const { user } = setup(task);
    await screen.findByText("Pagination is sound once expired cursors are handled.");
    expect(document.getElementById("review-first")).toBeNull();
    expect(card("second").getByText("Review 2", { exact: false })).toBeVisible();
    await user.click(card("second").getByRole("button", { name: "Show implementation review details" }));
    expect(card("second").getByText("Earlier reviews")).toBeVisible();
    expect(card("second").getByText(/The change misses the landing gate/)).toBeVisible();
  });

  it("an earlier review an additional one left in the merge gate stays visible, inspectable and skippable", async () => {
    const original = ReviewSchema.parse({ ...completed, id: "original", requested_by: "operator", unresolved: ["one"], can_again: false,
      dispositions: [{ finding_id: "one", disposition: "open", reason: "Needs the storage owner's decision." }, completed.dispositions[1]] });
    const additional = ReviewSchema.parse({ ...completed, id: "additional", additional: true, result: { text: "The addendum is sound.", findings: [] }, dispositions: [], can_again: false });
    const app = setup(withReviews(original, additional));
    await screen.findByText("The addendum is sound.");
    expect(card("additional").getByText("1 open in an earlier review, blocks merge")).toBeVisible();
    expect(document.querySelector("#review-additional .review-icon")).toHaveClass("review-icon-warn");
    const details = await openDetails(app.user);
    const box = within(within(details).getByRole("region", { name: "Implementation review" }));
    expect(box.getByText("1 open in an earlier review, blocks merge")).toBeVisible();
    expect(box.queryByRole("button", { name: "Review again" })).toBeNull();
    await app.user.keyboard("{Escape}");
    await app.user.click(card("additional").getByRole("button", { name: "Show implementation review details" }));
    const earlier = card("additional").getByRole("button", { name: /Review 1/ });
    expect(earlier).toHaveAttribute("aria-expanded", "false");
    expect(within(earlier).getByText("1 open, blocks merge")).toBeVisible();
    await app.user.click(earlier);
    expect(card("additional").getByText("Needs the storage owner's decision.")).toBeVisible();
    const entry = within(earlier.closest<HTMLElement>(".review-earlier-entry")!);
    await app.user.click(entry.getByRole("button", { name: "Skip review" }));
    await app.user.click(within(entry.getByRole("group", { name: "Skip review" })).getByRole("button", { name: "Skip review" }));
    expect(app.posts()).toEqual([{ project: "atlas", slug: "index", action: "withdraw", review_id: "original" }]);
  });

  it("skip asks no reason and records the operator's choice", async () => {
    const app = setup(withReviews(requested));
    await screen.findByText("· requested by you");
    await app.user.click(card("review-two").getByRole("button", { name: "Show implementation review details" }));
    await app.user.click(card("review-two").getByRole("button", { name: "Skip review" }));
    expect(card("review-two").getByText(/no longer blocks merging/)).toBeVisible();
    expect(card("review-two").queryByRole("textbox")).toBeNull();
    await app.user.click(within(card("review-two").getByRole("group", { name: "Skip review" })).getByRole("button", { name: "Skip review" }));
    expect(app.posts()).toEqual([{ project: "atlas", slug: "index", action: "withdraw", review_id: "review-two" }]);
  });

  it("requires a successful status read after an uncertain POST and preserves the draft", async () => {
    const app = setup(empty, () => { app.failRead(true); return json({ error: "lost receipt" }, 502); });
    const field = await screen.findByLabelText("Message the L2");
    await app.user.type(field, "Unsent context");
    const details = await openDetails(app.user);
    const box = () => within(within(details).getByRole("region", { name: "Implementation review" }));
    await app.user.click(box().getByRole("button", { name: "Request" }));
    await waitFor(() => expect(within(details).getByRole("button", { name: "Refresh review status" })).toBeEnabled());
    expect(box().getByRole("button", { name: "Request" })).toBeDisabled();
    app.failRead(false);
    await app.update(withReviews(requested));
    await app.user.click(within(details).getByRole("button", { name: "Refresh review status" }));
    await app.user.click(box().getByRole("button", { name: "View" }));
    expect(field).toHaveValue("Unsent context");
    expect(app.posts()).toHaveLength(1);
  });

  it("an unavailable kind says why without a button, and a denied request claims nothing", async () => {
    const unavailable = { ...empty, review: { history: [], subjects: { proposal: { available: false, why: "No reviewer is available.", latest: null, open: 0 }, changes: { available: true, why: "", latest: null, open: 0 } } } };
    const app = setup(unavailable, () => json({ error: "denied" }, 403));
    const details = await openDetails(app.user);
    const proposal = within(within(details).getByRole("region", { name: "Proposal review" }));
    expect(proposal.getByText("No reviewer is available.")).toBeVisible();
    expect(proposal.queryByRole("button")).toBeNull();
    await app.user.click(within(within(details).getByRole("region", { name: "Implementation review" })).getByRole("button", { name: "Request" }));
    await waitFor(() => expect(within(details).getByText("You do not have permission to request or change this review.")).toBeVisible());
    expect(document.querySelector(".review-card")).toBeNull();
  });

  it("a finished task keeps reviewed kinds without buttons", async () => {
    const done = { ...withReviews(ReviewSchema.parse({ ...completed, can_again: false, can_withdraw: false })), state: "done" };
    delete (done.review.subjects as Record<string, unknown>).proposal;
    const { user } = setup(done);
    const details = await openDetails(user);
    expect(within(details).queryByRole("region", { name: "Proposal review" })).toBeNull();
    expect(within(within(details).getByRole("region", { name: "Implementation review" })).queryByRole("button", { name: /Request|Review again|Try again/ })).toBeNull();
  });

  it("an old request receipt does not close another task’s details after navigation", async () => {
    let accept!: (response: Response) => void;
    const receipt = new Promise<Response>((resolve) => { accept = resolve; });
    const app = setup(empty, () => receipt);
    const details = await openDetails(app.user);
    await app.user.click(within(within(details).getByRole("region", { name: "Implementation review" })).getByRole("button", { name: "Request" }));
    await act(async () => { await app.router.navigate("/projects/atlas/tasks/other-task"); });
    await openDetails(app.user);
    await act(async () => { accept(json({ ok: true, review: requested })); });
    expect(screen.getByRole("dialog", { name: "Task details" })).toBeVisible();
  });
});
