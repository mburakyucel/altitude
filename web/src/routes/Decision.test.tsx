import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { DecisionSchema, QuestionGroupSchema, TaskMessageSchema, TaskViewSchema } from "../data/api";

const operator = TaskMessageSchema.shape.role.options.find((role) => role !== "l2" && role !== "l3")!;
const at = "2026-09-08T02:00:00Z";
const question = DecisionSchema.parse({ project: "atlas", slug: "index", title: "Index rollout", id: "q-index", revision: 1,
  anchor_id: "question-message", status: "open", audience: "operator", asked_by: "l3", asked: at,
  question: "How long should we retain the old index?", recommendation: { text: "Keep it for seven days.", label: "Use 7 days & resume", why: "This covers the rollback window." } });
const initial = TaskViewSchema.parse({ slug: "index", title: "Index rollout", state: "blocked", question, questions: [question],
  messages: [
    { id: "before", role: "l2", text: "The rollback plan needs a retention period.", at },
    { id: "question-message", role: "l3", text: question.question, at },
    { id: "after", role: "l2", text: "I also checked the current index size.", at },
  ], events: [{ kind: "tool", text: "Later technical activity" }] });
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function setup({ archived = false, denied = false, missing = false } = {}) {
  let task = structuredClone(initial);
  if (archived) task = { ...task, state: "done", question: { ...question, status: "resolved" }, questions: [{ ...question, status: "resolved" }] };
  if (missing) task = { ...task, question: null, questions: [] };
  let refuse = denied;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/overview") return json({ projects: [{ name: "atlas", managed: true }], queue: task.question?.status === "open" && !task.question.response ? [task.question] : [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false } });
    if (path === "/api/project/atlas") return json({ name: "atlas", tasks: [], repository: "https://github.com/example/atlas" });
    if (path === "/api/task/atlas/index") return json(task);
    if (path === "/api/decide") {
      if (refuse) return json({ error: "Access denied" }, 403);
      const closed = { ...question, response: { text: "Use seven days.", at, message_id: "answer" } };
      task = { ...task, state: "running", question: closed, questions: [closed], messages: [...task.messages!, { id: "answer", role: operator, text: "Use seven days.", at }] };
      return json({ question: closed });
    }
    if (path === "/api/l2/message") {
      const body = JSON.parse(String(init?.body));
      const message = { id: "followup", role: operator, text: body.text, at };
      task = { ...task, state: "running", messages: [...task.messages!, message] };
      return json({ message });
    }
    return json({ error: "unavailable" }, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, permit: () => { refuse = false; }, update: (value: typeof task) => { task = value; } };
}
const path = "/projects/atlas/tasks/index?question=q-index&revision=1";
const convo = () => screen.getByRole("region", { name: "Task conversation" });

function setupGroup() {
  let failure = 0;
  let group = QuestionGroupSchema.parse({ id: "rollout", revision: 1, anchor_id: question.anchor_id, questions: [
    { ...question, group_id: "rollout", group_revision: 1, recommended_key: "seven", options: [
      { key: "seven", label: "7 days", text: "Keep the index for seven days." },
      { key: "fourteen", label: "14 days", text: "Keep the index for fourteen days." },
    ] },
    { ...question, id: "q-region", anchor_id: "region-message", group_id: "rollout", group_revision: 1,
      question: "Which region should host the backup?", recommendation: null, options: [], recommended_key: null },
  ] });
  const messages = [...initial.messages!, { id: "region-message", role: "l2" as const, text: "Which region should host the backup?", at }];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const route = String(input);
    if (route === "/api/overview") return json({ projects: [{ name: "atlas", managed: true }], queue: group.questions.filter((q) => q.status === "open" && !q.response), wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false } });
    if (route === "/api/project/atlas") return json({ name: "atlas", tasks: [] });
    if (route === "/api/task/atlas/index") return json({ ...initial, question_group: group, question: group.questions.find((q) => q.status === "open"), questions: group.questions, messages });
    if (route === "/api/decide") {
      if (failure) return json({ error: "Send unavailable" }, failure);
      const body = JSON.parse(String(init?.body));
      const answers = body.answers ?? [body];
      group = { ...group, questions: group.questions.map((q) => {
        const chosen = answers.find((answer: { question_id: string }) => answer.question_id === q.id);
        return { ...q, ...(chosen ? { response: { text: chosen.text ?? q.options!.find((o) => o.key === chosen.option_key)!.text, at, message_id: "group-answer" } } : {}) };
      }) };
      return json({ question: group.questions[0], question_group: group });
    }
    if (route === "/api/l2/message") {
      const body = JSON.parse(String(init?.body));
      return json({ message: { id: "group-reply", role: operator, text: body.text, at } });
    }
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, fail: (status: number) => { failure = status; }, update: (change: (value: typeof group) => typeof group) => { group = change(group); } };
}

