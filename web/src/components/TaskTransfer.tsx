import { useEffect, useRef, useState } from "react";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { Overlay } from "../shell/Overlay";
import { BusyLabel } from "./StatusMark";
import "./task-review.css";
import "./task-transfer.css";

/** Transfer follows the task record; only an in-flight submission lives in this component. */
export function useTaskTransfer(project: string, task: TaskView, refresh: () => Promise<unknown>, disabled: boolean, onDenied: () => void, beforeDialog: () => void) {
  const info = task.transfer;
  const request = info?.request;
  const active = Boolean(request && ["pending", "executing"].includes(request.status));
  const stopping = active && request?.phase === "stopping";
  const queued = request?.status === "done" && task.state === "queued";
  const [dialog, setDialog] = useState<"" | "now">("");
  const [menuRequest, setMenuRequest] = useState(false);
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState("");
  const [shown, setShown] = useState(info);
  const opener = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (dialog || pending || !restoreFocus.current) return;
    restoreFocus.current = false;
    // Completing a request must not take focus from work started while its receipt was pending.
    if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) return;
    (document.querySelector<HTMLElement>('.transfer-card') || (opener.current?.isConnected ? opener.current
      : document.querySelector<HTMLElement>('[aria-label="Task details"], .phone-header [aria-haspopup="dialog"]')))?.focus({ preventScroll: true });
  }, [dialog, pending]);
  const openDialog = (value: "" | "now") => {
    if (value) { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setShown(info); beforeDialog(); }
    setDialog(value);
  };
  const blocked = disabled || pending || uncertain;
  /** The engine-stop card's Change model… opens the composer's model menu. */
  const start = () => {
    if (blocked || stopping || queued || !info) return;
    setError(""); setUnavailable(""); setMenuRequest(true);
  };
  const submit = async (action: "handoff" | "handoff-now" | "handoff-cancel", body: Record<string, unknown>) => {
    if (submitting.current || blocked || !info) return false;
    submitting.current = true; setPending(true); setError("");
    try {
      await taskAction({ project, slug: task.slug, action, ...body });
      setDialog("");
      await refresh();
      return true;
    } catch (cause) {
      setDialog("");
      if (cause instanceof ApiError && [401, 403].includes(cause.status)) {
        setError("You do not have permission to switch this task."); onDenied();
      } else {
        // A lost response is not a refusal. Reconcile with a read before enabling any other switch.
        setUncertain(true);
        setError(cause instanceof ApiError && cause.status === 409 ? "The task changed. Check status before switching." : "Could not confirm the switch. Check status before trying again.");
      }
      return false;
    } finally { restoreFocus.current = true; submitting.current = false; setPending(false); }
  };
  /** One confirmed pick from the model menu, fenced to the session the panel showed when it opened. */
  const switchTo = (pick: { engine: string; value: string; model: string; effort: string }, observed: unknown) => submit("handoff", {
    engine: pick.engine, observed,
    selection: { model: pick.model, effort: pick.effort === "native" ? null : pick.effort },
    default_model: pick.value === "default" ? pick.model : undefined,
  });
  const perform = (action: "handoff-now" | "handoff-cancel") => submit(action, { request_id: action === "handoff-now" ? shown?.request?.id : request?.id });
  const recheck = async () => {
    if (submitting.current) return;
    submitting.current = true; setPending(true);
    try { await refresh(); setUncertain(false); setError(""); }
    catch { setError("Could not check status. Try again."); }
    finally { submitting.current = false; setPending(false); }
  };
  const label = uncertain ? "Switch unconfirmed" : active ? "Switching model"
    : request?.status === "done" && task.state === "queued" ? `Waiting for ${request.target_label}` : "";
  // Names what the switch changes, so an effort-only change on the same engine reads as such.
  const destination = info?.targets.find((item) => item.engine === request?.engine);
  const selectedName = destination?.models.find((item) => item.model === request?.selection?.model && item.value !== "default")?.label || request?.selection?.model;
  const selectedEffort = destination?.efforts.find((item) => item.value === (request?.selection?.effort ?? "native"))?.label;
  const switchingTo = selectedName ? `${selectedName} · ${selectedEffort || "Default"}` : request?.target_label;
  const focusCard = () => document.querySelector<HTMLElement>(".transfer-card")?.focus();
  // The engine stopped the task (a usage limit still in force, or a refusal); it waits there until the operator switches.
  const engineStop = task.state !== "blocked" || !info ? null : info.quota_limited && info.resume_hold ? "limit" as const : info.refused ? "refused" as const : null;
  const engineOffer = Boolean(engineStop && info?.targets.length && !active && !uncertain && !error);
  return { info, shown, request, active, stopping, queued, label, dialog, setDialog: openDialog,
    pending, uncertain, error, unavailable, blocked, disabled, engineOffer, engineStop, switchingTo, start, perform, switchTo, recheck, focusCard,
    menuRequest, menuHandled: () => setMenuRequest(false),
    controls: active && !stopping || uncertain,
    close: () => { if (!submitting.current) {
      setDialog("");
      queueMicrotask(() => (opener.current?.isConnected ? opener.current : document.querySelector<HTMLElement>('[aria-label="Task details"], .phone-header [aria-haspopup="dialog"]'))?.focus({ preventScroll: true }));
    } },
  };
}
export type Transfer = ReturnType<typeof useTaskTransfer>;

