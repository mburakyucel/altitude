import { expect, type APIRequestContext } from "@playwright/test";

/** Read fictional records through the real API in this test's disposable service. */
export async function fixtureProject(request: APIRequestContext, _first = false) {
  const response = await request.get("/api/overview");
  expect(response.ok(), "Fixture overview must be available; an error page is not route coverage").toBe(true);
  const overview = await response.json();
  const project = overview.projects.find((row: { name: string; managed: boolean }) =>
    row.managed);
  expect(project, "The fixture supplies a managed project").toBeTruthy();
  return { name: project.name as string, path: `/projects/${encodeURIComponent(project.name)}` };
}

export async function fixtureTask(request: APIRequestContext, name: string) {
  const response = await request.get(`/api/project/${encodeURIComponent(name)}`);
  expect(response.ok(), "Fixture project must be available").toBe(true);
  const project = await response.json();
  const task = [...project.archive, ...project.tasks][0];
  expect(task, "The fixture supplies an archived task").toBeTruthy();
  // Archive summaries omit session identity; the live view needs the actual task record.
  const detail = await request.get(`/api/task/${encodeURIComponent(name)}/${encodeURIComponent(task.slug)}`);
  expect(detail.ok(), "Selected task must still be readable").toBe(true);
  return await detail.json() as { slug: string; title?: string; session_id?: string };
}
