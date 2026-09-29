/*
 * Host voice: the microphone streams to the Altitude computer, which transcribes it with its own speech
 * model and answers each chunk with the text so far. Wrapped in the same shape as RecognitionCapture, so
 * the composer runs one state machine: words arrive through `onupdate` while listening, and `text` holds
 * the final words once the capture stops.
 *
 * A dedicated audio graph, separate from the waveform, runs a worklet that turns the microphone into 16 kHz
 * 16-bit samples. One request is in flight at a time: each carries the samples gathered since the last
 * answer (normally half a second, at most ten seconds) and the next sequence number.
 *
 * The recording survives a lost connection. Its samples stay in this page's memory until the capture ends
 * (at most ten minutes, about 19 MB; never stored, so a reload loses them), and at most two captures are kept
 * at once: one sending, one recording. A request that gets no answer is repeated with the same samples,
 * number and final flag, so the host never counts it twice, while the microphone keeps recording. When the
 * host no longer knows the recording (it idled out or restarted), the capture opens a new one and replays
 * everything from the start; the host refuses that replay if the voice setting or its runtime changed, or if
 * the browser was paired again as another device. The words shown hold still until the replay catches up. After Stop, the capture waits two minutes
 * at most for the connection, then ends with the words shown.
 */
import { ApiError, cancelHostVoice, sendHostVoice, startHostVoice } from "../data/api";
import { traceVoice, voiceError } from "./voiceDiagnostics";

const RATE = 16000;
/** Send once this much is gathered; while a request is in flight, samples keep gathering. */
const CHUNK_SAMPLES = RATE / 2;
const MAX_CHUNK_SAMPLES = RATE * 10;
/** The microphone must deliver samples this soon after it opens. */
const FIRST_SAMPLES_MS = 2000;
/** A request that has no answer by then is repeated; the final one waits for the host's last words. */
const REQUEST_MS = 12000;
const FINAL_MS = 25000;
/** Repeats start quickly and settle at a few seconds apart. */
const RETRY_MS = 500;
const MAX_RETRY_MS = 3000;
/** After Stop or Send, how long the capture waits for the connection before ending with the words shown. */
export const WAIT_MS = 120_000;
/** One capture sending its last words and one recording. */
export const MAX_RETAINED = 2;
export const UNREACHED = "Couldn't reach this computer: your recording's last words weren't added.";

/** Resample the microphone to 16 kHz 16-bit samples: the average of each output sample's input window. */
const WORKLET = `
class Resample extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / ${RATE}; this.at = 0; this.sum = 0; this.count = 0; this.out = []; }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i]; this.count++; this.at++;
      if (this.at >= this.step) {
        this.at -= this.step;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.out.push(value < 0 ? value * 32768 : value * 32767);
        this.sum = 0; this.count = 0;
      }
    }
    if (this.out.length >= ${RATE / 10}) {
      const samples = Int16Array.from(this.out);
      this.out = [];
      this.port.postMessage(samples, [samples.buffer]);
    }
    return true;
  }
}
registerProcessor("altitude-resample", Resample);
`;

/** Start delivering 16 kHz samples from the stream; resolves with the function that stops it. */
export type Listen = (stream: MediaStream, samples: (chunk: Int16Array) => void) => Promise<() => void>;

