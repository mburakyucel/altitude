/*
 * The browser's own speech recognition, wrapped in MediaRecorder's shape so the composer runs one
 * capture state machine for every backend. Words arrive through `onupdate` while listening; `text`
 * holds them when the capture stops. Nothing is uploaded and no audio is kept.
 *
 * English dictation is punctuated on this device: each time the recognizer finalizes a phrase, the
 * bundled model adds sentence punctuation and capitals to the finalized words (the phrase still being
 * recognized stays as heard). The words themselves never change. Stop releases the microphone once
 * the recognizer ends, then waits for the last phrase's punctuation: at most PUNCTUATE_MS, or
 * LOADING_MS while the model is still loading. Words the model has not reached end as recognized
 * and `unpunctuated` says why.
 */
import { loadPunctuator, type Punctuator } from "../punctuation";

type RecognitionResult = { isFinal: boolean; 0: { transcript: string } };
type RecognitionEvent = { results: ArrayLike<RecognitionResult> };
type RecognitionError = { error: string };
interface Recognizer {
  continuous: boolean;
  interimResults: boolean;
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
/** How long Stop waits, after the recognizer ends, for punctuation of the last phrase. */
const PUNCTUATE_MS = 10000;
/** How long Stop waits for a model that has not finished loading. */
const LOADING_MS = 3000;
/**
 * When the next phrase is finalized, the model sees the last CONTEXT_WORDS punctuated words again
 * and may revise the last REVISE_WORDS of them: a phrase end can become a comma or run on. Earlier
 * words keep their punctuation; the model reads them only as context.
 */
const CONTEXT_WORDS = 16;
const REVISE_WORDS = 4;

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
  /** While a capture asked to stop or cancel still has its recognizer (at most SETTLE_MS), its release; else null. */
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
  /**
   * Why English dictation ended with words the model had not punctuated: "loading" when the model
   * was not ready yet, "failed" when it could not run or did not finish in time; null otherwise.
   */
  unpunctuated: "loading" | "failed" | null = null;
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
  /** The model, loading from the first capture; null for a language it does not cover. */
  private readonly punctuator: Promise<Punctuator> | null;
  /** The finalized words the model has punctuated, and those same words with punctuation and capitals. */
  private source: string[] = [];
  private punctuated: string[] = [];
  private punctuating: Promise<void> | null = null;
  private modelReady = false;
  private punctuationFailed = false;
  private recognizerEnded = false;

  /** The typed draft's unfinished sentence: context for the first dictated words, never changed. */
  private readonly lead: string[];

