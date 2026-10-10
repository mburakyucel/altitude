import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import { Command, IncidentReportsForm, NameForm, PrerequisiteList } from "../components/Onboarding";
import { ApiError, type Machine, changeHostVoice, closePhoneShare, makePairingCode, openPhoneShare, readVoiceSettings, revokeDevice, saveProjectsFolder, saveTerminalAccess, saveUpdateAutomatic, saveUpdateCheck, saveValidationAccess, saveVoiceSettings, startUpdate, useDevices, useMachine, useOverview, useProjectDefaults } from "../data/api";
import type { Certificate, Device, Overview, PairingCode, PhoneShare, ProjectDefaults, Update } from "../data/api";
import { ModelsDialog, choiceLabel, closedLabel } from "../components/Models";
import { managedProjects } from "../shell/projects";
import type { HostVoice, VoiceBackend, VoiceSettings, VoiceUpdate } from "../data/api";
import { updateVoiceSettings } from "../components/voiceBackend";
import { useViewport } from "../shell/breakpoints";
import VoiceDiagnostics from "../components/VoiceDiagnostics";
import "./settings.css";

const labels: Record<VoiceBackend, string> = { host: "This computer", browser: "Browser recognition" };
const explanations: Record<VoiceBackend, string> = {
  host: "Words appear as you speak, with punctuation and capitals, in English, in every browser. Audio goes from your device to this computer over Altitude’s own connection, is transcribed here and is never stored or sent to another service. If the connection drops, recording continues and your words catch up.",
  browser: "No setup in supported browsers. Words appear as you speak; English gets punctuation and capitals on this device. Your browser may send audio to its speech service; that service’s privacy policy applies.",
};
const queryKey = ["voice-settings"];

function voiceSummary(settings: VoiceSettings) {
  if (settings.backend === "browser") return labels.browser;
  return settings.host.state === "ready" ? labels.host : `${labels.host} · not set up`;
}
type SaveState = { status: "idle" } | { status: "saving" | "saved" } | { status: "failed"; value: VoiceUpdate; error: Error };

const megabytes = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;

