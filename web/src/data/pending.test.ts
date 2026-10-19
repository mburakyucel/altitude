import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgetProject, sendAnswers, sendApproval, taskQuery, useTask } from "./api";
import type { Decision, Overview, ProjectView, TaskView } from "./api";
import { overlayOverview, overlayProject, overlayTask } from "./pending";
import type { PendingWrite } from "./pending";

/*
 * The operator's writes on their way (SPEC.md §1.1 Actions answer at once): what each view shows from the press,
 * which read settles it, and what a refusal or a lost answer leaves. The cache only ever holds the server's reads.
 */

const asked = "2026-10-19T08:00:00Z";
const question = (id: string, change: Partial<Decision> = {}): Decision => ({ project: "sample", slug: "rollout", id, revision: 1,
  status: "open", audience: "operator", asked, question: `Question ${id}?`, options: [{ key: "a", label: "A", text: `Answer ${id}.` }], ...change });
const review: Decision = { project: "sample", slug: "rollout", kind: "review", pr: 7, asked, question: "Review PR #7 before merge" };
const stopped: Decision = { project: "sample", slug: "rollout", kind: "stopped", asked };
const elsewhere = question("other", { slug: "cleanup" });
const overview = (queue: Decision[]) => ({ projects: [], queue, suggestions: [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: {}, engines: [], roots: [] }) as unknown as Overview;
const task = (change: Partial<TaskView> = {}): TaskView => ({ slug: "rollout", state: "blocked", messages: [{ id: "m1", role: "l2", text: "Two questions.", at: asked }],
  questions: [question("q1"), question("q2")], ...change }) as TaskView;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** A server that holds each write until the test answers it, and serves reads from `record`. */
function server(record: { overview: Overview; task: TaskView }) {
  const writes: { path: string; body: Record<string, unknown>; answer: (response: Response | Error) => void }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/overview") return json(record.overview);
    if (path === "/api/task/sample/rollout") return json(record.task);
    if (path === "/api/project/sample") return json({ name: "sample", tasks: [], decisions: record.overview.queue });
    return new Promise<Response>((resolve, reject) => writes.push({ path, body: JSON.parse(String(init?.body)),
      answer: (response) => response instanceof Error ? reject(response) : resolve(response) }));
  }));
  return writes;
}

async function read(client: QueryClient) {
  await client.fetchQuery({ queryKey: ["overview"], queryFn: async () => (await (await fetch("/api/overview")).json()) as Overview, staleTime: 0 });
  await client.fetchQuery({ ...taskQuery("sample", "rollout"), staleTime: 0 });
}
const shown = (client: QueryClient) => {
  const entries = client.getQueryData<PendingWrite[]>(["pending"]) ?? [];
  return {
    queue: overlayOverview(client, client.getQueryData<Overview>(["overview"])!, entries).queue.map((row) => row.id ?? row.kind),
    task: overlayTask(client, "sample", client.getQueryData<TaskView>(["task", "sample", "rollout"])!, entries),
    entries,
  };
};
const answer = { project: "sample", slug: "rollout", question_id: "q1", revision: 1, option_key: "a" } as const;
const send = (client: QueryClient, id?: string) =>
  sendAnswers(client, answer, [{ question_id: "q1", revision: 1, text: "Answer q1." }], { "sample:rollout:q1:1": { option: "a" } }, id);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => vi.unstubAllGlobals());

