import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Presentation overlays supply overlapping task titles and long names. Existing lifecycle
// specs exercise persisted answers; these assertions exercise ownership, wrapping and routing.
test("interleaved attention becomes contiguous project sections in single and mixed queues", async ({ page, request }, info) => {
  const project = await fixtureProject(request);
  const projectData = await (await request.get(`/api/project/${project.name}`)).json();
  const task = projectData.tasks.find((row: { state: string }) => row.state === "running");
  const long = "atlas" + "international".repeat(5);
  const names = [project.name, `${long}east`, `${long}west`];
  const question = {
    project: names[1], slug: task.slug, title: task.title, kind: "asks", asked_by: "l2",
    id: "ownership", revision: 1, group_id: "scope", group_revision: 1,
    status: "open", audience: "operator", asked: new Date().toISOString(),
    question: "Which search regions should we include?",
    options: [{ key: "all", label: "All regions", text: "Include all regions." }],
  };
  const operational = { project: names[2], slug: task.slug, title: task.title, kind: "stopped", asked: question.asked, question: "The index build stopped." };
  const mixed = [
    question,
    { ...question, project: names[0], group_id: "l3", asked_by: "l3" },
    { ...question, project: names[2], group_id: "review", kind: "review" },
    { ...question, id: "retention", question: "How long should we keep the index?", options: [] },
    operational,
    { ...operational, project: names[0], kind: "fault", question: "The index service is unavailable." },
  ];
  let queue = mixed;
  const overview = await (await request.get("/api/overview")).json();
  await page.route("**/api/overview", (route) => route.fulfill({ json: {
    ...overview, projects: names.map((name) => ({ ...overview.projects[0], name })), queue,
  } }));
  await page.route("**/api/project/*", (route) => route.fulfill({ json: {
    ...projectData, name: decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()!),
  } }));
  // Selecting a fictional project opens its chat; the fixture project's conversation stands in.
  const chat = await (await request.get(`/api/chat/${project.name}?limit=60`)).json();
  await page.route("**/api/chat/*", (route) => route.fulfill({ json: chat }));
  await page.addInitScript((name) => localStorage.setItem("altitude.project", name), names[0]);
  const walk = walkthrough(page, info);
  await walk.open("/");
  const cards = page.getByLabel("Decisions").getByRole("article");
  const sections = page.getByLabel("Decisions").getByRole("region");
  const heading = (index: number) => sections.nth(index).getByRole("heading", { level: 2 });
  const toggle = (index: number) => heading(index).getByRole("button");
  await expect(cards).toHaveCount(5);
  await expect(sections).toHaveCount(3);
  // The selected project leads; the others keep first-appearance order.
  await expect(sections.getByRole("heading", { level: 2 })).toHaveText([names[0]!, names[1]!, names[2]!]);
  await expect(sections.nth(0).getByRole("article")).toHaveCount(2);
  await expect(sections.nth(1).getByRole("article")).toHaveCount(1);
  await expect(sections.nth(2).getByRole("article")).toHaveCount(2);
  await expect(page.locator('.badge[aria-label="6 pending"]:visible')).toBeVisible();
  const expectedOwners = [names[0], names[0], names[1], names[2], names[2]];
  for (const [index, name] of names.entries()) {
    const owner = heading(index);
    await expect(owner).toHaveText(name!);
    await expect(toggle(index)).toHaveAttribute("aria-expanded", "true");
    // A visible element alone does not prove that its text isn't clipped.
    expect(await owner.evaluate((node) => {
      const range = document.createRange();
      range.selectNodeContents(node);
      const bounds = node.getBoundingClientRect();
      return [...range.getClientRects()].every((rect) => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1);
    })).toBe(true);
  }
  await walk.state("01-selected-project-first-full-owners", {
    visible: [heading(0), page.getByText("4 questions · 1 stopped task · 1 fault across 3 projects")], hidden: [],
  });
  await heading(2).scrollIntoViewIfNeeded();
  await walk.state("02-long-project-review-and-stop", { visible: [heading(2), cards.nth(3), cards.nth(4)], hidden: [] });
  await expect(cards.nth(2).getByText("2 questions to answer")).toBeVisible();
  await expect(cards.nth(4).getByRole("button")).toHaveCount(0);
  for (const [index, name] of expectedOwners.entries()) {
    const link = cards.nth(index).getByRole("link", { name: "Open L2 chat" });
    await expect(link).toHaveAttribute("href", new RegExp(`^/projects/${name}/tasks/${task.slug}`));
  }
  // A staged pick survives collapsing its section; the heading keeps a count while closed.
  // Role locators skip hidden cards, so the count and the cards container prove the collapse.
  const sectionCards = (index: number) => sections.nth(index).getByRole("article");
  const cardsOf = (index: number) => sections.nth(index).locator(".needs-project-cards");
  await sectionCards(0).first().getByRole("button", { name: "All regions" }).click();
  await expect(sectionCards(0).first().getByRole("button", { name: "Send 1 answer" })).toBeEnabled();
  await walk.state("03-selected-section-collapsed", {
    action: () => toggle(0).click(),
    visible: [heading(0).getByText("1 question · 1 fault"), sectionCards(1).first()], hidden: [cardsOf(0)],
  });
  await expect(toggle(0)).toHaveAttribute("aria-expanded", "false");
  await expect(cards).toHaveCount(3);
  await toggle(1).focus();
  await walk.state("04-second-section-collapsed-by-keyboard", {
    action: () => page.keyboard.press("Enter"),
    visible: [heading(1).getByText("2 questions"), sectionCards(2).first()], hidden: [cardsOf(1)],
  });
  await expect(cards).toHaveCount(2);
  await page.keyboard.press("Space");
  await expect(toggle(1)).toHaveAttribute("aria-expanded", "true");
  await expect(cards).toHaveCount(3);
  await walk.state("05-selected-section-reopened", {
    action: () => toggle(0).click(),
    visible: [sectionCards(0).nth(0), sectionCards(0).nth(1)], hidden: [heading(0).getByText("1 question · 1 fault")],
  });
  await expect(cards).toHaveCount(5);
  await expect(sectionCards(0).first().getByRole("button", { name: "All regions" })).toHaveAttribute("aria-pressed", "true");
  await expect(sectionCards(0).first().getByRole("button", { name: "Send 1 answer" })).toBeEnabled();
  // Use the actual fixture task to walk browser and app Back from the global inbox.
  await sectionCards(0).nth(1).getByRole("link", { name: "Open L2 chat" }).click();
  await expect(page).toHaveURL(`/projects/${project.name}/tasks/${task.slug}`);
  await expect(page.getByRole("region", { name: "Task conversation", exact: true })).toBeVisible();
  await (info.project.name === "phone" ? page.getByRole("button", { name: "Back", exact: true }) : page.locator(".task-crumb")).click();
  await expect(page).toHaveURL("/");
  await expect(cards).toHaveCount(5);
  await page.goForward();
  await expect(page).toHaveURL(`/projects/${project.name}/tasks/${task.slug}`);
  await page.goBack();
  await expect(page).toHaveURL("/");
  // Selecting another project through the rail or switcher moves its section to the top.
  if (info.project.name === "phone") {
    const tabs = page.getByRole("navigation", { name: "Primary" });
    await tabs.getByRole("link", { name: "Chat", exact: true }).click();
    await page.getByRole("button", { name: names[0], exact: true }).click();
    await page.getByRole("dialog", { name: "Switch project" }).getByRole("link", { name: names[2], exact: true }).click();
    await tabs.getByRole("link", { name: /^Needs you/ }).click();
  } else {
    await page.getByLabel("Projects").getByRole("link", { name: names[2], exact: true }).click();
    await page.getByLabel("Rail").getByRole("link", { name: /^Needs you/ }).click();
  }
  await expect(page).toHaveURL("/");
  await walk.state("06-selection-changed-leads", {
    visible: [heading(0)], hidden: [],
  });
  await expect(sections.getByRole("heading", { level: 2 })).toHaveText([names[2]!, names[1]!, names[0]!]);
  queue = mixed.filter((item) => item.project === names[1]);
  await page.reload();
  await expect(cards).toHaveCount(1);
  await expect(sections).toHaveCount(1);
  await walk.state("03-single-project-group-full-owner", {
    visible: [sections.first().getByRole("heading"), page.getByText(`2 questions in ${names[1]}`)], hidden: [],
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
