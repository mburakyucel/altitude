import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewSchema, TaskViewSchema } from "../data/api";
import { renderApp, setViewport } from "../test/render";

const route = "/projects/atlas/tasks/index";
const snapshot = { head: "abc123", base: "base123", tree: "tree123", context_hash: "context123", captured_context_hash: "captured123", selected_owner_evidence: true, limitations: ["Original image bytes are not reviewed."] };
const completed = ReviewSchema.parse({ id: "review-one", requested_at: "2026-09-22T12:00:00Z", requested_by: "l2", state: "completed", engine_label: "Engine B", model: "Default", focus: "Pagination correctness", coverage: "assessed", snapshot,
  reconciled: { ...snapshot, head: "def456", reason: "The added test covers the fix." },
  result: { text: "Two findings.", findings: [{ id: "one", severity: "high", title: "Expired cursor", body: "Expired cursors restart pagination." }, { id: "two", severity: "low", title: "Empty page", body: "The last page has no cursor." }] },
  dispositions: [{ finding_id: "one", disposition: "fixed", reason: "Added the expiration response." }, { finding_id: "two", disposition: "dismissed", reason: "The storage contract ends pagination." }],
  can_withdraw: false, can_cancel: false, can_retry: false, can_review_latest: false });
const empty = TaskViewSchema.parse({ slug: "index", title: "Keep pagination stable", state: "running", messages: [{ id: "intro", role: "l2", text: "Checking pagination." }],
  review: { available: true, why: "", engine_label: "Engine B", model: "Default", allowance_known: false, subjects: { proposal: { available: true, why: "", latest: null }, changes: { available: true, why: "", latest: null } }, latest: null, history: [] } });
