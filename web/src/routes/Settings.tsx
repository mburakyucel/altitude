import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import { Command, IncidentReportsForm, NameForm, PrerequisiteList } from "../components/Onboarding";
import { ApiError, makePairingCode, readVoiceSettings, revokeDevice, saveProjectsFolder, saveTerminalAccess, saveVoiceSettings, useDevices, useMachine, useOverview } from "../data/api";
import type { Device, PairingCode } from "../data/api";
import { managedProjects } from "../shell/projects";
import type { VoiceBackend, VoiceSettings, VoiceUpdate } from "../data/api";
import { updateVoiceSettings } from "../components/voiceBackend";
import { useViewport } from "../shell/breakpoints";
import "./settings.css";

const labels: Record<VoiceBackend, string> = {
  browser: "Browser recognition", endpoint: "Your speech service",
};
const explanations: Record<VoiceBackend, string> = {
  browser: "No setup in supported browsers. Words appear as you speak; English gets punctuation and capitals on this device. Your browser may send audio to its speech service; that service’s privacy policy applies.",
  endpoint: "After you stop, Altitude sends the recording to a speech-to-text service you run or choose, using the standard OpenAI transcription API. It can run on this computer, on another machine on your network, or be a hosted provider. Audio goes only to that address; its storage policy and any charges apply.",
};
const queryKey = ["voice-settings"];
const DEFAULT_MODEL = "whisper-1";

/** A saved key or non-default model belongs to a hosted provider, so its fields stay open. */
const hostedFields = (settings: VoiceSettings) => settings.key_set || (settings.model !== "" && settings.model !== DEFAULT_MODEL);

/** Where recordings go, as the overview row names it: the service's host, never its key. */
function voiceSummary(settings: VoiceSettings) {
  if (settings.backend === "browser") return labels.browser;
  try { return `${labels.endpoint} · ${new URL(settings.url).host}`; } catch { return labels.endpoint; }
}
type SaveState = { status: "idle" } | { status: "saving" | "saved" } | { status: "failed"; value: VoiceUpdate; error: Error };

