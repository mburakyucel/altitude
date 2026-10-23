import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError, useShownFiles } from "../data/api";
import type { Shown } from "../data/api";
import { useGallery } from "../components/MessageImages";
import { Icon, SavedImage } from "../components/Shown";
import { useBack } from "../components/useTaskBack";
import Document from "../components/Document";

function Skeleton({ name }: { name: string }) {
  return <div className="shown-skeleton" role="status" aria-label={`Loading ${name}`}>
    <i data-heading /><i /><i /><i /><i /><i /><i data-image />
  </div>;
}

/**
 * The reader for files an owner showed (SPEC.md §3.11): a panel beside the conversation on a wide desktop, the whole
 * screen elsewhere. The task page stays mounted underneath, so closing returns to the same place with the draft intact.
 * `known` is the conversation's own record of the set, which names it while its files load.
 */
export function ShownReader({ project, slug, shownId, revision, known, panel }: {
  project: string; slug: string; shownId: string; revision?: string; known?: Shown; panel: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const close = useBack(`/projects/${encodeURIComponent(project)}/tasks/${slug}${location.search}`);
  const gallery = useGallery();
  const files = useShownFiles(project, slug, shownId, revision);
  const root = useRef<HTMLElement>(null);
  const data = files.isError ? undefined : files.data;
  const set = data ?? known;
  const images = set?.images ?? [];
  const name = set?.document?.name ?? (images.length === 1 ? images[0]!.name : images.length ? `${images.length} images` : "Preview");
  const newer = set?.newer_url;
  const denied = files.error instanceof ApiError && [401, 403].includes(files.error.status);
  const open = (index: number) => gallery(images.map((image) => ({ key: image.url, name: image.name, url: image.url, context: name, width: image.width, height: image.height })), index);

  useEffect(() => {
    if (!panel) root.current?.focus({ preventScroll: true });
  }, [panel]);
  // Escape closes the reader before the task page reads it as Stop; the gallery and text fields keep their own.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing ||
          target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") ||
          document.querySelector('dialog[open], [role="menu"], .overlay')) return;
      event.preventDefault();
      close();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [close]);

  const closeButton = <button type="button" className="shown-close" aria-label="Close" onClick={close}><Icon name="close" size={panel ? 20 : 22} /></button>;
  return <section className="shown-reader" data-panel={panel || undefined} aria-label={name} ref={root} tabIndex={-1}>
    <header className="shown-reader-head">
      {panel ? <Icon name="document" /> : closeButton}
      <b>{name}</b>
      {newer ? <button type="button" className="shown-newer" onClick={() => void navigate(`${newer}${location.search}`, { replace: true, state: location.state })}>
        Newer<Icon name="chevron" size={14} />
      </button> : null}
      {panel ? closeButton : null}
    </header>
    <div className="shown-reader-body">
      {files.isPending || (files.isFetching && !data) ? <Skeleton name={name} /> : !data ? <div className="shown-gone" role="alert">
        <Icon name={denied ? "lock" : "document-off"} size={40} />
        <h2>Preview unavailable</h2>
        <p>{denied ? "This device has no access right now." : "It could not be loaded. Try again, or ask in the conversation."}</p>
        <button type="button" className="btn" onClick={() => void files.refetch()}>Retry</button>
      </div> : <>
        {data.images.map((image, index) => image.embedded ? null
          : <SavedImage key={image.url} className="shown-extra" image={image} alt={image.name} open={() => open(index)} />)}
        {!data.document ? null : data.document.markdown
          ? <Document text={data.document.text} path={data.document.path} images={data.images} onOpenImage={open}
              loading={<Skeleton name={name} />} warnUnsent />
          : <pre className="shown-text">{data.document.text}</pre>}
      </>}
    </div>
  </section>;
}
