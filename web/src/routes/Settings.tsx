import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { StatusMark, BusyLabel } from "../components/StatusMark";
import FolderBrowser from "../components/FolderBrowser";
import { Command, IncidentReportsForm, NameForm, PrerequisiteList } from "../components/Onboarding";
import { HostingCard, networkSummary } from "../components/Network";
import { ApiError, type Machine, changeHostVoice, closePhoneShare, makePairingCode, openPhoneShare, readVoiceSettings, revokeDevice, saveProjectsFolder, saveTerminalAccess, saveUpdateAutomatic, saveUpdateCheck, saveValidationAccess, saveVoiceSettings, startUpdate, useDevices, useMachine, useOverview, useProjectDefaults } from "../data/api";
import type { Certificate, Device, Overview, PairingCode, PhoneShare, ProjectDefaults, Update } from "../data/api";
import { ProjectModelSettingsSection } from "../components/Models";
import { managedProjects } from "../shell/projects";
import type { HostVoice, VoiceBackend, VoiceSettings, VoiceUpdate } from "../data/api";
import { INVALID_SERVICE, updateVoiceSettings } from "../components/voiceBackend";
import { useViewport } from "../shell/breakpoints";
import { automaticText } from "../shell/UpdateNotice";
import VoiceDiagnostics from "../components/VoiceDiagnostics";
import { UnavailableAction } from "../components/UnavailableAction";
import { dateText } from "../data/observed";
import { disableAlerts, enableAlerts, savePreferences, useAlertNote, useAlertState, usePreferences } from "../data/alerts";
import type { Preferences, Reveal } from "../data/alerts";
import "./settings.css";

const labels: Record<VoiceBackend, string> = { host: "This computer", browser: "Browser recognition", service: "Your speech service" };
const explanations: Record<VoiceBackend, string> = {
  host: "Words appear as you speak, with punctuation and capitals, in English, in every browser. Audio goes from your device to this computer over Altitude’s own connection, is transcribed here and is never stored or sent to another service. If the connection drops, recording continues and your words catch up.",
  browser: "No setup in supported browsers. Words appear as you speak; English gets punctuation and capitals on this device. Your browser may send audio to its speech service; that service’s privacy policy applies.",
  service: "Words appear after you stop. Audio passes through this computer to the service you choose. Its privacy policy and charges apply.",
};
const queryKey = ["voice-settings"];

function voiceSummary(settings: VoiceSettings) {
  if (settings.backend === "service") return labels.service;
  if (settings.backend === "browser") return labels.browser;
  return settings.host.state === "ready" ? labels.host
    : `${labels.host} · ${settings.host.state === "outdated" ? "update required" : "not set up"}`;
}

const megabytes = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;

type HostAction = "setup" | "cancel" | "remove";

/** Availability and replacement progress are independent: supported voice still serves during updates.
 *  The actions share the voice page's lock: `locked` while a choice saves, `busy` while one of them runs. */
function HostVoicePanel({ host, use, locked, busy, setBusy, onChange }: {
  host: HostVoice; use: ReactNode; locked: boolean; busy: HostAction | null;
  setBusy: (action: HostAction | null) => void; onChange: (value: VoiceSettings) => void;
}) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const act = async (action: HostAction) => {
    setBusy(action);
    setError("");
    try {
      await client.cancelQueries({ queryKey });
      onChange(await changeHostVoice(action));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(null);
    }
  };
  if (host.state === "unavailable") return <UnavailableAction label="Set up voice" reason={`Not available on this computer: ${host.reason}.`} />;
  const size = megabytes(host.download_bytes);
  const update = host.update;
  const installed = host.state === "ready" || host.state === "outdated";
  const updating = update && update.state !== "failed" && update.state !== "cancelled";
  const button = (action: HostAction, label: string, working: string, style = "") =>
    <button type="button" className={`btn ${style}`} disabled={busy !== null || locked} onClick={() => void act(action)}>
      <BusyLabel busy={busy === action} label={label} working={working} />
    </button>;
  // A server reason ends with its own retry advice; the Retry beside it says that already.
  const reason = (text?: string) => (text ?? "").replace(/\s*Try again\.?$/, "");
  const required = host.state === "outdated";
  return <div className="voice-host">
    {host.state === "setting-up" ? <>
      <progress max={host.download_bytes} value={host.done_bytes ?? 0} aria-label="Voice setup" />
      <p role="status" className="text-meta text-muted"><span className="sr-only">Setting up… </span>{megabytes(host.done_bytes ?? 0)} of {size}</p>
      {button("cancel", "Cancel setup", "Cancelling setup")}
    </> : installed ? <>
      {updating && required ? <p className="text-meta text-muted">Voice needs an update.</p> : null}
      {updating ? <div role="status" className="voice-update">
        {update.state === "downloading" ? <progress max={update.download_bytes} value={update.done_bytes ?? 0} aria-label="Updating voice" /> : null}
        <p className="text-meta text-muted voice-update-line">
          {update.state === "downloading" ? null : <StatusMark label="Updating voice" announce={false} />}
          <span>{update.state === "downloading" ? `Updating · ${megabytes(update.done_bytes ?? 0)} of ${megabytes(update.download_bytes ?? 0)}`
            : update.state === "waiting" ? "Updating · finishes after dictation" : "Updating…"}</span>
          <button type="button" className="link" disabled={busy !== null || locked} onClick={() => void act("cancel")}>
            <BusyLabel busy={busy === "cancel"} label="Cancel update" working="Cancelling update" /></button>
        </p>
      </div> : required ? <p role={update?.state === "failed" ? "alert" : undefined} className={`text-meta ${update?.state === "failed" ? "text-danger" : "text-muted"}`}>
        Voice needs an update.{update?.state === "failed" ? ` ${reason(update.reason)}` : ""}
      </p> : update?.state === "failed" ? <p role="status" className="text-meta text-muted">
        Update did not finish. {reason(update.reason)}
      </p> : !update ? <p className="text-meta text-muted">While you dictate, the speech process uses about 2 GB of memory.</p> : null}
      {updating ? use : <div className="voice-host-actions">
        {use}
        {required || update?.state === "cancelled" ? button("setup", "Update voice", "Starting update", required ? "btn-primary" : "")
          : update?.state === "failed" ? button("setup", "Retry update", "Starting update") : null}
        {button("remove", `Remove voice (${size})`, "Removing voice")}
      </div>}
    </> : <>
      <p className={`text-meta ${host.state === "failed" ? "text-danger" : "text-muted"}`} role={host.state === "failed" ? "alert" : undefined}>
        {host.state === "failed" ? `Setup did not finish. ${host.reason ?? ""}`
          : `Needs a one-time download of about ${size}, checked against this release.`}
      </p>
      {button("setup", host.state === "failed" ? "Retry" : "Set up voice", "Starting setup", "btn-primary")}
    </>}
    {error ? <p role="alert" className="text-meta text-danger">{error}</p> : null}
    <p className="text-meta text-muted">Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.</p>
  </div>;
}

