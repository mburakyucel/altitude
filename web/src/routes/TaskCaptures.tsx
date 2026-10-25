import { Link, useParams } from "react-router";
import { useGallery } from "../components/MessageImages";
import { SavedImage } from "../components/Shown";
import { StatusMark } from "../components/StatusMark";
import { ApiError, useTaskCaptures } from "../data/api";
import type { TaskCapture } from "../data/api";
import { agoText, exactTime } from "../data/observed";

/** "299 KiB · 9.8 s · 11 frames": what a capture weighs and how long it plays. */
function captureMeta({ bytes, seconds, frames }: Pick<TaskCapture, "bytes" | "seconds" | "frames">): string {
  const size = bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  return `${size} · ${Number(seconds.toFixed(1))} s · ${frames} ${frames === 1 ? "frame" : "frames"}`;
}

/** One GIF over its title: a grey block at its saved size while it loads, a button into the viewer, or Retry. */
function Capture({ capture, open }: { capture: TaskCapture; open: () => void }) {
  const { title, url, width, height, ...meta } = capture;
  return <figure className="capture" aria-label={title}>
    <figcaption><b>{title}</b><span className="text-muted">{captureMeta(meta)}</span></figcaption>
    <SavedImage className="capture-open" image={{ url, name: title, width, height }} alt={title} open={open}
      label={`Open capture ${title}`} unavailable="Capture unavailable" />
  </figure>;
}

/** The GIFs one validation run recorded, attached to an L2 reply and opened beside its conversation. */
export default function TaskCaptures() {
  const { name = "", slug = "", messageId = "" } = useParams();
  const captures = useTaskCaptures(name, slug, messageId);
  const gallery = useGallery();
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
      {captures.data.captures.map((item, index, all) => <Capture key={item.url} capture={item} open={() => gallery(all.map((capture) => ({
        key: capture.url, name: capture.title, url: capture.url, width: capture.width, height: capture.height, context: `Validation run ${captures.data.run}` })), index)} />)}
    </>}
  </div>;
}
