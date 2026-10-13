import { useEffect, useRef, useState } from "react";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { Overlay } from "../shell/Overlay";
import { BusyLabel } from "./StatusMark";
import "./task-transfer.css";

/** Transfer follows the task record; only an in-flight submission lives in this component. */
export function useTaskTransfer(project: string, task: TaskView, refresh: () => Promise<unknown>, disabled: boolean, onDenied: () => void, beforeDialog: () => void) {
  const info = task.transfer;
  const request = info?.request;
  const active = Boolean(request && ["pending", "executing"].includes(request.status));
  const stopping = active && request?.phase === "stopping";
  const [dialog, setDialog] = useState<"" | "start" | "now">("");
  const [target, setTarget] = useState("");
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
    (document.querySelector<HTMLElement>('.transfer-card') || (opener.current?.isConnected ? opener.current
      : document.querySelector<HTMLElement>('[aria-label="Task details"], .phone-header [aria-haspopup="dialog"]')))?.focus({ preventScroll: true });
  }, [dialog, pending]);
  const openDialog = (value: "" | "start" | "now") => {
    if (value) { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setShown(info); beforeDialog(); }
    setDialog(value);
  };
  const selected = (dialog ? shown : info)?.targets.find((item) => item.engine === target);
  const blocked = disabled || pending || uncertain;
  const start = () => {
    if (blocked) return;
    if (!info?.eligible) { setUnavailable(info?.reason || "Engine switching is unavailable."); return; }
    const next = info.targets.find((item) => item.available);
    if (!next) { setUnavailable(info.reason || info.targets[0]?.reason || "No engine is available."); return; }
    setTarget(next.engine); setError(""); setUnavailable(""); openDialog("start");
  };
  const perform = async (action: "handoff" | "handoff-now" | "handoff-cancel") => {
    if (submitting.current || blocked || !info) return;
    submitting.current = true; setPending(true); setError("");
    try {
      await taskAction({ project, slug: task.slug, action,
        ...(action === "handoff" ? { engine: target, observed: shown?.observed } : { request_id: action === "handoff-now" ? shown?.request?.id : request?.id }) });
      setDialog("");
      await refresh();
    } catch (cause) {
      setDialog("");
      if (cause instanceof ApiError && [401, 403].includes(cause.status)) {
        setError("You do not have permission to switch this task."); onDenied();
      } else {
        // A lost response is not a refusal. Reconcile with a read before enabling any other switch.
        setUncertain(true);
        setError(cause instanceof ApiError && cause.status === 409 ? "The task changed. Check status before switching." : "Could not confirm the switch. Check status before trying again.");
      }
    } finally { restoreFocus.current = true; submitting.current = false; setPending(false); }
  };
  const recheck = async () => {
    if (submitting.current) return;
    submitting.current = true; setPending(true);
    try { await refresh(); setUncertain(false); setError(""); }
    catch { setError("Could not check status. Try again."); }
    finally { submitting.current = false; setPending(false); }
  };
  const label = uncertain ? "Switch unconfirmed" : active ? stopping ? `Switching to ${request?.target_label}` : "Preparing switch"
    : request?.status === "done" && task.state === "queued" ? `Waiting for ${request.target_label}` : "";
  return { info, shown, request, active, stopping, label, dialog, setDialog: openDialog, target, setTarget, selected,
    pending, uncertain, error, unavailable, blocked, disabled, start, perform, recheck,
    controls: active && !stopping || uncertain,
    close: () => { if (!submitting.current) {
      setDialog("");
      queueMicrotask(() => (opener.current?.isConnected ? opener.current : document.querySelector<HTMLElement>('[aria-label="Task details"], .phone-header [aria-haspopup="dialog"]'))?.focus({ preventScroll: true }));
    } },
  };
}
export type Transfer = ReturnType<typeof useTaskTransfer>;

