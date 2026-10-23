import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent, RefObject } from "react";
import { createPortal } from "react-dom";
import { useViewport } from "../shell/breakpoints";
import { Icon } from "./Shown";
import { StatusMark } from "./StatusMark";

/** One picture of a gallery, read by its private address. */
export interface GalleryImage {
  key: string;
  name: string;
  url: string;
  /** Where it came from, in the conversation's own words: "You · 14:05", "README.md", "Validation run 4". */
  context?: string;
  width?: number;
  height?: number;
  /** A picked picture not sent yet: the operator's own file, shown without actions. */
  local?: boolean;
}

type Read = { state: "loading" } | { state: "ready"; blob: Blob; url: string } | { state: "failed"; reason: string };
interface Held { read: Read; stop: AbortController }
interface Size { w: number; h: number }
/** A picture's scale against its own pixels and its top-left corner on the screen. */
interface View { s: number; x: number; y: number }
interface Point { x: number; y: number }
interface Gesture {
  mode: "pending" | "pan" | "swipe" | "drop" | "pinch" | "none";
  id: number; type: string; start: Point; view: View; samples: { x: number; y: number; t: number }[];
  pinch?: { distance: number; middle: Point; view: View };
}

const GAP = 16;
const LOADING: Read = { state: "loading" };
const reduced = () => Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
const between = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** A private picture's bytes, or the reason it cannot be read: "Access denied", "Image unavailable", "Could not load". */
export async function readImage(url: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(url, { signal });
  } catch (cause) {
    if (signal?.aborted) throw cause;
    throw new Error("Could not load");
  }
  if (!response.ok) throw new Error([401, 403].includes(response.status) ? "Access denied" : response.status === 404 ? "Image unavailable" : "Could not load");
  return response.blob();
}

async function asPng(blob: Blob): Promise<Blob> {
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
  bitmap.close();
  return new Promise((resolve, reject) => canvas.toBlob((png) => png ? resolve(png) : reject(new Error("No PNG")), "image/png"));
}

/**
 * The viewer's own reads (SPEC.md §3.9.3, "Reading ahead"): the current picture, then its
 * two neighbours once it is in, held as bytes in memory. Moving on releases what is more than one step away, a hidden
 * page releases the neighbours, and closing releases everything. Nothing is written anywhere.
 */
function useReads(images: GalleryImage[], index: number) {
  const held = useRef(new Map<string, Held>());
  const [version, setVersion] = useState(0);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");
  const release = (key: string) => {
    const entry = held.current.get(key);
    if (!entry) return;
    entry.stop.abort();
    if (entry.read.state === "ready") URL.revokeObjectURL(entry.read.url);
    held.current.delete(key);
  };
  useEffect(() => {
    const onChange = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => {
      document.removeEventListener("visibilitychange", onChange);
      [...held.current.keys()].forEach(release);
    };
  }, []);
  const current = images[index]!.key;
  const ready = held.current.get(current)?.read.state === "ready";
  useEffect(() => {
    const wanted = new Set([current]);
    if (ready && !hidden) [images[index - 1], images[index + 1]].forEach((image) => { if (image) wanted.add(image.key); });
    [...held.current.keys()].filter((key) => !wanted.has(key)).forEach(release);
    for (const image of images) {
      if (!wanted.has(image.key) || held.current.has(image.key)) continue;
      const entry: Held = { read: LOADING, stop: new AbortController() };
      held.current.set(image.key, entry);
      readImage(image.url, entry.stop.signal).then((blob) => {
        if (entry.stop.signal.aborted) return;
        entry.read = { state: "ready", blob, url: URL.createObjectURL(blob) };
        setVersion((value) => value + 1);
      }, (cause: unknown) => {
        if (entry.stop.signal.aborted) return;
        entry.read = { state: "failed", reason: cause instanceof Error ? cause.message : "Could not load" };
        setVersion((value) => value + 1);
      });
    }
  }, [current, ready, hidden, version]);
  return {
    read: (key: string): Read => held.current.get(key)?.read ?? LOADING,
    retry: (key: string) => { release(key); setVersion((value) => value + 1); },
  };
}

