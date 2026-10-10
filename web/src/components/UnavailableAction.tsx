import { useId, useState } from "react";
import type { ReactNode } from "react";

/** An unavailable action remains reachable so touch and keyboard users can ask why. */
export function UnavailableAction({ label, reason, className = "btn" }: { label: string; reason: ReactNode; className?: string }) {
  const id = useId();
  const [shown, setShown] = useState(false);
  return <>
    <button type="button" className={className} aria-disabled="true" aria-describedby={id} onClick={() => setShown(!shown)}>{label}</button>
    <span id={id} className={shown ? "text-meta text-muted" : "sr-only"}>{reason}</span>
  </>;
}
