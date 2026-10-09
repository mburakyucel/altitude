import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** Give past either end as a fraction of the width: the familiar rubber band, never a switch. */
const RESISTANCE = 0.55;
/** A slow release completes the switch from this fraction of the width. */
const COMPLETE_AT = 0.5;
/** A release moving at least this fast (px/ms) in the drag's direction completes it at any distance. */
const FLING = 0.4;
/** Only movement inside this window (ms) before the release counts as its speed. */
const VELOCITY_WINDOW = 100;
/** The settle after release. */
export const SETTLE_MS = 220;

export interface Sample { x: number; at: number }

/** Whether a drag by `dx` from view `view` of `views` heads toward a neighbouring view: left reveals the next. */
function toward(dx: number, view: number, views: number): boolean {
  return dx < 0 ? view < views - 1 : dx > 0 && view > 0;
}

/**
 * Track offset for a finger displacement from the gesture's start: toward a neighbouring view the
 * outgoing view follows the finger up to one width; past either end the give shrinks with distance.
 */
export function dragOffset(dx: number, width: number, view: number, views: number): number {
  if (dx === 0 || width <= 0) return 0;
  const magnitude = Math.abs(dx);
  if (toward(dx, view, views)) return Math.sign(dx) * Math.min(magnitude, width);
  return Math.sign(dx) * (1 - 1 / ((magnitude * RESISTANCE) / width + 1)) * width * RESISTANCE;
}

/**
 * Speed over the last hundred milliseconds of a gesture whose samples run from the touch to the
 * release: a short flick is measured over its whole length, and a pause before lifting reads as rest.
 */
export function releaseVelocity(samples: readonly Sample[]): number {
  const last = samples[samples.length - 1];
  if (!last) return 0;
  let reference = samples[0]!;
  for (let index = samples.length - 2; index >= 0; index--) {
    if (last.at - samples[index]!.at >= VELOCITY_WINDOW) { reference = samples[index]!; break; }
  }
  return last.at === reference.at ? 0 : (last.x - reference.x) / (last.at - reference.at);
}

/**
 * Whether a released drag completes the switch: a fling in the drag's direction, or a slow release
 * past half the width. Flings back toward the start and drags past either end spring back.
 */
export function completes(dx: number, velocity: number, width: number, view: number, views: number): boolean {
  if (!toward(dx, view, views)) return false;
  if (Math.abs(velocity) >= FLING) return Math.sign(velocity) === Math.sign(dx);
  return Math.abs(dx) >= width * COMPLETE_AT;
}

interface Gesture extends Sample {
  y: number;
  width: number;
  horizontal: boolean;
  reduced: boolean;
  samples: Sample[];
}

/**
 * Task content owns deliberate horizontal swipes between `views` views in order, `view` showing; native
 * scrolling, selection and the terminal screen keep theirs. From the first horizontal movement the track
 * follows the finger, so the view and its neighbour are on screen until the release settles into a
 * switch or springs back. Under reduced motion nothing moves and a release past the same thresholds
 * switches at once.
 */
