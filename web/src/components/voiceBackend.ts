import { useEffect, useState } from "react";
import { readVoiceSettings } from "../data/api";
import type { VoiceBackend, VoiceSettings } from "../data/api";

/*
 * The installation's transcription backend, read once per document from `GET /api/voice`. "browser"
 * runs the browser's own recognition; "endpoint" uploads the recording for the server to forward to
 * the machine's speech service. Until the read answers, the composer shows no microphone; a failed
 * read is retried while a composer is mounted.
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
    (backend) => { if (reading === request) { known = backend; notify(); } },
    () => { if (reading === request) retry = setTimeout(() => { retry = null; if (listeners.size) read(); }, RETRY_MS); },
  ).finally(() => { if (reading === request) reading = null; });
  reading = request;
}

/** Tests and fixtures name the backend directly instead of reading it. */
export function presetVoiceBackend(backend: VoiceBackend | null) {
  updateVoiceSettings(backend === null ? null : { backend, selection: `fixture-${backend}`, url: "", model: "", key_set: false });
}

/** A successful Settings save changes the next capture in every mounted composer. */
export function updateVoiceSettings(settings: VoiceSettings | null) {
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