export const listenWithWorklet: Listen = async (stream, samples) => {
  const context = new AudioContext();
  try {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    try {
      await context.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    const node = new AudioWorkletNode(context, "altitude-resample");
    node.port.onmessage = (event: MessageEvent<Int16Array>) => samples(event.data);
    const source = context.createMediaStreamSource(stream);
    source.connect(node);
    // A node with no path to the destination is never processed in some browsers; a muted gain adds none.
    const mute = context.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(context.destination);
    if (context.state !== "running") await context.resume();
    return () => {
      node.port.onmessage = null;
      source.disconnect();
      void context.close().catch(() => undefined);
    };
  } catch (error) {
    void context.close().catch(() => undefined);
    throw error;
  }
};

/** "lost": requests go unanswered; "catching-up": a replay has not yet reached the words shown. */
export type Connection = "ok" | "lost" | "catching-up";

export class HostCapture {
  /** How every capture hears the microphone; tests replace it, as the page has no audio worklet there. */
  static listen: Listen = listenWithWorklet;
  /** The captures whose samples this page holds. */
  static readonly retained = new Set<HostCapture>();
  /** Composers showing a capture's connection re-render when any capture's connection changes. */
  static readonly watchers = new Set<() => void>();

  state: RecordingState = "inactive";
  readonly stream: MediaStream;
  readonly mimeType = "";
  /** "failed" when the recording stopped before its final words; `reason` says why. */
  failure: "denied" | "failed" | null = null;
  reason = "";
  /** The host refused this recording's setting or setup (409): the page's voice settings are out of date. */
  outdated = false;
  /** The connection did not come back within WAIT_MS of Stop: the last words were not added. */
  unreached = false;
  connection: Connection = "ok";
  readonly unpunctuated = null;
  text = "";
  onstop: (() => void) | null = null;
  onupdate: ((text: string) => void) | null = null;
  /** The first microphone samples arrived: the composer shows Listening from here. */
  onlistening: (() => void) | null = null;
  /** The microphone ended (a call, a locked screen): the composer stops as if Stop were pressed. */
  oninterrupted: (() => void) | null = null;

  /** Every sample recorded, kept until the capture ends so a new host recording can replay it. */
  private audio: Int16Array[] = [];
  private recorded = 0;
  /** The host recording, the last sequence number it answered, and how many samples those answers cover. */
  private id: string | null = null;
  /** The paired device the first host recording belongs to: a replay is refused under any other. */
  private owner: string | undefined;
  /** A replay is opening a new host recording: a busy host is asked again rather than ending the capture. */
  private reopening = false;
  private seq = -1;
  private covered = 0;
  /** The words shown before a replay: held until the replay covers as many samples and as many words. */
  private holdUntil = 0;
  private inflight: { seq: number; pcm: Int16Array; final: boolean } | null = null;
  private stopping = false;
  private finished = false;
  private heard = false;
  private attempts = 0;
  private unlisten: (() => void) | null = null;
  private silence: ReturnType<typeof setTimeout> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private request: AbortController | null = null;
  private readonly selection: string;

  constructor(stream: MediaStream, selection: string) {
    this.stream = stream;
    this.selection = selection;
  }

  start() {
    traceVoice("host.start", this);
    this.state = "recording";
    HostCapture.retained.add(this);
    for (const track of this.stream.getAudioTracks()) {
      // A call or a locked screen takes the microphone: what was recorded still lands.
      track.addEventListener("ended", () => {
        if (this.state !== "recording" || this.stopping) return;
        this.reason = "Voice stopped: the microphone was interrupted.";
        (this.oninterrupted ?? (() => this.stop()))();
      });
    }
    this.silence = setTimeout(() => { if (!this.heard) this.fail("Voice stopped: the microphone produced no audio."); }, FIRST_SAMPLES_MS);
    HostCapture.listen(this.stream, (chunk) => this.samples(chunk)).then(
      (unlisten) => {
        if (this.state === "inactive" || this.stopping) unlisten();
        else this.unlisten = unlisten;
      },
      (error) => {
        traceVoice("host.listen-error", this, { error: voiceError(error) });
        this.fail("Voice stopped: this browser could not process the microphone.");
      },
    );
    this.pump();
  }

  /** Stop listening, send what is left as the final chunk and end with the host's final words. */
  stop() {
    traceVoice("host.stop", this);
    if (this.state !== "recording" || this.stopping) return;
    this.stopping = true;
    this.release();
    this.deadline = setTimeout(() => this.expire(), WAIT_MS);
    this.pump();
  }

  /** Discard everything at once; the host drops the recording. */
  cancel() {
    traceVoice("host.cancel", this);
    if (this.state === "inactive") return;
    this.request?.abort();
    if (this.id) void cancelHostVoice(this.id).catch(() => undefined);
    this.end();
  }

  private samples(chunk: Int16Array) {
    if (this.state !== "recording" || this.stopping) return;
    if (!this.heard) {
      this.heard = true;
      traceVoice("host.samples", this);
      this.onlistening?.();
    }
    this.audio.push(chunk);
    this.recorded += chunk.length;
    this.pump();
  }

  /** Open a host recording, or send the next chunk once enough is gathered, or the rest once stopping. */
  private pump() {
    if (this.request || this.retry || this.finished || this.state !== "recording") return;
    if (!this.id) { void this.open(); return; }
    if (!this.inflight) {
      const waiting = this.recorded - this.covered;
      if (!this.stopping && waiting < CHUNK_SAMPLES) return;
      const take = Math.min(waiting, MAX_CHUNK_SAMPLES);
      this.inflight = { seq: this.seq + 1, pcm: this.slice(this.covered, take), final: this.stopping && take === waiting };
    }
    void this.send(this.id, this.inflight);
  }

  /** `count` recorded samples starting at `from`. */
  private slice(from: number, count: number): Int16Array {
    const pcm = new Int16Array(count);
    let at = 0;
    let filled = 0;
    for (const chunk of this.audio) {
      if (filled === count) break;
      const end = at + chunk.length;
      if (end > from) {
        const start = Math.max(0, from - at);
        const part = chunk.subarray(start, Math.min(chunk.length, start + count - filled));
        pcm.set(part, filled);
        filled += part.length;
      }
      at = end;
    }
    return pcm;
  }

  private async open() {
    const request = this.begin(REQUEST_MS);
    try {
      const opened = await startHostVoice(this.selection, request.signal, this.owner);
      if (this.state === "inactive") { void cancelHostVoice(opened.id).catch(() => undefined); return; }
      this.id = opened.id;
      this.owner ??= opened.owner;
      traceVoice("host.opened", this);
      this.answered();
    } catch (error) {
      if (this.state === "inactive") return;
      // During a replay, a busy host frees a slot once the lost recording idles out; keep asking until the deadline.
      if (this.reopening && error instanceof ApiError && error.status === 429 || lost(error)) this.again(error);
      else this.fail(message(error), error);
    } finally {
      this.finish(request);
    }
    this.pump();
  }

  private async send(id: string, chunk: { seq: number; pcm: Int16Array; final: boolean }) {
    const request = this.begin(chunk.final ? FINAL_MS : REQUEST_MS);
    try {
      const answer = await sendHostVoice(id, this.selection, chunk.seq, chunk.pcm, chunk.final, request.signal);
      if (this.state === "inactive") return;
      this.inflight = null;
      this.seq = chunk.seq;
      this.covered += chunk.pcm.length;
      // The host answers audio before transcribing it, so a replay holds until its words catch up too.
      if (chunk.final || !this.holdUntil || this.covered >= this.holdUntil && answer.text.length >= this.text.length) {
        this.holdUntil = 0;
        this.text = answer.text;
        this.onupdate?.(this.text);
      }
      this.answered();
      if (chunk.final) { this.finished = true; this.end(); }
    } catch (error) {
      if (this.state === "inactive") return;
      if (error instanceof ApiError && error.status === 410) this.replay();
      else if (lost(error)) this.again(error);
      else this.fail(message(error), error);
    } finally {
      this.finish(request);
    }
    this.pump();
  }

  /** The host no longer knows this recording: open a new one and send everything recorded again. */
  private replay() {
    traceVoice("host.replay", this);
    this.holdUntil = Math.max(this.holdUntil, this.covered);
    this.id = null;
    this.reopening = true;
    this.seq = -1;
    this.covered = 0;
    this.inflight = null;
    this.watch("catching-up");
  }

  private begin(ms: number): AbortController {
    const request = new AbortController();
    this.request = request;
    const timer = setTimeout(() => request.abort(), ms);
    request.signal.addEventListener("abort", () => clearTimeout(timer));
    return request;
  }

  private finish(request: AbortController) {
    request.abort();
    if (this.request === request) this.request = null;
  }

  private answered() {
    this.attempts = 0;
    this.watch(this.holdUntil ? "catching-up" : "ok");
  }

  /** No answer: keep recording, and repeat the same request after a pause. */
  private again(error: unknown) {
    traceVoice("host.retry", this, { error: voiceError(error) });
    this.watch("lost");
    const pause = Math.min(RETRY_MS * 2 ** this.attempts, MAX_RETRY_MS);
    this.attempts += 1;
    this.retry = setTimeout(() => { this.retry = null; this.pump(); }, pause);
  }

  private watch(connection: Connection) {
    if (this.connection === connection) return;
    this.connection = connection;
    HostCapture.watchers.forEach((watcher) => watcher());
  }

  /** The connection did not return in time: end with the words shown. */
  private expire() {
    if (this.state === "inactive") return;
    this.unreached = true;
    this.fail(UNREACHED);
  }

  private fail(reason: string, error?: unknown) {
    if (this.state === "inactive") return;
    this.outdated = error instanceof ApiError && error.status === 409;
    traceVoice("host.failed", this);
    this.failure = "failed";
    this.reason = reason;
    this.request?.abort();
    if (this.id) void cancelHostVoice(this.id).catch(() => undefined);
    this.end();
  }

  private release() {
    if (this.silence) clearTimeout(this.silence);
    this.silence = null;
    this.unlisten?.();
    this.unlisten = null;
    this.stream.getTracks().forEach((track) => track.stop());
  }

  private end() {
    if (this.state === "inactive") return;
    this.release();
    for (const timer of [this.retry, this.deadline]) if (timer) clearTimeout(timer);
    this.retry = this.deadline = null;
    this.audio = [];
    this.state = "inactive";
    HostCapture.retained.delete(this);
    this.watch("ok");
    this.onstop?.();
  }
}

/** No answer from Altitude: the network dropped, the request timed out, or something between answered instead. */
function lost(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status >= 500 && error.message.startsWith("HTTP ");
}

function message(error: unknown): string {
  if (error instanceof ApiError && error.status !== 401 && !error.message.startsWith("HTTP ")) return error.message;
  return "Voice stopped: the connection to Altitude was lost.";
}
