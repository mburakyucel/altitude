import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { ImageViewer, readImage } from "./ImageViewer";
import type { GalleryImage } from "./ImageViewer";
import { Icon } from "./Shown";
import type { MessageImage } from "../data/api";
import { stampText } from "../data/observed";

export type { GalleryImage } from "./ImageViewer";
/** A picture this page holds, picked, sending or just sent, with the size read when it was picked. */
export interface ImagePreview { name: string; url: string; width?: number; height?: number }
type OpenGallery = (images: GalleryImage[], index: number) => void;
interface Viewing { images: GalleryImage[]; index: number; scope: string; entry: string }

const ViewerContext = createContext<OpenGallery | null>(null);

export function useGallery(): OpenGallery {
  const open = useContext(ViewerContext);
  if (!open) throw new Error("Images open inside ImageViewerHost.");
  return open;
}

/** The viewer where the page has one; a composer rendered on its own has none. */
export function useOptionalGallery(): OpenGallery | null {
  return useContext(ViewerContext);
}

/** The page's one image viewer. It keeps its own list of images, so a viewer opened on a queued message stays open while
 * history admission replaces that row (issue #461). A new scope closes it. Opening adds one history entry, so Back,
 * Escape and Close image all return to where it opened; moving between images adds none. */
export function ImageViewerHost({ scope, children }: { scope: string; children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [viewing, setViewing] = useState<Viewing | null>(null);
  const entry = (location.state as { gallery?: string } | null)?.gallery;
  if (viewing && viewing.scope !== scope) setViewing(null);
  useEffect(() => {
    if (viewing && viewing.entry !== entry) setViewing(null);
    // A gallery entry reached without its gallery (a reload, Forward after closing) steps back to the page it opened on.
    else if (!viewing && entry) void navigate(-1);
  }, [location.key]);
  const open: OpenGallery = (images, index) => {
    const id = `${Date.now()}-${Math.random()}`;
    setViewing({ images, index, scope, entry: id });
    void navigate(`${location.pathname}${location.search}${location.hash}`, { state: { ...(location.state as object | null), gallery: id } });
  };
  return <ViewerContext.Provider value={open}>
    {children}
    {viewing?.scope === scope && viewing.entry === entry ? <ImageViewer key={viewing.entry} images={viewing.images} start={viewing.index} onClose={() => void navigate(-1)} /> : null}
  </ViewerContext.Provider>;
}

/** Who sent a message's pictures and when, as the viewer's bar says it: "You · 14:05", "L2 · Oct 20, 09:12". */
export function sentBy(who: string, at?: string | null): string {
  return [who, stampText(at)].filter(Boolean).join(" · ");
}

/** A chat thumbnail's shape from its picture's size: one picture between 9:16 and 16:9, a tile between 3:4 and 4:3. */
function aspectOf(width: number | undefined, height: number | undefined, one: boolean): number {
  const aspect = width && height ? width / height : 4 / 3;
  return one ? Math.min(16 / 9, Math.max(9 / 16, aspect)) : Math.min(4 / 3, Math.max(3 / 4, aspect));
}

/** One private read owns its object URL. Neither retry nor opening the viewer resends a message. */
function StoredImage({ project, image, preview, one, open }: { project: string; image: MessageImage; preview?: string; one: boolean; open: () => void }) {
  const container = useRef<HTMLElement>(null);
  // A message this page just sent starts from the picture it sent, so nothing blinks between Send and the saved
  // thumbnail (SPEC.md §1.1 Actions answer at once). The image's own read replaces it, and its error still shows,
  // so that read starts at once, on screen or not.
  const [held, setHeld] = useState(preview);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined" || Boolean(preview));
  const [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const key = `${project}/${image.id}`;
  useEffect(() => {
    if (visible || !container.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "240px" });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    if (!visible) return;
    const request = new AbortController();
    let objectUrl = "";
    setUrl("");
    setError("");
    readImage(`/api/images/${project}/${encodeURIComponent(image.id)}`, request.signal).then((bytes) => {
      if (request.signal.aborted) return;
      objectUrl = URL.createObjectURL(bytes);
      setUrl(objectUrl);
      setHeld(undefined);
    }, (cause: unknown) => {
      if (!request.signal.aborted) { setError(cause instanceof Error ? cause.message : "Could not load"); setHeld(undefined); }
    });
    return () => { request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [project, image.id, attempt, visible]);
  const shown = url || held;
  return <figure className="message-image" ref={container} data-image={key} data-failed={error ? true : undefined} tabIndex={-1}
    style={{ "--aspect": aspectOf(image.width, image.height, one) } as CSSProperties}>
    {shown && !error ? <button className="message-image-tile" type="button" aria-label={`Open image ${image.name}`} onClick={open}>
      <img src={shown} alt={image.name} onError={() => setError("Could not load")} />
    </button> : error ? <div className="message-image-tile">
      <Icon name="image-off" size={20} />
      <span role="alert">{error}</span>
      <button type="button" className="message-image-retry" aria-label={`Retry image ${image.name}`} onClick={() => setAttempt((value) => value + 1)}>Retry</button>
    </div> : <div className="message-image-tile" data-loading><span className="visually-hidden">Loading image…</span></div>}
    <figcaption>{image.name}</figcaption>
  </figure>;
}

/**
 * A saved message's thumbnails; `previews` are the pictures this page sent with it while it still holds them. The
 * server keeps them in order and saves all or none, though it may shorten a name, so they match by position.
 * `context` says who sent them and when, for the viewer's bar.
 */
export function MessageImages({ project, images, previews, context }: { project: string; images?: MessageImage[] | null; previews?: ImagePreview[]; context?: string }) {
  const open = useGallery();
  if (!images?.length) return null;
  const gallery = images.map((image) => ({ key: `${project}/${image.id}`, name: image.name, url: `/api/images/${project}/${encodeURIComponent(image.id)}`,
    context, width: image.width, height: image.height }));
  return <div className="message-images" data-count={images.length} aria-label="Message images">{images.map((image, index) =>
    <StoredImage key={image.id} project={project} image={image} preview={previews?.length === images.length ? previews[index]!.url : undefined}
      one={images.length === 1} open={() => open(gallery, index)} />)}</div>;
}

/** A message's pictures on their way, laid out as its saved thumbnails will be from the size read when they were picked. */
export function PendingImages({ images }: { images?: ImagePreview[] }) {
  return images?.length ? <div className="message-images" data-count={images.length} aria-label="Sending images">{images.map((image, index) =>
    <figure className="message-image" key={index} style={{ "--aspect": aspectOf(image.width, image.height, images.length === 1) } as CSSProperties}>
      <span className="message-image-tile"><img src={image.url} alt={image.name} /></span>
      <figcaption aria-hidden="true">{image.name}</figcaption>
    </figure>)}</div> : null;
}
