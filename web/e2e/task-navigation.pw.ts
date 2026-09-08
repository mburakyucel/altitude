import { test } from "./fixtures";
import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

/** Fixture L3 task cards also appear in Work; navigation retains the real API and router. */
async function navigationTask(request: APIRequestContext, except?: string) {
  const project = await fixtureProject(request);
  const response = await request.get(`/api/project/${encodeURIComponent(project.name)}`);
  expect(response.ok()).toBe(true);
  const data = await response.json();
  const chatResponse = await request.get(`/api/chat/${encodeURIComponent(project.name)}`);
  expect(chatResponse.ok()).toBe(true);
  const chat = await chatResponse.json();
  const linked = chat.history.flatMap((row: { tasks?: string[] }) => row.tasks ?? []);
  const task = [...data.tasks, ...data.archive].find((row: { slug: string }) =>
    linked.includes(row.slug) && (!except || row.slug !== except));
  expect(task, "The fixture supplies tasks linked from L3 and Work").toBeTruthy();
  return { project, task, path: `${project.path}/tasks/${encodeURIComponent(task.slug)}` };
}

function controls(page: Page, info: TestInfo) {
  const phone = info.project.name === "phone";
  const conversation = page.getByRole("region", { name: "Task conversation", exact: true });
  const live = page.getByRole("region", { name: "Live session", exact: true });
  return {
    phone, conversation, live,
    back: phone ? page.getByRole("button", { name: "Back", exact: true }) : page.locator(".task-crumb"),
    async toggle(liveView: boolean) {
      if (phone) {
        await page.getByRole("navigation", { name: "Task views" })
          .getByRole("link", { name: liveView ? "Live session" : "Conversation", exact: true }).click();
      } else {
        const toggle = page.getByRole("button", { name: "Live session", exact: true });
        if ((await toggle.getAttribute("aria-pressed") === "true") !== liveView) await toggle.click();
      }
    },
  };
}

for (const origin of ["l3", "work"] as const) {
  for (const back of ["browser", "app"] as const) {
    test(`${origin} to task: repeated toggles then ${back} Back and Forward`, async ({ page, request }, info) => {
      const { project, task, path } = await navigationTask(request);
      const originPath = project.path + (origin === "work" ? "?tab=work" : "");
      const walk = walkthrough(page, info);
      const v = controls(page, info);
      await walk.open(originPath);
      const source = page.getByRole("region", { name: origin === "work" ? "Work" : "Conversation", exact: true });
      const link = source.locator(`a[href="${path}"]`).first();
      // Work folds completed tasks. An active task needs no expansion.
      if (origin === "work" && ["done", "rejected"].includes(task.state)) {
        await source.locator("summary").click();
      }
      await walk.state("01-origin", { visible: [link], hidden: [v.conversation] });
      await walk.state("02-task", { action: () => link.click(), visible: [v.conversation, v.back], hidden: [source] });
      const entries = await page.evaluate(() => history.length);
      for (let turn = 0; turn < 3; turn++) {
        await v.toggle(true);
        await v.toggle(false);
      }
      await walk.state("03-live-after-toggles", {
        action: () => v.toggle(true), visible: [v.live], hidden: v.phone ? [v.conversation] : [],
      });
      expect(await page.evaluate(() => history.length)).toBe(entries);
      const finalPath = path + (v.phone ? "/live" : "");
      await expect(page).toHaveURL(finalPath);
      await page.reload();
      await walk.state("04-reloaded", { visible: [v.live, v.back], hidden: v.phone ? [v.conversation] : [] });
      await walk.state("05-back-once", {
        action: () => back === "browser" ? page.goBack() : v.back.click(),
        visible: [source], hidden: [v.conversation, v.live],
      });
      await expect(page).toHaveURL(originPath);
      await walk.state("06-forward", { action: () => page.goForward(), visible: [v.live], hidden: [source] });
      await expect(page).toHaveURL(finalPath);
    });
  }
}

test("intentional navigation between different tasks retains each page in Back and Forward", async ({ page, request }, info) => {
  const first = await navigationTask(request);
  const second = await navigationTask(request, first.task.slug);
  const walk = walkthrough(page, info);
  const v = controls(page, info);
  const l3 = page.getByRole("region", { name: "Conversation", exact: true });
  await walk.open(first.project.path);
  await walk.state("01-first-task", {
    action: () => l3.locator(`a[href="${first.path}"]`).first().click(),
    visible: [v.conversation], hidden: [l3],
  });
  // This project link intentionally pushes a new page, even though L3 is also behind this task.
  const projectLink = v.phone
    ? page.getByRole("navigation", { name: "Primary", exact: true }).getByRole("link", { name: "Chat", exact: true })
    : page.getByRole("navigation", { name: "Rail", exact: true }).locator(`a[href="${first.project.path}"]`);
  await walk.state("02-l3-navigation", { action: () => projectLink.click(), visible: [l3], hidden: [v.conversation] });
  await walk.state("03-second-task", {
    action: () => l3.locator(`a[href="${second.path}"]`).first().click(),
    visible: [v.conversation], hidden: [l3],
  });
  await v.toggle(true);
  await v.toggle(false);
  await v.toggle(true);
  await walk.state("04-back-to-l3", { action: () => v.back.click(), visible: [l3], hidden: [v.conversation, v.live] });
  await expect(page).toHaveURL(first.project.path);
  await walk.state("05-back-to-first-task", { action: () => page.goBack(), visible: [v.conversation], hidden: [l3] });
  await expect(page).toHaveURL(first.path);
  await page.goForward();
  await expect(page).toHaveURL(first.project.path);
  await walk.state("06-forward-to-second-task", { action: () => page.goForward(), visible: [v.live], hidden: [l3] });
  await expect(page).toHaveURL(second.path + (v.phone ? "/live" : ""));
});

test("direct live entry: reload, local toggles and app Back to the owning L3", async ({ page, request }, info) => {
  const { project, path } = await navigationTask(request);
  const walk = walkthrough(page, info);
  const v = controls(page, info);
  // A same-origin document without the SPA gives the browser a preceding entry, but no in-app origin.
  await page.route("**/navigation-origin", (route) => route.fulfill({
    contentType: "text/html", body: "<!doctype html><title>Origin</title><p>Outside the app</p>",
  }));
  await walk.open("/navigation-origin");
  await walk.open(`${path}/live`);
  await walk.state("01-direct-live", { visible: [v.live, v.back], hidden: v.phone ? [v.conversation] : [] });
  const entries = await page.evaluate(() => history.length);
  await walk.state("02-conversation", {
    action: () => v.toggle(false), visible: [v.conversation], hidden: [v.live],
  });
  await walk.state("03-live-again", {
    action: () => v.toggle(true), visible: [v.live], hidden: v.phone ? [v.conversation] : [],
  });
  expect(await page.evaluate(() => history.length)).toBe(entries);
  await page.reload();
  await walk.state("04-reloaded-live", { visible: [v.live, v.back], hidden: v.phone ? [v.conversation] : [] });
  await walk.state("05-owning-l3", {
    action: () => v.back.click(), visible: [page.getByRole("textbox", { name: `Message L3 about ${project.name}`, exact: true })],
    hidden: [v.live, v.conversation],
  });
  await expect(page).toHaveURL(project.path);
  // The fallback replaces the direct entry, so Back cannot bounce into the task again.
  await page.goBack();
  await expect(page).toHaveURL("/navigation-origin");
});