describe("Independent questions in one conversation", () => {
  it.each(["missing", "new revision", "different text"])("keeps a failed batch unresolved when a response has %s evidence", async (caseName) => {
    const server = setupGroup();
    server.fail(500);
    const { user, queryClient } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "14 days" }));
    await user.type(screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" }), "Europe");
    await user.click(screen.getByRole("button", { name: "Send 2 answers" }));
    await screen.findByText("Could not send answers. Your responses are kept here.");
    server.update((g) => ({ ...g, questions: g.questions.map((q) => q.id === "q-index"
      ? { ...q, response: { text: "Keep the index for fourteen days.", at, message_id: "saved" } }
      : caseName === "missing" ? q : { ...q, revision: caseName === "new revision" ? 2 : q.revision,
        response: { text: caseName === "different text" ? "West" : "Europe", at, message_id: "saved" } }) }));
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "atlas", "index"] }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.getByText("Could not send answers. Your responses are kept here.")).toBeVisible();
  });

  it("mixes Other and a plain answer in one send, retaining the question context and receipts", async () => {
    const { fetch } = setupGroup();
    const { user, queryClient } = renderApp({ route: path });
    expect(await screen.findByRole("button", { name: "Send answers" })).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: `Your answer to: ${question.question}` })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Other…" }));
    const custom = screen.getByRole("textbox", { name: `Your answer to: ${question.question}` });
    expect(custom).toHaveFocus();
    await user.type(custom, "21 days");
    await user.type(screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" }), "Which is closest to our users?");
    await user.click(screen.getByRole("button", { name: "Send 2 answers" }));
    await waitFor(() => expect(screen.getAllByText("Sent to L2")).toHaveLength(2));
    const call = fetch.mock.calls.find(([url]) => url === "/api/decide")!;
    expect(JSON.parse(String(call[1]?.body)).answers).toEqual([
      { question_id: "q-index", revision: 1, text: "21 days" },
      { question_id: "q-region", revision: 1, text: "Which is closest to our users?" },
    ]);
    expect(screen.queryByRole("textbox", { name: /Your answer to:/ })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Message the L2" })).toBeEnabled();
    expect(screen.queryByText("Decision recorded")).toBeNull();
    expect(queryClient.getQueryData<{ queue: unknown[] }>(["overview"])?.queue).toHaveLength(0);
  });

  it("replaces custom text with a preset and leaves other members' text intact", async () => {
    setupGroup();
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "Other…" }));
    await user.type(screen.getByRole("textbox", { name: `Your answer to: ${question.question}` }), "21 days");
    const plain = screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" });
    await user.type(plain, "Europe");
    await user.click(screen.getByRole("button", { name: "14 days" }));
    expect(screen.queryByRole("textbox", { name: `Your answer to: ${question.question}` })).toBeNull();
    expect(plain).toHaveValue("Europe");
    await user.click(screen.getByRole("button", { name: "Other…" }));
    expect(screen.getByRole("textbox", { name: `Your answer to: ${question.question}` })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Send 1 answer" })).toBeEnabled();
  });

  it("keeps independent drafts when another member is submitted elsewhere", async () => {
    const server = setupGroup();
    const { user, queryClient } = renderApp({ route: path });
    await user.type(await screen.findByRole("textbox", { name: "Your answer to: Which region should host the backup?" }), "Europe");
    server.update((g) => ({ ...g, revision: 2, questions: g.questions.map((q) => ({ ...q, group_revision: 2,
      ...(q.id === "q-index" ? { response: { text: "21 days", at, message_id: "elsewhere" } } : {}),
    })) }));
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "atlas", "index"] }));
    await screen.findByText("Sent to L2");
    expect(screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" })).toHaveValue("Europe");
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    const call = server.fetch.mock.calls.find(([url]) => url === "/api/decide")!;
    expect(JSON.parse(String(call[1]?.body))).toMatchObject({ group_revision: 2, answers: [{ question_id: "q-region", revision: 1, text: "Europe" }] });
  });

  it("preserves responses on send failure and retries the same contextual submission", async () => {
    const server = setupGroup();
    server.fail(500);
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "14 days" }));
    const plain = screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" });
    await user.type(plain, "Europe");
    await user.click(screen.getByRole("button", { name: "Send 2 answers" }));
    await screen.findByText("Could not send answers. Your responses are kept here.");
    expect(plain).toHaveValue("Europe");
    expect(screen.getByRole("button", { name: "14 days" })).toHaveAttribute("aria-pressed", "true");
    server.fail(0);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getAllByText("Sent to L2")).toHaveLength(2));
    const calls = server.fetch.mock.calls.filter(([url]) => url === "/api/decide");
    expect(calls).toHaveLength(2);
    expect(calls[1]![1]?.body).toEqual(calls[0]![1]?.body);
  });

  it("stages explicit recommendations and requires Send before delivering them", async () => {
    const { fetch } = setupGroup();
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "Use recommendations" }));
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Send 1 answer" })).toBeEnabled();
    expect(fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
  });

  it("stages a choice before sending when only one group member remains unanswered", async () => {
    const server = setupGroup();
    server.update((g) => ({ ...g, questions: g.questions.map((q) => q.id === "q-region" ? { ...q, status: "resolved" } : q) }));
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "14 days" }));
    expect(server.fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    await screen.findByText("Sent to L2");
    const call = server.fetch.mock.calls.find(([url]) => url === "/api/decide")!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({ project: "atlas", slug: "index", group_id: "rollout", group_revision: 1, answers: [{ question_id: "q-index", revision: 1, option_key: "fourteen" }] });
    expect(screen.queryByRole("button", { name: "Send 1 answer" })).toBeNull();
  });

  it("opens the whole group at either question and stages an alternative before sending only that answer", async () => {
    const { fetch } = setupGroup();
    const { user, queryClient } = renderApp({ route: "/projects/atlas/tasks/index?question=q-region&revision=1" });
    const choice = await screen.findByRole("button", { name: "14 days" });
    expect(choice).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "false");
    expect(document.activeElement).toHaveTextContent("Which region should host the backup?");
    expect(within(convo()).getAllByText("Which region should host the backup?")).toHaveLength(1);
    expect(screen.queryByText("This question has been replaced.")).toBeNull();
    await user.click(choice);
    expect(fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Use recommendations" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    await screen.findByText("Sent to L2");
    const call = fetch.mock.calls.find(([url]) => url === "/api/decide")!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({ project: "atlas", slug: "index", group_id: "rollout", group_revision: 1, answers: [{ question_id: "q-index", revision: 1, option_key: "fourteen" }] });
    expect(convo()).toHaveTextContent("1 question to answer");
    expect(screen.queryByRole("button", { name: "14 days" })).toBeNull();
    expect(queryClient.getQueryData<{ queue: { id: string }[] }>(["overview"])?.queue.map((q) => q.id)).toEqual(["q-region"]);
  });

  it("sends ordinary discussion with the group context and never accepts any answer on send", async () => {
    const { fetch } = setupGroup();
    const { user } = renderApp({ route: path });
    await user.type(await screen.findByRole("textbox", { name: "Message the L2" }), "Why seven days, and which region is closest?");
    await user.click(within(convo()).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/l2/message")).toBe(true));
    const call = fetch.mock.calls.find(([url]) => url === "/api/l2/message")!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({ project: "atlas", slug: "index", text: "Why seven days, and which region is closest?", group_id: "rollout", group_revision: 1, request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    expect(fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
    expect(convo()).toHaveTextContent("2 questions to answer");
  });

  it("discards only the changed member's draft and preserves independent answers", async () => {
    const server = setupGroup();
    const { user, queryClient } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "14 days" }));
    await user.type(screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" }), "Europe");
    server.update((g) => ({ ...g, revision: 2, questions: g.questions.map((q) => ({ ...q, revision: q.id === "q-index" ? 2 : 1, group_revision: 2 })) }));
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "atlas", "index"] }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send 1 answer" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Your answer to: Which region should host the backup?" })).toHaveValue("Europe");
    expect(screen.getByRole("button", { name: "14 days" })).toHaveAttribute("aria-pressed", "false");
    expect(server.fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
  });
});