const withReview = (review = completed) => ({ ...empty, review: { ...empty.review!, subjects: { ...empty.review!.subjects, [review.subject]: { available: true, why: "", latest: review } }, latest: review, history: [review] },
  messages: [...empty.messages!, { id: "anchor", role: "system" as const, text: "Review requested", review_id: review.id }] });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function setup(initial = empty, post?: () => Response | Promise<Response>) {
  let task = initial;
  let failedRead = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/task/review")) return post ? post() : json({ ok: true, review: completed });
    if (url.includes("/api/task/")) return failedRead ? json({ error: "read failed" }, 503) : json(task);
    if (url.includes("/api/overview")) return json({ projects: [{ name: "atlas", managed: true }], queue: [], fyis: [], wip: { machine: 1, per_project: {}, waiting: [] }, quota: { known: false }, engines: [] });
    if (url.includes("/api/project/")) return json({ name: "atlas", tasks: [] });
    if (url.includes("/api/transcript/")) return json({ project: "atlas", slug: "index", events: [], cursor: 0 });
    return json({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  const app = renderApp({ route });
  return { ...app, fetcher, failRead: (value: boolean) => { failedRead = value; }, update: async (next: typeof initial) => { task = next; await act(async () => { app.queryClient.setQueryData(["task", "atlas", "index"], task); }); } };
}
afterEach(() => setViewport(1024));

describe("Review in task chat", () => {
  it.each(["requested", "running", "completed", "failed", "cancelled"] as const)("opens an existing %s proposal alongside changes without invoking a reviewer", async (state) => {
    const proposal = { ...completed, id: "proposal-review", subject: "proposal" as const, state, same_engine: true, fallback_reason: "No alternate engine is available.", can_review_again: state === "completed", coverage: "current" as const,
      snapshot: { ...snapshot, proposal: { id: "proposal", at: "2026-09-22T11:00:00Z", text: "Filter deleted records before applying the page limit." } } };
    const task = withReview();
    task.review.history.push(proposal);
    task.review.subjects.proposal = { available: true, why: "", latest: proposal };
    const { user, fetcher } = setup(task);
    await user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    const menu = screen.getByRole("dialog", { name: "Task details" });
    expect(within(menu).getByRole("button", { name: "View changes review" })).toBeVisible();
    expect(within(menu).queryByRole("button", { name: "Review proposal" })).toBeNull();
    await user.click(within(menu).getByRole("button", { name: "View proposal review" }));
    const row = within(document.getElementById("review-proposal-review")!);
    expect(row.getByText("Filter deleted records before applying the page limit.")).toBeVisible();
    expect(row.getByText(/Separate same-engine reviewer/)).toHaveTextContent("No alternate engine is available.");
    expect(row.getByText(/Implementation is not reviewed/)).toBeVisible();
    expect(row.getByText(/remaining allowance was unknown when requested/)).toBeVisible();
    await user.click(row.getByText("Review details"));
    await user.click(row.getByRole("link", { name: "View captured proposal" }));
    expect(row.getByText("Filter deleted records before applying the page limit.")).toBeVisible();
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"))).toHaveLength(0);
    if (state === "completed") {
      await user.click(row.getByRole("button", { name: "Review again" }));
      const posts = fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"));
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0]![1]!.body as string)).toMatchObject({ action: "rerun", review_id: "proposal-review" });
    }
  });

  it("requests the selected subject and discloses fallback before spending", async () => {
    const task = { ...empty, review: { ...empty.review!, same_engine: true, fallback_reason: "No alternate engine is available." } };
    const { user, fetcher } = setup(task);
    await user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    expect(screen.getByText(/Separate same-engine reviewer/)).toHaveTextContent("No alternate engine is available.");
    await user.click(screen.getByRole("button", { name: "Review proposal" }));
    const posts = fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"));
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]![1]!.body as string)).toMatchObject({ action: "request", subject: "proposal" });
  });
  it.each([390, 1440])("reflects L2 review, coverage and dispositions without another request at %i", async (width) => {
    setViewport(width);
    const { user, fetcher, router } = setup(withReview());
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Keep my draft");
    expect(screen.getByText("Reviewed an earlier revision; L2 assessed the later edits.")).toBeVisible();
    expect(screen.getByText("high · Expired cursor")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    const menu = screen.getByRole("dialog", { name: "Task details" });
    expect(within(menu).queryByRole("button", { name: "Review changes" })).toBeNull();
    await user.click(within(menu).getByRole("button", { name: "View changes review" }));
    expect(router.state.location.search).toBe("?review=review-one");
    expect(document.getElementById("review-review-one")).toHaveFocus();
    expect(screen.getByText("high · Expired cursor")).toBeVisible();
    expect(screen.getByText("Added the expiration response.")).toBeVisible();
    expect(screen.getByText("Selected L2 evidence; all operator and coordinator messages included.")).toBeVisible();
    expect(screen.getByText("Captured context: captured123")).toBeVisible();
    expect(screen.getByText("Original image bytes are not reviewed.")).toBeVisible();
    expect(screen.getByText("The storage contract ends pagination.")).toBeVisible();
    await user.click(screen.getByText("Review details"));
    expect(screen.getByText("high · Expired cursor")).not.toBeVisible();
    expect(field).toHaveValue("Keep my draft");
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("/api/task/review"))).toBe(false);
  });

  it("shows allowance before requesting, suppresses repeats while saving, and reflects authoritative L2 progress", async () => {
    let accept!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { accept = resolve; });
    const app = setup(empty, () => pending);
    await app.user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    expect(screen.getByText(/remaining allowance is unknown/)).toBeVisible();
    const request = screen.getByRole("button", { name: "Review changes" });
    // The only actions in the review section are bordered buttons, never ghost text (SPEC §1.1 affordance check).
    for (const name of ["Review proposal", "Review changes"]) { expect(screen.getByRole("button", { name })).toHaveClass("btn"); expect(screen.getByRole("button", { name })).not.toHaveClass("btn-ghost"); }
    await app.user.click(request);
    expect(request).toBeDisabled();
    const requested = { ...completed, state: "requested" as const, result: null, reconciled: null, coverage: "unknown" as const, can_withdraw: true };
    await app.update(withReview(requested));
    await act(async () => { accept(json({ ok: true, review: requested })); });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Review changes" })).toBeNull());
    expect(screen.queryByRole("dialog", { name: "Task details" })).toBeNull();
    expect(screen.getByText("L2 requested changes review · waiting for L2")).toBeVisible();
    expect(app.fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"))).toHaveLength(1);
  });

  it("requires a successful status read after an uncertain POST and preserves the draft", async () => {
    const app = setup(empty, () => { app.failRead(true); return json({ error: "lost receipt" }, 502); });
    const field = await screen.findByLabelText("Message the L2");
    await app.user.type(field, "Unsent context");
    await app.user.click(screen.getByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    await app.user.click(screen.getByRole("button", { name: "Review changes" }));
    const menu = screen.getByRole("dialog", { name: "Task details" });
    await waitFor(() => expect(within(menu).getByRole("button", { name: "Refresh review status" })).toBeEnabled());
    expect(within(menu).getByRole("button", { name: "Review changes" })).toBeDisabled();
    app.failRead(false);
    await app.update(withReview());
    await app.user.click(within(menu).getByRole("button", { name: "Refresh review status" }));
    await app.user.click(within(menu).getByRole("button", { name: "View changes review" }));
    expect(field).toHaveValue("Unsent context");
    expect(app.fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"))).toHaveLength(1);
  });

  it("shows unavailable and denied requests without claiming a review started", async () => {
    const app = setup({ ...empty, review: { ...empty.review!, available: false, why: "No reviewer is available.", subjects: { proposal: { available: false, why: "No reviewer is available.", latest: null }, changes: { available: false, why: "No reviewer is available.", latest: null } } } }, () => json({ error: "denied" }, 403));
    await app.user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    expect(screen.getAllByText("No reviewer is available.")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Review proposal" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review changes" })).toBeDisabled();
    await app.update(empty);
    await app.user.click(screen.getByRole("button", { name: "Review changes" }));
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByText("You do not have permission to request or change this review.")).toBeVisible());
    expect(screen.queryByText(/review complete/)).toBeNull();
  });

  it("discloses a changed reviewer and unknown allowance before retry", async () => {
    const task = withReview({ ...completed, state: "failed", can_retry: true });
    task.review.engine_label = "Engine C";
    task.review.model = "Next model";
    const { user } = setup(task);
    await user.click(await screen.findByText("Review details"));
    expect(screen.getByText(/Next review: Engine C · Next model/)).toHaveTextContent("remaining allowance is unknown");
    expect(screen.getByRole("button", { name: "Retry review" })).toBeVisible();
  });

  it("an old request receipt does not close another task’s details after navigation", async () => {
    let accept!: (response: Response) => void;
    const receipt = new Promise<Response>((resolve) => { accept = resolve; });
    const app = setup(empty, () => receipt);
    await app.user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    await app.user.click(screen.getByRole("button", { name: "Review changes" }));
    await act(async () => { await app.router.navigate("/projects/atlas/tasks/other-task"); });
    await app.user.click(await screen.findByRole("button", { name: /^(?:Keep pagination stable — )?Task details$/ }));
    await act(async () => { accept(json({ ok: true, review: completed })); });
    expect(screen.getByRole("dialog", { name: "Task details" })).toBeVisible();
  });
});
