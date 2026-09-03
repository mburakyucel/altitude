import { Navigate } from "react-router";
import type { RouteObject } from "react-router";
import AppShell from "./shell/AppShell";
import { useOverview } from "./data/api";
import Inbox from "./routes/Inbox";
import Projects from "./routes/Projects";
import Project from "./routes/Project";
import Task, { TaskConversation } from "./routes/Task";
import Chat from "./routes/Chat";
import Monitor from "./routes/Monitor";
import LiveSession from "./routes/LiveSession";

/** /chat with no project: redirect to the first managed project's chat. */
function ChatRedirect() {
  const overview = useOverview();
  if (overview.isPending) return <p className="text-muted">Loading…</p>;
  if (overview.isError) return <p className="text-danger">{overview.error.message}</p>;
  const first = overview.data.projects.find((p) => p.managed);
  if (!first) return <p className="text-muted">No managed projects yet.</p>;
  return <Navigate to={`/chat/${first.name}`} replace />;
}

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <Inbox /> },
      { path: "/projects", element: <Projects /> },
      { path: "/projects/:name", element: <Project /> },
      {
        path: "/projects/:name/tasks/:slug",
        element: <Task />,
        children: [
          { index: true, element: <TaskConversation /> },
          { path: "live", element: <LiveSession /> },
        ],
      },
      { path: "/chat", element: <ChatRedirect /> },
      { path: "/chat/:name", element: <Chat /> },
      { path: "/monitor", element: <Monitor /> },
      // Last: a typo'd deep link lands on the Inbox inside the shell, not on react-router's
      // bare error page outside it.
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];
