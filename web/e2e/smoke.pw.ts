import { readFileSync } from "node:fs";
import ts from "typescript";
import { expect, test } from "@playwright/test";
import { liveProject, liveTask } from "./live-data";

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

for (const route of routePaths) {
  test(`${route} renders without errors or horizontal overflow (issue #195, SPEC §2.2)`, async ({ page, request }) => {
    const project = await liveProject(request, route === "/projects" || route === "/chat");
    const task = route.includes(":slug") ? await liveTask(request, project.name) : undefined;
    const url = route.replace(":name", encodeURIComponent(project.name))
      .replace(":slug", encodeURIComponent(task?.slug ?? ""))
      .replace("*", "__ui_unknown_route__");
    const errors: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", (error) => errors.push(error.message));
    // Wait for initial data too: a shell/heading above a pending query is not a rendered route.
    const pending = new Set<import("@playwright/test").Request>();
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) pending.add(request);
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
    if (task) {
      await expect(main.getByRole("heading", { name: task.title || task.slug, exact: true })).toBeVisible();
      if (route.endsWith("/live")) {
        await expect(task.session_id ? main.getByRole("textbox", { name: "Search transcript" })
          : main.getByText("No session yet: the live view opens with the task's first worker.")).toBeVisible();
        await expect(main.getByText("Loading the session…", { exact: true })).toBeHidden();
      } else {
        await expect(main.getByRole("region", { name: "Task conversation" })).toBeVisible();
      }
    } else if (route === "/monitor") {
      await expect(main.getByRole("heading", { name: "Routing now", exact: true })).toBeVisible();
    } else if (route.startsWith("/projects") || route.startsWith("/chat")) {
      await expect(page).toHaveURL(new RegExp(`${project.path}$`));
      await expect(main.getByRole("button", { name: "More actions" })).toBeVisible();
      await expect(main.getByRole("textbox", { name: "Message L3", exact: true })).toBeVisible();
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
    expect(errors, "A rendered route has no console errors, uncaught exceptions, or failed API reads").toEqual([]);
  });
}
