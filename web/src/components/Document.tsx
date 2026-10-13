import { Component, lazy, Suspense } from "react";
import type { ComponentProps, ReactNode } from "react";
import type MarkdownDocument from "./MarkdownDocument";
import { SavedImage } from "./Shown";
import "./markdown.css";

/** Only a rejected import uses readable-source recovery; renderer errors still reach the route. */
class DocumentLoadFailure extends Error {}

const Renderer = lazy(() => import("./MarkdownDocument").catch((cause: unknown) => {
  throw new DocumentLoadFailure("Document formatting did not load", { cause });
}));

class DocumentBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null };
  static getDerivedStateFromError(error: unknown) { return { error }; }
  render() {
    if (this.state.error && !(this.state.error instanceof DocumentLoadFailure)) throw this.state.error;
    return this.state.error ? this.props.fallback : this.props.children;
  }
}

/** Document source remains usable after a failed deferred load, without replacing its surrounding page. */
export default function Document({ loading, warnUnsent = false, ...props }: ComponentProps<typeof MarkdownDocument> & {
  loading: ReactNode;
  /** The file reader lives in its own tab; conversation-hosted readers share their page with a draft. */
  warnUnsent?: boolean;
}) {
  const fallback = <>
    <div className="document-load-notice" role="alert">
      <strong>Formatting unavailable</strong>
      {warnUnsent ? <p className="text-meta text-muted">Reloading clears text you have typed but not sent.</p> : null}
      <button type="button" className="btn" onClick={() => window.location.reload()}>Reload page</button>
    </div>
    <pre className="document-source">{props.text}</pre>
    {props.images?.map((image, index) => image.embedded
      ? <SavedImage key={image.url} className="shown-extra" image={image} alt={image.name}
          open={props.onOpenImage ? () => props.onOpenImage!(index) : undefined} />
      : null)}
  </>;
  return <DocumentBoundary fallback={fallback}>
    <Suspense fallback={loading}><Renderer {...props} /></Suspense>
  </DocumentBoundary>;
}
