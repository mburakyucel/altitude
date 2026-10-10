import { useNavigate } from "react-router";

/** Back to the previous entry of this tab's app history, else replace this page with `fallback`.
 * The router's entry index starts at 0 on a fresh document and survives replacements and reloads. */
export function useBack(fallback: string) {
  const navigate = useNavigate();
  return () => {
    if (window.history.state?.idx > 0) navigate(-1);
    else navigate(fallback, { replace: true });
  };
}

export function useTaskBack(project: string) {
  return useBack(`/projects/${encodeURIComponent(project)}`);
}
