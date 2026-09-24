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

/**
 * Track offset for a finger displacement from the gesture's start: toward the other view the
 * outgoing view follows the finger up to one width; past either end the give shrinks with distance.
 */
export function dragOffset(dx: number, width: number, live: boolean): number {
  if (dx === 0 || width <= 0) return 0;
  const magnitude = Math.abs(dx);
  const toward = live ? dx > 0 : dx < 0;
  if (toward) return Math.sign(dx) * Math.min(magnitude, width);
  return Math.sign(dx) * (1 - 1 / ((magnitude * RESISTANCE) / width + 1)) * width * RESISTANCE;
}

/** Speed at release from the movement inside the window; a pause before lifting counts as rest. */
export function releaseVelocity(samples: readonly Sample[], at: number): number {
  const last = samples[samples.length - 1];
  if (!last || at - last.at > VELOCITY_WINDOW) return 0;
  let reference = last;
  for (let index = samples.length - 2; index >= 0; index--) {
    reference = samples[index]!;
    if (last.at - reference.at >= VELOCITY_WINDOW) break;
  }
  if (reference.at === last.at) return 0;
  return (last.x - reference.x) / (last.at - reference.at);
}

/**
 * Whether a released drag completes the switch: a fling in the drag's direction, or a slow release
 * past half the width. Flings back toward the start and drags past either end spring back.
 */
export function completes(dx: number, velocity: number, width: number, live: boolean): boolean {
  if (!(live ? dx > 0 : dx < 0)) return false;
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
 * Task content owns deliberate horizontal swipes; native scrolling and selection keep theirs.
 * From the first horizontal movement the track follows the finger, so both views are on screen
 * until the release settles into a switch or springs back. Under reduced motion nothing moves and a
 * release past the same thresholds switches at once.
 */
export function useTaskSwipe(enabled: boolean, live: boolean, switchView: (live: boolean) => void) {
  const track = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const navigate = useRef(switchView);
  navigate.current = switchView;
  // The resting position is the layout's own: a completed switch keeps its settled transform until the
  // route has changed, then drops it in the same paint, so the view never snaps.
  useLayoutEffect(() => {
    rest(track.current);
    setDragging(false);
  }, [live]);
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
      const switching = completes(dx, velocity, saved.width, live);
      if (saved.reduced) {
        if (switching) navigate.current(!live);
      } else if (switching) {
        settle(live ? saved.width : -saved.width, () => navigate.current(!live));
      } else {
        settle(0, () => { rest(node); setDragging(false); });
      }
    };
    const start = (event: TouchEvent) => {
      gesture = null;
      const target = event.target instanceof Element ? event.target : null;
      const touch = event.touches[0];
      if (settling || event.touches.length !== 1 || !touch || selecting() ||
          !target?.closest('.convo-scroll, .live-body') ||
          target.closest('a, button, input, textarea, select, summary, pre, [contenteditable], [role="dialog"]') ||
          touch.clientX < 24 || touch.clientX > window.innerWidth - 24) return;
      for (let element: Element | null = target; element && element !== node; element = element.parentElement) {
        if (element.scrollWidth > element.clientWidth && /auto|scroll/.test(getComputedStyle(element).overflowX)) return;
      }
      gesture = { x: touch.clientX, y: touch.clientY, at: event.timeStamp, width: node.clientWidth, horizontal: false,
        reduced: Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches), samples: [] };
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
      if (!gesture.reduced) node.style.transform = `translateX(${dragOffset(dx, gesture.width, live)}px)`;
    };
    const end = (event: TouchEvent) => {
      const touch = event.changedTouches[0];
      if (!gesture?.horizontal || !touch) { gesture = null; return; }
      finish(touch.clientX - gesture.x, releaseVelocity(gesture.samples, event.timeStamp));
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
  }, [enabled, live]);
  return { track, dragging };
}

function rest(node: HTMLDivElement | null) {
  if (!node) return;
  node.style.transform = "";
  node.style.transition = "";
}