function TransferEntry({ transfer: t }: { transfer: Transfer }) {
  if (!t.info?.targets.length || t.active || t.label || t.uncertain) return null;
  return <div className="transfer-entry">
    <button type="button" className="btn" disabled={t.blocked}
      aria-disabled={!t.info.eligible} onClick={t.start}>
      Change model…
    </button>
    {t.unavailable && !t.info.eligible ? <p className="text-meta" role="status">{t.unavailable}</p> : null}
  </div>;
}

export function TransferCard({ transfer: t }: { transfer: Transfer }) {
  const quota = t.engineOffer && !t.disabled;
  if (!t.active && !t.label && !t.error && !quota) return null;
  const busy = !t.uncertain && (t.active || t.pending || Boolean(t.label));
  const warning = quota || t.uncertain || Boolean(t.error) || ["failed", "refused"].includes(t.request?.status ?? "");
  const tone = warning ? "warn" : busy ? "run" : t.request?.status === "done" ? "ok" : "wait";
  // One word for a switch everywhere: the header reads "Switching model", the card and composer "Switching to <model · effort>".
  const switching = t.active && !t.uncertain;
  const refused = quota && t.engineStop === "refused";
  const title = refused ? "Engine refused the task" : quota ? "Engine usage limit" : switching ? `Switching to ${t.switchingTo}` : "Model switch";
  const status = refused ? `${t.info?.current_label}: ${t.info?.refused}.` : quota ? `${t.info?.current_label} reached its usage limit.`
    : switching ? t.stopping ? "Starting the fresh session." : "Waiting for the current session’s handoff note."
    : t.label || (t.request?.status === "cancelled" ? "Switch cancelled." : t.request?.status === "done" ? `Switched to ${t.request.target_label}.`
      : t.request ? "Switch failed." : t.info?.current_label);
  return <section className="review-box transfer-card" aria-label={quota ? title : "Model switch"} tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}>
    <span className={`review-icon review-icon-${tone}`} aria-hidden><svg viewBox="0 0 24 24"><path d={warning ? "M12 3 2 21h20L12 3zm0 6v5m0 3v1" : busy ? "M20 12a8 8 0 1 1-8-8"
      : t.request?.status === "done" ? "m5 12 4 4L19 6" : t.request?.status === "cancelled" ? "M6 6h12v12H6z" : "M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"} /></svg></span>
    <div className="review-main">
    <p className="review-name">{title}</p>
    {/* An unconfirmed switch says so once, in the header; the card gives the reason and the way out. */}
    {t.uncertain && t.error ? null : <p className="review-meta" role="status">{status}</p>}
    {!quota && !t.uncertain && (t.active || t.queued) && t.request?.by ? <p className="review-meta">{t.request.by === "l3"
      ? `Started by the coordinator${t.request.reason ? ` · ${t.request.reason}` : ""}` : "Started by you"}</p> : null}
    {t.error ? <p role="alert">{t.error}</p> : null}
    {t.controls && (t.uncertain || !t.disabled) ? <div className="transfer-actions">
      {t.uncertain ? <button type="button" className="btn" disabled={t.pending} onClick={() => void t.recheck()}>Check status</button> : <>
        <button type="button" className="btn" disabled={t.blocked} onClick={() => t.setDialog("now")}>Switch now</button>
        <button type="button" className="btn" disabled={t.blocked} onClick={() => void t.perform("handoff-cancel")}>Cancel switch</button>
      </>}
    </div> : null}
    {quota ? <TransferEntry transfer={t} /> : null}
    </div>
  </section>;
}

/** Switch now: stop waiting for the handoff note and continue from saved context. */
export function TransferDialog({ transfer: t }: { transfer: Transfer }) {
  if (t.dialog !== "now" || !t.shown) return null;
  const name = t.shown.request?.target_label;
  const title = "Switch without a fresh note?";
  return <Overlay label={title} side="center" onClose={t.close}>
    <div className="transfer-dialog">
      <h2>{title}</h2>
      <p>The current worker and its pending commands will stop. {name} continues from saved context.</p>
      <div className="transfer-actions">
        <button type="button" className="btn" data-autofocus disabled={t.pending} onClick={t.close}>Keep waiting</button>
        <button type="button" className="btn btn-primary" disabled={t.blocked} onClick={() => void t.perform("handoff-now")}>
          <BusyLabel busy={t.pending} label="Switch now" working="Submitting…" />
        </button>
      </div>
    </div>
  </Overlay>;
}
