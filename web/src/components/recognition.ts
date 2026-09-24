/*
 * The browser's own speech recognition, wrapped in MediaRecorder's shape so the composer runs one
 * capture state machine for every backend. Words arrive through `onupdate` while listening; `text`
 * holds them when the capture stops. Nothing is uploaded and no audio is kept.
 */

type RecognitionResult = { isFinal: boolean; 0: { transcript: string } };
type RecognitionEvent = { results: ArrayLike<RecognitionResult> };
type RecognitionError = { error: string };
interface Recognizer {
  continuous: boolean;
  interimResults: boolean;
  unspokenPunctuation?: boolean;
  lang: string;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: RecognitionError) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognizerConstructor = new () => Recognizer;

/** How long Stop or Cancel waits for the recognizer to end before ending without it. */
const SETTLE_MS = 3000;
/** A session that ends this soon after starting produced nothing; MAX_QUICK_ENDS in a row is a failure. */
const QUICK_END_MS = 1000;
const MAX_QUICK_ENDS = 5;

function recognizerConstructor(): RecognizerConstructor | null {
  if (typeof window === "undefined") return null;
  const scope = window as unknown as { SpeechRecognition?: RecognizerConstructor; webkitSpeechRecognition?: RecognizerConstructor };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

export function recognitionAvailable(): boolean {
  return recognizerConstructor() !== null;
}

export class RecognitionCapture {
  /** The latest capture asked to end; one recognizer per page, so the next one waits for it. */
  private static ending: Promise<void> | null = null;
  /** While a capture asked to stop or cancel has not yet ended (at most SETTLE_MS), its end; else null. */
  static idle(): Promise<void> | null {
    return RecognitionCapture.ending;
  }

  state: RecordingState = "inactive";
  readonly stream: MediaStream;
  readonly mimeType = "";
  /** "denied" when the browser refused speech recognition; "failed" for any other recognizer error. */
  failure: "denied" | "failed" | null = null;
  onstop: (() => void) | null = null;
  onupdate: ((text: string) => void) | null = null;
  private settled: string[] = [];
  private finals: string[] = [];
  private interim = "";
  private ending = false;
  private aborting = false;
  private startedAt = 0;
  private quickEnds = 0;
  private settleTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly recognizer: Recognizer;
  private ended: () => void = () => undefined;

  constructor(stream: MediaStream, lang = typeof navigator === "undefined" ? "" : navigator.language) {
    const Constructor = recognizerConstructor();
    if (!Constructor) throw new Error("This browser has no speech recognition.");
    this.stream = stream;
    this.recognizer = new Constructor();
    this.recognizer.continuous = true;
    this.recognizer.interimResults = true;
    if ("unspokenPunctuation" in this.recognizer) this.recognizer.unspokenPunctuation = true;
    if (lang) this.recognizer.lang = lang;
    this.recognizer.onresult = (event) => {
      this.finals = [];
      this.interim = "";
      for (let index = 0; index < event.results.length; index++) {
        const result = event.results[index];
        const phrase = result?.[0]?.transcript.trim() ?? "";
        if (!result || !phrase) continue;
        if (result.isFinal) this.finals.push(phrase);
        else this.interim = [this.interim, phrase].filter(Boolean).join(" ");
      }
      this.onupdate?.(this.text);
    };
    this.recognizer.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") this.failure = "denied";
      else if (event.error !== "no-speech" && event.error !== "aborted") this.failure = "failed";
    };
    this.recognizer.onend = () => {
      if (this.state === "recording" && !this.ending && !this.failure) {
        // Chrome ends a continuous session after silence; keep listening with the words so far.
        // A recognizer that ends right after every start (the microphone taken by another audio
        // session) would otherwise restart until the recording cap: five in a row is a failure.
        this.quickEnds = Date.now() - this.startedAt < QUICK_END_MS ? this.quickEnds + 1 : 0;
        this.settled.push(...this.finals);
        this.finals = [];
        this.interim = "";
        if (this.quickEnds < MAX_QUICK_ENDS) {
          try {
            this.startedAt = Date.now();
            this.recognizer.start();
            this.onupdate?.(this.text);
            return;
          } catch {
            this.failure = "failed";
          }
        } else {
          this.failure = "failed";
        }
      }
      this.end();
    };
  }

  /** Final phrases first, then the phrase still being recognized. */
  get text(): string {
    return [...this.settled, ...this.finals, this.interim].filter(Boolean).join(" ");
  }

  start() {
    this.state = "recording";
    this.startedAt = Date.now();
    this.recognizer.start();
  }

  /** Ask for the last phrase, then end; a recognizer that never answers still ends within SETTLE_MS. */
  stop() {
    if (this.state !== "recording" || this.ending) return;
    this.ending = true;
    this.endWithin();
    try {
      this.recognizer.stop();
    } catch {
      this.end();
    }
  }

  /**
   * Discard everything: no waiting for the last phrase. The recognizer lets go of the microphone
   * later; the capture ends with its own end, and `idle()` holds the next capture until then.
   */
  cancel() {
    if (this.state !== "recording" || this.aborting) return;
    this.ending = this.aborting = true;
    this.endWithin();
    try {
      this.recognizer.abort();
    } catch {
      this.end();
    }
  }

  private endWithin() {
    if (this.settleTimer) return;
    const ending = new Promise<void>((resolve) => {
      this.ended = () => {
        if (RecognitionCapture.ending === ending) RecognitionCapture.ending = null;
        resolve();
      };
    });
    RecognitionCapture.ending = ending;
    this.settleTimer = setTimeout(() => {
      try { this.recognizer.abort(); } catch { /* already gone */ }
      this.end();
    }, SETTLE_MS);
  }

  private end() {
    if (this.state === "inactive") return;
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.settleTimer = null;
    this.state = "inactive";
    try {
      this.onstop?.();
    } finally {
      this.ended();
    }
  }
}
