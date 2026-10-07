import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

/**
 * A scrim with a panel over it: the work panel from the right below the inline width, the phone's
 * project switcher from the bottom, First run centred. Esc or the scrim closes it. Focus starts on the
 * panel's `[data-autofocus]` element when it has one.
 */
export function Overlay({
  label,
  side,
  onClose,
  children,
}: {
  label: string;
  side: "right" | "bottom" | "center";
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const node = panel.current!;
    (node.querySelector<HTMLElement>("[data-autofocus]") ?? node).focus({ preventScroll: true });
    const containFocus = (event: FocusEvent) => {
      if (!node.contains(event.target as Node)) node.focus({ preventScroll: true });
    };
    const focusables = () => [...node.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]')].filter((el) => el.getClientRects().length > 0);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
      if (event.key !== "Tab") return;
      const items = focusables();
      const first = items[0] ?? node;
      const last = items.at(-1) ?? node;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === node)) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", containFocus);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", containFocus);
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  return (
    <div className="overlay" data-side={side}>
      <button type="button" className="scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div ref={panel} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} className="overlay-panel">
        {children}
      </div>
    </div>
  );
}
