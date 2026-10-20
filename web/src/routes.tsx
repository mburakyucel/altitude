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
import TaskCaptures from "./routes/TaskCaptures";
import TaskFile from "./routes/TaskFile";
import Monitor from "./routes/Monitor";
import Settings from "./routes/Settings";
import Repair from "./routes/Repair";
import ProjectSettings from "./routes/ProjectSettings";
import ProjectAgents from "./routes/ProjectAgents";
import ProjectAgent from "./routes/ProjectAgent";
import ProjectAgentRun from "./routes/ProjectAgentRun";
import ProjectAgentForm from "./routes/ProjectAgentForm";
import WeeklyReview from "./routes/WeeklyReview";

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
      // A project's agents: the list, the form for a new one, the Weekly improvement review, one agent, its form and one
      // run (project-agents/PROPOSAL.md).
      { path: "/projects/:name/agents", element: <ProjectAgents /> },
      { path: "/projects/:name/agents/new", element: <ProjectAgentForm /> },
      { path: "/projects/:name/agents/weekly", element: <WeeklyReview /> },
      { path: "/projects/:name/agents/:id", element: <ProjectAgent /> },
      { path: "/projects/:name/agents/:id/edit", element: <ProjectAgentForm /> },
      { path: "/projects/:name/agents/:id/runs/:n", element: <ProjectAgentRun /> },
      // The project folder's terminal: the desktop panel beside the conversation, full screen on phone.
      { path: "/projects/:name/terminal", element: <ProjectPage /> },
      // Saved decision URLs replace themselves with the owning conversation and durable question anchor.
      { path: "/projects/:name/decisions/:slug", element: <DecisionPage /> },
      // One page for both: the desktop shows the conversation beside the live session, the phone
      // tabs between them and `/live` selects the second tab (SPEC.md §2.1, §3.10).
      { path: "/projects/:name/tasks/:slug", element: <Task /> },
      { path: "/projects/:name/tasks/:slug/live", element: <Task /> },
      // The task worktree's terminal: the phone's third tab, the desktop panel's Terminal view.
      { path: "/projects/:name/tasks/:slug/terminal", element: <Task /> },
      // The task's report view: what the expanded system card links as Full report and Digest (§3.4).
      { path: "/projects/:name/tasks/:slug/report", element: <TaskReport /> },
      // Shown files open in a reader over the conversation: a reply's, or a question revision's (SPEC.md §3.11).
      { path: "/projects/:name/tasks/:slug/view/:messageId", element: <Task /> },
      { path: "/projects/:name/tasks/:slug/view/:questionId/:revision", element: <Task /> },
      { path: "/projects/:name/tasks/:slug/captures/:messageId", element: <TaskCaptures /> },
      { path: "/chat", loader: () => redirect("/projects") },
      { path: "/chat/:name", loader: ({ params }) => redirect(`/projects/${params.name}`) },
      { path: "/monitor", element: <Monitor /> },
      { path: "/settings", element: <Settings /> },
      { path: "/settings/voice", element: <Settings page="voice" /> },
      { path: "/settings/projects", element: <Settings page="projects" /> },
      { path: "/settings/projects-folder", element: <Settings page="projects-folder" /> },
      { path: "/settings/name", element: <Settings page="name" /> },
      { path: "/settings/prerequisites", element: <Settings page="prerequisites" /> },
      { path: "/settings/incident-reports", element: <Settings page="incident-reports" /> },
      { path: "/settings/devices", element: <Settings page="devices" /> },
      { path: "/settings/network", element: <Settings page="network" /> },
      { path: "/settings/repairs", element: <Settings page="repairs" /> },
      // A repair belongs to no project, so no tab is lit (SPEC.md §3.15).
      { path: "/repairs/:id", element: <Repair /> },
      { path: "/settings/projects/:name", element: <ProjectSettings /> },
      // Last: a typo'd deep link lands on Needs you inside the shell, not on react-router's bare
      // error page outside it.
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];
