import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DecisionSchema } from "../data/api";
import { ToastProvider } from "../data/Toast";
import { QuestionSet, ReviewDecision } from "./DecisionCard";
import { ProseScope } from "./Prose";

const installer = "Approve the report root?\n\nRun this once on the host:\n\n```sh\nsudo install -d /srv/reports\nalt ci reports --root /srv/reports\n```\n\nThen `alt task recheck-ci` reads it; see PR #12.";
const base = { project: "atlas", slug: "gate", title: "Gate merges", id: "q1", revision: 1, status: "open", audience: "operator", asked_by: "l2", asked: "2026-09-23T22:00:00Z" };
function mount(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><ToastProvider><MemoryRouter>
    <ProseScope project="atlas" repository="https://github.com/example/atlas">{node}</ProseScope>
  </MemoryRouter></ToastProvider></QueryClientProvider>);
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
  });
});