  constructor(stream: MediaStream, options: { lang?: string; before?: string; load?: () => Promise<Punctuator> } = {}) {
    const { lang = typeof navigator === "undefined" ? "" : navigator.language, before = "", load = loadPunctuator } = options;
    const Constructor = recognizerConstructor();
    if (!Constructor) throw new Error("This browser has no speech recognition.");
    this.stream = stream;
    this.recognizer = new Constructor();
    this.recognizer.continuous = true;
    this.recognizer.interimResults = true;
    if (lang) this.recognizer.lang = lang;
    this.punctuator = /^en\b/i.test(lang || "en") ? load() : null;
    const typed = before.split(/\s+/).filter(Boolean);
    let sentenceStart = typed.length;
    while (sentenceStart > 0 && !/[.?!]["')\]]*$/.test(typed[sentenceStart - 1]!)) sentenceStart--;
    this.lead = typed.slice(sentenceStart).slice(-CONTEXT_WORDS);
    this.punctuator?.then(
      () => { this.modelReady = true; },
      () => { this.punctuationFailed = true; },
    );
    this.recognizer.onresult = (event) => {
      if (this.state === "inactive") return;
      this.finals = [];
      this.interim = "";
      for (let index = 0; index < event.results.length; index++) {
        const result = event.results[index];
        const phrase = result?.[0]?.transcript.trim() ?? "";
        if (!result || !phrase) continue;
        if (result.isFinal) this.finals.push(phrase);
        else this.interim = [this.interim, phrase].filter(Boolean).join(" ");
      }
      this.sync();
      this.punctuate();
      this.onupdate?.(this.text);
    };
    this.recognizer.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") this.failure = "denied";
      else if (event.error !== "no-speech" && event.error !== "aborted") this.failure = "failed";
    };
    this.recognizer.onend = () => {
      if (this.recognizerEnded) return;
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
      this.recognizerDone();
    };
  }

  /** Final phrases first (punctuated as far as the model has reached), then the phrase still being recognized. */
  get text(): string {
    const finalized = this.finalizedWords();
    return [...this.punctuated, ...finalized.slice(this.punctuated.length), this.interim].filter(Boolean).join(" ");
  }

  private finalizedWords(): string[] {
    return [...this.settled, ...this.finals].flatMap((phrase) => phrase.split(/\s+/)).filter(Boolean);
  }

  /** Drop the punctuation of words the recognizer has since revised. */
  private sync() {
    const words = this.finalizedWords();
    let same = 0;
    while (same < this.source.length && this.source[same] === words[same]) same++;
    this.source.length = this.punctuated.length = same;
  }

  /**
   * Punctuate the finalized words the model has not yet seen, with earlier punctuated words as
   * context. One request at a time; phrases finalized meanwhile join the next one.
   */
  private punctuate() {
    const punctuator = this.punctuator;
    if (!punctuator || this.punctuating || this.punctuationFailed || this.aborting || this.state === "inactive") return;
    const words = this.finalizedWords();
    if (words.length <= this.punctuated.length) return;
    // Positions count the typed lead first; its words are context only.
    const lead = this.lead.length;
    const done = lead + this.punctuated.length;
    const from = Math.max(0, done - CONTEXT_WORDS);
    const keep = Math.max(lead, done - REVISE_WORDS);
    const input = [...this.lead, ...words].slice(from);
    const kept = this.punctuated.slice(0, keep - lead);
    this.punctuating = punctuator
      .then((model) => model.punctuate(input))
      .then((output) => {
        if (output.length !== input.length) throw new Error("The punctuation model changed the word count.");
        const revised = output.slice(keep - from);
        // A kept sentence end starts the next sentence, whatever the model now makes of that boundary.
        if (/[.?]$/.test(kept.at(-1) ?? "") && revised[0]) {
          const [first = "", ...rest] = revised[0];
          revised[0] = first.toUpperCase() + rest.join("");
        }
        this.punctuated = [...kept, ...revised];
        this.source = words;
        this.sync();
      })
      .catch(() => { this.punctuationFailed = true; })
      .finally(() => {
        this.punctuating = null;
        if (this.state === "inactive" || this.aborting) return;
        this.onupdate?.(this.text);
        this.punctuate();
      });
  }

  private recognizerDone() {
    if (this.recognizerEnded) return;
    this.recognizerEnded = true;
    // The recognizer has let go: the microphone is released and the next capture may start.
    this.stream.getTracks().forEach((track) => track.stop());
    this.ended();
    if (this.ending && !this.aborting) void this.finishPunctuation();
    else this.end();
  }

  /** After Stop: the recognizer has ended; end once every finalized word is punctuated, or at PUNCTUATE_MS. */
  private async finishPunctuation() {
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => this.end(), this.modelReady ? PUNCTUATE_MS : LOADING_MS);
    this.punctuate();
    while (this.punctuating && this.state !== "inactive") {
      await this.punctuating;
      this.punctuate();
    }
    this.end();
  }

  start() {
    this.state = "recording";
    this.startedAt = Date.now();
    this.recognizer.start();
  }

  /** Ask for the last phrase, then end once it is punctuated; a recognizer that never answers is abandoned at SETTLE_MS. */
  stop() {
    if (this.state !== "recording" || this.ending) return;
    this.ending = true;
    this.endWithin();
    try {
      this.recognizer.stop();
    } catch {
      this.recognizerDone();
    }
  }

  /**
   * Discard everything: no waiting for the last phrase. The recognizer lets go of the microphone
   * later; the capture ends with its own end, and `idle()` holds the next capture until then.
   */
  cancel() {
    if (this.state !== "recording" || this.aborting) return;
    this.ending = this.aborting = true;
    // Stopped already, only waiting for punctuation: nothing is left to abort.
    if (this.recognizerEnded) { this.end(); return; }
    this.endWithin();
    try {
      this.recognizer.abort();
    } catch {
      this.recognizerDone();
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
      this.recognizerDone();
    }, SETTLE_MS);
  }

  private end() {
    if (this.state === "inactive") return;
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.settleTimer = null;
    this.state = "inactive";
    const complete = this.punctuated.length >= this.finalizedWords().length;
    this.unpunctuated = !this.punctuator || complete ? null : this.modelReady || this.punctuationFailed ? "failed" : "loading";
    try {
      this.onstop?.();
    } finally {
      this.ended();
    }
  }
}
