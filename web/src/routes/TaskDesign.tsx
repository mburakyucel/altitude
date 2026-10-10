import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, useTaskDesign } from "../data/api";
import { previewQuestion } from "../data/decisions";
import { Prose } from "../components/Prose";
import { useBack } from "../components/useTaskBack";
import { useViewport } from "../shell/breakpoints";

function Screenshot({ title, url }: { title: string; url: string }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  return <figure className="design-screenshot" aria-label={title} aria-busy={state === "loading"}>
    <figcaption><b>{title}</b>{state === "ready" ? <a href={url} target="_blank" rel="noopener noreferrer">Full size ↗</a> : null}</figcaption>
    {state === "error" ? <p className="text-danger" role="alert">Screenshot unavailable. <button className="link" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}>Retry screenshot</button></p> : <>
      {state === "loading" ? <p className="text-muted" role="status">Loading screenshot…</p> : null}
      <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${title} full size`} hidden={state !== "ready"}>
        <img key={attempt} src={`${url}${attempt ? `?retry=${attempt}` : ""}`} alt={title} onLoad={() => setState("ready")} onError={() => setState("error")} />
      </a>
    </>}
  </figure>;
}

/** A fixed preview revision, read beside its existing question without a second approval flow. Back returns
 * to the page it was opened from; the phone header carries that control, the desktop page its own. */
export default function TaskDesign() {
  const { name = "", slug = "", questionId = "", revision = "" } = useParams();
  const preview = useTaskDesign(name, slug, questionId, revision);
  const back = useBack(previewQuestion(name, slug, questionId, revision));
  const { phone } = useViewport();
  const denied = preview.error instanceof ApiError && [401, 403].includes(preview.error.status);
  return <div className="page design-page">
    {phone ? null : <button type="button" className="link text-meta self-start" onClick={back}>← Back</button>}
    {preview.isPending ? <p className="text-muted" role="status">Loading preview…</p> : preview.isError ? <div role="alert" className="design-unavailable">
      <h1>Design unavailable</h1>
      <p className="text-muted">{denied ? "Access to this preview is unavailable. Retry after access is restored." : "This saved preview could not be loaded. Return to the question for an update, or try again."}</p>
      <button className="link" onClick={() => void preview.refetch()}>Retry</button>
    </div> : <>
      <header className="design-heading">
        <h1>{preview.data.title}</h1>
        {preview.data.superseded ? <p className="text-meta" role="status">Earlier preview. {preview.data.current_question_url ? <Link to={preview.data.current_question_url} replace>Open current question</Link> : "Return to the question for the latest discussion."}</p> : null}
      </header>
      {preview.data.images.map((item) => <Screenshot key={item.url} {...item} />)}
      <section aria-label="Preview text" className="design-text"><h2>Preview</h2><Prose text={preview.data.text} /></section>
    </>}
  </div>;
}
