import { useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, readVoiceSettings, saveVoiceSettings, useOverview } from "../data/api";
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

function VoiceForm({ saved, reload }: { saved: VoiceSettings; reload: () => void }) {
  const client = useQueryClient();
  const [choice, setChoice] = useState(saved.backend);
  const [url, setUrl] = useState(saved.url);
  const [model, setModel] = useState(saved.model);
  const [key, setKey] = useState("");
  const [keepKey, setKeepKey] = useState(saved.key_set);
  const save = useMutation({
    mutationFn: saveVoiceSettings,
    onSuccess: (value) => {
      client.setQueryData(queryKey, value);
      updateVoiceSettings(value);
      setChoice(value.backend);
      setUrl(value.url);
      setModel(value.model);
      setKey("");
      setKeepKey(value.key_set);
    },
  });
  const choose = (backend: VoiceBackend) => {
    save.reset();
    if (backend === "endpoint") setChoice(backend);
    else save.mutate({ backend, selection: saved.selection });
  };
  const endpoint = (): VoiceUpdate => ({
    backend: "endpoint", selection: saved.selection, url: url.trim(), model: model.trim(),
    ...(keepKey ? { keep_key: true } : { key }),
  });
  const stale = save.error instanceof ApiError && save.error.status === 409;
  return <>
    <form className="settings-card" onSubmit={(event) => { event.preventDefault(); save.mutate(endpoint()); }}>
      <fieldset disabled={save.isPending}>
        <legend>Transcription backend</legend>
        {(["browser", "local", "endpoint"] as const).map((backend) => <div className="voice-option" key={backend}>
          <label className="voice-choice">
            <input type="radio" name="voice-backend" value={backend} checked={choice === backend} onChange={() => choose(backend)} />
            <span>{labels[backend]}</span>
          </label>
          <p className="text-muted text-meta">{explanations[backend]}</p>
          {backend === "endpoint" && choice === "endpoint" ? <div className="voice-endpoint">
            <label>Endpoint URL<input type="url" required value={url} placeholder="https://speech.example.test/v1/audio/transcriptions" onChange={(event) => { setUrl(event.target.value); setKeepKey(false); save.reset(); }} /></label>
            <label>Model (optional)<input value={model} placeholder="Default: whisper-1" onChange={(event) => { setModel(event.target.value); save.reset(); }} /></label>
            {keepKey ? <div className="voice-key-set"><span>Key set · never shown</span><button type="button" className="link" onClick={() => { setKeepKey(false); save.reset(); }}>Replace</button></div>
              : <label>API key (optional)<input type="password" autoComplete="new-password" value={key} onChange={(event) => { setKey(event.target.value); save.reset(); }} /></label>}
            {!keepKey && saved.key_set ? <p className="text-meta text-muted">Leave blank to remove the stored key. A stored key is never sent to a changed URL.</p> : null}
            <p className="text-meta text-muted">Changes are saved only with Save endpoint.</p>
            <button type="submit" className="btn btn-primary">Save endpoint</button>
          </div> : null}
        </div>)}
      </fieldset>
      {save.isPending || save.isSuccess ? <p role="status" className="text-meta text-muted">{save.isPending ? "Saving…" : "Saved."}</p> : null}
      {save.isError ? <p role="alert" className="text-meta text-danger">{save.error.message}{" "}
        <button type="button" className="link" onClick={() => stale ? reload() : save.mutate(save.variables)}>{stale ? "Reload settings" : "Retry"}</button>
      </p> : null}
    </form>
    <p className="text-meta text-muted">Changes apply to your next recording. Altitude deletes temporary recordings after transcription. External services control their own audio retention.</p>
  </>;
}

/** One machine setting, with an overview that stays compact after setup. */
export default function Settings({ voice = false }: { voice?: boolean }) {
  const { phone } = useViewport();
  const location = useLocation();
  const navigate = useNavigate();
  const overview = useOverview();
  const settings = useQuery({ queryKey, queryFn: readVoiceSettings, refetchOnWindowFocus: false });
  const [reloadKey, setReloadKey] = useState(0);
  const page = useRef<HTMLDivElement>(null);
  const state = location.state as { settingsFrom?: string; settingsScroll?: number } | null;
  useLayoutEffect(() => {
    if (!voice && page.current) page.current.scrollTop = state?.settingsScroll ?? 0;
  }, [voice, state?.settingsScroll]);
  const back = voice ? <Link to="/settings" state={state} className="btn settings-back">‹ Settings</Link>
    : <button type="button" className="btn settings-back" onClick={() => navigate(state?.settingsFrom || "/projects", { replace: true })}>‹ Back</button>;
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1>{voice ? "Voice input" : "Settings"}</h1></header> : null}
    <div className="page settings-page" ref={page}>
      {!phone ? <>{voice ? back : null}<h1>{voice ? "Voice input" : "Settings"}</h1></> : null}
      {voice ? <p className="text-meta text-muted">Choose how speech becomes text. Applies to every project.</p>
        : <div><h2>This machine</h2><p className="text-meta text-muted">Applies to every project in this Altitude installation.</p></div>}
      {settings.isPending ? <p role="status">Loading settings…</p>
        : settings.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void settings.refetch()}>Retry</button></p>
          : voice ? <VoiceForm key={reloadKey} saved={settings.data} reload={() => void settings.refetch().then(() => setReloadKey((value) => value + 1))} />
            : <Link className="settings-row" to="/settings/voice" state={{ ...state, settingsScroll: page.current?.scrollTop ?? 0 }}>
              <span><strong>Voice input</strong>{" "}<small>{labels[settings.data.backend]}</small></span><span aria-hidden>›</span>
            </Link>}
      {!voice ? <section className="settings-card" aria-label="Network">
        <h2>Network</h2><p className="text-meta text-muted">Connection details · view only</p>
        <dl className="settings-network"><dt>Address</dt><dd>{window.location.origin}</dd><dt>HTTPS</dt><dd>{window.location.protocol === "https:" ? "On" : "Off"}</dd><dt>Operator</dt><dd>{overview.data?.operator || "The operator"}</dd></dl>
      </section> : null}
    </div>
  </>;
}
