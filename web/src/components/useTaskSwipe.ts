import { useEffect, useRef } from "react";

/** Task content owns deliberate horizontal swipes; native scrolling and selection keep theirs. */
export function useTaskSwipe(enabled: boolean, live: boolean, switchView: (live: boolean) => void) {
  const root = useRef<HTMLDivElement>(null);
  const navigate = useRef(switchView);
  navigate.current = switchView;
  useEffect(() => {
    const node = root.current;
    if (!enabled || !node) return;
    let gesture: { x: number; y: number; at: number; horizontal: boolean } | null = null;
    const selecting = () => !window.getSelection()?.isCollapsed;
    const start = (event: TouchEvent) => {
      gesture = null;
      const target = event.target instanceof Element ? event.target : null;
      const touch = event.touches[0];
      if (event.touches.length !== 1 || !touch || selecting() ||
          !target?.closest('.convo-scroll, .live-body') ||
          target.closest('a, button, input, textarea, select, summary, pre, [contenteditable], [role="dialog"]') ||
          touch.clientX < 24 || touch.clientX > window.innerWidth - 24) return;
      for (let element: Element | null = target; element && element !== node; element = element.parentElement) {
        if (element.scrollWidth > element.clientWidth && /auto|scroll/.test(getComputedStyle(element).overflowX)) return;
      }
      gesture = { x: touch.clientX, y: touch.clientY, at: event.timeStamp, horizontal: false };
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      const touch = event.touches[0];
      if (event.touches.length !== 1 || !touch || selecting()) { gesture = null; return; }
      const dx = touch.clientX - gesture.x;
      const dy = touch.clientY - gesture.y;
      if (!gesture.horizontal && Math.abs(dy) > 12 && Math.abs(dx) <= Math.abs(dy) * 1.5) { gesture = null; return; }
      if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.5) gesture.horizontal = true;
      if (gesture.horizontal && event.cancelable) event.preventDefault();
    };
    const end = (event: TouchEvent) => {
      const saved = gesture;
      gesture = null;
      const touch = event.changedTouches[0];
      if (!saved?.horizontal || !touch || selecting() || event.timeStamp - saved.at > 700) return;
      const dx = touch.clientX - saved.x;
      if (Math.abs(dx) >= 64 && Math.abs(dx) > Math.abs(touch.clientY - saved.y) * 1.5 && (live ? dx > 0 : dx < 0)) navigate.current(!live);
    };
    const cancel = () => { gesture = null; };
    node.addEventListener('touchstart', start, { passive: true });
    node.addEventListener('touchmove', move, { passive: false });
    node.addEventListener('touchend', end);
    node.addEventListener('touchcancel', cancel);
    return () => {
      node.removeEventListener('touchstart', start);
      node.removeEventListener('touchmove', move);
      node.removeEventListener('touchend', end);
      node.removeEventListener('touchcancel', cancel);
    };
  }, [enabled, live]);
  return root;
}
