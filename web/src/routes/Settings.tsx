import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import { ApiError, readVoiceSettings, saveProjectsFolder, saveVoiceSettings, useOverview } from "../data/api";
import type { VoiceBackend, VoiceSettings, VoiceUpdate } from "../data/api";
import { updateVoiceSettings } from "../components/voiceBackend";
import { useViewport } from "../shell/breakpoints";
import "./settings.css";

const labels: Record<VoiceBackend, string> = {
  browser: "Browser recognition", local: "Local speech service", endpoint: "Custom endpoint",
};
const explanations: Record<VoiceBackend, string> = {
  browser: "No setup in supported browsers. Words appear as you speak. Your browser may send audio to its speech service; that service’s privacy policy applies.",
  local: "Audio is transcribed on the computer running Altitude after you stop. Requires a configured local speech service and ffmpeg.",
  endpoint: "Audio goes to your chosen service after you stop. Its storage policy and any charges apply.",
};
const queryKey = ["voice-settings"];
type SaveState = { status: "idle" } | { status: "saving" | "saved" } | { status: "failed"; value: VoiceUpdate; error: Error };

function VoiceForm({ saved, reload }: { saved: VoiceSettings; reload: () => void }) {
  const client = useQueryClient();
  const committed = useRef(saved);
  const [choice, setChoice] = useState(saved.backend);
  const [url, setUrl] = useState(saved.url);
  const [model, setModel] = useState(saved.model);
  const [key, setKey] = useState("");
  const [keepKey, setKeepKey] = useState(saved.key_set);
  useEffect(() => {
    if (saved === committed.current) return;
    committed.current = saved;
    setChoice(saved.backend);
    setUrl(saved.url);
    setModel(saved.model);
    setKey("");
    setKeepKey(saved.key_set);
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
        <legend>Transcription backend</legend>
        {(["browser", "local", "endpoint"] as const).map((backend) => <div className="voice-option" key={backend}>
          <label className="voice-choice">
            <input type="radio" name="voice-backend" value={backend} checked={choice === backend} onChange={() => choose(backend)} />
            <span>{labels[backend]}</span>
          </label>
          <p className="text-muted text-meta">{explanations[backend]}</p>
          {backend === "endpoint" && choice === "endpoint" ? <div className="voice-endpoint">
            <label>Endpoint URL<input type="url" required value={url} placeholder="https://speech.example.test/v1/audio/transcriptions" onChange={(event) => { setUrl(event.target.value); setKeepKey(false); reset(); }} /></label>
            <label>Model (optional)<input value={model} placeholder="Default: whisper-1" onChange={(event) => { setModel(event.target.value); reset(); }} /></label>
            {keepKey ? <div className="voice-key-set"><span>Key set · never shown</span><button type="button" className="link" onClick={() => { setKeepKey(false); reset(); }}>Replace</button></div>
              : <label>API key (optional)<input type="password" autoComplete="new-password" value={key} onChange={(event) => { setKey(event.target.value); reset(); }} /></label>}
            {!keepKey && saved.key_set ? <p className="text-meta text-muted">Leave blank to remove the stored key. A stored key is never sent to a changed URL.</p> : null}
            <p className="text-meta text-muted">Changes are saved only with Save endpoint.</p>
            <button type="submit" className="btn btn-primary">Save endpoint</button>
          </div> : null}
        </div>)}
      </fieldset>
      {save.status === "saving" || save.status === "saved" ? <p role="status" className="text-meta text-muted">{save.status === "saving" ? "Saving…" : "Saved."}</p> : null}
      {save.status === "failed" ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
        <button type="button" className="link" onClick={() => stale ? reload() : void submit(save.value)}>{stale ? "Reload settings" : "Retry"}</button>
      </p> : null}
    </form>
    <p className="text-meta text-muted">Changes apply to your next recording. Altitude deletes temporary recordings after transcription. External services control their own audio retention.</p>
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

const titles = { voice: "Voice input", "projects-folder": "Projects folder" } as const;

/** Machine settings, with an overview that stays compact after setup. */
export default function Settings({ page }: { page?: keyof typeof titles }) {
  const { phone } = useViewport();
  const location = useLocation();
  const navigate = useNavigate();
  const overview = useOverview();
  const settings = useQuery({ queryKey, queryFn: readVoiceSettings, refetchOnWindowFocus: false });
  const [reloadKey, setReloadKey] = useState(0);
  const state = location.state as { settingsFrom?: string } | null;
  const voice = page === "voice";
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
      {page === "projects-folder" ? <>
        <p className="text-meta text-muted">First run offers the folders directly inside this folder. Altitude lists them only when you open First run or Add a folder; it never looks deeper or reads files.</p>
        <ProjectsFolderForm roots={roots} />
      </> : <>
        {voice ? <p className="text-meta text-muted">Choose how speech becomes text. Applies to every project.</p>
          : <div><h2>This machine</h2><p className="text-meta text-muted">Applies to every project in this Altitude installation.</p></div>}
        {settings.isPending ? <p role="status">Loading settings…</p>
          : settings.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
            : voice ? <VoiceForm key={reloadKey} saved={settings.data} reload={() => void settings.refetch().then(() => setReloadKey((value) => value + 1))} />
              : <Link className="settings-row" to="/settings/voice" state={state}>
                <span><strong>Voice input</strong>{" "}<small>{labels[settings.data.backend]}</small></span><span aria-hidden>›</span>
              </Link>}
        {!voice ? <Link className="settings-row" to="/settings/projects-folder" state={state}>
          <span><strong>Projects folder</strong>{" "}<small>{roots.join(" and ") || "Loading…"} · First run offers the folders directly inside it</small></span><span aria-hidden>›</span>
        </Link> : null}
        {!voice ? <section className="settings-card" aria-label="Network">
          <h2>Network</h2><p className="text-meta text-muted">Connection details · view only</p>
          <dl className="settings-network"><dt>Address</dt><dd>{window.location.origin}</dd><dt>HTTPS</dt><dd>{window.location.protocol === "https:" ? "On" : "Off"}</dd><dt>Operator</dt><dd>{overview.data?.operator || "The operator"}</dd></dl>
        </section> : null}
      </>}
    </div>
  </>;
}
