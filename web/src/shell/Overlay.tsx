import { useEffect } from "react";
import type { ReactNode } from "react";

/**
 * A scrim with a panel over it: the work panel from the right below the inline width, the phone's
 * project switcher from the bottom, First run centred. Esc or the scrim closes it.
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
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" data-side={side}>
      <button type="button" className="scrim" aria-label="Close" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={label} className="overlay-panel">
        {children}
      </div>
    </div>
  );
}
