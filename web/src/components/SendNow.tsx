import { useId, useState } from "react";
import { ApiError } from "../data/api";

/**
 * One Send now under the last queued message: it delivers the queue it closes. The control carries its own state
 * (SPEC.md §1.1): a spinner in place while delivery is pending, and a muted look while the server gives a reason it
 * cannot send now; pressing it then shows that reason.
 */
export function SendNow({ pending, disabled, reason, task = false, onClick }: {
  pending: boolean; disabled: boolean; reason?: string | null; task?: boolean; onClick: () => void;
}) {
  const help = useId();
  const [asked, setAsked] = useState(false);
  const held = pending || Boolean(reason);
  const description = reason || (task ? "Joins the current turn without stopping its work." : "");
  return <>
    <button type="button" className="send-now" disabled={disabled && !held} aria-disabled={held || undefined}
      aria-busy={pending || undefined} aria-label={pending ? "Sending now" : undefined} title={description || undefined}
      aria-describedby={description ? help : undefined} onClick={held ? () => setAsked((shown) => !shown) : onClick}>
      <span className="send-now-label">Send now</span>{pending ? <span className="spinner" aria-hidden="true" /> : null}
    </button>
    {description ? <span id={help} className={reason && asked ? "send-now-help text-muted" : "sr-only"}>{description}</span> : null}
  </>;
}

/** A failed Send now request keeps the queue and names what happened beside it. */
export function SendNowError({ error }: { error: Error }) {
  return <span className="send-now-help" role="alert">{error instanceof ApiError && [401, 403].includes(error.status)
    ? "You do not have permission to send this message now."
    : error instanceof ApiError && error.status === 409 ? error.message
    : "Send now unconfirmed. Check this message’s status before trying again."}</span>;
}
