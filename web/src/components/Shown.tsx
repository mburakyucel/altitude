import { useState } from "react";
import type { MouseEvent } from "react";
import { Link, useLocation } from "react-router";
import type { Shown, ShownImage } from "../data/api";
import { useGallery } from "./MessageImages";
import "./shown.css";

const PATHS = {
  back: <path d="M15 6l-6 6 6 6" />,
  chevron: <path d="M9 6l6 6-6 6" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  document: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></>,
  "document-off": <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9.5 12.5l5 5M14.5 12.5l-5 5" /></>,
  image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="M20.5 15.5l-5-5-8 9" /></>,
  "image-off": <><path d="M20.5 13V6.5a2 2 0 0 0-2-2h-13a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2H12" /><circle cx="9" cy="10" r="1.6" /><path d="M3.5 17l5-5 3.5 3.5" /><path d="M15.5 15.5l5 5M20.5 15.5l-5 5" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /><path d="M12 14.5v2.5" /></>,
  retry: <><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" /><path d="M4.5 4v4h4" /></>,
  expand: <path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5" />,
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS; size?: number }) {
  return <svg className="shown-icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>;
}

/** A saved image's address, with a fresh one for each retry. */
function useSaved(url: string) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  return {
    state,
    src: attempt ? `${url}?retry=${attempt}` : url,
    key: attempt,
    onLoad: () => setState("ready"),
    onError: () => setState("failed"),
    retry: () => { setState("loading"); setAttempt((value) => value + 1); },
  };
}

/** A copied image in the reader: a grey block at its saved size while it loads, a button that opens the gallery, or a
 * box with Retry when it fails. Inside a link it is a plain image. */
export function SavedImage({ image, alt, className, open }: { image: ShownImage; alt: string; className: string; open?: () => void }) {
  const saved = useSaved(image.url);
  if (saved.state === "failed") {
    return <span className={`${className} shown-image-failed`} role="group" aria-label={`${image.name} unavailable`}>
      <Icon name="image-off" size={28} /><b>Image unavailable</b>
      <button type="button" className="btn" onClick={saved.retry}>Retry</button>
    </span>;
  }
  const picture = <img key={saved.key} src={saved.src} alt={alt} width={image.width} height={image.height}
    data-loading={saved.state === "loading" || undefined} onLoad={saved.onLoad} onError={saved.onError} />;
  return open ? <button type="button" className={className} aria-label={`Open image ${image.name}`} onClick={open}>
    {picture}<span className="shown-expand"><Icon name="expand" size={16} /></span>
  </button> : <span className={className}>{picture}</span>;
}

/** One thumbnail, cropped to its own aspect up to 4:3; a failed one becomes its own Retry. */
function Thumbnail({ image, more, open }: { image: ShownImage; more: number; open: () => void }) {
  const saved = useSaved(image.url);
  const aspect = Math.min(4 / 3, Math.max(3 / 4, image.width / image.height));
  if (saved.state === "failed") {
    return <button type="button" className="shown-thumb" data-failed style={{ aspectRatio: aspect }} aria-label={`Retry image ${image.name}`} onClick={saved.retry}>
      <Icon name="image-off" size={24} /><span className="shown-retry"><Icon name="retry" size={14} /></span>
    </button>;
  }
  return <button type="button" className="shown-thumb" data-loading={saved.state === "loading" || undefined} style={{ aspectRatio: aspect }}
    aria-label={more ? `Open ${more} more images` : `Open image ${image.name}`} onClick={open}>
    <img key={saved.key} src={saved.src} alt="" onLoad={saved.onLoad} onError={saved.onError} />
    {more ? <span className="shown-more">+{more}</span> : null}
  </button>;
}

/** Where the reader opens from: the history state a card carries (Needs you names itself), and what the opener keeps. */
interface Opening { state?: unknown; onOpen?: (event: MouseEvent<HTMLAnchorElement>) => void }

/** A document's page card, one control. Its cover strip is grey while it loads and gone when it fails; a card without
 * a cover carries the Earlier chip after its name. */
function PageCard({ shown, document, state, onOpen }: { shown: Shown & { url: string }; document: NonNullable<Shown["document"]> } & Opening) {
  const location = useLocation();
  const image = shown.images.find((item) => item.embedded) ?? shown.images[0];
  const cover = useSaved(image?.url ?? "");
  const covered = Boolean(image) && cover.state !== "failed";
  const earlier = Boolean(shown.newer_url);
  const chip = earlier ? <span className="shown-chip">Earlier</span> : null;
  const excerpt = [document.heading, document.excerpt].filter(Boolean).join(" — ");
  return <Link className="shown-card" to={`${shown.url}${location.search}`} aria-label={`Open ${document.name}`}
    aria-description={earlier ? "Earlier version" : undefined} aria-current={location.pathname === shown.url ? "page" : undefined}
    data-earlier={earlier || undefined} state={state ?? location.state} onClick={onOpen}>
    {covered ? <span className="shown-cover" data-loading={cover.state === "loading" || undefined}>
      <img src={cover.src} alt="" onLoad={cover.onLoad} onError={cover.onError} />{chip}
    </span> : null}
    <span className="shown-card-body">
      <span className="shown-card-title"><Icon name="document" /><b>{document.name}</b>{covered ? null : chip}<Icon name="chevron" /></span>
      {excerpt ? <span className="shown-card-excerpt">{excerpt}</span> : null}
    </span>
  </Link>;
}

/**
 * Files an owner showed, as what they are (SPEC.md §3.11): a page card for a document, thumbnails for images alone.
 * A card opens the reader beside or over the conversation; a thumbnail opens the gallery. A set whose saved files are
 * gone stays as a card that does not open.
 */
export function ShownFiles({ shown, state, onOpen }: { shown: Shown } & Opening) {
  const gallery = useGallery();
  const { document, images, url } = shown;
  if (!shown.available || !url) {
    const name = document?.name ?? (images.length === 1 ? images[0]!.name : `${images.length} images`);
    return <div className="shown-card" data-gone>
      <span className="shown-card-body">
        <span className="shown-card-title"><Icon name="document-off" /><b>{name}</b></span>
        <span className="shown-card-excerpt">Preview unavailable</span>
      </span>
    </div>;
  }
  if (document) return <PageCard shown={{ ...shown, url }} document={document} state={state} onOpen={onOpen} />;
  const open = (index: number) => gallery(images.map((image) => ({ key: image.url, name: image.name, url: image.url })), index);
  return <div className="shown-thumbs" aria-label="Shown images">
    {images.slice(0, 4).map((image, index) => <Thumbnail key={image.url} image={image} more={index === 3 ? images.length - 3 : 0} open={() => open(index)} />)}
  </div>;
}