describe("pending writes", () => {
  it("hands the task's questions and review back at the press, and shows the answer as the operator's waiting message", async () => {
    const record = { overview: overview([question("q1"), question("q2"), review, stopped, elsewhere]), task: task() };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    void send(client);
    await flush();
    const at = shown(client);
    // A stopped task and another task's question stay until the record clears them.
    expect(at.queue).toEqual(["stopped", "other"]);
    expect(at.task.questions?.find((row) => row.id === "q1")?.response).toEqual({ text: "Answer q1.", at: expect.any(String), message_id: writes[0]!.body["request_id"] });
    expect(at.task.questions?.find((row) => row.id === "q2")?.response).toBeUndefined();
    expect(at.task.messages?.at(-1)).toEqual({ id: writes[0]!.body["request_id"], at: expect.any(String), role: "operator", text: "Answer q1.",
      delivery: { state: "queued", at: null, removable: false } });
    expect(at.task["handed_back"]).toBe(asked);
    expect(at.task.resume_after).toEqual(expect.any(String));
    // The cache holds only what the server said.
    expect(client.getQueryData<Overview>(["overview"])!.queue).toHaveLength(5);
    expect(writes[0]!.body["request_id"]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("keeps showing a question asked after the press", async () => {
    const record = { overview: overview([question("q1")]), task: task() };
    server(record);
    const client = new QueryClient();
    await read(client);
    void send(client);
    await flush();
    const later = question("q3", { asked: "2026-10-19T09:00:00Z" });
    record.overview = overview([question("q1"), later]);
    await read(client);
    expect(shown(client).queue).toEqual(["q3"]);
    expect(overlayProject(client, { name: "sample", tasks: [], decisions: [question("q1"), later] } as unknown as ProjectView, shown(client).entries).decisions?.map((row) => row.id)).toEqual(["q3"]);
  });

  it("settles a saved answer at the first read begun after the save, and then forgets it", async () => {
    const record = { overview: overview([question("q1")]), task: task() };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    const outcome = send(client);
    await flush();
    const id = String(writes[0]!.body["request_id"]);
    // A read begun before the save returns the old record; it is cancelled, so it cannot bring the card back.
    void client.fetchQuery({ queryKey: ["overview"], queryFn: () => new Promise<Overview>(() => {}), staleTime: 0 }).catch(() => undefined);
    writes[0]!.answer(json({ question: question("q1") }));
    expect((await outcome).state).toBe("saved");
    expect(client.getQueryState(["overview"])?.fetchStatus).toBe("idle");
    expect(shown(client).queue).toEqual([]);
    expect(shown(client).task.messages?.at(-1)?.id).toBe(id);
    record.overview = overview([]);
    const saved = question("q1", { response: { text: "Answer q1.", at: asked, message_id: id } });
    record.task = task({ handed_back: asked, questions: [saved, question("q2")], messages: [...task().messages!, { id, role: "operator", text: "Question q1?\nAnswer q1.", at: asked }] });
    await read(client);
    await flush();
    expect(shown(client).task.messages?.filter((message) => message.id === id)).toHaveLength(1);
    expect(shown(client).entries).toEqual([]);
  });

  it("shows a task's next read as the record even while a view not read since still holds the saved answer", async () => {
    const record = { overview: overview([question("q1")]), task: task() };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    await client.fetchQuery({ queryKey: ["project", "sample"], queryFn: async () => (await (await fetch("/api/project/sample")).json()) as ProjectView });
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => useTask("sample", "rollout"), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const outcome = send(client);
    await act(flush);
    expect(result.current.data?.["handed_back"]).toBe(asked);
    writes[0]!.answer(json({ question: question("q1") }));
    await act(async () => { await outcome; });
    await waitFor(() => expect(client.isFetching()).toBe(0));
    // The owner asks the same revision again before anything reads the project view.
    record.task = task({ questions: [question("q1", { asked_again: true } as Partial<Decision>), question("q2")] });
    await act(async () => { await client.refetchQueries({ queryKey: ["task", "sample", "rollout"] }); });
    await waitFor(() => expect(result.current.data?.["handed_back"]).toBeUndefined());
    expect(result.current.data?.questions?.[0]?.response).toBeUndefined();
    expect(shown(client).entries).toHaveLength(1);
  });

  it("rolls a refused answer back at once and keeps its picks for the card", async () => {
    const record = { overview: overview([question("q1")]), task: task() };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    const outcome = send(client);
    await flush();
    writes[0]!.answer(json({ error: "These questions have changed." }, 409));
    const refused = await outcome;
    expect(refused.state).toBe("refused");
    expect(refused.error?.message).toBe("These questions have changed.");
    expect(shown(client).queue).toEqual(["q1"]);
    expect(shown(client).task.messages).toHaveLength(1);
    expect(shown(client).entries.map((entry) => entry.picks)).toEqual([{ "sample:rollout:q1:1": { option: "a" } }]);
  });

  it("keeps an answer with no reply waiting until the task's next read decides it", async () => {
    const record = { overview: overview([question("q1")]), task: task() };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    const outcome = send(client);
    await flush();
    const id = String(writes[0]!.body["request_id"]);
    // The answer was saved but its reply was lost: the read lists it, so it settles as saved.
    record.task = task({ questions: [question("q1", { response: { text: "Answer q1.", at: asked, message_id: id } })],
      messages: [...task().messages!, { id, role: "operator", text: "Answer q1.", at: asked }] });
    writes[0]!.answer(new TypeError("Failed to fetch"));
    expect((await outcome).state).toBe("saved");

    record.task = task();
    const lost = send(client);
    await flush();
    writes[1]!.answer(json({ error: "unavailable" }, 503));
    const refused = await lost;
    expect(refused.state).toBe("refused");
    expect(refused.error?.message).toBe("Not sent");
    // It was saved after all, behind a read that had not caught up: the next read listing it drops the Not sent.
    const later = String(writes[1]!.body["request_id"]);
    record.task = task({ questions: [question("q1", { response: { text: "Answer q1.", at: asked, message_id: later } })] });
    await read(client);
    await flush();
    expect(shown(client).entries).toEqual([]);
  });

  it("sends Retry of unchanged picks under the same id", async () => {
    const writes = server({ overview: overview([question("q1")]), task: task() });
    const client = new QueryClient();
    await read(client);
    void send(client, "0123456789abcdef0123456789abcdef");
    await flush();
    expect(writes[0]!.body["request_id"]).toBe("0123456789abcdef0123456789abcdef");
  });

  it("shows an approval as the operator's message and appends the saved row in its place", async () => {
    const record = { overview: overview([review]), task: task({ questions: [] }) };
    const writes = server(record);
    const client = new QueryClient();
    await read(client);
    const outcome = sendApproval(client, review);
    await flush();
    const id = String(writes[0]!.body["request_id"]);
    expect(writes[0]!.path).toBe("/api/l2/message");
    expect(writes[0]!.body).toMatchObject({ text: "Approved: merge PR #7.", review_pr: 7, review_hold_id: null });
    expect(shown(client).queue).toEqual([]);
    expect(shown(client).task.messages?.at(-1)).toMatchObject({ id, text: "Approved: merge PR #7.", delivery: { state: "queued" } });
    writes[0]!.answer(json({ message: { id, role: "operator", text: "Approved: merge PR #7.", at: asked, delivery: { state: "queued", at: null, removable: true } } }));
    expect((await outcome).state).toBe("saved");
    expect(shown(client).task.messages?.filter((message) => message.id === id)).toEqual([expect.objectContaining({ delivery: expect.objectContaining({ removable: true }) })]);
  });

  it("drops a removed project's writes with its reads, and pairing drops every one", async () => {
    server({ overview: overview([question("q1")]), task: task() });
    const client = new QueryClient();
    await read(client);
    void send(client);
    await flush();
    await forgetProject(client, "sample");
    expect(client.getQueryData<PendingWrite[]>(["pending"])).toEqual([]);
    void send(client);
    await flush();
    client.removeQueries();
    expect(client.getQueryData(["pending"])).toBeUndefined();
  });
});
