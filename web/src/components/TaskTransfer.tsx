import { useEffect, useRef, useState } from "react";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
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
  const [dialog, setDialog] = useState<"" | "start" | "now">("");
  const [target, setTarget] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("native");
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
  const openDialog = (value: "" | "start" | "now") => {
    if (value) { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setShown(info); beforeDialog(); }
    setDialog(value);
  };
  const selected = (dialog ? shown : info)?.targets.find((item) => item.engine === target);
  const selectedModel = selected?.models.find((item) => item.value === model);
  const effectiveEffort = effort === "native" ? null : effort;
  const changed = Boolean(shown && (target !== shown.current_engine || selectedModel?.model !== shown.current_model || effectiveEffort !== shown.current_effort));
  const pickEngine = (engine: string) => {
    const option = (dialog ? shown : info)?.targets.find((item) => item.engine === engine);
    setTarget(engine);
    const current = engine === info?.current_engine && option?.models.find((item) => item.model === info.current_model);
    setModel((current || option?.models.find((item) => item.available) || option?.models[0])?.value ?? "");
    setEffort(engine === info?.current_engine ? info.current_effort ?? "native" : option?.default_effort ?? "native");
  };
  const blocked = disabled || pending || uncertain;
  const start = () => {
    if (blocked || stopping || queued) return;
    if (!info) return;
    const next = (info.quota_limited && info.resume_hold
      ? info.targets.find((item) => item.engine !== info.current_engine && item.available)
      : info.targets.find((item) => item.engine === info.current_engine && item.available))
      || info.targets.find((item) => item.available) || info.targets[0];
    if (next) pickEngine(next.engine);
    setError(""); setUnavailable(""); openDialog("start");
  };
  const perform = async (action: "handoff" | "handoff-now" | "handoff-cancel") => {
    if (submitting.current || blocked || !info) return;
    submitting.current = true; setPending(true); setError("");
    try {
      await taskAction({ project, slug: task.slug, action,
        ...(action === "handoff" ? { engine: target, observed: shown?.observed,
          selection: selectedModel ? { model: selectedModel.model, effort: effectiveEffort } : undefined,
          default_model: selectedModel?.value === "default" ? selectedModel.model : undefined,
        } : { request_id: action === "handoff-now" ? shown?.request?.id : request?.id }) });
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
  const quotaOffer = Boolean(task.state === "blocked" && info?.quota_limited && info.resume_hold && info.targets.length && !active && !uncertain && !error);
  return { info, shown, request, active, stopping, queued, label, dialog, setDialog: openDialog, target, setTarget, selected,
    pending, uncertain, error, unavailable, blocked, disabled, quotaOffer, start, perform, recheck,
    model, setModel, effort, setEffort, pickEngine, selectedModel, changed,
    controls: active && !stopping || uncertain,
    close: () => { if (!submitting.current) {
      setDialog("");
      queueMicrotask(() => (opener.current?.isConnected ? opener.current : document.querySelector<HTMLElement>('[aria-label="Task details"], .phone-header [aria-haspopup="dialog"]'))?.focus({ preventScroll: true }));
    } },
  };
}
export type Transfer = ReturnType<typeof useTaskTransfer>;

export function TransferModelButton({ transfer: t }: { transfer: Transfer }) {
  if (!t.info || (!t.info.targets.length && !t.label) || t.disabled) return null;
  const label = t.info.current_model_label || `${t.info.current_label} · Settings unknown`;
  const effort = t.info.current_model ? t.info.current_effort_label : "";
  return <button type="button" className="models-pill task-model-pill" aria-haspopup="dialog"
    aria-label={`Task model: ${label}${effort ? ` · ${effort}` : ""}`} disabled={t.blocked || t.stopping || t.queued} onClick={t.start}>
    L2 · <strong>{label}{effort ? ` · ${effort}` : ""}</strong> <span aria-hidden>⌄</span>
  </button>;
}

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

export function TransferCard({ transfer: t, inDialog = false }: { transfer: Transfer; inDialog?: boolean }) {
  const quota = t.quotaOffer && !t.disabled;
  if (!t.active && !t.label && !t.error && !quota && !(inDialog && t.request)) return null;
  const busy = !t.uncertain && (t.active || t.pending || Boolean(t.label));
  const warning = quota || t.uncertain || Boolean(t.error) || ["failed", "refused"].includes(t.request?.status ?? "");
  const tone = warning ? "warn" : busy ? "run" : t.request?.status === "done" ? "ok" : "wait";
  const title = quota ? "Engine usage limit" : t.active && !t.stopping && !t.uncertain ? "Preparing change" : "Task change";
  const destination = t.info?.targets.find((item) => item.engine === t.request?.engine);
  const selectedName = destination?.models.find((item) => item.model === t.request?.selection?.model && item.value !== "default")?.label || t.request?.selection?.model;
  const selectedEffort = destination?.efforts.find((item) => item.value === (t.request?.selection?.effort ?? "native"))?.label;
  const status = quota ? `${t.info?.current_label} reached its usage limit.`
    : t.active && !t.stopping && !t.uncertain ? selectedName ? `${selectedName} · ${selectedEffort || "Native"}` : t.request?.target_label
    : t.label || (t.request?.status === "cancelled" ? "Switch cancelled." : t.request?.status === "done" ? `Switched to ${t.request.target_label}.`
      : t.request ? "Switch failed." : t.info?.current_label);
  return <section className="review-box transfer-card" aria-label={title} tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape" && !inDialog) event.stopPropagation(); }}>
    <span className={`review-icon review-icon-${tone}`} aria-hidden><svg viewBox="0 0 24 24"><path d={warning ? "M12 3 2 21h20L12 3zm0 6v5m0 3v1" : busy ? "M20 12a8 8 0 1 1-8-8"
      : t.request?.status === "done" ? "m5 12 4 4L19 6" : t.request?.status === "cancelled" ? "M6 6h12v12H6z" : "M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"} /></svg></span>
    <div className="review-main">
    <p className="review-name">{title}</p>
    <p className="review-meta" role="status">{status}</p>
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

export function TransferDialog({ transfer: t }: { transfer: Transfer }) {
  const { phone } = useViewport();
  if (!t.dialog || !t.shown) return null;
  const info = t.shown;
  const now = t.dialog === "now";
  const name = now ? info.request?.target_label : t.selected?.label;
  const title = now ? "Switch without a fresh note?" : info.targets.length === 1 ? `Switch to ${name}?` : "Switch engine";
  if (!now) return <Overlay label="This task’s model" side={phone ? "bottom" : "center"} onClose={t.close}>
    <div className="models task-models">
      <header className="models-head"><h2>This task’s model</h2><button type="button" className="icon-btn" aria-label="Close" onClick={t.close}>×</button></header>
      <p className="models-in-use">Current: <strong>{info.current_model_label || `${info.current_label} · Settings unknown`}{info.current_model ? ` · ${info.current_effort_label}` : ""}</strong></p>
      {t.active || t.label || ["pending", "executing"].includes(info.request?.status ?? "") ? <TransferCard transfer={t} inDialog /> : <>
        <fieldset className="task-model-fields" disabled={t.pending}>
          <label>Engine<select value={t.target} onChange={(event) => t.pickEngine(event.target.value)}>{info.targets.map((item) => <option key={item.engine} value={item.engine}>{item.label}</option>)}</select></label>
          <label>Model<select value={t.model} onChange={(event) => t.setModel(event.target.value)}>{!t.selected?.models.length ? <option value="">No named model configured</option> : t.selected.models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label>Effort<select value={t.effort} onChange={(event) => t.setEffort(event.target.value)}>{t.selected?.efforts.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        </fieldset>
        {!t.selectedModel?.available ? <p className="models-note" role="status">{t.selectedModel?.reason || t.selected?.reason || info.reason}</p> : null}
        <p className="transfer-warning">{info.preparation ? "Waits for a handoff note, then starts a fresh session." : "Starts a fresh session from saved context."} Some context may be lost; rebuilding it uses extra tokens. Saved work, questions and holds stay.</p>
        <div className="models-actions"><button type="button" className="btn" disabled={t.pending} onClick={t.close}>Cancel</button><button type="button" className="btn btn-primary" disabled={t.blocked || !t.selectedModel?.available || !t.changed} onClick={() => void t.perform("handoff")}><BusyLabel busy={t.pending} label={info.preparation ? "Prepare change" : "Start fresh session"} working="Submitting…" /></button></div>
      </>}
    </div>
  </Overlay>;
  return <Overlay label={title} side="center" onClose={t.close}>
    <div className="transfer-dialog">
      <h2>{title}</h2>
      <p>The current worker and its pending commands will stop. {name} continues from saved context.</p>
      <div className="transfer-actions">
        <button type="button" className="btn" data-autofocus disabled={t.pending} onClick={t.close}>{now ? "Keep waiting" : "Cancel"}</button>
        <button type="button" className="btn btn-primary" disabled={t.blocked || (!now && !t.selected?.available)} onClick={() => void t.perform(now ? "handoff-now" : "handoff")}>
          <BusyLabel busy={t.pending} label={now ? "Switch now" : info.preparation ? "Prepare switch" : `Switch to ${name}`} working="Submitting…" />
        </button>
      </div>
    </div>
  </Overlay>;
}
