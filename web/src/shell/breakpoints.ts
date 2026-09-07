import { useSyncExternalStore } from "react";
import type { CSSProperties } from "react";

/**
 * The only place the web code names a width or a shell height (SPEC.md §2.2). Components read the
 * viewport through useViewport(); the stylesheet reads the sizes as the custom properties in
 * LAYOUT_SIZES, which the shell root carries inline so they travel with the DOM.
 */
export const DESKTOP_MIN = 1024;
export const PANEL_INLINE_MIN = 1280;
export const RAIL_WIDTH = 260;
export const PANEL_WIDTH = 340;
export const PHONE_HEADER_HEIGHT = 54;
export const TAB_BAR_HEIGHT = 84;

export const LAYOUT_SIZES = {
  "--rail-w": `${RAIL_WIDTH}px`,
  "--panel-w": `${PANEL_WIDTH}px`,
  "--phone-header-h": `${PHONE_HEADER_HEIGHT}px`,
  "--tab-bar-h": `${TAB_BAR_HEIGHT}px`,
} as CSSProperties;

export interface Viewport {
  /** Below the desktop breakpoint: header, content, tab bar. */
  phone: boolean;
  /** Wide enough for the work panel to sit inline as a third column. */
  panelInline: boolean;
}

export function viewportFor(width: number): Viewport {
  return { phone: width < DESKTOP_MIN, panelInline: width >= PANEL_INLINE_MIN };
}

function subscribe(listener: () => void) {
  window.addEventListener("resize", listener);
  return () => window.removeEventListener("resize", listener);
}

export function useViewport(): Viewport {
  return viewportFor(useSyncExternalStore(subscribe, () => window.innerWidth, () => DESKTOP_MIN));
}
