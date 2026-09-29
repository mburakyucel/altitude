import { useEffect, useRef, useState } from "react";

/*
 * Live dictation flows into the field (SPEC.md §3.6). Recognizers answer every half second or so with the
 * whole text so far, so a few words and any corrected last words arrive at once. The field shows the latest
 * text up to a revealed length that grows at a steady pace, timed to finish about when the next answer is
 * due: roughly the pace of speech, and faster while a backlog catches up. A revision swaps in place and never
 * pulls the revealed length back, so text does not appear to be erased and retyped. Only the display paces:
 * whoever finishes the capture reads the recognizer's own text. Under reduced motion the text shows at once.
 */

/** The slowest reveal, in characters per second, so a short backlog never crawls. */
const MIN_RATE = 12;
/** The expected wait for the next answer: first a guess, then the recent gaps within these bounds. */
const FIRST_GAP_MS = 500;
const MIN_GAP_MS = 150;
const MAX_GAP_MS = 1200;

function reducedMotion() {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/** `text` as far as it has flowed in so far; null while there is no live text. */
export function useLiveReveal(text: string | null): string | null {
  const [revealed, setRevealed] = useState(0);
  const pace = useRef({ at: 0, rate: MIN_RATE, gap: FIRST_GAP_MS, arrived: 0, target: 0, frame: 0, last: 0 });

  useEffect(() => {
    const p = pace.current;
    if (text === null) {
      cancelAnimationFrame(p.frame);
      pace.current = { at: 0, rate: MIN_RATE, gap: FIRST_GAP_MS, arrived: 0, target: 0, frame: 0, last: 0 };
      setRevealed(0);
      return;
    }
    const now = performance.now();
    if (p.arrived) p.gap = Math.min(MAX_GAP_MS, Math.max(MIN_GAP_MS, (p.gap + now - p.arrived) / 2));
    p.arrived = now;
    p.target = text.length;
    p.at = Math.min(p.at, p.target);
    if (reducedMotion()) {
      p.at = p.target;
      setRevealed(p.target);
      return;
    }
    setRevealed(Math.floor(p.at));
    p.rate = Math.max(MIN_RATE, ((p.target - p.at) * 1000) / p.gap);
    if (p.frame || p.at >= p.target) return;
    p.last = now;
    const step = (time: number) => {
      p.at = Math.min(p.target, p.at + (p.rate * Math.max(0, time - p.last)) / 1000);
      p.last = time;
      setRevealed(Math.floor(p.at));
      p.frame = p.at < p.target ? requestAnimationFrame(step) : 0;
    };
    p.frame = requestAnimationFrame(step);
  }, [text]);

  useEffect(() => () => cancelAnimationFrame(pace.current.frame), []);

  return text === null ? null : text.slice(0, revealed);
}
