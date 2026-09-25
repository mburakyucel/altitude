import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import { IncidentReportsForm, NameForm, PrerequisiteList } from "../components/Onboarding";
import { ApiError, readVoiceSettings, saveProjectsFolder, saveTerminalAccess, saveVoiceSettings, useMachine, useOverview } from "../data/api";
import { managedProjects } from "../shell/projects";
import type { VoiceBackend, VoiceSettings, VoiceUpdate } from "../data/api";
import { updateVoiceSettings } from "../components/voiceBackend";
import { useViewport } from "../shell/breakpoints";
import "./settings.css";

const labels: Record<VoiceBackend, string> = {
  browser: "Browser recognition", endpoint: "Your speech service",
};
const explanations: Record<VoiceBackend, string> = {
  browser: "No setup in supported browsers. Words appear as you speak. Your browser may send audio to its speech service; that service’s privacy policy applies.",
  endpoint: "After you stop, Altitude sends the recording to a speech-to-text service you run or choose, using the standard OpenAI transcription API. It can run on this computer, on another machine on your network, or be a hosted provider. Audio goes only to that address; its storage policy and any charges apply.",
};
const queryKey = ["voice-settings"];
const DEFAULT_MODEL = "whisper-1";

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
  const [hosted, setHosted] = useState(saved.key_set || (saved.model !== "" && saved.model !== DEFAULT_MODEL));
  useEffect(() => {
    if (saved === committed.current) return;
    committed.current = saved;
    setChoice(saved.backend);
    setUrl(saved.url);
    setModel(saved.model);
    setKey("");
    setKeepKey(saved.key_set);
    setHosted(saved.key_set || (saved.model !== "" && saved.model !== DEFAULT_MODEL));
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
      <small>Anyone who can open Altitude can run commands as you on this computer. Terminals close when Altitude restarts or when you turn this off.</small>
      {save.status === "failed" ? <small role="alert" className="text-danger">{save.error.message}</small> : null}
    </label>
    <input id="terminal-switch" type="checkbox" role="switch" className="settings-switch" checked={enabled ?? false}
      disabled={enabled === undefined || save.status === "saving"} onChange={(event) => void change(event.target.checked)} />
  </div>;
}

const titles = {
  voice: "Voice input", "projects-folder": "Projects folder", name: "Your name",
  prerequisites: "Prerequisites", "incident-reports": "Incident reports",
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