function VoiceForm({ saved, reload, repository }: { saved: VoiceSettings; reload: () => void; repository?: string }) {
  const client = useQueryClient();
  const committed = useRef(saved);
  const [choice, setChoice] = useState(saved.backend);
  const [url, setUrl] = useState(saved.url);
  const [model, setModel] = useState(saved.model);
  const [key, setKey] = useState("");
  const [keepKey, setKeepKey] = useState(saved.key_set);
  const [hosted, setHosted] = useState(hostedFields(saved));
  useEffect(() => {
    if (saved === committed.current) return;
    committed.current = saved;
    setChoice(saved.backend);
    setUrl(saved.url);
    setModel(saved.model);
    setKey("");
    setKeepKey(saved.key_set);
    setHosted(hostedFields(saved));
  }, [saved]);
  const [save, setSave] = useState<SaveState>({ status: "idle" });
  const reset = () => setSave({ status: "idle" });
  const submit = async (update: VoiceUpdate) => {
    setSave({ status: "saving" });
    try {
      await client.cancelQueries({ queryKey });
      const value = await saveVoiceSettings(update);
      // Use the cache's structurally shared value so its later notification cannot reset a new edit.
      committed.current = client.setQueryData<VoiceSettings>(queryKey, value)!;
      updateVoiceSettings(value);
      setChoice(value.backend);
      setUrl(value.url);
      setModel(value.model);
      setKey("");
      setKeepKey(value.key_set);
      setHosted(hostedFields(value));
      setSave({ status: "saved" });
    } catch (error) {
      setSave({ status: "failed", value: update, error: error as Error });
    }
  };
  const choose = (backend: VoiceBackend) => {
    reset();
    if (backend === "endpoint") setChoice(backend);
    else void submit({ backend, selection: committed.current.selection });
  };
  const endpoint = (): VoiceUpdate => ({
    backend: "endpoint", selection: committed.current.selection, url: url.trim(), model: model.trim(),
    ...(keepKey ? { keep_key: true } : { key }),
  });
  const stale = save.status === "failed" && save.error instanceof ApiError && save.error.status === 409;
  return <>
    <form className="settings-card" onSubmit={(event) => { event.preventDefault(); void submit(endpoint()); }}>
      <fieldset disabled={save.status === "saving"}>
        <legend>Transcription</legend>
        {(["browser", "endpoint"] as const).map((backend) => <div className="voice-option" key={backend}>
          <label className="voice-choice">
            <input type="radio" name="voice-backend" value={backend} checked={choice === backend} onChange={() => choose(backend)} />
            <span>{labels[backend]}</span>
          </label>
          <p className="text-muted text-meta">{explanations[backend]}</p>
          {backend === "endpoint" && choice === "endpoint" ? <div className="voice-endpoint">
            <label>Service URL<input type="url" required value={url} placeholder="http://127.0.0.1:8080/v1/audio/transcriptions" onChange={(event) => { setUrl(event.target.value); setKeepKey(false); reset(); }} /></label>
            <p className="text-meta text-muted">The full address of its <code>/v1/audio/transcriptions</code> endpoint.{repository ? <>{" "}<a href={`https://github.com/${repository}/blob/main/docs/OPERATIONS.md#your-speech-service`} target="_blank" rel="noopener noreferrer">How to run one</a></> : null}</p>
            {hosted ? <>
              <label>Model (optional)<input value={model} placeholder={`Default: ${DEFAULT_MODEL}`} onChange={(event) => { setModel(event.target.value); reset(); }} /></label>
              {keepKey ? <div className="voice-key-set"><span>Key set · never shown</span><button type="button" className="link" onClick={() => { setKeepKey(false); reset(); }}>Replace</button></div>
                : <label>API key (optional)<input type="password" autoComplete="new-password" value={key} onChange={(event) => { setKey(event.target.value); reset(); }} /></label>}
              {!keepKey && saved.key_set ? <p className="text-meta text-muted">Leave blank to remove the stored key. A stored key is never sent to a changed URL.</p> : null}
            </> : <button type="button" className="link voice-hosted" onClick={() => setHosted(true)}>Hosted provider? Add a key or model</button>}
            <p className="text-meta text-muted">Changes are saved only with Save service.</p>
            <button type="submit" className="btn btn-primary">Save service</button>
          </div> : null}
        </div>)}
      </fieldset>
      {save.status === "saving" || save.status === "saved" ? <p role="status" className="text-meta text-muted">{save.status === "saving" ? "Saving…" : "Saved."}</p> : null}
      {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
        <button type="button" className="link" onClick={() => stale ? reload() : void submit(save.value)}>{stale ? "Reload settings" : "Retry"}</button>
      </p> : null}
    </form>
    <p className="text-meta text-muted">Changes apply to your next recording. Altitude keeps no recordings; the speech service that transcribes them controls its own retention.</p>
  </>;
}

/** The project Settings was opened from, or every managed project on a direct visit. */
function ProjectRows({ from, state }: { from?: string; state: unknown }) {
  const overview = useOverview();
  const names = managedProjects(overview.data).map((row) => row.name);
  const current = decodeURIComponent(/^\/projects\/([^/?]+)/.exec(from ?? "")?.[1] ?? "");
  const shown = names.includes(current) ? [current] : names;
  if (!shown.length) return null;
  return <section className="settings-section" aria-label={current && shown[0] === current ? "This project" : "Projects"}>
    <div><h2>{current && shown[0] === current ? "This project" : "Projects"}</h2><p className="text-meta text-muted">Applies only to that project.</p></div>
    {shown.map((name) => <Link key={name} className="settings-row" to={`/settings/projects/${encodeURIComponent(name)}`} state={state}>
      <span><strong>{name}</strong>{" "}<small>L3 engine, models and reasoning effort</small></span><span aria-hidden>›</span>
    </Link>)}
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
    {save.status === "saved" ? <p role="status" className="text-meta text-muted">Saved. First run now lists the folders in {roots.join(" and ")}.</p> : null}
    {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
      <button type="button" className="link" onClick={() => void choose(save.path)}>Retry</button></p> : null}
  </>;
}

