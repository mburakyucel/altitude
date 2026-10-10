import { useState } from "react";
import { Link, useParams } from "react-router";
import { StatusMark } from "../components/StatusMark";
import { ApiError, useTaskCaptures } from "../data/api";
import type { TaskCapture } from "../data/api";
import { agoText, exactTime } from "../data/observed";

/** "299 KiB · 9.8 s · 11 frames": what a capture weighs and how long it plays. */
function captureMeta({ bytes, seconds, frames }: Pick<TaskCapture, "bytes" | "seconds" | "frames">): string {
  const size = bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  return `${size} · ${Number(seconds.toFixed(1))} s · ${frames} ${frames === 1 ? "frame" : "frames"}`;
}

function Capture({ title, url, width, height, ...meta }: TaskCapture) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  return <figure className="capture" aria-label={title} aria-busy={state === "loading"}>
    <figcaption><b>{title}</b>{state !== "error" ? <span className="text-muted">{captureMeta(meta)}</span> : null}</figcaption>
    {state === "error" ? <p className="text-danger" role="alert">Capture unavailable. <button className="link" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}>Retry capture</button></p> : <>
      {state === "loading" ? <p className="text-muted"><StatusMark label="Loading capture…" /></p> : null}
      {/* A GIF loops by itself; its saved dimensions reserve the space before it loads. */}
      <img key={attempt} src={`${url}${attempt ? `?retry=${attempt}` : ""}`} alt={title} width={width} height={height} hidden={state !== "ready"}
        onLoad={() => setState("ready")} onError={() => setState("error")} />
    </>}
  </figure>;
}

/** The GIFs one validation run recorded, attached to an L2 reply and opened beside its conversation. */
export default function TaskCaptures() {
  const { name = "", slug = "", messageId = "" } = useParams();
  const captures = useTaskCaptures(name, slug, messageId);
  const denied = captures.error instanceof ApiError && [401, 403].includes(captures.error.status);
  return <div className="page design-page">
    <Link className="text-meta" to={captures.data?.conversation_url ?? `/projects/${name}/tasks/${slug}`} replace>← Back to conversation</Link>
    {captures.isPending ? <p className="text-muted"><StatusMark label="Loading captures…" /></p> : captures.isError ? <div role="alert" className="design-unavailable">
      <h1>Capture unavailable</h1>
      <p className="text-muted">{denied ? "Access to these captures is unavailable. Retry after access is restored." : "These captures could not be loaded. Return to the conversation for an update, or try again."}</p>
      <button className="link" onClick={() => void captures.refetch()}>Retry</button>
    </div> : <>
      <header className="design-heading">
        <h1>Captures from validation run {captures.data.run}</h1>
        {captures.data.at ? <p className="text-meta text-muted" title={exactTime(captures.data.at)}>Attached {agoText(captures.data.at)}</p> : null}
      </header>
      {captures.data.captures.map((item) => <Capture key={item.url} {...item} />)}
    </>}
  </div>;
}
