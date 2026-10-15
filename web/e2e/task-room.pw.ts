import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// SPEC.md §3.10: the header carries the state; what the task waits on ends the conversation.
const wait = "Wait for the required checks on the approved pull request, then merge it.";
const conversation = Array.from({ length: 6 }, (_, i) => [
  { id: `op-${i}`, at: `2026-10-07T04:0${i}:00Z`, role: "operator", text: i % 2 ? "Looks good. Keep the header compact on the phone." : "Can the status read more calmly on the task page?" },
  { id: `l2-${i}`, at: `2026-10-07T04:0${i}:30Z`, role: "l2", text: "The header keeps the state and the reason moves to the end of the conversation, so reading starts right under the tabs. The checks are running on the candidate now." },
]).flat();
const scenes = [
  { key: "coordinator-question", audience: "l3", line: null },
  { key: "coordinator-wait", waiting: "l3", line: wait },
  { key: "fault", fault: "l2-died", line: "The task session ended before completion. Waiting for the coordinator to check the blocker." },
  { key: "fault-question", fault: "l2-died", audience: "l3", line: `The task session ended before completion. Coordinator needed: ${wait}` },
  { key: "planned", planned: true, line: "Waiting for “Publish the installation package” to finish." },
] as const;

for (const scene of scenes) test(`task room: ${scene.key}`, async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const source = await fixtureTask(request, project.name);
  const audience = "audience" in scene ? scene.audience : null;
  const planned = "planned" in scene;
  const question = audience ? {
    id: "room-question", revision: 1, anchor_id: "room-anchor", status: "open", audience,
    project: project.name, slug: source.slug, title: "Tidy the task page", kind: "asks", asked_by: "l2",
    question: wait, detail: wait, asked: "2026-10-07T04:07:00Z", response: null,
  } : null;
  const task = { ...source, title: "Strip the explanatory status strings from the task page", state: planned ? "queued" : "blocked",
    fault: "fault" in scene ? scene.fault : null, blocked_reason: wait, waiting_on: audience ?? ("waiting" in scene ? scene.waiting : null),
    handed_back: null, resume_after: null, block_actor: "l2", hold_merge: "Review the completed change before merging.", stop_id: null,
    steering: { state: "idle", stop_id: null, generation: null, error: null },
    planned_wait: planned ? { reason: "publish-installation-package", after: "publish-installation-package", after_title: "Publish the installation package" } : null,
    question, questions: question ? [question] : [],
    question_group: question ? { id: "room-group", revision: 1, anchor_id: question.anchor_id, questions: [question] } : null,
    messages: conversation, activity: null, prs: [],
  };
  await page.route(`**/api/task/${project.name}/${source.slug}`, (route) => route.fulfill({ json: task }));
  const walk = walkthrough(page, info);
  await walk.open(`${project.path}/tasks/${encodeURIComponent(source.slug)}`);
  const status = page.locator(".conversation-status");
  const card = page.getByText("L3 is answering", { exact: true });
  await walk.state("01-latest", {
    visible: [page.getByText("Merge held", { exact: false }).first(), ...(scene.line ? [status.getByText(scene.line, { exact: true })] : []), ...(audience ? [card] : [])],
    hidden: [...(scene.line ? [] : [status]), ...(audience ? [] : [card])],
  });
  // The line follows any card, so at the latest messages it is the last content in the chat.
  if (scene.line && audience) expect(await status.evaluate((node) => node.previousElementSibling?.classList.contains("conversation-question"))).toBe(true);
  if (scene.line) expect(await status.evaluate((node) => node.nextElementSibling === null)).toBe(true);
  const scroller = page.locator(".convo-scroll");
  await scroller.evaluate((node) => { node.scrollTop = 0; });
  // Reading older messages scrolls the line away; the header keeps the state and nothing sits above the tabs.
  await walk.state("02-reading-older", { visible: [page.getByText("Can the status read more calmly on the task page?").first(), page.getByText(scene.key.startsWith("fault") ? "Work interrupted" : planned ? "Planned" : "Waiting for coordinator", { exact: true }).first()], hidden: [] });
  if (info.project.name === "phone") expect(await page.locator(".task-page[data-phone] > .task-tabs").evaluate((node) => node.previousElementSibling)).toBeNull();
  await page.getByRole("button", { name: /Task details$/ }).first().click();
  const details = page.getByRole("dialog", { name: "Task details", exact: true });
  await walk.state("03-details", { visible: [planned ? details : details.getByText(wait, { exact: true }).first()], hidden: [] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Close task details", exact: true }).click();
  await walk.state("04-details-closed", { visible: [page.getByText("Can the status read more calmly on the task page?").first()], hidden: [details] });
  expect(await scroller.evaluate((node) => node.scrollTop)).toBe(0);
});
