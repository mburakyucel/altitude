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
  review: { available: true, why: "", engine_label: "Engine B", model: "Default", allowance_known: false, latest: null, history: [] } });
const withReview = (review = completed) => ({ ...empty, review: { ...empty.review!, latest: review, history: [review] },
  messages: [...empty.messages!, { id: "anchor", role: "system" as const, text: "Review requested", review_id: review.id }] });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function setup(initial = empty, post?: () => Response | Promise<Response>) {
  let task = initial;
  let failedRead = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
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
  it.each([390, 1440])("reflects L2 review, coverage and dispositions without another request at %i", async (width) => {
    setViewport(width);
    const { user, fetcher, router } = setup(withReview());
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Keep my draft");
    expect(screen.getByText("Reviewed an earlier revision; L2 assessed the later edits.")).toBeVisible();
    expect(screen.getByText("high · Expired cursor")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Task details" }));
    const menu = screen.getByRole("dialog", { name: "Task details" });
    expect(within(menu).queryByRole("button", { name: "Request cross-engine review" })).toBeNull();
    await user.click(within(menu).getByRole("button", { name: "View review" }));
    expect(router.state.location.search).toBe("?review=review-one");
    expect(document.getElementById("review-review-one")).toHaveFocus();
    await user.click(screen.getByText("Review details"));
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
    await app.user.click(await screen.findByRole("button", { name: "Task details" }));
    expect(screen.getByText(/remaining allowance is unknown/)).toBeVisible();
    const request = screen.getByRole("button", { name: "Request cross-engine review" });
    await app.user.click(request);
    expect(request).toBeDisabled();
    const requested = { ...completed, state: "requested" as const, result: null, reconciled: null, coverage: "unknown" as const, can_withdraw: true };
    await app.update(withReview(requested));
    await act(async () => { accept(json({ ok: true, review: requested })); });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Request cross-engine review" })).toBeNull());
    expect(screen.queryByRole("dialog", { name: "Task details" })).toBeNull();
    expect(screen.getByText("L2 requested a cross-engine review · waiting for L2")).toBeVisible();
    expect(app.fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"))).toHaveLength(1);
  });

  it("requires a successful status read after an uncertain POST and preserves the draft", async () => {
    const app = setup(empty, () => { app.failRead(true); return json({ error: "lost receipt" }, 502); });
    const field = await screen.findByLabelText("Message the L2");
    await app.user.type(field, "Unsent context");
    await app.user.click(screen.getByRole("button", { name: "Task details" }));
    await app.user.click(screen.getByRole("button", { name: "Request cross-engine review" }));
    const menu = screen.getByRole("dialog", { name: "Task details" });
    await waitFor(() => expect(within(menu).getByRole("button", { name: "Refresh review status" })).toBeEnabled());
    expect(within(menu).getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
    app.failRead(false);
    await app.update(withReview());
    await app.user.click(within(menu).getByRole("button", { name: "Refresh review status" }));
    await app.user.click(within(menu).getByRole("button", { name: "View review" }));
    expect(field).toHaveValue("Unsent context");
    expect(app.fetcher.mock.calls.filter(([url]) => String(url).includes("/api/task/review"))).toHaveLength(1);
  });

  it("shows unavailable and denied requests without claiming a review started", async () => {
    const app = setup({ ...empty, review: { ...empty.review!, available: false, why: "No second engine is configured." } }, () => json({ error: "denied" }, 403));
    await app.user.click(await screen.findByRole("button", { name: "Task details" }));
    expect(screen.getByText("No second engine is configured.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Request cross-engine review" })).toBeDisabled();
    await app.update(empty);
    await app.user.click(screen.getByRole("button", { name: "Request cross-engine review" }));
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
    await app.user.click(await screen.findByRole("button", { name: "Task details" }));
    await app.user.click(screen.getByRole("button", { name: "Request cross-engine review" }));
    await act(async () => { await app.router.navigate("/projects/atlas/tasks/other-task"); });
    await app.user.click(await screen.findByRole("button", { name: "Task details" }));
    await act(async () => { accept(json({ ok: true, review: completed })); });
    expect(screen.getByRole("dialog", { name: "Task details" })).toBeVisible();
  });
});