export function TransferEntry({ transfer: t, quota = false }: { transfer: Transfer; quota?: boolean }) {
  if (!t.info?.targets.length || t.active || t.uncertain) return null;
  const offered = t.info.targets.filter((target) => target.available);
  return <div className="transfer-entry">
    <button type="button" className={quota ? "btn primary" : "btn"} disabled={t.blocked}
      aria-disabled={!t.info.eligible} onClick={t.start}>
      {quota && offered.length === 1 ? `Switch to ${offered[0]?.label}` : "Switch engine…"}
    </button>
    {t.unavailable && !t.info.eligible ? <p className="text-meta" role="status">{t.unavailable}</p> : null}
  </div>;
}

export function TransferCard({ transfer: t, details = false }: { transfer: Transfer; details?: boolean }) {
  if (!t.active && !t.uncertain && !t.error && !(details && t.request)) return null;
  const title = t.label || (t.request?.status === "cancelled" ? "Switch cancelled." : t.request?.status === "done" ? `Switch to ${t.request.target_label} queued.` : "Switch failed.");
  return <section className="transfer-card" aria-label="Engine transfer" tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}>
    <p role="status">{t.active || t.pending ? <span className="spinner" aria-hidden /> : null}{t.active && !t.stopping && !t.uncertain ? `Preparing switch to ${t.request?.target_label}` : title}</p>
    {t.error ? <p role="alert">{t.error}</p> : null}
    {details && !t.active && t.request?.note ? <p className="text-meta">{t.request.note}</p> : null}
    {t.controls && (t.uncertain || !t.disabled) ? <div className="transfer-actions">
      {t.uncertain ? <button type="button" className="btn" disabled={t.pending} onClick={() => void t.recheck()}>Check status</button> : <>
        <button type="button" className="btn" disabled={t.blocked} onClick={() => t.setDialog("now")}>Switch now</button>
        <button type="button" className="btn" disabled={t.blocked} onClick={() => void t.perform("handoff-cancel")}>Cancel switch</button>
      </>}
    </div> : null}
  </section>;
}

export function TransferDialog({ transfer: t }: { transfer: Transfer }) {
  if (!t.dialog || !t.shown) return null;
  const info = t.shown;
  const now = t.dialog === "now";
  const name = now ? info.request?.target_label : t.selected?.label;
  const title = now ? "Switch without a fresh note?" : info.targets.length === 1 ? `Switch to ${name}?` : "Switch engine";
  return <Overlay label={title} side="center" onClose={t.close}>
    <div className="transfer-dialog">
      <h2>{title}</h2>
      {!now && info.targets.length > 1 ? <fieldset><legend>Continue with</legend>{info.targets.map((item) =>
        <label key={item.engine}><input type="radio" name="transfer-engine" value={item.engine} checked={t.target === item.engine}
          disabled={!item.available || t.pending} onChange={() => t.setTarget(item.engine)} />{item.label}{!item.available && item.reason ? <span className="text-meta">{item.reason}</span> : null}</label>)}</fieldset> : null}
      <p>{now ? `The current worker and its pending commands will stop. ${name} continues from saved context.`
        : info.preparation ? `${info.current_label} finishes a handoff note, then stops. ${name} continues. There is no time limit; you can switch now or cancel while waiting.`
        : `${name} continues from the saved task record.`}</p>
      {!now ? <><p className="transfer-warning">Some session context may not carry over. The new engine uses extra tokens to get up to speed.</p><p>Your saved work, questions and merge holds stay.</p></> : null}
      <div className="transfer-actions">
        <button type="button" className="btn" data-autofocus disabled={t.pending} onClick={t.close}>{now ? "Keep waiting" : "Cancel"}</button>
        <button type="button" className="btn primary" disabled={t.blocked || (!now && !t.selected?.available)} onClick={() => void t.perform(now ? "handoff-now" : "handoff")}>
          <BusyLabel busy={t.pending} label={now ? "Switch now" : info.preparation ? "Prepare switch" : `Switch to ${name}`} working="Submitting…" />
        </button>
      </div>
    </div>
  </Overlay>;
}
