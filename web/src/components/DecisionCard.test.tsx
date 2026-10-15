import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DecisionSchema } from "../data/api";
import { ToastProvider } from "../data/Toast";
import { QuestionSet, ReviewDecision, StepCard } from "./DecisionCard";
import type { Step } from "../data/api";
import { ImageViewerHost } from "./MessageImages";
import { ProseScope } from "./Prose";
import { ProseTerminal } from "./CodeBlock";

const installer = "Approve the report root?\n\nRun this once on the host:\n\n```sh\nsudo install -d /srv/reports\nalt ci reports --root /srv/reports\n```\n\nThen `alt task recheck-ci` reads it; see PR #12.";
const base = { project: "atlas", slug: "gate", title: "Gate merges", id: "q1", revision: 1, status: "open", audience: "operator", asked_by: "l2", asked: "2026-09-23T22:00:00Z" };
function mount(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><ToastProvider><MemoryRouter><ImageViewerHost scope="/">
    <ProseScope project="atlas" repository="https://github.com/example/atlas">{node}</ProseScope>
  </ImageViewerHost></MemoryRouter></ToastProvider></QueryClientProvider>);
}

describe("question and review text", () => {
  it.each([false, true])("renders paragraphs and a copyable command block in a question (chat: %s)", (chat) => {
    const decision = DecisionSchema.parse({ ...base, question: installer, options: [{ key: "apply", label: "Install", text: "Install the report root." }], recommended_key: "apply" });
    const { container } = mount(<QuestionSet decisions={[decision]} chat={chat} />);
    const question = container.querySelector(".decision-question")!;
    expect(question.tagName).toBe("DIV");
    expect([...question.querySelectorAll(":scope > .session-prose > *")].map((node) => node.tagName)).toEqual(["P", "P", "DIV", "P"]);
    expect(question.querySelector("pre.session-code")?.textContent).toBe("sudo install -d /srv/reports\nalt ci reports --root /srv/reports");
    expect(question.querySelector("p code")?.textContent).toBe("alt task recheck-ci");
    expect(screen.getByRole("link", { name: "PR #12" })).toHaveAttribute("href", "https://github.com/example/atlas/pull/12");
    expect(screen.getByRole("group")).toHaveAttribute("aria-label", installer);
    expect(screen.getByRole("button", { name: "Install" })).toBeEnabled();
  });
  it("keeps a one-line question compact", () => {
    const decision = DecisionSchema.parse({ ...base, question: "Keep the old index for `7` days?" });
    const { container } = mount(<QuestionSet decisions={[decision]} />);
    const question = container.querySelector("p.decision-question")!;
    expect(question.querySelector("div, pre")).toBeNull();
    expect(question.textContent).toBe("Keep the old index for 7 days?");
    expect(screen.getByRole("textbox", { name: "Your answer to: Keep the old index for `7` days?" })).toBeVisible();
  });
  it("renders a review card's hold reason with paragraphs and a code block, and a one-line reason compact", () => {
    const review = DecisionSchema.parse({ ...base, kind: "review", pr: 7, question: "Review PR #7 before merge", detail: "Two owners share this file.\n\n```sh\nalt pr 7\n```" });
    const { container, unmount } = mount(<ReviewDecision decision={review} repository="https://github.com/example/atlas" />);
    expect(container.querySelector("p.decision-question")?.textContent).toBe("Review PR #7 before merge");
    expect(container.querySelector("div.decision-why pre.session-code")?.textContent).toBe("alt pr 7");
    expect(container.querySelectorAll(".decision-why p")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Approve merge" })).toBeEnabled();
    unmount();
    mount(<ReviewDecision decision={{ ...review, detail: "Operator review of the release notes" }} />);
    expect(document.querySelector("p.decision-why")?.textContent).toBe("Operator review of the release notes");
  });
  it("leads a group with its open question and folds closed members into one earlier row", () => {
    const closed = (id: string, question: string, disposition: string) => DecisionSchema.parse({ ...base, id, question, status: "resolved", group_id: "g",
      recommendation: { text: `Recommended for ${id}`, label: "Use", why: "" }, resolution: { disposition, text: `Closed ${id}`, at: "2026-09-23T21:00:00Z", by: "l2" } });
    const current = DecisionSchema.parse({ ...base, id: "q3", group_id: "g", question: "Merge the fix today?", options: [{ key: "yes", label: "Merge", text: "Merge today." }],
      recommended_key: "yes", recommendation: { text: "Merge today.", label: "Merge", why: "The fix is green." } });
    const wait = DecisionSchema.parse({ ...base, id: "q4", group_id: "g", audience: "l3", question: "Waiting for the runner image." });
    const { container } = mount(<QuestionSet decisions={[closed("q1", "Publish rc.2?", "withdrawn"), closed("q2", "Rerun CI?", "answered"), current, wait]} group={{ id: "g", revision: 3, anchor_id: "a", questions: [] }} chat />);
    const earlier = container.querySelector("details.question-earlier") as HTMLDetailsElement;
    expect(earlier.open).toBe(false);
    expect(earlier.querySelector("summary")?.textContent).toBe("2 earlier questions");
    expect([...earlier.querySelectorAll("[data-question-id]")].map((node) => node.getAttribute("data-question-id"))).toEqual(["q1", "q2"]);
    expect(screen.getByText("1 question to answer")).toBeVisible();
    expect(screen.getByText("Merge the fix today?")).toBeVisible();
    expect(screen.getByRole("button", { name: /Merge/ })).toHaveAttribute("data-recommended", "true");
    expect(screen.getByText("The fix is green.")).toBeVisible();
    expect(screen.getByText("Waiting for the runner image.").closest(".question-body")?.textContent).toContain("L3 is handling this");
    expect(screen.queryByText("Recommended for q2")).not.toBeVisible();
  });
  it("shows every member of a closed group without folding", () => {
    const closed = DecisionSchema.parse({ ...base, status: "resolved", question: "Publish rc.2?", resolution: { disposition: "answered", text: "Published.", at: "2026-09-23T21:00:00Z", by: "l2" } });
    const { container } = mount(<QuestionSet decisions={[closed]} chat />);
    expect(container.querySelector("details.question-earlier")).toBeNull();
    expect(screen.getByText("Decision recorded")).toBeVisible();
    expect(screen.getByText(/^L2 · /)).toBeVisible();
  });
  it("names the operator as you when they close a question, never by the stored identity", () => {
    const closed = DecisionSchema.parse({ ...base, status: "resolved", question: "Publish rc.2?", resolution: { disposition: "rejected", text: "Task rejected", at: "2026-09-23T21:00:00Z", by: "operator" } });
    mount(<QuestionSet decisions={[closed]} chat />);
    expect(screen.getByText(/^You · /)).toBeVisible();
    expect(screen.queryByText(/operator · /)).toBeNull();
  });
});

describe("an operator step (SPEC.md §3.8.4)", () => {
  const step: Step = { project: "atlas", slug: null, kind: "step", id: "s1", asked_by: "l3", asked: "2026-10-15T09:00:00Z",
    alert_key: "atlas:step:s1", question: "Restore the checkout.\n\n```run\nexample-tool repair\n```", tasks: [{ slug: "gate", title: "Gate merges" }] };
  it("offers its command for the project terminal, and only Copy on a saved read", () => {
    const fresh = mount(<StepCard step={step} />);
    expect(screen.getByRole("button", { name: "Open in terminal" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Gate merges" })).toHaveAttribute("href", "/projects/atlas/tasks/gate");
    expect(screen.getByRole("link", { name: "Open project chat" })).toHaveAttribute("href", "/projects/atlas");
    fresh.unmount();
    mount(<StepCard step={step} disabled />);
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
    const open = screen.getByRole("button", { name: "Open in terminal" });
    expect(open).toHaveAttribute("aria-disabled", "true");
    expect(open).toHaveAccessibleDescription("Refresh Needs you before running this.");
  });
});

describe("a question's command run in the task terminal (SPEC.md §3.8.5)", () => {
  const signIn = DecisionSchema.parse({ ...base, question: "Sign in from the task terminal.\n\n```run\nexample-tool sign-in\n```" });
  const at = new Date(Date.now() - 60_000).toISOString();
  it.each([
    [true, "Ran in the terminal", "Waiting for the L2 to check it · 1 min ago", "btn btn-ghost"],
    [false, "The terminal closed before this command finished", "1 min ago", "btn btn-primary"],
  ])("says how it went and keeps the question answerable (finished: %s)", (finished, headline, line, button) => {
    mount(<ProseTerminal value={{ open: () => {} }}><QuestionSet decisions={[{ ...signIn, ran: { at, command: "example-tool sign-in", finished } }]} /></ProseTerminal>);
    const status = screen.getByRole("status");
    expect(status.querySelector("b")?.textContent).toBe(headline);
    expect(status.querySelector("p")?.textContent).toBe(line);
    expect(status.querySelector(".review-icon")).toHaveClass(finished ? "review-icon-wait" : "review-icon-warn");
    // Running it again stays offered; once it finished, as a secondary action.
    expect(screen.getByRole("button", { name: "Open in terminal" })).toHaveAttribute("class", button);
    expect(screen.getByRole("textbox", { name: /^Your answer to:/ })).toBeEnabled();
  });
  it("quiets only the command that ran when the question hands several", () => {
    const two = { ...signIn, question: "Sign in, then sync.\n\n```run\nexample-tool sign-in\n```\n\n```run\nexample-tool sync\n```" };
    mount(<ProseTerminal value={{ open: () => {} }}><QuestionSet decisions={[{ ...two, ran: { at, command: "example-tool sign-in", finished: true } }]} /></ProseTerminal>);
    expect(screen.getAllByRole("button", { name: "Open in terminal" }).map((button) => button.className)).toEqual(["btn btn-ghost", "btn btn-primary"]);
  });
  it("has no note before the command runs, and an answer's receipt replaces it", () => {
    const { unmount } = mount(<ProseTerminal value={{ open: () => {} }}><QuestionSet decisions={[signIn]} /></ProseTerminal>);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Open in terminal" })).toHaveAttribute("class", "btn btn-primary");
    unmount();
    mount(<QuestionSet decisions={[{ ...signIn, ran: { at, command: "example-tool sign-in", finished: true },
      response: { text: "It printed a warning.", at, message_id: "m1" } }]} />);
    expect(screen.queryByText("Ran in the terminal")).toBeNull();
    expect(screen.getByText("It printed a warning.")).toBeVisible();
  });
});

describe("review status on proposal and merge questions (SPEC.md §3.8.3)", () => {
  const shown = { url: "/projects/atlas/tasks/gate/view/q1/1", available: true, newer_url: null, images: [],
    document: { name: "settings.md", path: "design/settings.md", heading: "Settings layout", excerpt: "One page, three sections." } };
  const design = DecisionSchema.parse({ ...base, question: "Build the settings layout as shown?", shown });
  const merge = DecisionSchema.parse({ ...base, id: null, revision: null, kind: "review", pr: 7, question: "Review PR #7 before merge" });
  const done = { id: "r1", state: "completed", reconciled: true };
  const states: [string, Record<string, unknown>, string, string][] = [
    ["not done", { id: null, state: "none" }, "not done", "warn"],
    ["requested", { id: "r1", state: "requested" }, "requested", "wait"],
    ["in progress", { id: "r1", state: "running" }, "in progress", "run"],
    ["done without findings", { ...done, findings: 0 }, "done, no findings", "ok"],
    ["done, findings not yet answered", { ...done, reconciled: false, findings: 3 }, "done, 3 findings pending", "warn"],
    ["done with unresolved findings", { ...done, findings: 3, open: 1 }, "done, 1 open finding", "warn"],
    ["done and reconciled", { ...done, findings: 2 }, "done, 2 findings resolved", "ok"],
    ["done and reconciled, of an earlier version", { ...done, findings: 1, earlier: true }, "done, 1 finding resolved · earlier version", "warn"],
    ["didn't finish", { id: "r1", state: "failed" }, "didn’t finish", "warn"],
    ["skipped by L2, with its reason", { id: "r1", state: "withdrawn", skipped_by: "l2", reason: "One-line copy change." }, "skipped by L2 · One-line copy change.", "skip"],
    ["skipped by you", { id: "r1", state: "withdrawn", skipped_by: "operator" }, "skipped by you", "skip"],
    ["withdrawn over another review's open finding", { id: "r1", state: "withdrawn", skipped_by: "operator", open: 1 }, "skipped by you · 1 open finding", "warn"],
  ];
  it.each(states)("a design question's proposal review: %s", (_name, review, text, tone) => {
    const { container } = mount(<QuestionSet decisions={[{ ...design, review: DecisionSchema.shape.review.parse({ subject: "proposal", ...review }) }]} from="needs" />);
    const line = container.querySelector(".question-review")!;
    expect(line.textContent).toBe(`Proposal review · ${text}`);
    expect(line.querySelector(`.review-icon-${tone}`)).not.toBeNull();
    // A review opens its card in the task conversation; no review is plain text, never a request.
    if (review.id) expect(screen.getByRole("link", { name: `Proposal review · ${text}` })).toHaveAttribute("href", `/projects/atlas/tasks/gate?review=${review.id}`);
    else expect(line.tagName).toBe("P");
    // The line sits between the shown files and the recommendation's choices.
    expect(container.querySelector(".shown-card + .question-review")).toBe(line);
  });
  it.each(states)("the merge card's implementation review: %s", (_name, review, text, tone) => {
    const { container } = mount(<ReviewDecision decision={{ ...merge, review: DecisionSchema.shape.review.parse({ subject: "changes", ...review }) }} />);
    const line = container.querySelector(".question-review")!;
    expect(line.textContent).toBe(`Implementation review · ${text}`);
    expect(line.querySelector(`.review-icon-${tone}`)).not.toBeNull();
    expect(line.tagName).toBe(review.id ? "A" : "P");
  });
  it("a proposal approval without a design preview shows its proposal review under the question", () => {
    const question = DecisionSchema.parse({ ...base, question: "Adopt the queue-per-project design?", review: { subject: "proposal", id: "p1", state: "withdrawn", skipped_by: "l2", reason: "Small, reversible change." } });
    const { container } = mount(<QuestionSet decisions={[question]} chat />);
    expect(screen.getByRole("link", { name: "Proposal review · skipped by L2 · Small, reversible change." })).toHaveAttribute("href", "/projects/atlas/tasks/gate?review=p1");
    expect(container.querySelector(".shown-card")).toBeNull();
  });
  it("a design question opens its shown files in the reader; an earlier preview is a card that does not open", () => {
    const earlier = DecisionSchema.parse({ ...base, id: "q2", question: "Build the old layout?",
      shown: { url: null, available: false, newer_url: null, images: [], document: { name: "Old layout", path: "", heading: "", excerpt: "" } } });
    mount(<QuestionSet decisions={[design, earlier]} chat />);
    expect(screen.getByRole("link", { name: "Open settings.md" })).toHaveAttribute("href", "/projects/atlas/tasks/gate/view/q1/1");
    expect(screen.getByText("Settings layout — One page, three sections.")).toBeVisible();
    expect(screen.getByText("Old layout").closest("a")).toBeNull();
    expect(screen.getByText("Preview unavailable")).toBeVisible();
  });
  it("the held PR's merge question shows the implementation review and links its PR beside Send", () => {
    const question = DecisionSchema.parse({ ...base, question: "Merge the fix?", pr: 7, review: { subject: "changes", ...done, findings: 0 } });
    const { container, unmount } = mount(<QuestionSet decisions={[question]} chat />);
    expect(screen.getByRole("link", { name: "Implementation review · done, no findings" })).toHaveAttribute("href", "/projects/atlas/tasks/gate?review=r1");
    expect(screen.getByRole("link", { name: "View PR #7" })).toHaveAttribute("href", "https://github.com/example/atlas/pull/7");
    expect(container.querySelector(".question-batch .btn-primary + a.btn-ghost")).not.toBeNull();
    unmount();
    mount(<QuestionSet decisions={[{ ...question, pr: null }]} chat />);
    expect(screen.queryByRole("link", { name: /^View PR/ })).toBeNull();
  });
  it("a question that is neither a proposal nor a merge approval has no line", () => {
    const { container, unmount } = mount(<QuestionSet decisions={[DecisionSchema.parse({ ...base, question: "Where should the backup live?" })]} />);
    expect(container.querySelector(".question-review")).toBeNull();
    unmount();
    const card = mount(<ReviewDecision decision={merge} />);
    expect(card.container.querySelector(".question-review")).toBeNull();
  });
});