type VoiceSave = { action: string; backend: VoiceBackend; status: "saving" | "saved" } | { action: string; backend: VoiceBackend; status: "failed"; error: Error };
const useLabels: Record<VoiceBackend, string> = { host: "Use this computer", browser: "Use browser recognition", service: "Use your speech service" };

/*
 * Each transcription is a card. The one in use carries an In use check; the others offer one Use button,
 * which saves at once. Only the speech service has fields, so only its edits wait for a Save, and leaving it
 * asks first because that deletes its address and key. Progress, Saved and errors appear in the card acted on.
 */
function VoiceForm({ saved, reload }: { saved: VoiceSettings; reload: () => void }) {
  const client = useQueryClient();
  // The setting this form edits against: a ref for handlers, state so the page shows it as soon as it changes.
  const committed = useRef(saved);
  const [baseline, setBaseline] = useState(saved);
  const commit = useCallback((value: VoiceSettings) => { committed.current = value; setBaseline(value); }, []);
  const [host, setHost] = useState(saved.host);
  const dirty = useRef(false);
  const [opened, setOpened] = useState(false);
  const [confirming, setConfirming] = useState<VoiceBackend | null>(null);
  const [url, setUrl] = useState(saved.service?.url ?? "");
  const [model, setModel] = useState(saved.service?.model ?? "");
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const reset = useCallback((value: VoiceSettings) => {
    dirty.current = false;
    setUrl(value.service?.url ?? ""); setModel(value.service?.model ?? "");
    setKey(""); setRemoveKey(false); setInvalid(false); setOpened(false); setConfirming(null);
  }, []);
  const synchronize = useCallback((value: VoiceSettings) => {
    const unchanged = value.backend === committed.current.backend && value.selection === committed.current.selection;
    setHost(value.host);
    // Preserve the edit's expected selection. A concurrent backend change must conflict at Save,
    // rather than silently replacing the draft or granting it the new destination's saved key.
    if (unchanged || !dirty.current) {
      commit(value);
      if (!unchanged) reset(value);
    }
  }, [commit, reset]);
  useEffect(() => synchronize(saved), [saved, synchronize]);
  const [save, setSave] = useState<VoiceSave | null>(null);
  const [hostBusy, setHostBusy] = useState<HostAction | null>(null);
  const active = baseline.backend;
  const saving = save?.status === "saving";
  const busy = saving || hostBusy !== null;
  // A control that disappears after a press hands focus to its successor (a CSS selector inside the page).
  const section = useRef<HTMLElement>(null);
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => {
    if (!focus) return;
    section.current?.querySelector<HTMLElement>(focus)?.focus();
    setFocus(null);
  }, [focus]);
  const submit = async (update: VoiceUpdate, action: string) => {
    setSave({ action, backend: update.backend, status: "saving" });
    try {
      await client.cancelQueries({ queryKey });
      const value = await saveVoiceSettings(update);
      // Use the cache's structurally shared value so its later notification cannot reset a new edit.
      commit(client.setQueryData<VoiceSettings>(queryKey, value)!);
      updateVoiceSettings(value);
      setHost(value.host);
      reset(value);
      setSave({ action, backend: update.backend, status: "saved" });
      setFocus(`#voice-${update.backend}`);
    } catch (error) {
      setSave({ action, backend: update.backend, status: "failed", error: error as Error });
    }
  };
  const use = (backend: VoiceBackend) => {
    if (active === "service" && confirming !== backend) { setConfirming(backend); setSave(null); setFocus(`[data-action="use-${backend}"]`); return; }
    void submit({ backend, selection: committed.current.selection }, `use-${backend}`);
  };
  const saveService = () => {
    try {
      const parsed = new URL(url.trim());
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error();
    } catch { setInvalid(true); return; }
    setInvalid(false);
    void submit({ backend: "service", selection: committed.current.selection, url: url.trim(), model: model.trim(), key, remove_key: removeKey }, "service");
  };
  const edit = () => { dirty.current = true; setSave(null); };
  const showFields = active === "service" || opened;
  const edited = active !== "service" || url.trim() !== (baseline.service?.url ?? "")
    || model.trim() !== (baseline.service?.model ?? "") || Boolean(key) || removeKey;
  const hostChanged = (value: VoiceSettings) => {
    synchronize(client.setQueryData<VoiceSettings>(queryKey, value)!);
    updateVoiceSettings(value);
  };
  // Buttons stay focusable while a save runs: aria-disabled, and presses ignored.
  const button = (action: string, label: string, onPress: () => void, { primary = false, describedBy }: { primary?: boolean; describedBy?: string } = {}) =>
    <button type={action === "service" ? "submit" : "button"} className={`btn ${primary ? "btn-primary" : ""}`} data-action={action}
      aria-disabled={busy || undefined} aria-describedby={describedBy}
      onClick={(event) => { if (action === "service") return; event.preventDefault(); if (!busy) onPress(); }}>
      <BusyLabel busy={saving && save?.action === action} label={label} working="Saving…" />
    </button>;
  const outcome = (backend: VoiceBackend) => {
    if (save?.backend !== backend) return null;
    if (save.status !== "failed") return null;
    const stale = save.error instanceof ApiError && save.error.status === 409;
    const retry = save.action === "service" ? saveService : () => use(backend);
    return <p role="alert" className="text-meta text-danger">
      {stale ? "Voice settings changed." : save.error instanceof ApiError && save.error.message && !/^HTTP \d/.test(save.error.message) ? save.error.message : "Could not save voice settings."}{" "}
      <button type="button" className="link" onClick={() => stale ? reload() : retry()}>{stale ? "Reload settings" : "Retry"}</button>
    </p>;
  };
  return <>
    <section ref={section} className="settings-card" aria-labelledby="voice-transcription">
      <h2 id="voice-transcription">Transcription</h2>
      <p className="sr-only" role="status">{save?.status === "saved" ? "Saved." : ""}</p>
      {(["host", "browser", "service"] as const).map((backend) => <div className="voice-option" key={backend}>
        <div className="voice-option-head">
          <h3 id={`voice-${backend}`} tabIndex={-1}>{labels[backend]}</h3>
          {active === backend ? <span className="voice-in-use"><svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 10.5 8 14l7.5-8" /></svg>In use</span> : null}
        </div>
        <p className="text-muted text-meta">{explanations[backend]}</p>
        {backend === "service" && saved.error ? <p role="alert" className="text-meta text-danger">{INVALID_SERVICE}</p> : null}
        {active !== backend && confirming === backend ? <div className="voice-confirm">
          <p id={`voice-confirm-${backend}`} className="text-meta">Switching removes your speech service’s address and key.</p>
          <div className="voice-actions">
            {button(`use-${backend}`, "Remove service and switch", () => use(backend), { primary: true, describedBy: `voice-confirm-${backend}` })}
            {button("keep-service", "Keep service", () => { setConfirming(null); setSave(null); setFocus(`[data-action="use-${backend}"]`); })}
          </div>
        </div> : active !== backend && backend === "browser" ? <div className="voice-actions">
          {button(`use-${backend}`, useLabels[backend], () => use(backend))}
        </div> : backend === "service" && !showFields ? <div className="voice-actions">
          {button("open-service", useLabels.service, () => { setOpened(true); setSave(null); })}
        </div> : null}
        {backend === "host" ? <HostVoicePanel host={host} onChange={hostChanged} locked={saving} busy={hostBusy} setBusy={setHostBusy}
          use={active !== "host" && confirming !== "host" && host.state === "ready" ? button("use-host", useLabels.host, () => use("host")) : null} /> : null}
        {backend === "service" && showFields ? <form className="choice-fields service-fields" noValidate onSubmit={(event) => { event.preventDefault(); if (!busy) saveService(); }}>
          <label htmlFor="voice-url">Transcription URL</label>
          <input id="voice-url" type="url" autoComplete="off" autoFocus={opened || undefined} value={url} placeholder="https://speech.example.test/v1/audio/transcriptions" aria-invalid={invalid || undefined} aria-describedby={invalid ? "voice-url-error" : undefined} onChange={(event) => { edit(); setUrl(event.target.value); setKey(""); setRemoveKey(false); }} />
          {invalid ? <p id="voice-url-error" role="alert" className="text-meta text-danger">Enter a complete http:// or https:// transcription URL.</p> : null}
          {url.trim().toLowerCase().startsWith("http:") ? <p className="text-meta text-muted">This connection is unencrypted. Audio and any key travel over HTTP.</p> : null}
          <details><summary>Key or model (optional)</summary>
            <label htmlFor="voice-key">API key</label>
            <input id="voice-key" type="password" autoComplete="new-password" value={key} placeholder={baseline.service?.key_present && url.trim() === baseline.service.url ? "Key saved — leave blank to keep" : undefined} onChange={(event) => { edit(); setKey(event.target.value); setRemoveKey(false); }} />
            {baseline.service?.key_present && url.trim() === baseline.service.url ? <label className="service-remove-key"><input type="checkbox" checked={removeKey} onChange={(event) => { edit(); setRemoveKey(event.target.checked); setKey(""); }} /> Remove saved key</label> : null}
            <label htmlFor="voice-model">Model</label><input id="voice-model" value={model} autoComplete="off" onChange={(event) => { edit(); setModel(event.target.value); }} />
          </details>
          <a className="service-help" href="https://github.com/mburakyucel/altitude/blob/main/docs/SETUP.md#your-speech-service" target="_blank" rel="noreferrer">How to run one</a>
          {edited ? <div className="voice-actions">
            {button("service", active === "service" ? "Save" : "Use this service", saveService, { primary: true })}
            {button("cancel-service", "Cancel", () => { commit(saved); reset(saved); setSave(null); setFocus(active === "service" ? "#voice-url" : '[data-action="open-service"]'); })}
          </div> : null}
        </form> : null}
        {outcome(backend)}
      </div>)}
    </section>
    <VoiceDiagnostics />
  </>;
}

/** The project Settings was opened from: its L3 choice and routing, one tap from its page. */
function ThisProject({ name, state }: { name: string; state: unknown }) {
  const defaults = useProjectDefaults(name);
  const data = defaults.data;
  const routing = !data ? "" : data.l2_engine ? `tasks only on ${engineName(data, data.l2_engine)}`
    : data.l2_preference ? `tasks prefer ${engineName(data, data.l2_preference)}` : "routing Auto";
  return <Group title="This project">
    <Link className="settings-row" to={`/settings/projects/${encodeURIComponent(name)}`} state={state}>
      <span><strong>{name}</strong>{" "}<small>{data ? routing : "Routing, setup and removal"}</small></span><span aria-hidden>›</span>
    </Link>

  </Group>;
}

const engineName = (data: ProjectDefaults, engine: string) => data.engines.find((e) => e.value === engine)?.label ?? engine;

/** Every managed project's settings, and the folder First run lists. */
function ProjectsPage({ state }: { state: unknown }) {
  const overview = useOverview();
  const names = managedProjects(overview.data).map((row) => row.name);
  const roots = overview.data?.roots ?? [];
  return <>
    <p className="text-meta text-muted">Each project's engine routing, setup and removal.</p>
    {overview.isPending ? <p><StatusMark label="Loading projects…" /></p>
      : overview.isError && !overview.data ? <p role="alert" className="text-danger">Could not load projects. <button className="link" onClick={() => void overview.refetch()}>Retry</button></p>
        : <div className="settings-group">
          {names.map((name) => <Link key={name} className="settings-row" to={`/settings/projects/${encodeURIComponent(name)}`} state={state}>
            <span><strong>{name}</strong></span><span aria-hidden>›</span>
          </Link>)}
          {names.length === 0 ? <p className="settings-row text-muted">No project yet.</p> : null}
        </div>}
    <Group title="Folder">
      <Link className="settings-row" to="/settings/projects-folder" state={state}>
        <span><strong>Projects folder</strong>{" "}<small>{roots.join(" and ") || "Loading…"} · First run offers the folders directly inside it</small></span><span aria-hidden>›</span>
      </Link>
    </Group>
  </>;
}

/** One titled group of rows on the Settings overview. */
function Group({ title, children }: { title: string; children: ReactNode }) {
  return <section className="settings-section" aria-label={title}>
    <h2>{title}</h2>
    <div className="settings-group">{children}</div>
  </section>;
}

/** The projects folder: First run offers the folders directly inside it. */
function ProjectsFolderForm({ roots }: { roots: string[] }) {
  const client = useQueryClient();
  const [save, setSave] = useState<{ status: "idle" | "saving" | "saved" } | { status: "failed"; path: string; error: Error }>({ status: "idle" });
  const choose = async (path: string) => {
    setSave({ status: "saving" });
    try {
      await saveProjectsFolder(path);
      await client.invalidateQueries({ queryKey: ["overview"] });
      setSave({ status: "saved" });
    } catch (error) {
      setSave({ status: "failed", path, error: error as Error });
    }
  };
  return <>
    <dl className="settings-card settings-network"><dt>Current</dt><dd>{roots.join(" and ") || "Loading…"}</dd></dl>
    <FolderBrowser action="Use" allowHome busy={save.status === "saving"} busyLabel="Saving…" onChoose={(path) => void choose(path)} />
    {save.status === "saved" ? <StatusMark busy={false} label="Saved." /> : null}
    {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
      <button type="button" className="link" onClick={() => void choose(save.path)}>Retry</button></p> : null}
  </>;
}

/** One on/off setting for this computer; a failed change keeps the switch where it was and says why. */
function MachineSwitch({ id, title, detail, enabled, unavailable, save: send }: {
  id: string; title: string; detail: string; enabled: boolean | undefined; unavailable?: string | null;
  save: (on: boolean) => Promise<unknown>;
}) {
  const [save, setSave] = useState<{ status: "idle" | "saving" } | { status: "failed"; error: Error }>({ status: "idle" });
  const [showReason, setShowReason] = useState(false);
  const change = async (on: boolean) => {
    setSave({ status: "saving" });
    try {
      await send(on);
      setSave({ status: "idle" });
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
    }
  };
  return <div className="settings-row settings-switch-row">
    <label htmlFor={id}>
      <strong>{title}</strong>{" "}
      <small id={`${id}-reason`} className={unavailable && !showReason ? "sr-only" : undefined}>{unavailable ? `Not available here: ${unavailable}.` : detail}</small>
      {save.status === "failed" ? <small role="alert" className="text-danger">{save.error.message}</small> : null}
    </label>
    {unavailable ? <button id={id} type="button" role="switch" className="settings-switch" aria-checked="false" aria-label={title}
      aria-disabled="true" aria-describedby={`${id}-reason`} onClick={() => setShowReason(!showReason)} />
      : <input id={id} type="checkbox" role="switch" className="settings-switch" checked={enabled ?? false}
        disabled={enabled === undefined || save.status === "saving"} onChange={(event) => void change(event.target.checked)} />}
  </div>;
}

const reveals: Record<Reveal, string> = { task: "Project and task", project: "Project only", none: "Only that a decision waits" };
const paces: Record<Preferences["every"], string> = { 0: "Every new decision", 900: "At most every 15 minutes", 3600: "At most once an hour" };

/**
 * Decision alerts on this device (SPEC.md §3.15): the switch, then what a banner shows, how often one arrives
 * and whether it sounds. Each device keeps its own, so the choices appear only once alerts are on here.
 */
function NotificationsGroup({ overview }: { overview: Overview | undefined }) {
  const state = useAlertState();
  const note = useAlertNote(state);
  const chosen = usePreferences();
  const [asking, setAsking] = useState(false);
  const [showReason, setShowReason] = useState(false);
  const [save, setSave] = useState<{ status: "idle" | "saving"; change?: Partial<Preferences> } | { status: "failed"; error: Error }>({ status: "idle" });
  const settled = state === "unsupported" || state === "blocked";
  const project = managedProjects(overview).at(0)?.name ?? "atlas";
  // The change being saved shows at once; a refusal puts the saved choice back.
  const shown = chosen ? { ...chosen, ...(save.status === "saving" ? save.change : {}) } : undefined;
  const choose = async (change: Partial<Preferences>) => {
    if (!chosen) return;
    setSave({ status: "saving", change });
    try {
      await savePreferences({ ...chosen, ...change });
      setSave({ status: "idle" });
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
    }
  };
  const busy = save.status === "saving";
  const shows = !shown ? "" : shown.reveal === "task" ? `“${project} needs a decision” and the task name`
    : shown.reveal === "project" ? `“${project} needs a decision”` : "“A decision needs you”";
  return <section className="settings-section" aria-label="Notifications">
    <h2>Notifications</h2>
    <div className="settings-group">
      <div className="settings-row settings-switch-row">
        <label htmlFor="alerts-switch"><strong>Decision alerts</strong>{" "}
          <small id="alerts-switch-note" className={settled && !showReason ? "sr-only" : undefined}>{note}</small></label>
        {settled ? <button id="alerts-switch" type="button" role="switch" className="settings-switch" aria-checked="false"
          aria-label="Decision alerts" aria-disabled="true" aria-describedby="alerts-switch-note" onClick={() => setShowReason(!showReason)} />
          : <input id="alerts-switch" type="checkbox" role="switch" className="settings-switch" aria-label="Decision alerts"
            checked={state === "on"} disabled={asking} aria-describedby="alerts-switch-note" onChange={(event) => {
              if (!event.target.checked) return disableAlerts();
              setAsking(true);
              void enableAlerts(overview?.queue ?? []).catch(disableAlerts).finally(() => setAsking(false));
            }} />}
      </div>
      {state === "on" && shown ? <>
        <label className="settings-row">
          <span><strong>Show</strong>{" "}<small>{shows}</small></span>
          <select className="settings-select" aria-label="Show" value={shown.reveal} disabled={busy}
            onChange={(event) => void choose({ reveal: event.target.value as Reveal })}>
            {Object.entries(reveals).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="settings-row">
          <span><strong>How often</strong>{shown.every ? <>{" "}<small>Decisions in between arrive together in the next alert.</small></> : null}</span>
          <select className="settings-select" aria-label="How often" value={shown.every} disabled={busy}
            onChange={(event) => void choose({ every: Number(event.target.value) as Preferences["every"] })}>
            {Object.entries(paces).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <div className="settings-row settings-switch-row">
          <label htmlFor="alerts-sound"><strong>Sound</strong></label>
          <input id="alerts-sound" type="checkbox" role="switch" className="settings-switch" checked={shown.sound} disabled={busy}
            onChange={(event) => void choose({ sound: event.target.checked })} />
        </div>
      </> : null}
    </div>
    {state === "on" && chosen === null ? <p role="alert" className="text-meta text-danger">This browser cannot keep alert choices here, so alerts name nothing.</p> : null}
    {busy ? <StatusMark label="Saving…" /> : null}
    {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}</p> : null}
  </section>;
}

/** The operator's terminal, off after install. */
function TerminalSwitch({ machine }: { machine: Machine | undefined }) {
  const client = useQueryClient();
  return <>
    <MachineSwitch id="terminal-switch" title="Terminal" enabled={machine?.terminal} unavailable={machine?.terminal_unavailable}
      detail="Every paired browser can run commands as you on this computer. Terminals close when Altitude restarts or when you turn this off."
      save={async (on) => {
        client.setQueryData(["machine"], await saveTerminalAccess(on));
        await client.invalidateQueries({ queryKey: ["terminal"] });
      }} />
  </>;
}

/** The agents' validation runs, on after install; absent where this computer cannot run them. */
function ValidationSwitch({ machine }: { machine: Machine }) {
  const client = useQueryClient();
  return <>
    <MachineSwitch id="validation-switch" title="Validation runs" enabled={machine.validation}
      detail="Agents test their unmerged changes in throwaway, isolated runs on this computer, and each run is recorded on its task. Turning this off stops a running one."
      save={async (on) => { client.setQueryData(["machine"], await saveValidationAccess(on)); }} />
  </>;
}

/** A date without its time: a device's last use is recorded at most daily. */
/** One paired device; Remove asks once before it signs the device out. */
function DeviceRow({ device, current, onRemoved }: { device: Device; current: boolean; onRemoved: () => void }) {
  const [state, setState] = useState<{ status: "idle" | "confirm" | "removing" } | { status: "failed"; error: Error }>({ status: "idle" });
  const remove = async () => {
    setState({ status: "removing" });
    try {
      await revokeDevice(device.id);
      onRemoved();
    } catch (error) {
      setState({ status: "failed", error: error as Error });
    }
  };
  return <li className="device-row">
    <span className="device-name"><span className="device-title"><strong>{device.name}</strong>{current ? <span className="device-current">This device</span> : null}</span>
      <small>Paired {dateText(device.paired)} · last used {dateText(device.used)}</small>
      {state.status === "confirm" ? <small className="device-confirm">{current ? "This browser will need a new code to open Altitude again." : "It will need a new code to open Altitude again."}</small> : null}
      {state.status === "failed" ? <small role="alert" className="text-danger">{state.error.message}</small> : null}
    </span>
    {state.status === "confirm" || state.status === "removing" ? <span className="device-actions" role="group" aria-label={`Remove ${device.name}?`}
      onKeyDown={(event) => { if (event.key === "Escape" && state.status === "confirm") setState({ status: "idle" }); }}>
      <button type="button" className="btn" autoFocus disabled={state.status === "removing"} onClick={() => setState({ status: "idle" })}>Cancel</button>
      <button type="button" className="btn btn-danger" disabled={state.status === "removing"} onClick={() => void remove()}><BusyLabel busy={state.status === "removing"} label="Remove device" working="Removing…" /></button>
    </span> : <button type="button" className="btn" onClick={() => setState({ status: "confirm" })}>Remove</button>}
  </li>;
}

/** Paired devices with Remove, and a one-time code for pairing another one. */
function DevicesPage() {
  const client = useQueryClient();
  const devices = useDevices();
  const network = useMachine().data?.network;
  const local = network && !("error" in network) && network.scope === "local";
  const [code, setCode] = useState<{ status: "idle" | "making" } | { status: "made"; code: PairingCode } | { status: "failed"; error: Error }>({ status: "idle" });
  const make = async () => {
    setCode({ status: "making" });
    try {
      setCode({ status: "made", code: await makePairingCode() });
    } catch (error) {
      setCode({ status: "failed", error: error as Error });
    }
  };
  return <>
    <p className="text-meta text-muted">Browsers that can open Altitude. Each pairs once with a one-time code and stays paired until you remove it here. Removing a device signs it out at once.</p>
    {devices.isPending ? <p><StatusMark label="Loading devices…" /></p>
      : devices.isError ? <p role="alert" className="text-danger">Could not load devices. <button className="link" onClick={() => void devices.refetch()}>Retry</button></p>
        : <ul className="settings-card device-list" aria-label="Paired devices">
          {devices.data.devices.map((device) => <DeviceRow key={device.id} device={device} current={device.id === devices.data.current}
            onRemoved={() => void client.invalidateQueries({ queryKey: ["devices"] })} />)}
          {devices.data.devices.length === 0 ? <li className="text-muted">No browser is paired. This page is open through this computer’s own key.</li> : null}
        </ul>}
    <section className="settings-card device-pair" aria-label="Pair another device">
      <h2>Pair another device</h2>
      {code.status === "made" ? <>
        <p className="device-code" aria-label="Pairing code">{code.code.code}</p>
        <p className="text-meta text-muted">Works once, for the next {code.code.minutes} minutes. {local ? "On the hosting computer," : "On the other device,"}{" "}
          {code.code.address ? <>open <span className="device-address">{code.code.address}</span> and type it.</> : "type it on the Pair this device screen."}</p>
        {local ? <p className="text-meta text-muted">This URL works only on the hosting computer. A phone cannot use its localhost address, so there is no phone QR. <Link className="link" to="/settings/network">Set up another device</Link>.</p> : null}
        {code.code.address && code.code.qr ? <>
          <QRCode rows={code.code.qr} label={`QR code for ${code.code.address}`} />
          <p className="text-meta text-muted">Scan with a phone to open Altitude.</p>
        </> : null}
        {code.code.certificate ? <>
          <p className="text-meta text-muted">Before trusting the certificate there, check it is named “{code.code.certificate.name}” with this SHA-256:</p>
          <p className="device-check" aria-label="Certificate SHA-256">{code.code.certificate.sha256.map((row) => <span key={row}>{row}</span>)}</p>
        </> : null}
        <button type="button" className="btn" onClick={() => void make()}>Make a new code</button>
      </> : <>
        <p className="text-meta text-muted">Make a one-time code here, or run <code>alt pair</code> on the computer running Altitude. A new code cancels the previous one.</p>
        <button type="button" className="btn btn-primary" disabled={code.status === "making"} onClick={() => void make()}><BusyLabel busy={code.status === "making"} label="Make a pairing code" working="Making a code…" /></button>
        {code.status === "failed" ? <p role="alert" className="text-meta text-danger">{code.error.message}</p> : null}
      </>}
    </section>
    {devices.data?.certificate ? <CertificateCard certificate={devices.data.certificate} /> : null}
  </>;
}

/** The CA devices trust (SPEC.md §3.15): Set up a device's QR code, and what a device must match before installing it. */
function CertificateCard({ certificate }: { certificate: Certificate }) {
  if ("error" in certificate) {
    return <section className="settings-card device-certificate" aria-label="Certificate">
      <h2>Certificate</h2>
      <p role="alert" className="text-meta text-danger">Could not read the certificate: {certificate.error}</p>
    </section>;
  }
  const pairs = certificate.sha256.split(":");
  const rows = [0, 8, 16, 24].map((start) => pairs.slice(start, start + 8).join(" "));
  return <section className="settings-card device-certificate" aria-label="Certificate">
    <h2>Certificate</h2>
    <p className="text-meta text-muted">Set up HTTPS trust on Linux, macOS, iPhone, iPad or Android. Open a setup link and QR code here, or run <code>alt tls-share</code> on the computer running Altitude.</p>
    <DeviceSetup />
    <p className="text-meta text-muted">Before trusting the downloaded certificate, check that its name and SHA-256 match these.</p>
    <dl className="settings-network">
      <dt>Name</dt><dd>{certificate.name}</dd>
      <dt>SHA-256</dt><dd className="certificate-fingerprint">{rows.join("\n")}</dd>
      <dt>Trusting it allows</dt><dd>{certificate.scope}</dd>
      <dt>Expires</dt><dd>{dateText(certificate.expires)}</dd>
    </dl>
  </section>;
}

type ShareState = { status: "idle" | "opening" | "closed" } | { status: "failed"; error: Error }
  | { status: "open" | "closing"; share: PhoneShare; until: number; error?: Error };

/** Set up a device: the service's ten-minute share window as a QR code with its time left. Close confirms
 * only once the service has closed the link; the end of the ten minutes closes it on the service, and
 * leaving the page closes it too, even while it is still opening. */
function DeviceSetup() {
  const [state, setState] = useState<ShareState>({ status: "idle" });
  const [now, setNow] = useState(() => Date.now());
  const live = useRef<{ mounted: boolean; link: string | null }>({ mounted: true, link: null });
  useEffect(() => {
    const current = live.current;
    current.mounted = true;
    return () => {
      current.mounted = false;
      if (current.link) void closePhoneShare(current.link).catch(() => undefined);
      current.link = null;
    };
  }, []);
  const shown = state.status === "open" || state.status === "closing";
  useEffect(() => {
    if (!shown) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [shown]);
  const left = shown ? Math.max(0, Math.ceil((state.until - now) / 1000)) : 0;
  useEffect(() => {
    if (shown && left === 0) {
      live.current.link = null;
      setState({ status: "closed" });
    }
  }, [shown, left]);
  const open = async () => {
    setState({ status: "opening" });
    try {
      const share = await openPhoneShare();
      if (!live.current.mounted) {
        void closePhoneShare(share.link).catch(() => undefined);
        return;
      }
      live.current.link = share.link;
      const opened = Date.now();
      setNow(opened);
      setState({ status: "open", share, until: opened + share.seconds * 1000 });
    } catch (error) {
      if (live.current.mounted) setState({ status: "failed", error: error as Error });
    }
  };
  if (shown) {
    const close = async () => {
      setState({ ...state, status: "closing", error: undefined });
      try {
        await closePhoneShare(state.share.link);
        live.current.link = null;
        if (live.current.mounted) setState({ status: "closed" });
      } catch (error) {
        if (live.current.mounted) setState({ ...state, status: "open", error: error as Error });
      }
    };
    const minutes = Math.floor(left / 60), seconds = String(left % 60).padStart(2, "0");
    return <div className="phone-share">
      <QRCode rows={state.share.qr} label={`QR code for ${state.share.link}`} />
      <p className="text-meta">Open setup on this device, enter the link on another computer, or scan the QR with a phone. Keep this Settings page open while downloading.</p>
      {state.status === "open" && <a className="btn btn-primary" href={state.share.link} target="_blank" rel="noopener noreferrer">Open setup page</a>}
      <p className="text-meta text-muted phone-share-link">{state.share.link}</p>
      <div className="phone-share-time">
        <span role="timer" aria-live="off">Closes in {minutes}:{seconds}</span>
        <button type="button" className="btn" disabled={state.status === "closing"} onClick={() => void close()}><BusyLabel busy={state.status === "closing"} label="Close" working="Closing…" /></button>
      </div>
      {state.error ? <p role="alert" className="text-meta text-danger">The link is still open: {state.error.message}</p> : null}
    </div>;
  }
  return <>
    <button type="button" className="btn btn-primary" disabled={state.status === "opening"} onClick={() => void open()}><BusyLabel busy={state.status === "opening"} label="Set up a device" working="Opening…" /></button>
    {state.status === "closed" ? <StatusMark label="The link is closed." busy={false} /> : null}
    {state.status === "failed" ? <p role="alert" className="text-meta text-danger">{state.error.message}</p> : null}
  </>;
}

/** A QR code from the service's module rows, dark on white with the four-module quiet zone a camera needs. */
function QRCode({ rows, label }: { rows: string[]; label: string }) {
  const size = rows.length + 8;
  const path = rows.flatMap((row, y) => [...row].map((bit, x) => bit === "1" ? `M${x + 4} ${y + 4}h1v1h-1z` : "")).join("");
  return <svg className="phone-share-qr" role="img" aria-label={label} viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges">
    <rect width={size} height={size} fill="#fff" />
    <path d={path} fill="#000" />
  </svg>;
}

/** An installed copy's version, retry and check/automatic preferences. */
function VersionRows({ update }: { update: Update }) {
  const client = useQueryClient();
  const [save, setSave] = useState<{ status: "idle" | "saving" } | { status: "failed"; error: Error }>({ status: "idle" });
  const change = async (on: boolean, automatic = false) => {
    setSave({ status: "saving" });
    try {
      const { update: status, ...machine } = await (automatic ? saveUpdateAutomatic(on) : saveUpdateCheck(on));
      client.setQueryData(["machine"], machine);
      client.setQueryData<Overview>(["overview"], (overview) => overview && { ...overview, update: status });
      setSave({ status: "idle" });
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
    }
  };
  const available = update.available;
  const failed = update.attempt?.state === "failed" && update.attempt.version === available?.version ? update.attempt : null;
  const retry = async () => {
    setSave({ status: "saving" });
    try {
      const status = await startUpdate(failed!.version);
      client.setQueryData<Overview>(["overview"], (overview) => overview && { ...overview, update: status });
      setSave({ status: "idle" });
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
    }
  };
  if (update.managed === "image") return <div className="settings-row settings-version">
    <span><strong>Version</strong>{" "}<small>{update.current} · {update.reason}</small></span>
  </div>;
  return <>
    <div className="settings-row settings-version">
      <span><strong>Version</strong>{" "}<small>{update.current}{available ? <> · {update.automatic_pending && !failed ? automaticText(available.version, update.waiting_for) : `${available.version} is available`} · <a href={available.notes} target="_blank" rel="noreferrer">What’s new</a></> : ""}</small></span>
      {available && update.command ? <Command text={update.command} /> : null}
    </div>
    {failed ? <div className="settings-row">
      <span>The update to {failed.version} did not finish. {failed.error} Altitude {update.current} keeps running.</span>
      <button type="button" className="btn" disabled={save.status === "saving"} onClick={() => void retry()}>Try again</button>
    </div> : null}
    <div className="settings-row settings-switch-row">
      <label htmlFor="update-check-switch">
        <strong>Check for new versions</strong>{" "}
        <small>Twice a day Altitude asks GitHub for the latest release. Nothing else is sent. Turning this off also stops automatic updates.</small>
      </label>
      <input id="update-check-switch" type="checkbox" role="switch" className="settings-switch" checked={update.check}
        disabled={save.status === "saving"} onChange={(event) => void change(event.target.checked)} />
    </div>
    <div className="settings-row settings-switch-row">
      <label htmlFor="update-automatic-switch">
        <strong>Automatic updates</strong>{" "}
        <small>Install new versions once current work finishes. Meanwhile new validation and review runs wait, and an open browser terminal holds the update. Turn this off to be asked before installing.</small>
      </label>
      <input id="update-automatic-switch" type="checkbox" role="switch" className="settings-switch" checked={update.automatic}
        disabled={!update.check || save.status === "saving"} onChange={(event) => void change(event.target.checked, true)} />
    </div>
    {save.status === "saving" ? <StatusMark label="Saving…" /> : null}
    {save.status === "failed" ? <p role="alert" className="text-danger">{save.error.message}</p> : null}
  </>;
}

const titles = {
  voice: "Voice input", projects: "Projects", "projects-folder": "Projects folder", name: "Your name",
  prerequisites: "Prerequisites", "incident-reports": "Incident reports", devices: "Devices", network: "Network",
} as const;

/** A machine setting the onboarding flow also sets: its form, saving in place with a Saved confirmation. */
function MachinePage({ page }: { page: "name" | "prerequisites" | "incident-reports" }) {
  const inContainer = useMachine().data?.deployment === "container";
  const [saved, setSaved] = useState(false);
  const status = saved ? <StatusMark busy={false} label="Saved." /> : null;
  const submitRow = (submit: ReactNode) => <div className="onboarding-nav">{submit}{status}</div>;
  if (page === "name") return <>
    <p className="text-meta text-muted">Screens, task records and agents use this name wherever they would otherwise say “the operator”.</p>
    <NameForm save="Save" onSaved={() => setSaved(true)} actions={submitRow} />
  </>;
  if (page === "incident-reports") return <>
    <p className="text-meta text-muted">When Altitude’s machinery fails, it records an incident on this computer. It can also open a GitHub issue for each one.</p>
    <IncidentReportsForm save="Save" onSaved={() => setSaved(true)} actions={submitRow} />
  </>;
  return <>
    <p className="text-meta text-muted">{inContainer ? "These checks run inside the container. Open its shell from a host terminal and run the commands below there, then check again. Host tools and sign-ins do not count." : "What the agents need on the computer running Altitude. Run any missing command in a terminal there, then check again."} Altitude never asks for a password or token in the browser.</p>
    <PrerequisiteList actions={(check) => <div className="onboarding-nav">{check}</div>} />
  </>;
}

/**
 * Settings by destination (SPEC.md §3.15): the project it was opened from, models, projects, voice, devices
 * and access, coding agents and the version. Each row shows its current value and opens its page.
 */
export default function Settings({ page }: { page?: keyof typeof titles }) {
  const { phone } = useViewport();
  const location = useLocation();
  const navigate = useNavigate();
  const overview = useOverview();
  // Follow first-time setup and replacement phases until they settle.
  const settings = useQuery({ queryKey, queryFn: ({ signal }) => readVoiceSettings(signal), refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const host = query.state.data?.host;
      if (!host || host.state === "unavailable") return false;
      return host.state === "setting-up" || (host.update && !["failed", "cancelled"].includes(host.update.state)) ? 1000 : false;
    } });
  const [reloadKey, setReloadKey] = useState(0);
  const state = location.state as { settingsFrom?: string } | null;
  const machine = useMachine();
  const devices = useDevices();
  const roots = overview.data?.roots ?? [];
  const managed = managedProjects(overview.data).map((row) => row.name);
  const from = decodeURIComponent(/^\/projects\/([^/?]+)/.exec(state?.settingsFrom ?? "")?.[1] ?? "");
  const current = managed.includes(from) ? from : undefined;
  useEffect(() => {
    if (settings.data) updateVoiceSettings(settings.data);
  }, [settings.data]);
  const title = page ? titles[page] : "Settings";
  const back = page ? <Link to="/settings" state={state} className="btn settings-back">‹ Settings</Link>
    : <button type="button" className="btn settings-back" onClick={() => navigate(state?.settingsFrom || "/projects", { replace: true })}>‹ Back</button>;
  const voiceRow = settings.isPending ? <p className="settings-row"><StatusMark label="Loading voice settings…" /></p>
    : settings.isError ? <p role="alert" className="settings-row text-danger">Could not load voice settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
      : <Link className="settings-row" to="/settings/voice" state={state}>
        <span><strong>Voice input</strong>{" "}<small>{voiceSummary(settings.data)}</small></span><span aria-hidden>›</span>
      </Link>;
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1 tabIndex={-1}>{title}</h1></header> : null}
    <div className="page settings-page" data-overview={!page || undefined}>
      {!phone ? <>{page || state?.settingsFrom ? back : null}<h1 tabIndex={-1}>{title}</h1></> : null}
      {page === "name" || page === "prerequisites" || page === "incident-reports" ? <MachinePage key={page} page={page} />
        : page === "devices" ? <DevicesPage />
        : page === "network" ? <HostingCard detail />
        : page === "projects" ? <ProjectsPage state={state} />
        : page === "projects-folder" ? <>
        <p className="text-meta text-muted">First run offers the folders directly inside this folder. Altitude lists them only when you open First run or Add a folder; it never looks deeper or reads files.</p>
        <ProjectsFolderForm roots={roots} />
      </> : page === "voice" ? <>
        <p className="text-meta text-muted">Choose how speech becomes text. Applies to every project.</p>
        {settings.isPending ? <p><StatusMark label="Loading settings…" /></p>
          : settings.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
            : <VoiceForm key={reloadKey} saved={settings.data} reload={() => void settings.refetch().then(() => setReloadKey((value) => value + 1))} />}
      </> : <div className="settings-columns">
        <div className="settings-column">
          <div className="settings-group">
            <Link className="settings-row" to="/settings/name" state={state}>
              <span><strong>{machine.data?.operator || "Your name"}</strong>{" "}<small>{machine.data ? machine.data.operator ? "Your name on every screen" : "Not set · screens say “you”" : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
          </div>
          {current ? <ThisProject name={current} state={state} /> : null}
          <ProjectModelSettingsSection />
          <Group title="Projects">
            <Link className="settings-row" to="/settings/projects" state={state}>
              <span><strong>All projects</strong>{" "}<small>{overview.data ? `${managed.length} project${managed.length === 1 ? "" : "s"} · folder ${roots.join(" and ") || "not set"}` : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
          </Group>
        </div>
        <div className="settings-column">
          <Group title="Voice">{voiceRow}</Group>
          <NotificationsGroup overview={overview.data} />
          <Group title="Devices and access">
            <Link className="settings-row" to="/settings/devices" state={state}>
              <span><strong>Devices</strong>{" "}<small>{devices.data ? `${devices.data.devices.length} paired · pair another, certificate` : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
            <TerminalSwitch machine={machine.data} />
            <Link className="settings-row" to="/settings/network" state={state}>
              <span><strong>Network</strong>{" "}<small>{machine.isPending ? "Reading service address…" : networkSummary(machine.data?.network)}</small></span><span aria-hidden>›</span>
            </Link>
          </Group>
          <Group title="Coding agents">
            <Link className="settings-row" to="/settings/prerequisites" state={state}>
              <span><strong>Prerequisites</strong>{" "}<small>GitHub CLI sign-in, coding agents and Git</small></span><span aria-hidden>›</span>
            </Link>
            {machine.data && !machine.data.validation_unavailable ? <ValidationSwitch machine={machine.data} /> : null}
            <Link className="settings-row" to="/settings/incident-reports" state={state}>
              <span><strong>Incident reports</strong>{" "}<small>{machine.data ? machine.data.incident_repository ? `Published to ${machine.data.incident_repository}` : "Kept on this computer" : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
          </Group>
          {overview.data?.update ? <Group title="About"><VersionRows update={overview.data.update} /></Group> : null}
        </div>
      </div>}
    </div>
  </>;
}
