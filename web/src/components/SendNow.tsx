import { useId } from "react";
import { ApiError } from "../data/api";

/** Queue actions retain their row until the server supplies a delivery receipt. */
export function SendNow({ visible, pending, disabled, reason, task = false, error, onClick }: {
  visible: boolean; pending: boolean; disabled: boolean; reason?: string | null;
  task?: boolean; error?: Error | null; onClick: () => void;
}) {
  const help = useId();
  return <>
    {visible ? <button type="button" className="send-now" disabled={disabled || pending}
      aria-describedby={reason || task ? help : undefined} onClick={onClick}>{pending ? "Sending now…" : "Send now"}</button> : null}
    {visible && (reason || task) ? <span id={help} className="send-now-help text-muted">{reason || "Joins the current turn without stopping its work."}</span> : null}
    {error ? <span className="send-now-help" role="alert">{error instanceof ApiError && [401, 403].includes(error.status)
      ? "You do not have permission to send this message now."
      : error instanceof ApiError && error.status === 409 ? error.message
      : "Send now unconfirmed. Check this message’s status before trying again."}</span> : null}
  </>;
}
