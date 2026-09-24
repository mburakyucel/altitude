import { test } from "./fixtures";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { expect } from "@playwright/test";
import { fixtureProject, fixtureTask } from "./fixture-data";
import { walkthrough } from "./walkthrough";

// Read route declarations without importing React/browser modules into the test runner.
// Issue #195: a new route cannot silently escape rendered-state coverage.
const source = ts.createSourceFile("routes.tsx",
  readFileSync(new URL("../src/routes.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function paths(node: ts.Node, parent = ""): string[] {
  if (ts.isObjectLiteralExpression(node)) {
    const property = (name: string) => node.properties.find((p) =>
      ts.isPropertyAssignment(p) && p.name.getText(source) === name) as ts.PropertyAssignment | undefined;
    const path = property("path")?.initializer;
    const index = property("index");
    const current = path && ts.isStringLiteral(path)
      ? path.text.startsWith("/") ? path.text : `${parent}/${path.text}`
      : parent;
    const children = property("children")?.initializer;
    return [...(path || index ? [current] : []), ...(children ? paths(children, current) : [])];
  }
  const result: string[] = [];
  node.forEachChild((child) => { result.push(...paths(child, parent)); });
  return result;
}
const routePaths = [...new Set(paths(source))];

for (const route of [...routePaths, "/projects/:name?tab=work"]) test.describe(() => {
  const design = route.includes("/design/");
  const file = route.endsWith("/file");
  test.use({ serviceScript: design ? "task-design-service.py" : file ? "file-references-service.py" : "" });
  test(`${route} renders without errors or horizontal overflow (issue #195, SPEC §2.2)`, async ({ page, request }, info) => {
    const project = await fixtureProject(request, route === "/projects" || route === "/chat");
    const task = route.includes(":slug") ? await fixtureTask(request, project.name) : undefined;
    if (design) expect(task?.question?.design_url, "The fixture supplies a real saved proposal").toBeTruthy();
    let url = route.replace(":name", encodeURIComponent(project.name))
      .replace(":slug", encodeURIComponent(task?.slug ?? ""))
      .replace(":questionId", encodeURIComponent(task?.question?.id ?? ""))
      .replace(":revision", String(task?.question?.revision ?? ""))
      .replace("*", "__ui_unknown_route__");
    if (file) {
      const { paths } = await (await request.get("/fixture/files")).json();
      url += `?${new URLSearchParams({ path: paths["commands.md"] })}`;
    }
    const errors: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", (error) => errors.push(error.message));
    // Wait for initial data too: a shell/heading above a pending query is not a rendered route.
    const pending = new Set<import("@playwright/test").Request>();
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      // The change stream stays open by design; it is not an initial read.
      if (path.startsWith("/api/") && path !== "/api/changes") pending.add(request);
    });
    page.on("requestfinished", (request) => pending.delete(request));
    page.on("requestfailed", (request) => {
      if (pending.delete(request)) errors.push(`Failed API read: ${new URL(request.url()).pathname}`);
    });
    page.on("response", (response) => {
      if (new URL(response.url()).pathname.startsWith("/api/") && !response.ok()) {
        errors.push(`HTTP ${response.status()}: ${new URL(response.url()).pathname}`);
      }
    });
    const response = await page.goto(url);
    expect(response?.ok()).toBe(true);
    const main = page.getByRole("main");
    await expect(main).toBeVisible();
    if (file) {
      await expect(main.getByRole("heading", { name: "commands.md", exact: true })).toBeVisible();
      const contents = main.getByRole("region", { name: "File contents", exact: true });
      await expect(contents.getByRole("heading", { name: "Setup instructions", exact: true })).toBeVisible();
      await expect(contents.locator("pre")).toContainText("never executed");
      await expect(main.getByText("Loading file…", { exact: true })).toHaveCount(0);
    } else if (task && design) {
      await expect(main.getByRole("heading", { name: "Conversation layout", exact: true })).toBeVisible();
      await expect(main.getByText(`Preview · v${task.question!.revision}`, { exact: true })).toBeVisible();
      await expect(main.getByRole("img", { name: "Phone conversation", exact: true })).toBeVisible();
      await expect(main.getByRole("img", { name: "Desktop conversation", exact: true })).toBeVisible();
      await expect(main.getByRole("region", { name: "Preview text", exact: true }))
        .toContainText("Keep the conversation easy to read.");
      await expect(main.getByRole("link", { name: "← Back to question", exact: true }))
        .toHaveAttribute("href", `/projects/${project.name}/tasks/${task.slug}?question=${task.question!.id}&revision=${task.question!.revision}`);
      await expect(main.getByText("Loading preview…", { exact: true })).toHaveCount(0);
      await expect(main.getByText("Loading screenshot…", { exact: true })).toHaveCount(0);
    } else if (task && route.includes("/decisions/")) {
      // Legacy decision links resolve to the owning human conversation, including archived tasks.
      await expect(page).toHaveURL(new RegExp(`/projects/${project.name}/tasks/${task.slug}(\\?|$)`));
      await expect(main.getByRole("region", { name: "Task conversation", exact: true })).toBeVisible();
    } else if (task && route.endsWith("/report")) {
      // The report view: its own heading, and the crumb back to the task (SPEC §3.4 links).
      await expect(main.getByRole("heading", { name: "Report", exact: true })).toBeVisible();
      await expect(main.getByRole("link", { name: task.title || task.slug, exact: false })).toBeVisible();
    } else if (task) {
      // The phone header carries the title outside main; the desktop header inside it (SPEC §3.10).
      await expect(page.getByRole("heading", { level: 1, name: task.title || task.slug, exact: true })).toBeVisible();
      if (route.endsWith("/live")) {
        const live = main.getByRole("region", { name: "Live session", exact: true });
        await expect(live).toBeVisible();
        await expect(live.getByLabel("Connecting", { exact: true })).toBeHidden();
        // A transcript, a session that is gone, or what a queued task waits for: never a blank panel.
        await expect(live.getByRole("region", { name: "Live transcript", exact: true })
          .or(live.getByText(/^(No session file for this attempt|Waits for )/)).first()).toBeVisible();
      } else {
        await expect(main.getByRole("region", { name: "Task conversation", exact: true })).toBeVisible();
      }
    } else if (route === "/monitor") {
      await expect(main.getByRole("heading", { name: "Routing now", exact: true })).toBeVisible();
    } else if (route === "/settings") {
      await expect(main.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
      await expect(main.getByRole("link", { name: "Voice input Local speech service", exact: true })).toBeVisible();
      await expect(main.getByRole("region", { name: "Network", exact: true })).toBeVisible();
      await expect(main.getByRole("link", { name: /^Projects folder / })).toHaveAttribute("href", "/settings/projects-folder");
      await expect(main.getByRole("radio")).toHaveCount(0);
      await expect(main.getByText("Loading settings…", { exact: true })).toHaveCount(0);
    } else if (route === "/settings/voice") {
      await expect(main.getByRole("heading", { name: "Voice input", exact: true })).toBeVisible();
      await expect(main.getByRole("radio", { name: "Local speech service", exact: true })).toBeChecked();
      await expect(main.getByRole("radio")).toHaveCount(3);
      await expect(main.getByRole("link", { name: "‹ Settings", exact: true })).toHaveAttribute("href", "/settings");
      await expect(main.getByLabel("Endpoint URL")).toHaveCount(0);
      await expect(main.getByText("Loading settings…", { exact: true })).toHaveCount(0);
    } else if (route === "/settings/projects-folder") {
      await expect(main.getByRole("heading", { name: "Projects folder", exact: true })).toBeVisible();
      await expect(main.getByRole("region", { name: "Choose a folder", exact: true })).toBeVisible();
      await expect(main.getByRole("button", { name: "Use “Home”", exact: true })).toBeEnabled();
      await expect(main.getByRole("link", { name: "‹ Settings", exact: true })).toHaveAttribute("href", "/settings");
    } else if (route.startsWith("/projects") || route.startsWith("/chat")) {
      await expect(page).toHaveURL(new RegExp(`${project.path}(\\?tab=work)?$`));
      await expect(main.getByRole("button", { name: "More actions" })).toBeVisible();
      if (route.endsWith("?tab=work")) {
        await expect(main.getByRole("region", { name: "Work", exact: true })).toBeVisible();
      } else {
        await expect(main.getByRole("textbox", { name: /^Message L3 about / })).toBeVisible();
      }
    } else {
      expect(["/", "/*"], "Add a rendered-state assertion for the new route").toContain(route);
      await expect(main.getByRole("heading", { name: "Needs you", exact: true })).toBeVisible();
      await expect(main.getByLabel("Loading", { exact: true })).toBeHidden();
    }
    await expect.poll(() => pending.size, { message: "Initial API reads finish before layout checks", timeout: 10_000 }).toBe(0);
    await expect(main.getByLabel("Loading", { exact: true })).toHaveCount(0);
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    expect(await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)
      - document.documentElement.clientWidth), "SPEC §2.2: no viewport scrolls horizontally").toBeLessThanOrEqual(0);
    await test.step("phone-shell-is-fixed-only-inner-containe: only inner regions scroll", async () => {
      const fixed = page.locator(info.project.name === "phone" ? ".phone-header, .tab-bar, .convo-dock" : ".rail, .project-header, .task-header, .convo-dock");
      const boxes = () => fixed.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
      const before = await boxes();
      const owners = main.locator(".page:visible, .convo-scroll:visible, .work-panel:visible, .live-body:visible");
      if (info.project.name === "phone") await expect(owners).toHaveCount(1);
      for (const owner of await owners.all()) {
        await owner.evaluate((node) => { node.scrollTop = 0; });
        await owner.evaluate((node) => { node.scrollTop = node.scrollHeight; });
        await expect.poll(() => owner.evaluate((node) => node.scrollHeight - node.clientHeight - node.scrollTop)).toBeLessThanOrEqual(1);
        await owner.hover();
        await page.mouse.wheel(0, 900);
      }
      await walkthrough(page, info).state("phone-shell-is-fixed-only-inner-containe-scrolled-to-end", {
        visible: [fixed.first(), owners.first()], hidden: [],
      });
      expect(await boxes(), "Scrolling and overscrolling cannot move the shell or composer").toEqual(before);
      expect(await page.evaluate(() => ({
        height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
        viewport: window.innerHeight,
        top: window.scrollY,
        mainTop: document.querySelector("main")!.scrollTop,
      }))).toEqual({ height: page.viewportSize()!.height, viewport: page.viewportSize()!.height, top: 0, mainTop: 0 });
      for (const selector of ["html", "body", ".shell", ".shell-main"]) {
        await expect(page.locator(selector)).toHaveCSS("overflow", "hidden");
        await expect(page.locator(selector)).toHaveCSS("overscroll-behavior", "none");
      }
      for (const owner of await owners.all()) await expect(owner).toHaveCSS("overscroll-behavior", "contain");
    });
    expect(errors, "A rendered route has no console errors, uncaught exceptions, or failed API reads").toEqual([]);
  });
});