/** Host voice's one-time setup on this computer: its state, progress and the one action that fits. */
function HostVoicePanel({ host, onChange }: { host: HostVoice; onChange: (value: VoiceSettings) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (action: "setup" | "cancel" | "remove") => {
    setBusy(true);
    setError("");
    try {
      onChange(await changeHostVoice(action));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (host.state === "unavailable") return <p className="text-meta text-danger">Not available on this computer: {host.reason}.</p>;
  const size = megabytes(host.download_bytes);
  return <div className="voice-host">
    {host.state === "setting-up" ? <>
      <progress max={host.download_bytes} value={host.done_bytes ?? 0} aria-label="Voice setup" />
      <p role="status" className="text-meta text-muted">Setting up… {megabytes(host.done_bytes ?? 0)} of {size}</p>
      <button type="button" className="btn" disabled={busy} onClick={() => void act("cancel")}>Cancel setup</button>
    </> : host.state === "ready" ? <>
      <p className="text-meta text-muted">Ready on this computer. While you dictate, the speech process uses about 2 GB of memory.</p>
      <button type="button" className="btn" disabled={busy} onClick={() => void act("remove")}>Remove voice ({size})</button>
    </> : <>
      <p className={`text-meta ${host.state === "failed" ? "text-danger" : "text-muted"}`} role={host.state === "failed" ? "alert" : undefined}>
        {host.state === "failed" ? `Setup did not finish. ${host.reason ?? ""}`
          : host.state === "outdated" ? `Voice needs an update: a one-time download of about ${size}.`
            : `Needs a one-time download of about ${size}, checked against this release.`}
      </p>
      <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void act("setup")}>{host.state === "failed" ? "Retry" : "Set up voice"}</button>
    </>}
    {error ? <p role="alert" className="text-meta text-danger">{error}</p> : null}
    <p className="text-meta text-muted">Speech model: NVIDIA Parakeet TDT 0.6B v2, licensed CC-BY-4.0.</p>
  </div>;
}

function VoiceForm({ saved, reload }: { saved: VoiceSettings; reload: () => void }) {
  const client = useQueryClient();
  const committed = useRef(saved);
  const [choice, setChoice] = useState(saved.backend);
  useEffect(() => {
    // A new reading of host voice's setup state never resets a choice being saved.
    if (saved === committed.current) return;
    const unchanged = saved.backend === committed.current.backend && saved.selection === committed.current.selection;
    committed.current = saved;
    if (!unchanged) setChoice(saved.backend);
  }, [saved]);
  const [save, setSave] = useState<SaveState>({ status: "idle" });
  const submit = async (update: VoiceUpdate) => {
    setSave({ status: "saving" });
    try {
      await client.cancelQueries({ queryKey });
      const value = await saveVoiceSettings(update);
      // Use the cache's structurally shared value so its later notification cannot reset a new edit.
      committed.current = client.setQueryData<VoiceSettings>(queryKey, value)!;
      updateVoiceSettings(value);
      setChoice(value.backend);
      setSave({ status: "saved" });
    } catch (error) {
      setSave({ status: "failed", value: update, error: error as Error });
    }
  };
  const choose = (backend: VoiceBackend) => {
    setChoice(backend);
    void submit({ backend, selection: committed.current.selection });
  };
  const stale = save.status === "failed" && save.error instanceof ApiError && save.error.status === 409;
  const hostChanged = (value: VoiceSettings) => {
    committed.current = client.setQueryData<VoiceSettings>(queryKey, value)!;
    updateVoiceSettings(value);
  };
  return <>
    <form className="settings-card" onSubmit={(event) => event.preventDefault()}>
      <fieldset disabled={save.status === "saving"}>
        <legend>Transcription</legend>
        {(["host", "browser"] as const).map((backend) => <div className="voice-option" key={backend}>
          <label className="voice-choice">
            <input type="radio" name="voice-backend" value={backend} checked={choice === backend} onChange={() => choose(backend)}
              disabled={backend === "host" && saved.host.state === "unavailable" && choice !== "host"} />
            <span>{labels[backend]}</span>
          </label>
          <p className="text-muted text-meta">{explanations[backend]}</p>
          {backend === "host" && (choice === "host" || saved.host.state !== "absent") ? <HostVoicePanel host={saved.host} onChange={hostChanged} /> : null}
        </div>)}
      </fieldset>
      {save.status === "saving" || save.status === "saved" ? <p role="status" className="text-meta text-muted">{save.status === "saving" ? "Saving…" : "Saved."}</p> : null}
      {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
        <button type="button" className="link" onClick={() => stale ? reload() : void submit(save.value)}>{stale ? "Reload settings" : "Retry"}</button>
      </p> : null}
    </form>
    <p className="text-meta text-muted">Changes apply to your next recording. Altitude keeps no recordings.</p>
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
      <span><strong>{name}</strong>{" "}<small>{data ? `L3: ${choiceLabel(data.l3_choice, data, data.engines)} · ${routing}` : "L3, default models, routing, setup and removal"}</small></span><span aria-hidden>›</span>
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
    <p className="text-meta text-muted">Each project's L3, default models, routing, setup and removal.</p>
    {overview.isPending ? <p role="status">Loading projects…</p>
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

/** New tasks as a Settings row: the same dialog as the control beside the quota. */
function NewTasksRow({ project }: { project?: string }) {
  const [open, setOpen] = useState(false);
  const overview = useOverview();
  const tasks = overview.data?.new_tasks;
  const label = tasks ? closedLabel(tasks.value, tasks.unavailable, tasks, overview.data?.engines) : "Loading…";
  return <>
    <button type="button" className="settings-row" aria-haspopup="dialog" disabled={!tasks} onClick={() => setOpen(true)}>
      <span><strong>New tasks</strong>{" "}<small>{!tasks ? label : tasks.value ? `${label} in every project, until you go back to Auto` : "Auto · each project's own defaults"}</small></span><span aria-hidden>›</span>
    </button>
    {open ? <ModelsDialog project={project} tab="tasks" onClose={() => setOpen(false)} /> : null}
  </>;
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

/** One on/off setting for this computer; a failed change keeps the switch where it was and says why. */
function MachineSwitch({ id, title, detail, enabled, unavailable, save: send }: {
  id: string; title: string; detail: string; enabled: boolean | undefined; unavailable?: string | null;
  save: (on: boolean) => Promise<unknown>;
}) {
  const [save, setSave] = useState<{ status: "idle" | "saving" } | { status: "failed"; error: Error }>({ status: "idle" });
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
      <small>{unavailable ? `Not available here: ${unavailable}.` : detail}</small>
      {save.status === "failed" ? <small role="alert" className="text-danger">{save.error.message}</small> : null}
    </label>
    <input id={id} type="checkbox" role="switch" className="settings-switch" checked={!unavailable && (enabled ?? false)}
      disabled={enabled === undefined || Boolean(unavailable) || save.status === "saving"} onChange={(event) => void change(event.target.checked)} />
  </div>;
}

/** The operator's terminal, off after install. */
function TerminalSwitch({ machine }: { machine: Machine | undefined }) {
  const client = useQueryClient();
  return <>
    {machine?.terminal_unavailable ? <div className="settings-row"><span><strong>Terminal unavailable</strong>{" "}<small>{machine.terminal_unavailable}</small></span></div> : <MachineSwitch id="terminal-switch" title="Terminal" enabled={machine?.terminal}
      detail="Every paired browser can run commands as you on this computer. Terminals close when Altitude restarts or when you turn this off."
      save={async (on) => {
        client.setQueryData(["machine"], await saveTerminalAccess(on));
        await client.invalidateQueries({ queryKey: ["terminal"] });
      }} />}
  </>;
}

/** The agents' validation runs, on after install. */
function ValidationSwitch({ machine }: { machine: Machine | undefined }) {
  const client = useQueryClient();
  return <>
    <MachineSwitch id="validation-switch" title="Validation runs" enabled={machine?.validation}
      unavailable={machine?.validation_unavailable}
      detail="Agents test their unmerged changes in throwaway, isolated runs on this computer, and each run is recorded on its task. Turning this off stops a running one."
      save={async (on) => { client.setQueryData(["machine"], await saveValidationAccess(on)); }} />
  </>;
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
    {state.status === "confirm" || state.status === "removing" ? <span className="device-actions" role="group" aria-label={`Remove ${device.name}?`}
      onKeyDown={(event) => { if (event.key === "Escape" && state.status === "confirm") setState({ status: "idle" }); }}>
      <button type="button" className="btn" autoFocus disabled={state.status === "removing"} onClick={() => setState({ status: "idle" })}>Cancel</button>
      <button type="button" className="btn btn-danger" disabled={state.status === "removing"} onClick={() => void remove()}>{state.status === "removing" ? "Removing…" : "Remove device"}</button>
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
        <p className="text-meta text-muted">Works once, for the next {code.code.minutes} minutes. On the other device,{" "}
          {code.code.address ? <>open <span className="device-address">{code.code.address}</span> and type it.</> : "type it on the Pair this device screen."}</p>
        {code.code.address && code.code.qr ? <>
          <QRCode rows={code.code.qr} label={`QR code for ${code.code.address}`} />
          <p className="text-meta text-muted">Scan with a phone to open Altitude.</p>
        </> : null}
        {code.code.certificate ? <p className="text-meta text-muted">Certificate “{code.code.certificate.name}” — SHA-256 ends with{" "}
          <span className="device-check">{code.code.certificate.check}</span></p> : null}
        <button type="button" className="btn" onClick={() => void make()}>Make a new code</button>
      </> : <>
        <p className="text-meta text-muted">Make a one-time code here, or run <code>alt pair</code> on the computer running Altitude. A new code cancels the previous one.</p>
        <button type="button" className="btn btn-primary" disabled={code.status === "making"} onClick={() => void make()}>{code.status === "making" ? "Making a code…" : "Make a pairing code"}</button>
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
      <dt>Expires</dt><dd>{certificate.expires}</dd>
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
        <button type="button" className="btn" disabled={state.status === "closing"} onClick={() => void close()}>{state.status === "closing" ? "Closing…" : "Close"}</button>
      </div>
      {state.error ? <p role="alert" className="text-meta text-danger">The link is still open: {state.error.message}</p> : null}
    </div>;
  }
  return <>
    <button type="button" className="btn btn-primary" disabled={state.status === "opening"} onClick={() => void open()}>{state.status === "opening" ? "Opening…" : "Set up a device"}</button>
    {state.status === "closed" ? <p role="status" className="text-meta text-muted">The link is closed.</p> : null}
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
      <span><strong>Version</strong>{" "}<small>{update.current}{available ? <> · {available.version} is available · <a href={available.notes} target="_blank" rel="noreferrer">What’s new</a></> : update.check && update.checked ? " · Up to date" : ""}</small></span>
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
        <small>Install new versions at the next quiet point, when no browser terminal is open. Turn this off to be asked before installing.</small>
      </label>
      <input id="update-automatic-switch" type="checkbox" role="switch" className="settings-switch" checked={update.automatic}
        disabled={!update.check || save.status === "saving"} onChange={(event) => void change(event.target.checked, true)} />
    </div>
    {save.status === "saving" ? <p role="status">Saving…</p> : null}
    {save.status === "failed" ? <p role="alert" className="text-danger">{save.error.message}</p> : null}
  </>;
}

const titles = {
  voice: "Voice input", projects: "Projects", "projects-folder": "Projects folder", name: "Your name",
  prerequisites: "Prerequisites", "incident-reports": "Incident reports", devices: "Devices",
} as const;

/** A machine setting the onboarding flow also sets: its form, saving in place with a Saved confirmation. */
function MachinePage({ page }: { page: "name" | "prerequisites" | "incident-reports" }) {
  const inContainer = useMachine().data?.deployment === "container";
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
  // While host voice sets up, its progress is read every second.
  const settings = useQuery({ queryKey, queryFn: readVoiceSettings, refetchOnWindowFocus: false,
    refetchInterval: (query) => query.state.data?.host.state === "setting-up" ? 1000 : false });
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
  const voiceRow = settings.isPending ? <p role="status" className="settings-row">Loading voice settings…</p>
    : settings.isError ? <p role="alert" className="settings-row text-danger">Could not load voice settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
      : <Link className="settings-row" to="/settings/voice" state={state}>
        <span><strong>Voice input</strong>{" "}<small>{voiceSummary(settings.data)}</small></span><span aria-hidden>›</span>
      </Link>;
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1>{title}</h1></header> : null}
    <div className="page settings-page" data-overview={!page || undefined}>
      {!phone ? <>{page ? back : null}<h1>{title}</h1></> : null}
      {page === "name" || page === "prerequisites" || page === "incident-reports" ? <MachinePage key={page} page={page} />
        : page === "devices" ? <DevicesPage />
        : page === "projects" ? <ProjectsPage state={state} />
        : page === "projects-folder" ? <>
        <p className="text-meta text-muted">First run offers the folders directly inside this folder. Altitude lists them only when you open First run or Add a folder; it never looks deeper or reads files.</p>
        <ProjectsFolderForm roots={roots} />
      </> : page === "voice" ? <>
        <p className="text-meta text-muted">Choose how speech becomes text. Applies to every project.</p>
        {settings.isPending ? <p role="status">Loading settings…</p>
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
          <Group title="Models"><NewTasksRow project={current} /></Group>
          <Group title="Projects">
            <Link className="settings-row" to="/settings/projects" state={state}>
              <span><strong>All projects</strong>{" "}<small>{overview.data ? `${managed.length} project${managed.length === 1 ? "" : "s"} · folder ${roots.join(" and ") || "not set"}` : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
          </Group>
        </div>
        <div className="settings-column">
          <Group title="Voice">{voiceRow}</Group>
          <Group title="Devices and access">
            <Link className="settings-row" to="/settings/devices" state={state}>
              <span><strong>Devices</strong>{" "}<small>{devices.data ? `${devices.data.devices.length} paired · pair another, certificate` : "Loading…"}</small></span><span aria-hidden>›</span>
            </Link>
            <TerminalSwitch machine={machine.data} />
            <div className="settings-row" aria-label="Network">
              <span><strong>Network</strong>{" "}<small>{window.location.origin} · HTTPS {window.location.protocol === "https:" ? "on" : "off"} · view only</small></span>
            </div>
          </Group>
          <Group title="Coding agents">
            <Link className="settings-row" to="/settings/prerequisites" state={state}>
              <span><strong>Prerequisites</strong>{" "}<small>GitHub CLI sign-in, coding agents and Git</small></span><span aria-hidden>›</span>
            </Link>
            <ValidationSwitch machine={machine.data} />
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
