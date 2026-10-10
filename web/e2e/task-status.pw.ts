import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Presentation overlays retain the real service and API shapes; no lifecycle transition is faked.
const polluted = 'system fault [l2-died]: L2 worker aabbccddeeff00112233445566778899 (attempt 1) ended without a fresh report: worker state=failed; elta\\":100,\\"session_id\\":\\"fictional\\"}\\n{"type":"system","subtype":"thinking_tokens"}';
const prerequisite = "Arrange approved machine access before installation checks can continue.";
const recordedHandoff = "L3: record the supported machine-grant handoff from the existing published-release VM approval (abcdef12, prior task) then resume this owner. No new operator decision is needed.";
const scenes = [
  { key: "interrupted-access", fault: "l2-died", reason: polluted, audience: "l3", explanation: "The task session ended before completion. Coordinator needed: record the supported machine-grant handoff from the existing published-release VM approval." },
  { key: "unknown-fault", fault: "unknown-code", reason: polluted, explanation: "A system problem paused work. Waiting for the coordinator to check the blocker." },
  { key: "coordinator", audience: "l3", reason: prerequisite, explanation: `Waiting for the coordinator: ${prerequisite}` },
  { key: "operator", audience: "operator", reason: "Which release should the checks use?", explanation: "Waiting for your answer to the task’s question." },
  { key: "fault-and-question", fault: "l2-died", audience: "operator", reason: polluted, explanation: "The task session ended before completion. Waiting for the coordinator to check the blocker." },
  { key: "unknown-pause", reason: "", explanation: "Work is paused; no reason is recorded." },
  { key: "stopped", stop: true, reason: "Operator requested Stop.", explanation: "Stopped by you; continue when you’re ready." },
  { key: "coordinator-stopped", stop: true, by: "l3", reason: "The orphaned landing holds the repository turn; stopping to release it.", explanation: "Stopped by the coordinator; its note is in the conversation." },
  { key: "planned", planned: true, reason: "", explanation: "Waiting for “Publish the installation package” to finish." },
] as const;

for (const scene of scenes) test(`task status: ${scene.key}, row and page`, async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const source = await fixtureTask(request, project.name);
  const audience = "audience" in scene ? scene.audience : null;
  const fault = "fault" in scene ? scene.fault : null;
  const question = audience ? {
    id: "status-question", revision: 1, anchor_id: "status-anchor", status: "open", audience,
    project: project.name, slug: source.slug, title: "Verify installation", kind: "asks", asked_by: "l2",
    question: audience === "l3" ? prerequisite : "Which release should the checks use?",
    detail: scene.key === "interrupted-access" ? recordedHandoff : audience === "l3" ? prerequisite : "Which release should the checks use?",
    asked: "2026-10-07T04:03:00Z", response: null,
  } : null;
  const stopped = "stop" in scene;
  const coordinatorStop = "by" in scene;
  const planned = "planned" in scene;
  const task = { ...source, title: "Verify installation", state: planned ? "queued" : "blocked",
    fault, blocked_reason: scene.reason, waiting_on: coordinatorStop ? "l3" : audience, handed_back: null, resume_after: null,
    block_actor: coordinatorStop ? "l3" : stopped ? "operator" : "l2",
    hold_merge: "Review the completed change before merging.", stop_id: stopped ? "fictional-stop" : null,
    steering: { state: stopped ? "stopped" : "idle", stop_id: stopped ? "fictional-stop" : null, generation: null, error: null },
    planned_wait: planned ? { reason: "publish-installation-package", after: "publish-installation-package", after_title: "Publish the installation package" } : null,
    question, questions: question ? [question] : [],
    question_group: question ? { id: "status-group", revision: 1, anchor_id: question.anchor_id, questions: [question] } : null,
    messages: coordinatorStop ? [{ id: "fictional-stop", at: "2026-10-07T04:03:00Z", role: "l3", by: "l3", text: scene.reason, summary: "Stopped the task" }] : [],
    activity: null, prs: [],
  };
  await page.route(`**/api/project/${project.name}`, async (route) => {
    const response = await route.fetch();
    // Project rows contain saved questions and Stop identity, not the task view's steering receipt.
    await route.fulfill({ response, json: { ...await response.json(), tasks: [{ ...task, steering: undefined, question_group: undefined }], archive: [] } });
  });
  await page.route(`**/api/task/${project.name}/${source.slug}`, (route) => route.fulfill({ json: task }));
  await page.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), queue: audience === "operator" ? [question] : [] } });
  });
  const walk = walkthrough(page, info);
  await walk.open(`${project.path}?tab=work`);
  const row = page.getByRole("region", { name: "Work", exact: true }).getByRole("link", { name: /^Verify installation ·/ });
  const rowExplanation = coordinatorStop ? "The coordinator requested a stop; confirmation is in the task."
    : stopped ? "You requested a stop; confirmation is in the task." : planned ? `Planned · ${scene.explanation}` : scene.explanation;
  await walk.state("01-row-explanation", { visible: [row.getByText(rowExplanation, { exact: true })], hidden: [] });
  expect(await row.innerText()).not.toMatch(/aabbccdd|attempt 1|worker state|l2-died|thinking_tokens|\\n/);
  if (fault || stopped) await expect(row.locator(".dot")).toHaveAttribute("data-state", coordinatorStop ? "running" : "danger");
  if (audience === "operator") await expect(row.getByText("Your turn · 1 question")).toBeVisible();
  await row.click();
  const explanation = page.locator(".task-explanation");
  await walk.state("02-page-explanation", { visible: [explanation.getByText(scene.explanation, { exact: true }), page.getByText("Merge held", { exact: false }).first()], hidden: [] });
  expect((await explanation.innerText()).length).toBeLessThan(220);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (audience === "operator") await expect(page.locator('[data-question-id="status-question"]').first()).toBeVisible();
  if (stopped) await expect(page.getByRole("button", { name: "Continue", exact: true })).toBeVisible();
  if (coordinatorStop) {
    await walk.state("02b-coordinator-note", { visible: [page.getByText("L3 · Stopped the task", { exact: true })], hidden: [page.locator('[data-question-id]')] });
  }
  const opener = page.getByRole("button", { name: /Task details$/ });
  await opener.click();
  const details = page.getByRole("dialog", { name: "Task details", exact: true });
  if (scene.reason) {
    await details.getByText(scene.reason, { exact: true }).scrollIntoViewIfNeeded();
    const label = stopped ? [details.getByRole("heading", { name: coordinatorStop ? "Stopped by coordinator" : "Stopped by you", exact: true })] : [];
    await walk.state("03-original-evidence", { visible: [details.getByText(scene.reason, { exact: true }), ...label], hidden: [] });
  }
  await page.getByRole("button", { name: "Close task details", exact: true }).click();
  await walk.state("04-return-to-conversation", { visible: [explanation], hidden: [details] });
});