export function useTaskSwipe(enabled: boolean, view: number, views: number, switchView: (view: number) => void) {
  const track = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const navigate = useRef(switchView);
  navigate.current = switchView;
  // The resting position is the layout's own: a completed switch keeps its settled transform until the
  // route has changed, then drops it in the same paint, so the view never snaps.
  useLayoutEffect(() => {
    rest(track.current);
    setDragging(false);
  }, [view]);
  useEffect(() => {
    const node = track.current;
    if (!enabled || !node) return;
    let gesture: Gesture | null = null;
    let settling: (() => void) | null = null;
    const selecting = () => !window.getSelection()?.isCollapsed;
    const settle = (offset: number, then: () => void) => {
      const timer = setTimeout(() => done(), SETTLE_MS + 50);
      const done = (event?: TransitionEvent) => {
        if (event && (event.target !== node || event.propertyName !== "transform")) return;
        settling?.();
        then();
      };
      settling = () => { clearTimeout(timer); node.removeEventListener("transitionend", done); settling = null; };
      node.addEventListener("transitionend", done);
      node.style.transition = `transform ${SETTLE_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1)`;
      node.style.transform = `translateX(${offset}px)`;
    };
    const finish = (dx: number, velocity: number) => {
      const saved = gesture!;
      gesture = null;
      const switching = completes(dx, velocity, saved.width, view, views);
      const next = dx < 0 ? view + 1 : view - 1;
      if (saved.reduced) {
        if (switching) navigate.current(next);
      } else if (switching) {
        settle(dx < 0 ? -saved.width : saved.width, () => navigate.current(next));
      } else {
        settle(0, () => { rest(node); setDragging(false); });
      }
    };
    const start = (event: TouchEvent) => {
      // A second finger lands as a fresh touchstart: the drag it interrupts springs back first.
      cancel();
      const target = event.target instanceof Element ? event.target : null;
      const touch = event.touches[0];
      if (settling || event.touches.length !== 1 || !touch || selecting() ||
          !target?.closest('.convo-scroll, .live-body, .terminal-body') ||
          target.closest('a, button, input, textarea, select, summary, pre, [contenteditable], [role="dialog"], .terminal-frame') ||
          touch.clientX < 24 || touch.clientX > window.innerWidth - 24) return;
      for (let element: Element | null = target; element && element !== node; element = element.parentElement) {
        if (element.scrollWidth > element.clientWidth && /auto|scroll/.test(getComputedStyle(element).overflowX)) return;
      }
      gesture = { x: touch.clientX, y: touch.clientY, at: event.timeStamp, width: node.clientWidth, horizontal: false,
        reduced: Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches), samples: [{ x: touch.clientX, at: event.timeStamp }] };
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      const touch = event.touches[0];
      if (event.touches.length !== 1 || !touch || selecting()) { cancel(); return; }
      const dx = touch.clientX - gesture.x;
      const dy = touch.clientY - gesture.y;
      if (!gesture.horizontal) {
        if (Math.abs(dy) > 12 && Math.abs(dx) <= Math.abs(dy) * 1.5) { gesture = null; return; }
        if (!(Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.5)) return;
        gesture.horizontal = true;
        if (!gesture.reduced) setDragging(true);
      }
      if (event.cancelable) event.preventDefault();
      gesture.samples.push({ x: touch.clientX, at: event.timeStamp });
      if (!gesture.reduced) node.style.transform = `translateX(${dragOffset(dx, gesture.width, view, views)}px)`;
    };
    const end = (event: TouchEvent) => {
      const touch = event.changedTouches[0];
      if (!gesture?.horizontal || !touch) { gesture = null; return; }
      gesture.samples.push({ x: touch.clientX, at: event.timeStamp });
      finish(touch.clientX - gesture.x, releaseVelocity(gesture.samples));
    };
    const cancel = () => {
      if (gesture?.horizontal) finish(0, 0);
      gesture = null;
    };
    node.addEventListener('touchstart', start, { passive: true });
    node.addEventListener('touchmove', move, { passive: false });
    node.addEventListener('touchend', end);
    node.addEventListener('touchcancel', cancel);
    return () => {
      node.removeEventListener('touchstart', start);
      node.removeEventListener('touchmove', move);
      node.removeEventListener('touchend', end);
      node.removeEventListener('touchcancel', cancel);
      // Losing the gesture's owner mid-way (details opened, the view switched) rests the track at once.
      if (settling || gesture?.horizontal) { settling?.(); rest(node); setDragging(false); }
      gesture = null;
    };
  }, [enabled, view, views]);
  return { track, dragging };
}

function rest(node: HTMLDivElement | null) {
  if (!node) return;
  node.style.transform = "";
  node.style.transition = "";
}