/** The operator's terminal, off after install: one switch for this computer. */
function TerminalSwitch({ enabled }: { enabled: boolean | undefined }) {
  const client = useQueryClient();
  const [save, setSave] = useState<{ status: "idle" | "saving" } | { status: "failed"; error: Error }>({ status: "idle" });
  const change = async (on: boolean) => {
    setSave({ status: "saving" });
    try {
      client.setQueryData(["machine"], await saveTerminalAccess(on));
      await client.invalidateQueries({ queryKey: ["terminal"] });
      setSave({ status: "idle" });
    } catch (error) {
      setSave({ status: "failed", error: error as Error });
    }
  };
  return <div className="settings-row settings-switch-row">
    <label htmlFor="terminal-switch">
      <strong>Terminal</strong>{" "}
      <small>Every paired browser can run commands as you on this computer. Terminals close when Altitude restarts or when you turn this off.</small>
      {save.status === "failed" ? <small role="alert" className="text-danger">{save.error.message}</small> : null}
    </label>
    <input id="terminal-switch" type="checkbox" role="switch" className="settings-switch" checked={enabled ?? false}
      disabled={enabled === undefined || save.status === "saving"} onChange={(event) => void change(event.target.checked)} />
  </div>;
}

/** A date without its time: a device's last use is recorded at most daily. */
const day = (at: string) => new Date(at).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });

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
      <small>Paired {day(device.paired)} · last used {day(device.used)}</small>
      {state.status === "confirm" ? <small className="device-confirm">{current ? "This browser will need a new code to open Altitude again." : "It will need a new code to open Altitude again."}</small> : null}
      {state.status === "failed" ? <small role="alert" className="text-danger">{state.error.message}</small> : null}
    </span>
    {state.status === "confirm" || state.status === "removing" ? <span className="device-actions">
      <button type="button" className="btn" disabled={state.status === "removing"} onClick={() => setState({ status: "idle" })}>Cancel</button>
      <button type="button" className="btn btn-danger" disabled={state.status === "removing"} onClick={() => void remove()}>{state.status === "removing" ? "Removing…" : "Remove"}</button>
    </span> : <button type="button" className="btn" onClick={() => setState({ status: "confirm" })}>Remove</button>}
  </li>;
}

/** Paired devices with Remove, and a one-time code for pairing another one. */
function DevicesPage() {
  const client = useQueryClient();
  const devices = useDevices();
  const [code, setCode] = useState<{ status: "idle" | "making" } | { status: "made"; code: PairingCode } | { status: "failed"; error: Error }>({ status: "idle" });
  const make = async () => {
    setCode({ status: "making" });
    try {
      setCode({ status: "made", code: await makePairingCode() });
    } catch (error) {
      setCode({ status: "failed", error: error as Error });
    }
  };
  const link = code.status === "made" ? `${window.location.origin}/pair?code=${code.code.code}` : "";
  return <>
    <p className="text-meta text-muted">Browsers that can open Altitude. Each pairs once with a one-time code and stays paired until you remove it here. Removing a device signs it out at once.</p>
    {devices.isPending ? <p role="status">Loading devices…</p>
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
        <p className="text-meta text-muted">Works once, for the next {code.code.minutes} minutes. On the other device, type it on the Pair this device screen, or open:</p>
        <Command text={link} />
        <button type="button" className="btn" onClick={() => void make()}>Make a new code</button>
      </> : <>
        <p className="text-meta text-muted">Make a one-time code here, or run <code>alt pair</code> on the computer running Altitude. A new code cancels the previous one.</p>
        <button type="button" className="btn btn-primary" disabled={code.status === "making"} onClick={() => void make()}>{code.status === "making" ? "Making a code…" : "Make a pairing code"}</button>
        {code.status === "failed" ? <p role="alert" className="text-meta text-danger">{code.error.message}</p> : null}
      </>}
    </section>
  </>;
}

const titles = {
  voice: "Voice input", "projects-folder": "Projects folder", name: "Your name",
  prerequisites: "Prerequisites", "incident-reports": "Incident reports", devices: "Devices",
} as const;

/** A machine setting the onboarding flow also sets: its form, saving in place with a Saved confirmation. */
function MachinePage({ page }: { page: "name" | "prerequisites" | "incident-reports" }) {
  const [saved, setSaved] = useState(false);
  const status = saved ? <p role="status" className="text-meta text-muted">Saved.</p> : null;
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
    <p className="text-meta text-muted">What the agents need on the computer running Altitude. Run any missing command in a terminal there, then check again. Altitude never asks for a password or token in the browser.</p>
    <PrerequisiteList actions={(check) => <div className="onboarding-nav">{check}</div>} />
  </>;
}