describe("Conversation-first decisions", () => {
  it("replaces a saved decision URL with the anchored L2 conversation and keeps evidence folded", async () => {
    setViewport(1440);
    setup();
    const { router } = renderApp({ route: "/projects/atlas/decisions/index" });
    await screen.findByText("How long should we retain the old index?");
    expect(router.state.location.pathname + router.state.location.search).toBe(path);
    expect(convo()).toHaveTextContent("The rollback plan needs a retention period.");
    expect(convo()).toHaveTextContent("L3 brought this question to the L2");
    expect(document.activeElement).toHaveClass("conversation-question");
    expect(document.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.queryByPlaceholderText("Add a note for the L2 (optional)")).toBeNull();
    expect(screen.queryByText("Where this came from")).toBeNull();
    expect(screen.getByRole("button", { name: "Live session" })).toHaveAttribute("aria-pressed", "false");
  });

  it("sends a follow-up with its question identity and leaves the dilemma open", async () => {
    const { fetch } = setup();
    const { user, queryClient } = renderApp({ route: path });
    const field = await screen.findByRole("textbox", { name: "Message the L2" });
    await user.type(field, "Can we roll back after day seven?");
    await user.click(within(convo()).getByRole("button", { name: "Send" }));
    await within(convo()).findByText("Can we roll back after day seven?");
    expect(within(convo()).getByRole("button", { name: "Use 7 days & resume" })).toBeEnabled();
    expect(fetch.mock.calls.filter(([url]) => url === "/api/decide")).toHaveLength(0);
    const sent = fetch.mock.calls.find(([url]) => url === "/api/l2/message")!;
    expect(JSON.parse(String(sent[1]?.body))).toEqual({ project: "atlas", slug: "index", text: "Can we roll back after day seven?", question_id: "q-index", revision: 1, request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    expect(queryClient.getQueryData<{ queue: unknown[] }>(["overview"])?.queue).toHaveLength(1);
  });

  it("positions the question when its conversation anchor arrives after the initial read", async () => {
    const server = setup();
    server.update({ ...initial, messages: initial.messages!.filter((row) => row.id !== question.anchor_id) });
    const { queryClient } = renderApp({ route: path });
    await within(await screen.findByRole("region", { name: "Task conversation" })).findByText("I also checked the current index size.");
    expect(document.activeElement).not.toHaveClass("conversation-question");
    server.update(initial);
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "atlas", "index"] }));
    await waitFor(() => expect(document.activeElement).toHaveClass("conversation-question"));
    expect(document.activeElement).toHaveTextContent(question.question!);
  });

  it("sends a single preset response once and shows delivery without declaring a decision", async () => {
    const { fetch } = setup();
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "Use 7 days & resume" }));
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    await within(convo()).findByText("Sent to L2");
    expect(within(convo()).queryByRole("button", { name: "Use 7 days & resume" })).toBeNull();
    expect(within(convo()).queryByText("Decision recorded")).toBeNull();
    const calls = fetch.mock.calls.filter(([url]) => url === "/api/decide");
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0]![1]?.body))).toEqual({ project: "atlas", slug: "index", question_id: "q-index", revision: 1, option_key: "recommended" });
  });

  it("closes an obsolete dilemma with its reason and removes acceptance when the owner records changed direction", async () => {
    const server = setup();
    const { queryClient } = renderApp({ route: path });
    await screen.findByRole("button", { name: "Use 7 days & resume" });
    const closed = { ...question, status: "resolved", resolution: { disposition: "superseded", text: "No old index is needed after choosing the simpler rollout.", by: operator, at } };
    server.update({ ...initial, state: "running", question: closed, questions: [closed] });
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "atlas", "index"] }));
    await within(convo()).findByText("Question closed");
    expect(convo()).toHaveTextContent("No old index is needed after choosing the simpler rollout.");
    expect(within(convo()).queryByRole("button", { name: "Use 7 days & resume" })).toBeNull();
    expect(within(convo()).queryByText("Decision recorded")).toBeNull();
  });

  it("keeps acceptance and sending denied together until an explicit refreshed read", async () => {
    const server = setup({ denied: true });
    const { user } = renderApp({ route: path });
    await user.click(await screen.findByRole("button", { name: "Use 7 days & resume" }));
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    await screen.findByText("You cannot send messages or answers here.");
    expect(screen.getByRole("textbox", { name: "Message the L2" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use 7 days & resume" })).toBeDisabled();
    server.permit();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message the L2" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Use 7 days & resume" })).toBeEnabled();
  });

  it("keeps an unavailable historical question explicit while the ordinary task stays readable", async () => {
    setup({ missing: true });
    renderApp({ route: path });
    await screen.findByText("This question is unavailable. The task conversation is below.");
    expect(convo()).toHaveTextContent("The rollback plan needs a retention period.");
    expect(screen.queryByRole("button", { name: "Use 7 days & resume" })).toBeNull();
  });

  it("renders archived question history read-only on the phone", async () => {
    setViewport(390);
    setup({ archived: true });
    renderApp({ route: path });
    await within(await screen.findByRole("region", { name: "Task conversation" })).findByText("Question closed");
    expect(screen.queryByRole("textbox", { name: "Message the L2" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Use 7 days & resume" })).toBeNull();
  });
});
