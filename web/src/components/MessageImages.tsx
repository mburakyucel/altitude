import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { PointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router";
import { Icon } from "./Shown";
import { StatusMark } from "./StatusMark";
import type { MessageImage } from "../data/api";

export interface ImagePreview { name: string; url: string }
/** One image of a gallery: a message's images or a shown set, read by address. */
export interface GalleryImage { key: string; name: string; url: string }
type OpenGallery = (images: GalleryImage[], index: number) => void;
interface Viewing { images: GalleryImage[]; index: number; scope: string; entry: string }

const ViewerContext = createContext<OpenGallery | null>(null);

export function useGallery(): OpenGallery {
  const open = useContext(ViewerContext);
  if (!open) throw new Error("Images open inside ImageViewerHost.");
  return open;
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

/** One private read owns its object URL. Neither retry nor opening the viewer resends a message. */
function StoredImage({ project, image, open }: { project: string; image: MessageImage; open: () => void }) {
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
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
    void (async () => {
      try {
        const response = await fetch(`/api/images/${project}/${encodeURIComponent(image.id)}`, { signal: request.signal });
        if (!response.ok) {
          throw new Error(response.status === 403 || response.status === 401 ? "Image access denied." : response.status === 404 ? "Image unavailable." : "Could not load image.");
        }
        const bytes = await response.blob();
        if (request.signal.aborted) return;
        objectUrl = URL.createObjectURL(bytes);
        setUrl(objectUrl);
      } catch (cause) {
        if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load image.");
      }
    })();
    return () => { request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [project, image.id, attempt, visible]);
  return <div className="message-image" ref={container} data-image={key} tabIndex={-1}>
    {url && !error ? <button className="message-image-open" type="button" aria-label={`Open image ${image.name}`} onClick={open}>
      <img src={url} alt={image.name} onError={() => setError("Could not display image.")} />
      <span>{image.name}</span>
    </button> : <div className="message-image-placeholder">
      <span className="message-image-name">{image.name}</span>
      {error ? <><span role="alert">{error}</span><button type="button" className="link" onClick={() => setAttempt((value) => value + 1)}>Retry image {image.name}</button></>
        : <span><StatusMark label="Loading image…" /></span>}
    </div>}
  </div>;
}

function ImageViewer({ images, start, onClose }: { images: GalleryImage[]; start: number; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [index, setIndex] = useState(start);
  const [zoomed, setZoomed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const image = images[index]!;
  const opener = images[start]!.key;
  const go = (next: number) => {
    if (next < 0 || next >= images.length || next === index) return;
    setIndex(next); setZoomed(false); setAttempt(0); setState("loading");
  };
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => {
      // A replaced opener returns focus to the same image where it now appears.
      const place = [...document.querySelectorAll<HTMLElement>("[data-image]")].find((node) => node.dataset.image === opener);
      const target = previous?.isConnected ? previous : place?.querySelector("button") ?? place;
      target?.focus({ preventScroll: true });
    };
  }, [opener]);
  // Arrow keys move between images wherever focus rests, including after the last image's Next button goes away.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      go(index + (event.key === "ArrowRight" ? 1 : -1));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
  // A horizontal swipe changes image unless zoomed, when it pans instead.
  const down = (event: PointerEvent) => { swipe.current = zoomed ? null : { x: event.clientX, y: event.clientY }; };
  const up = (event: PointerEvent) => {
    const from = swipe.current;
    swipe.current = null;
    if (!from) return;
    const dx = event.clientX - from.x;
    if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(event.clientY - from.y)) go(index + (dx < 0 ? 1 : -1));
  };
  return createPortal(<dialog ref={dialog} role="dialog" className="image-viewer" aria-label={`Image ${image.name}`} onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="image-viewer-header">
      <span>{image.name}</span>
      {images.length > 1 ? <span className="image-viewer-count">{index + 1} / {images.length}</span> : null}
      {state !== "failed" ? <button type="button" className="btn" aria-pressed={zoomed} onClick={() => setZoomed((value) => !value)}>{zoomed ? "Fit image" : "Zoom image"}</button> : null}
      <button type="button" className="btn" onClick={onClose}>Close image</button>
    </div>
    <div className="image-viewer-stage" onPointerDown={down} onPointerUp={up} onPointerCancel={() => { swipe.current = null; }}>
    <div className="image-viewer-canvas" data-zoomed={zoomed || undefined}>
      {state === "failed" ? <div className="image-viewer-failed" role="alert">
        <Icon name="image-off" size={40} />
        <b>Image unavailable</b>
        <button type="button" className="btn" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}>Retry</button>
      </div> : <>
        {state === "loading" ? <span className="image-viewer-loading"><StatusMark label="Loading image…" /></span> : null}
        <img key={`${image.key}:${attempt}`} src={attempt ? `${image.url}${image.url.includes("?") ? "&" : "?"}retry=${attempt}` : image.url} alt={image.name}
          hidden={state !== "ready"} draggable={false} onLoad={() => setState("ready")} onError={() => setState("failed")} />
      </>}
    </div>
      {index > 0 ? <button type="button" className="image-viewer-nav" data-side="previous" aria-label="Previous image" onClick={() => go(index - 1)}><Icon name="back" /></button> : null}
      {index < images.length - 1 ? <button type="button" className="image-viewer-nav" data-side="next" aria-label="Next image" onClick={() => go(index + 1)}><Icon name="chevron" /></button> : null}
    </div>
  </dialog>, document.body);
}

export function MessageImages({ project, images }: { project: string; images?: MessageImage[] | null }) {
  const open = useGallery();
  if (!images?.length) return null;
  const gallery = images.map((image) => ({ key: `${project}/${image.id}`, name: image.name, url: `/api/images/${project}/${encodeURIComponent(image.id)}` }));
  return <div className="message-images" aria-label="Message images">{images.map((image, index) =>
    <StoredImage key={image.id} project={project} image={image} open={() => open(gallery, index)} />)}</div>;
}

export function PendingImages({ images }: { images?: ImagePreview[] }) {
  return images?.length ? <div className="message-images" aria-label="Sending images">{images.map((image, index) =>
    <div className="message-image" key={index}><img className="message-image-preview" src={image.url} alt={image.name} /></div>)}</div> : null;
}