/** The opener's picture where it sits on the page, if it is on screen. */
function openerPicture(key: string): HTMLImageElement | null {
  const place = [...document.querySelectorAll<HTMLElement>("[data-image]")].find((node) => node.dataset.image === key);
  const picture = place?.querySelector("img");
  if (!picture) return null;
  const box = picture.getBoundingClientRect();
  return box.width && box.bottom > 0 && box.top < window.innerHeight && box.right > 0 && box.left < window.innerWidth ? picture : null;
}

/** A view that lays a picture of `size` whole inside the opener's picture. */
function viewInside(picture: HTMLImageElement, size: Size): View {
  const box = picture.getBoundingClientRect();
  const s = Math.min(box.width / size.w, box.height / size.h);
  return { s, x: box.left + (box.width - size.w * s) / 2, y: box.top + (box.height - size.h * s) / 2 };
}

/**
 * The full-screen viewer (SPEC.md §3.9.3): one picture fitted between its bars, pinch, double-tap, wheel and keys to
 * zoom, a finger-following strip between pictures, swipe down or Escape to close, and the picture's actions.
 */
export function ImageViewer({ images, start, onClose }: { images: GalleryImage[]; start: number; onClose: () => void }) {
  const { phone } = useViewport();
  const dialog = useRef<HTMLDialogElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLElement>(null);
  const band = useRef<HTMLDivElement>(null);
  const closer = useRef<HTMLButtonElement>(null);
  const [index, setIndex] = useState(start);
  const reads = useReads(images, index);
  const image = images[index]!;
  const read = reads.read(image.key);
  const [sizes, setSizes] = useState<Record<string, Size>>(() => Object.fromEntries(images.flatMap((item) =>
    item.width && item.height ? [[item.key, { w: item.width, h: item.height }]] : [])));
  // The screen and its bars; zero-height bars until they are measured.
  const frame = useRef({ width: window.innerWidth, height: window.innerHeight, top: 0, bottom: 0, side: phone ? 0 : 88 });
  const [, relayout] = useState(0);
  // An explicit view while zoomed or moving; null is the fitted picture, whatever the screen.
  const [view, setViewState] = useState<View | null>(null);
  const viewNow = useRef<View | null>(null);
  const setView = (next: View | null) => { viewNow.current = next; setViewState(next); };
  // The strip's offset while swiping, and how much of the dark and the bars shows while opening, closing or dropping.
  const [slide, setSlide] = useState(0);
  const [dim, setDim] = useState(1);
  const [notice, setNotice] = useState<{ text: string; done: boolean } | null>(null);
  const noticeTimer = useRef(0);
  const [share] = useState(() => {
    try { return typeof navigator.canShare === "function" && navigator.canShare({ files: [new File([""], "image.png", { type: "image/png" })] }); } catch { return false; }
  });
  const copyable = typeof ClipboardItem !== "undefined" && typeof navigator.clipboard?.write === "function";
  // How long the current eased movement takes, as the CSS transition's duration; 0 while a finger drives the picture.
  const [moving, setMoving] = useState(0);
  const settle = useRef<{ timer: number; done?: () => void } | null>(null);
  const sliding = useRef(false);
  const closing = useRef(false);
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture | null>(null);
  const lastTap = useRef<{ x: number; y: number; t: number } | null>(null);
  // Until its own read arrives, the opened picture shows the thumbnail's, for display only; the thumbnail may release it.
  const [placeholder, setPlaceholder] = useState(() => openerPicture(images[start]!.key)?.currentSrc ?? "");
  const opener = images[start]!.key;

  const fitOf = (key: string): View | null => {
    const size = sizes[key];
    if (!size) return null;
    const { width, height, top, bottom, side } = frame.current;
    const room = { w: width - 2 * side, h: height - top - bottom };
    const s = Math.min(1, room.w / size.w, room.h / size.h);
    return { s, x: side + (room.w - size.w * s) / 2, y: top + (room.h - size.h * s) / 2 };
  };
  const size = sizes[image.key];
  const fit = fitOf(image.key);
  const most = fit ? Math.max(4 * fit.s, 2) : 1;
  const step = fit ? between(1, 2 * fit.s, 4 * fit.s) : 1;
  const shown = view ?? fit;
  const zoomed = Boolean(view && fit && view.s > fit.s * 1.001);
  const ready = read.state === "ready";

  // A side of a zoomed picture never leaves its edge of the screen between the bars; an axis that fits stays centred.
  const clamp = (next: View): View => {
    if (!size) return next;
    const { width, height, top, bottom } = frame.current;
    const axis = (at: number, length: number, from: number, room: number) =>
      length <= room ? from + (room - length) / 2 : between(at, from + room - length, from);
    return { s: next.s, x: axis(next.x, size.w * next.s, 0, width), y: axis(next.y, size.h * next.s, top, height - top - bottom) };
  };
  const around = (from: View, point: Point, s: number): View =>
    clamp({ s, x: point.x - (point.x - from.x) * s / from.s, y: point.y - (point.y - from.y) * s / from.s });
  const middle = (): Point => ({ x: frame.current.width / 2, y: (frame.current.top + frame.current.height - frame.current.bottom) / 2 });

  /** Ends an eased movement where it is headed, running what waits for it. */
  const finish = () => {
    const pending = settle.current;
    if (!pending) return;
    settle.current = null;
    window.clearTimeout(pending.timer);
    setMoving(0);
    pending.done?.();
  };
  /** One eased movement: the next state applies at once and the browser eases to it; reduced motion lands at once. */
  const ease = (ms: number, apply: () => void, done?: () => void) => {
    finish();
    const duration = reduced() ? 0 : ms;
    setMoving(duration);
    apply();
    if (duration) settle.current = { timer: window.setTimeout(finish, duration), done };
    else { setMoving(0); done?.(); }
  };
  const toFit = (ms = 200) => ease(ms, () => setView(null));
  const zoomTo = (s: number, point: Point, ms = 200) => {
    if (!fit || !ready) return;
    const from = viewNow.current ?? fit;
    const target = between(s, fit.s, most);
    if (target <= fit.s * 1.001) toFit(ms);
    else ease(ms, () => setView(around(from, point, target)));
  };
  const toggle = (point: Point) => { if (zoomed) toFit(); else zoomTo(step, point); };
  const say = (text: string, done = false) => {
    window.clearTimeout(noticeTimer.current);
    setNotice({ text, done });
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2000);
  };

  const close = () => {
    if (closing.current) return;
    closing.current = true;
    sliding.current = false;
    setSlide(0);
    const picture = openerPicture(image.key);
    const to = picture && size ? viewInside(picture, size) : null;
    ease(180, () => { if (to) setView(to); setDim(0); }, onClose);
  };
  const go = (next: number) => {
    if (next < 0 || next >= images.length || next === index || sliding.current || closing.current) return;
    const toward = next > index ? 1 : -1;
    sliding.current = true;
    setView(null);
    ease(220, () => setSlide(-toward * (frame.current.width + GAP)), () => {
      sliding.current = false;
      setIndex(next); setSlide(0); setView(null);
    });
  };

  // The page behind is inert while the viewer shows. It opens before the bars below are measured: a closed dialog
  // lays nothing out, so its bars would measure nothing and the picture would fit under them.
  const previous = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    previous.current = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
  }, []);
  // Focus starts on Close and goes back to where it was.
  useEffect(() => {
    const before = previous.current;
    closer.current?.focus();
    // Opening grows the thumbnail's own picture into place, so nothing flashes.
    const picture = openerPicture(opener);
    const first = sizes[opener];
    const to = fitOf(opener);
    let frames = 0;
    if (!reduced()) {
      if (picture && first && to) setView(viewInside(picture, first));
      setDim(0);
      // Two frames let the browser lay out the starting place before it eases to the fitted picture.
      frames = requestAnimationFrame(() => { frames = requestAnimationFrame(() => ease(200, () => { setView(null); setDim(1); })); });
    }
    return () => {
      cancelAnimationFrame(frames);
      if (settle.current) window.clearTimeout(settle.current.timer);
      window.clearTimeout(noticeTimer.current);
      // A replaced opener returns focus to the same image where it now appears.
      const place = [...document.querySelectorAll<HTMLElement>("[data-image]")].find((node) => node.dataset.image === opener);
      const target = before?.isConnected ? before : place?.querySelector("button") ?? place;
      target?.focus({ preventScroll: true });
    };
  }, [opener]);
  // The bars are measured once laid out; resizing or turning the phone returns to fit.
  useLayoutEffect(() => {
    const measure = () => {
      frame.current = {
        width: stage.current?.clientWidth || window.innerWidth, height: stage.current?.clientHeight || window.innerHeight,
        top: bar.current?.offsetHeight ?? 0, bottom: band.current?.offsetHeight ?? 0, side: phone ? 0 : 88,
      };
      relayout((value) => value + 1);
    };
    measure();
    const onResize = () => { measure(); if (!closing.current && !settle.current) { setView(null); setSlide(0); } };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [phone]);

  // Wheel and keys read the current render through one ref, so their listeners are registered once.
  const latest = useRef({ wheel: (() => {}) as (event: WheelEvent) => void, key: (() => {}) as (event: KeyboardEvent) => void });
  // Nothing in the viewer scrolls, so no wheel reaches the page: a trackpad pinch over a bar would zoom it.
  latest.current.wheel = (event) => {
    event.preventDefault();
    if (!fit || !ready || closing.current || !stage.current?.contains(event.target as Node)) return;
    const from = viewNow.current ?? fit;
    const point = { x: event.clientX, y: event.clientY };
    const apply = (s: number) => {
      finish();
      const target = between(s, fit.s, most);
      setView(target <= fit.s * 1.001 ? null : around(from, point, target));
    };
    // A trackpad pinch arrives as a wheel with ctrlKey; a mouse notch is a line step or a large vertical step.
    if (event.ctrlKey) apply(from.s * Math.exp(-event.deltaY * 0.01));
    else if (event.deltaMode !== 0 || (event.deltaX === 0 && Math.abs(event.deltaY) >= 50)) apply(from.s * (event.deltaY < 0 ? 1.25 : 0.8));
    else if (zoomed) { finish(); setView(clamp({ ...from, x: from.x - event.deltaX, y: from.y - event.deltaY })); }
  };
  latest.current.key = (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || closing.current) return;
    const from = viewNow.current ?? fit;
    const pan = (dx: number, dy: number) => {
      if (!from) return;
      ease(160, () => setView(clamp({ ...from, x: from.x + dx * frame.current.width * 0.15, y: from.y + dy * frame.current.height * 0.15 })));
    };
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const toward = event.key === "ArrowRight" ? 1 : -1;
      if (zoomed) pan(-toward, 0); else go(index + toward);
    } else if ((event.key === "ArrowUp" || event.key === "ArrowDown") && zoomed) pan(0, event.key === "ArrowDown" ? -1 : 1);
    else if ((event.key === "+" || event.key === "=") && from) zoomTo(from.s * 1.5, middle(), 160);
    else if (event.key === "-" && from) zoomTo(from.s / 1.5, middle(), 160);
    else if (event.key === "0") toFit(160);
    else return;
    event.preventDefault();
  };
  useEffect(() => {
    const onWheel = (event: WheelEvent) => latest.current.wheel(event);
    const onKey = (event: KeyboardEvent) => latest.current.key(event);
    const node = dialog.current;
    node?.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("keydown", onKey);
    return () => { node?.removeEventListener("wheel", onWheel); document.removeEventListener("keydown", onKey); };
  }, []);

  // One gesture owns each touch, decided by its first 10 px (PROPOSAL.md, Gestures).
  const down = (event: PointerEvent<HTMLDivElement>) => {
    // Retry is a button on the stage, not the dark space around the picture.
    if (event.button > 0 || sliding.current || closing.current || (event.target as Element).closest("button")) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()] as [Point, Point];
      finish();
      setSlide(0);
      setDim(1);
      // A pinch that interrupts a swipe or a swipe down starts from the fitted picture.
      const from = gesture.current?.mode === "pan" || gesture.current?.mode === "pending" ? viewNow.current ?? fit : fit;
      const at = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      if (from !== viewNow.current) setView(null);
      gesture.current = fit && ready && from ? { mode: "pinch", id: event.pointerId, type: event.pointerType, start: at, view: from,
        samples: [{ ...at, t: event.timeStamp }], pinch: { distance: Math.hypot(a.x - b.x, a.y - b.y), middle: at, view: from } } : null;
      return;
    }
    if (pointers.current.size > 2) return;
    finish();
    const from = viewNow.current ?? fit ?? { s: 1, x: 0, y: 0 };
    gesture.current = { mode: "pending", id: event.pointerId, type: event.pointerType, start: { x: event.clientX, y: event.clientY }, view: from,
      samples: [{ x: event.clientX, y: event.clientY, t: event.timeStamp }] };
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const point = pointers.current.get(event.pointerId);
    const g = gesture.current;
    if (!point || !g) return;
    point.x = event.clientX; point.y = event.clientY;
    if (g.mode === "pinch" && g.pinch && fit) {
      const [a, b] = [...pointers.current.values()] as [Point, Point];
      if (!b) return;
      const at = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const { distance, middle: first, view: from } = g.pinch;
      // Past the limits a pinch gives a little and settles back on release.
      let s = from.s * Math.hypot(a.x - b.x, a.y - b.y) / distance;
      if (s < fit.s) s = fit.s - (fit.s - s) / 3;
      if (s > most) s = most + (s - most) / 3;
      setView({ s, x: at.x - (first.x - from.x) * s / from.s, y: at.y - (first.y - from.y) * s / from.s });
      g.samples = [{ ...at, t: event.timeStamp }];
      return;
    }
    if (event.pointerId !== g.id) return;
    const dx = event.clientX - g.start.x;
    const dy = event.clientY - g.start.y;
    g.samples = [...g.samples.slice(-4), { x: event.clientX, y: event.clientY, t: event.timeStamp }];
    if (g.mode === "pending") {
      if (Math.hypot(dx, dy) < 10) return;
      g.mode = zoomed ? "pan" : Math.abs(dx) > Math.abs(dy) ? images.length > 1 ? "swipe" : "none" : dy > 0 ? "drop" : "none";
    }
    if (g.mode === "pan") setView(clamp({ ...g.view, x: g.view.x + dx, y: g.view.y + dy }));
    else if (g.mode === "swipe") setSlide((dx > 0 ? index === 0 : index === images.length - 1) ? dx / 3 : dx);
    else if (g.mode === "drop") {
      // The picture follows the finger, shrinking to 85% at most, and the dark and the bars fade with the distance.
      const k = 1 - 0.15 * between(dy / 300, 0, 1);
      if (fit && size) setView({ s: fit.s * k, x: fit.x + (1 - k) * size.w * fit.s / 2 + dx, y: fit.y + (1 - k) * size.h * fit.s / 2 + dy });
      setDim(1 - between(dy / 360, 0, 0.9));
    }
  };
  const velocity = (g: Gesture) => {
    const last = g.samples[g.samples.length - 1]!;
    const first = g.samples.find((sample) => last.t - sample.t <= 100) ?? last;
    const dt = last.t - first.t;
    return dt > 0 ? { x: (last.x - first.x) / dt, y: (last.y - first.y) / dt } : { x: 0, y: 0 };
  };
  // A flick glides on and slows to a stop, about as far as the finger would have gone in another 300 ms.
  const glide = (speed: Point) => {
    const from = viewNow.current;
    if (!from || Math.hypot(speed.x, speed.y) < 0.1) return;
    ease(450, () => setView(clamp({ ...from, x: from.x + speed.x * 300, y: from.y + speed.y * 300 })));
  };
  const tap = (event: PointerEvent<HTMLDivElement>, g: Gesture) => {
    const point = { x: event.clientX, y: event.clientY };
    const onPicture = Boolean(shown && size && point.x >= shown.x && point.x <= shown.x + size.w * shown.s
      && point.y >= shown.y && point.y <= shown.y + size.h * shown.s);
    // A click on the dark space closes, as the backdrop does, unless it ended a drag; a tap on a phone does nothing.
    if (g.type === "mouse" && !onPicture) {
      if (Math.hypot(point.x - g.start.x, point.y - g.start.y) <= 6) close();
      return;
    }
    const before = lastTap.current;
    if (before && event.timeStamp - before.t < 300 && Math.hypot(point.x - before.x, point.y - before.y) < 24) {
      lastTap.current = null;
      toggle(point);
    } else lastTap.current = { ...point, t: event.timeStamp };
  };
  const up = (event: PointerEvent<HTMLDivElement>, cancelled = false) => {
    if (!pointers.current.delete(event.pointerId)) return;
    const g = gesture.current;
    if (!g) return;
    if (g.mode === "pinch") {
      // The first finger up ends the pinch; the other is ignored until it lifts.
      if (g.pinch && fit && viewNow.current) {
        const from = viewNow.current;
        const s = between(from.s, fit.s, most);
        const point = g.samples[0] ?? g.pinch.middle;
        if (s <= fit.s * 1.01) toFit();
        else ease(200, () => setView(around(from, point, s)));
      }
      gesture.current = pointers.current.size ? { ...g, mode: "none", pinch: undefined } : null;
      return;
    }
    if (event.pointerId !== g.id) return;
    gesture.current = null;
    const speed = velocity(g);
    const dx = event.clientX - g.start.x;
    const dy = event.clientY - g.start.y;
    if (g.mode === "pending" && !cancelled) tap(event, g);
    else if (g.mode === "pan") { if (!cancelled) glide(speed); }
    else if (g.mode === "swipe") {
      const toward = dx < 0 ? 1 : -1;
      const far = Math.abs(dx) > frame.current.width / 3 || (Math.abs(speed.x) > 0.5 && Math.sign(speed.x) === Math.sign(dx));
      if (!cancelled && far && images[index + toward]) {
        sliding.current = true;
        ease(250, () => setSlide(-toward * (frame.current.width + GAP)), () => {
          sliding.current = false;
          setIndex(index + toward); setSlide(0); setView(null);
        });
      } else ease(200, () => setSlide(0));
    } else if (g.mode === "drop") {
      if (!cancelled && (dy > 120 || speed.y > 0.5)) close();
      else ease(200, () => { setView(null); setDim(1); });
    }
  };

  const disabled = !ready;
  const actions = image.local || read.state === "failed" ? null : phone
    ? share
      ? <button type="button" className="image-viewer-action" aria-label="Share image" title="Share image" disabled={disabled} onClick={() => {
          if (read.state !== "ready") return;
          // The file is built at the press from bytes in hand, so the press keeps its user activation.
          const file = new File([read.blob], image.name, { type: read.blob.type || "image/png" });
          navigator.share({ files: [file] }).catch((cause: unknown) => {
            if (!(cause instanceof DOMException && cause.name === "AbortError")) say("Could not share the image");
          });
        }}><Icon name="share" size={22} /></button>
      : <DownloadButton read={read} name={image.name} host={dialog} />
    : <>
      <button type="button" className="image-viewer-action" aria-pressed={zoomed} aria-label={zoomed ? "Fit image" : "Zoom image"} title={zoomed ? "Fit image" : "Zoom image"}
        disabled={disabled} onClick={() => toggle(middle())}><Icon name={zoomed ? "zoom-out" : "zoom-in"} size={20} /></button>
      {copyable && !/\.gif$/i.test(image.name) && !/\.gif$/i.test(image.url) && (read.state !== "ready" || read.blob.type !== "image/gif")
        ? <button type="button" className="image-viewer-action" aria-label="Copy image" title="Copy image" disabled={disabled} onClick={() => {
            if (read.state !== "ready") return;
            // A JPEG or WebP becomes PNG inside the clipboard item's promise, which Safari accepts.
            const png = read.blob.type === "image/png" ? read.blob : asPng(read.blob);
            navigator.clipboard.write([new ClipboardItem({ "image/png": png })]).then(() => say("Image copied", true), () => say("Could not copy the image"));
          }}><Icon name="copy" size={20} /></button> : null}
      <DownloadButton read={read} name={image.name} host={dialog} />
      <button type="button" className="image-viewer-action" aria-label="Open in new tab" title="Open in new tab" disabled={disabled}
        onClick={() => window.open(image.url, "_blank", "noopener")}><Icon name="open" size={20} /></button>
    </>;
  const closeButton = <button ref={closer} type="button" className="image-viewer-action" aria-label="Close image" title="Close image" onClick={close}>
    <Icon name="close" size={phone ? 24 : 20} />
  </button>;
  const span = frame.current.width + GAP;
  const slides = [index - 1, index, index + 1].filter((at) => images[at] && (at === index || slide !== 0));

  return createPortal(<dialog ref={dialog} role="dialog" className="image-viewer" data-phone={phone || undefined} aria-label={`Image ${image.name}`}
    style={{ "--dim": dim, "--moving": `${moving}ms` } as CSSProperties} onCancel={(event) => { event.preventDefault(); close(); }}
    onClose={() => { if (!closing.current) { closing.current = true; onClose(); } }}>
    <div className="image-viewer-dark" />
    <header className="image-viewer-bar" ref={bar}>
      {phone ? closeButton : null}
      <div className="image-viewer-title">
        <b>{image.name}</b>
        {image.context ? <span>{image.context}</span> : null}
      </div>
      <div className="image-viewer-actions">
        {actions}
        {phone ? null : <>{actions ? <span className="image-viewer-divider" /> : null}{closeButton}</>}
      </div>
    </header>
    {index > 0 ? <button type="button" className="image-viewer-nav" data-side="previous" aria-label="Previous image" onClick={() => go(index - 1)}><Icon name="back" /></button> : null}
    {index < images.length - 1 ? <button type="button" className="image-viewer-nav" data-side="next" aria-label="Next image" onClick={() => go(index + 1)}><Icon name="chevron" /></button> : null}
    <div className="image-viewer-band" ref={band}>
      {images.length > 1 ? <span className="image-viewer-count" aria-live="polite" data-quiet={notice ? true : undefined}>{index + 1} / {images.length}</span> : null}
      <span className="image-viewer-notice" role="status">{notice ? <>{notice.done ? <Icon name="check" size={16} /> : null}{notice.text}</> : null}</span>
    </div>
    <div className="image-viewer-stage" ref={stage} data-zoomed={zoomed || undefined} onPointerDown={down} onPointerMove={move}
      onPointerUp={(event) => up(event)} onPointerCancel={(event) => up(event, true)}>
      {slides.map((at) => {
        const item = images[at]!;
        const itemRead = at === index ? read : reads.read(item.key);
        const itemSize = sizes[item.key];
        const itemView = at === index ? shown : fitOf(item.key);
        const src = itemRead.state === "ready" ? itemRead.url : item.key === opener ? placeholder : "";
        const { top, bottom } = frame.current;
        return <div key={item.key} className="image-viewer-slide" style={{ transform: `translateX(${(at - index) * span + slide}px)` }} aria-hidden={at !== index || undefined}>
          {itemRead.state === "failed" ? <div className="image-viewer-failed" style={{ top, bottom }} role={at === index ? "alert" : undefined}>
            <Icon name="image-off" size={40} />
            <b>{itemRead.reason}</b>
            <button type="button" className="image-viewer-retry" onClick={() => reads.retry(item.key)}>Retry</button>
          </div> : <>
            {itemRead.state !== "ready" ? <span className="image-viewer-loading" style={{ top, bottom }}><StatusMark label="Loading image…" /></span> : null}
            {src ? <img src={src} alt={item.name} draggable={false} data-placeholder={itemRead.state !== "ready" || undefined}
              style={itemSize && itemView ? { width: itemSize.w * itemView.s, height: itemSize.h * itemView.s, transform: `translate(${itemView.x}px, ${itemView.y}px)` } : { visibility: "hidden" }}
              onError={() => { if (itemRead.state !== "ready") setPlaceholder(""); }}
              onLoad={(event) => {
                const { naturalWidth: w, naturalHeight: h } = event.currentTarget;
                if (w && h && (sizes[item.key]?.w !== w || sizes[item.key]?.h !== h)) setSizes((old) => ({ ...old, [item.key]: { w, h } }));
              }} /> : null}
          </>}
        </div>;
      })}
    </div>
  </dialog>, document.body);
}

/** Saves the held bytes under the picture's name, with the extension its type gives when the name has none (a capture's title). */
function DownloadButton({ read, name, host }: { read: Read; name: string; host: RefObject<HTMLDialogElement | null> }) {
  return <button type="button" className="image-viewer-action" aria-label="Download image" title="Download image" disabled={read.state !== "ready"} onClick={() => {
    if (read.state !== "ready") return;
    const link = document.createElement("a");
    link.href = read.url;
    const type = read.blob.type.split("/")[1]?.replace("jpeg", "jpg");
    link.download = /\.\w+$/.test(name) || !type ? name : `${name}.${type}`;
    host.current?.append(link);
    link.click();
    link.remove();
  }}><Icon name="download" size={20} /></button>;
}
