import { Navigate, useLocation, useParams } from "react-router";
import { useTask } from "../data/api";
import { questionPath } from "../data/decisions";

/** Saved decision links replace themselves with the owning conversation, preserving Back. */
export default function DecisionPage() {
  const { name = "", slug = "" } = useParams();
  const location = useLocation();
  const task = useTask(name, slug);
  if (task.isPending) return <div className="page" aria-label="Loading"><div className="skeleton h-28" /></div>;
  if (!task.data) return <div className="page"><p role="alert">Could not load the task. <button className="link" onClick={() => task.refetch()}>Retry</button></p></div>;
  return <Navigate replace to={questionPath(task.data.question ?? { project: name, slug })} state={location.state} />;
}
