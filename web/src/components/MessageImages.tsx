import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MessageImage } from "../data/api";

export interface ImagePreview { name: string; url: string }

/** One private read owns its object URL. Neither retry nor opening the viewer resends a message. */
function StoredImage({ project, image }: { project: string; image: MessageImage }) {
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
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
        const blob = await response.blob();
        if (request.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch (cause) {
        if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load image.");
      }
    })();
    return () => { request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [project, image.id, attempt, visible]);
  return <div className="message-image" ref={container}>
    {url && !error ? <button className="message-image-open" type="button" aria-label={`Open image ${image.name}`} onClick={() => setOpen(true)}>
      <img src={url} alt={image.name} onError={() => setError("Could not display image.")} />
      <span>{image.name}</span>
    </button> : <div className="message-image-placeholder">
      <span className="message-image-name">{image.name}</span>
      {error ? <><span role="alert">{error}</span><button type="button" className="link" onClick={() => setAttempt((value) => value + 1)}>Retry image {image.name}</button></>
        : <span role="status">Loading image…</span>}
    </div>}
    {open && url ? <ImageViewer image={{ name: image.name, url }} onClose={() => setOpen(false)} /> : null}
  </div>;
}

function ImageViewer({ image, onClose }: { image: ImagePreview; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { previous?.focus({ preventScroll: true }); };
  }, []);
  return createPortal(<dialog ref={dialog} role="dialog" className="image-viewer" aria-label={`Image ${image.name}`} onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="image-viewer-header">
      <span>{image.name}</span>
      <button type="button" className="btn btn-ghost" aria-pressed={zoomed} onClick={() => setZoomed((value) => !value)}>{zoomed ? "Fit image" : "Zoom image"}</button>
      <button type="button" className="btn btn-ghost" onClick={onClose}>Close image</button>
    </div>
    <div className="image-viewer-canvas" data-zoomed={zoomed || undefined}>
      <img src={image.url} alt={image.name} />
    </div>
  </dialog>, document.body);
}

export function MessageImages({ project, images }: { project: string; images?: MessageImage[] | null }) {
  if (!images?.length) return null;
  return <div className="message-images" aria-label="Message images">{images.map((image) => <StoredImage key={image.id} project={project} image={image} />)}</div>;
}

export function PendingImages({ images }: { images?: ImagePreview[] }) {
  return images?.length ? <div className="message-images" aria-label="Sending images">{images.map((image, index) =>
    <div className="message-image" key={index}><img className="message-image-preview" src={image.url} alt={image.name} /></div>)}</div> : null;
}
