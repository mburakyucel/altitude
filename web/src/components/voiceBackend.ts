import { useEffect, useState } from "react";
import { readVoiceSettings } from "../data/api";
import type { HostVoice, VoiceBackend, VoiceSettings } from "../data/api";
import { HostCapture } from "./hostCapture";

export const INVALID_SERVICE = "Your speech service settings are invalid. Re-enter them in Voice settings.";

/*
 * The installation's transcription backend, read once per document from `GET /api/voice`. "host" streams
 * to this computer's speech model; "browser" runs the browser's own recognition; "service" uploads after Stop.
 * Until the read answers,
 * the composer shows no microphone; a failed read is retried while a composer is mounted.
 */
const RETRY_MS = 5000;
let known: VoiceSettings | null = null;
let reading: Promise<void> | null = null;
let retry: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

function read() {
  if (reading) return;
  const request = readVoiceSettings().then(
    (backend) => { if (reading === request) updateVoiceSettings(backend); },
    () => { if (reading === request) retry = setTimeout(() => { retry = null; if (listeners.size) read(); }, RETRY_MS); },
  ).finally(() => { if (reading === request) reading = null; });
  reading = request;
}

/** Tests and fixtures name the backend directly instead of reading it; host voice is set up unless `host` says otherwise. */
export function presetVoiceBackend(backend: VoiceBackend | null, host: HostVoice = { state: "ready", download_bytes: 0 }) {
  updateVoiceSettings(backend === null ? null : { backend, selection: `fixture-${backend}`, host });
}

/** A successful Settings save changes the next capture in every mounted composer. */
export function updateVoiceSettings(settings: VoiceSettings | null) {
  if (settings) for (const capture of HostCapture.retained) capture.checkServiceSelection(settings.selection);
  known = settings;
  reading = null;
  if (retry) clearTimeout(retry);
  retry = null;
  notify();
}

/** The server contradicted the cached backend (the setting changed): forget it and read again. */
export function refreshVoiceBackend() {
  updateVoiceSettings(null);
  read();
}

export function useVoiceBackend(): VoiceSettings | null {
  const [backend, setBackend] = useState(known);
  useEffect(() => {
    const listener = () => setBackend(known);
    listeners.add(listener);
    if (known === null) read();
    listener();
    return () => { listeners.delete(listener); };
  }, []);
  return backend;
}
