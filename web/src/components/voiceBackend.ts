import { useEffect, useState } from "react";
import { readVoiceBackend } from "../data/api";
import type { VoiceBackend } from "../data/api";

/*
 * The installation's transcription backend, read once per document from `GET /api/voice`. "browser"
 * runs the browser's own recognition; "local" and "endpoint" upload the recording for the server to
 * transcribe. Until the read answers, the composer shows no microphone; a failed read is retried
 * while a composer is mounted.
 */
const RETRY_MS = 5000;
let known: VoiceBackend | null = null;
let reading: Promise<void> | null = null;
let retry: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

function read() {
  reading ??= readVoiceBackend().then(
    (backend) => { known = backend; notify(); },
    () => { retry = setTimeout(() => { retry = null; if (listeners.size) read(); }, RETRY_MS); },
  ).finally(() => { reading = null; });
}

/** Tests and fixtures name the backend directly instead of reading it. */
export function presetVoiceBackend(backend: VoiceBackend | null) {
  known = backend;
  reading = null;
  if (retry) clearTimeout(retry);
  retry = null;
  notify();
}

/** The server contradicted the cached backend (the setting changed): forget it and read again. */
export function refreshVoiceBackend() {
  known = null;
  notify();
  read();
}

export function useVoiceBackend(): VoiceBackend | null {
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