/** Machine settings, then project settings, each as a compact row that opens its page. */
export default function Settings({ page }: { page?: keyof typeof titles }) {
  const { phone } = useViewport();
  const location = useLocation();
  const navigate = useNavigate();
  const overview = useOverview();
  const settings = useQuery({ queryKey, queryFn: readVoiceSettings, refetchOnWindowFocus: false });
  const [reloadKey, setReloadKey] = useState(0);
  const state = location.state as { settingsFrom?: string } | null;
  const voice = page === "voice";
  const machine = useMachine();
  const devices = useDevices();
  const roots = overview.data?.roots ?? [];
  useEffect(() => {
    if (settings.data) updateVoiceSettings(settings.data);
  }, [settings.data]);
  const title = page ? titles[page] : "Settings";
  const back = page ? <Link to="/settings" state={state} className="btn settings-back">‹ Settings</Link>
    : <button type="button" className="btn settings-back" onClick={() => navigate(state?.settingsFrom || "/projects", { replace: true })}>‹ Back</button>;
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1>{title}</h1></header> : null}
    <div className="page settings-page">
      {!phone ? <>{page ? back : null}<h1>{title}</h1></> : null}
      {page === "name" || page === "prerequisites" || page === "incident-reports" ? <MachinePage key={page} page={page} />
        : page === "devices" ? <DevicesPage />
        : page === "projects-folder" ? <>
        <p className="text-meta text-muted">First run offers the folders directly inside this folder. Altitude lists them only when you open First run or Add a folder; it never looks deeper or reads files.</p>
        <ProjectsFolderForm roots={roots} />
      </> : <>
        {voice ? <p className="text-meta text-muted">Choose how speech becomes text. Applies to every project.</p>
          : <div><h2>This machine</h2><p className="text-meta text-muted">Applies to every project in this Altitude installation.</p></div>}
        {settings.isPending ? <p role="status">Loading settings…</p>
          : settings.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
            : voice ? <VoiceForm key={reloadKey} saved={settings.data} repository={machine.data?.altitude_repository} reload={() => void settings.refetch().then(() => setReloadKey((value) => value + 1))} />
              : <Link className="settings-row" to="/settings/voice" state={state}>
                <span><strong>Voice input</strong>{" "}<small>{voiceSummary(settings.data)}</small></span><span aria-hidden>›</span>
              </Link>}
        {!voice ? <>
          <Link className="settings-row" to="/settings/name" state={state}>
            <span><strong>Your name</strong>{" "}<small>{machine.data ? machine.data.operator || "Not set · screens say “you”" : "Loading…"}</small></span><span aria-hidden>›</span>
          </Link>
          <Link className="settings-row" to="/settings/prerequisites" state={state}>
            <span><strong>Prerequisites</strong>{" "}<small>GitHub CLI sign-in, coding agents and Git</small></span><span aria-hidden>›</span>
          </Link>
          <Link className="settings-row" to="/settings/incident-reports" state={state}>
            <span><strong>Incident reports</strong>{" "}<small>{machine.data ? machine.data.incident_repository ? `Published to ${machine.data.incident_repository}` : "Kept on this computer" : "Loading…"}</small></span><span aria-hidden>›</span>
          </Link>
          <Link className="settings-row" to="/settings/devices" state={state}>
            <span><strong>Devices</strong>{" "}<small>{devices.data ? `${devices.data.devices.length} paired · remove one or pair another` : "Loading…"}</small></span><span aria-hidden>›</span>
          </Link>
          <TerminalSwitch enabled={machine.data?.terminal} />
        </> : null}
        {!voice ? <Link className="settings-row" to="/settings/projects-folder" state={state}>
          <span><strong>Projects folder</strong>{" "}<small>{roots.join(" and ") || "Loading…"} · First run offers the folders directly inside it</small></span><span aria-hidden>›</span>
        </Link> : null}
        {!voice ? <section className="settings-card" aria-label="Network">
          <h2>Network</h2><p className="text-meta text-muted">Connection details · view only</p>
          <dl className="settings-network"><dt>Address</dt><dd>{window.location.origin}</dd><dt>HTTPS</dt><dd>{window.location.protocol === "https:" ? "On" : "Off"}</dd></dl>
        </section> : null}
        {!voice ? <ProjectRows from={state?.settingsFrom} state={state} /> : null}
      </>}
    </div>
  </>;
}
