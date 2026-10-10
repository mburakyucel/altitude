import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router";
import { z } from "zod";
import { StatusMark } from "../components/StatusMark";
import { api, ApiError } from "../data/api";
import { Prose } from "../components/Prose";
import "./task-file.css";

const TaskFileSchema = z.object({
  name: z.string(), path: z.string(), current_path: z.string(), text: z.string(), markdown: z.boolean(),
});

function FileReader({ project, path }: { project: string; path: string }) {
  const [raw, setRaw] = useState(false);
  const [copy, setCopy] = useState("");
  const file = useQuery({
    queryKey: ["task-file", project, path],
    queryFn: async ({ signal }) => TaskFileSchema.parse(await api(`/api/files/${encodeURIComponent(project)}?${new URLSearchParams({ path })}`, { signal })),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const loading = file.isPending || file.isFetching;
  const data = loading || file.isError ? undefined : file.data;
  const denied = file.error instanceof ApiError && [401, 403].includes(file.error.status);

  async function copyPath() {
    try {
      await navigator.clipboard.writeText(path);
      setCopy("Path copied.");
    } catch {
      setCopy("Could not copy. Select the full path above to copy it manually.");
    }
  }

  return <div className="page task-file-page">
    <header className="task-file-heading">
      <p className="text-meta text-muted">Close this tab to return to the conversation.</p>
      <h1>{data?.name ?? "File"}</h1>
      <p className="task-file-path" aria-label="Referenced path">{path || "No file path provided."}</p>
      {data && data.current_path !== data.path ? <p className="task-file-path">Current location: {data.current_path}</p> : null}
      {path ? <div className="task-file-actions">
        <button className="link" onClick={() => void copyPath()}>Copy path</button>
        {copy === "Path copied." ? <StatusMark busy={false} label={copy} /> : copy ? <span role="status" className="text-meta text-muted">{copy}</span> : null}
      </div> : null}
    </header>
    {loading ? <p className="text-muted"><StatusMark label="Loading file…" /></p> : file.isError ? <section role="alert" className="task-file-unavailable">
      <h2>File unavailable</h2>
      <p className="text-muted">{denied ? "This file cannot be opened here. Only supported text documents in this project's task folders are available." : file.error instanceof ApiError ? file.error.message : "The file could not be loaded. Check your connection and try again."}</p>
      <button className="link" onClick={() => void file.refetch()}>Retry</button>
    </section> : data ? <section className="task-file-content" aria-label="File contents">
      {data.markdown ? <button className="link task-file-raw-toggle" aria-pressed={raw} onClick={() => setRaw(!raw)}>Raw</button> : null}
      {!data.text ? <p className="text-muted">This file is empty.</p> : data.markdown && !raw ? <Prose text={data.text} document /> : <pre className="task-file-raw">{data.text}</pre>}
    </section> : null}
  </div>;
}

export default function TaskFile() {
  const { name = "" } = useParams();
  const [params] = useSearchParams();
  const path = params.get("path") ?? "";
  return <FileReader key={`${name}\n${path}`} project={name} path={path} />;
}
