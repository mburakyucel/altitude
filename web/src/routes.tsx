import { Navigate, redirect } from "react-router";
import { useRef } from "react";
import type { RouteObject } from "react-router";
import AppShell from "./shell/AppShell";
import { useOverview } from "./data/api";
import { managedProjects } from "./shell/projects";
import FirstRun from "./routes/FirstRun";
import NeedsYou from "./routes/NeedsYou";
import ProjectPage from "./routes/Project";
import DecisionPage from "./routes/Decision";
import Task from "./routes/Task";
import TaskReport from "./routes/TaskReport";
import TaskDesign from "./routes/TaskDesign";
import TaskFile from "./routes/TaskFile";
import Monitor from "./routes/Monitor";

/** /projects: the first managed project, or First run when nothing is managed
 * (SPEC.md §2.1, §3.12). */
function ProjectIndex() {
  const overview = useOverview();
  // Choose the index destination once. Once First run is shown, registration owns navigation.
  const destination = useRef<string | null>(null);
  if (overview.isSuccess && destination.current === null) destination.current = managedProjects(overview.data)[0]?.name ?? "";
  if (overview.isPending) {
    return (
      <div className="page" aria-label="Loading">
        <div className="skeleton h-6 w-48" />
      </div>
    );
  }
  if (overview.isError) {
    return (
      <div className="page">
        <p className="text-danger">
          Could not read the projects.{" "}
          <button type="button" className="link" onClick={() => overview.refetch()}>
            Retry
          </button>
        </p>
      </div>
    );
  }
  const first = managedProjects(overview.data)[0];
  if (!first || destination.current === "") {
    return (
      <div className="page first-run-page">
        <FirstRun overview={overview} />
      </div>
    );
  }
  return <Navigate to={`/projects/${first.name}`} replace />;
}

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <NeedsYou /> },
      { path: "/projects", element: <ProjectIndex /> },
      { path: "/projects/:name", element: <ProjectPage /> },
      { path: "/projects/:name/file", element: <TaskFile /> },
      // Saved decision URLs replace themselves with the owning conversation and durable question anchor.
      { path: "/projects/:name/decisions/:slug", element: <DecisionPage /> },
      // One page for both: the desktop shows the conversation beside the live session, the phone
      // tabs between them and `/live` selects the second tab (SPEC.md §2.1, §3.10).
      { path: "/projects/:name/tasks/:slug", element: <Task /> },
      { path: "/projects/:name/tasks/:slug/live", element: <Task /> },
      // The task's report view: what the expanded system card links as Full report and Digest (§3.4).
      { path: "/projects/:name/tasks/:slug/report", element: <TaskReport /> },
      { path: "/projects/:name/tasks/:slug/design/:questionId/:revision", element: <TaskDesign /> },
      { path: "/chat", loader: () => redirect("/projects") },
      { path: "/chat/:name", loader: ({ params }) => redirect(`/projects/${params.name}`) },
      { path: "/monitor", element: <Monitor /> },
      // Last: a typo'd deep link lands on Needs you inside the shell, not on react-router's bare
      // error page outside it.
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];
