import { useNavigate } from "react-router";

/** Use the router's existing browser entry index, which survives replacements and reloads. */
export function useTaskBack(project: string) {
  const navigate = useNavigate();
  return () => {
    if (window.history.state?.idx > 0) navigate(-1);
    else navigate(`/projects/${encodeURIComponent(project)}`, { replace: true });
  };
}
