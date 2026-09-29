/** Opt-in, page-memory evidence for native capture failures. Never accepts speech or draft text. */
type Detail = {
  state?: string; error?: string; backend?: "browser" | "host";
  muted?: boolean; enabled?: boolean; signal?: boolean; time?: number;
};
type Entry = Detail & { ms: number; event: string; source?: number };
const LIMIT = 256;
const DURATION_MS = 10 * 60 * 1000;
let entries: Entry[] = [];
let dropped = 0;
let sources = new WeakMap<object, number>();
let nextSource = 0;
let started = 0;
let active = false;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

export const voiceDiagnosticsActive = () => active;
export const subscribeVoiceDiagnostics = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function traceVoice(event: string, source?: object, detail: Detail = {}) {
  if (!active || performance.now() - started >= DURATION_MS) return;
  let id = source ? sources.get(source) : undefined;
  if (source && id === undefined) { id = ++nextSource; sources.set(source, id); }
  entries.push({ ms: Math.round(performance.now() - started), event, ...(id === undefined ? {} : { source: id }), ...detail });
  if (entries.length > LIMIT) { entries.shift(); dropped++; }
}

/** Record only standardized error codes; browser exception messages can contain private content. */
export function voiceError(error: unknown): string {
  const code = typeof error === "string" ? error : error instanceof Error ? error.name : "";
  return ["NotAllowedError", "NotFoundError", "NotReadableError", "AbortError", "InvalidStateError", "SecurityError",
    "not-allowed", "service-not-allowed", "no-speech", "aborted", "audio-capture", "network", "language-not-supported"].includes(code) ? code : "other";
}

export function traceVoiceTracks(event: string, stream: MediaStream) {
  if (!active) return;
  for (const track of stream.getAudioTracks()) {
    traceVoice(event, track, { state: track.readyState, muted: track.muted, enabled: track.enabled });
  }
}

export function stopVoiceDiagnostics() {
  traceVoice("diagnostics.stop");
  active = false;
  clearTimeout(timer);
  notify();
}

export function clearVoiceDiagnostics() {
  stopVoiceDiagnostics();
  entries = [];
  dropped = 0;
  sources = new WeakMap();
  nextSource = 0;
}

export function startVoiceDiagnostics() {
  clearVoiceDiagnostics();
  started = performance.now();
  active = true;
  traceVoice("diagnostics.start");
  timer = setTimeout(stopVoiceDiagnostics, DURATION_MS);
  notify();
}

export function voiceDiagnosticReport(): string {
  return JSON.stringify({
    kind: "altitude-voice-diagnostic", version: 1,
    browser: navigator.userAgent,
    standalone: window.matchMedia("(display-mode: standalone)").matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone),
    // Asset basename identifies the loaded client without exposing the origin or conversation URL.
    build: Array.from(document.scripts).map((script) => {
      try { return new URL(script.src).pathname.split("/").at(-1); } catch { return undefined; }
    }).find((name) => /^index-[\w-]+\.js$/.test(name ?? "")) ?? "development",
    droppedEvents: dropped, events: entries,
  }, null, 2);
}
