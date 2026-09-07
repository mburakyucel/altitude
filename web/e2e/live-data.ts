import { expect, type APIRequestContext } from "@playwright/test";

/** Read real data without creating projects, dispatching work, or mutating the running service. */
export async function liveProject(request: APIRequestContext, first = false) {
  const response = await request.get("/api/overview");
  expect(response.ok(), "Live overview must be available; an error page is not route coverage").toBe(true);
  const overview = await response.json();
  const project = overview.projects.find((row: { name: string; managed: boolean }) =>
    row.managed && (first || !process.env.UI_PROJECT || row.name === process.env.UI_PROJECT));
  expect(project, "UI suite needs a managed project (optionally select UI_PROJECT)").toBeTruthy();
  return { name: project.name as string, path: `/projects/${encodeURIComponent(project.name)}` };
}

export async function liveTask(request: APIRequestContext, name: string) {
  const response = await request.get(`/api/project/${encodeURIComponent(name)}`);
  expect(response.ok(), "Live project must be available").toBe(true);
  const project = await response.json();
  // Archived tasks are stable while other workers run; active tasks also work on a fresh service.
  const task = [...project.archive, ...project.tasks].find((row: { slug: string }) =>
    !process.env.UI_TASK || row.slug === process.env.UI_TASK);
  expect(task, "UI route coverage needs a real task, active or archived (optionally UI_TASK)").toBeTruthy();
  // Archive summaries omit session identity; the live view needs the actual task record.
  const detail = await request.get(`/api/task/${encodeURIComponent(name)}/${encodeURIComponent(task.slug)}`);
  expect(detail.ok(), "Selected task must still be readable").toBe(true);
  return await detail.json() as { slug: string; title?: string; session_id?: string };
}
