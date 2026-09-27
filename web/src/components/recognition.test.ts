import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecognitionCapture } from "./recognition";
import { FakeSpeechRecognition, punctuationFixture, sentence } from "./voiceTest";

/* The recognizer wrapper's timing rules; the composer tests cover what the user sees. */
describe("RecognitionCapture", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSpeechRecognition.instances = [];
    vi.stubGlobal("SpeechRecognition", FakeSpeechRecognition);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const stream = { getTracks: () => [] } as unknown as MediaStream;

  it("punctuates finalized phrases and shows the phrase being recognized as heard", async () => {
    punctuationFixture.punctuate = sentence;
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const updates: string[] = [];
    capture.onupdate = (text) => updates.push(text);
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.hear(["so i think"], "we should");
    expect(capture.text).toBe("so i think we should");
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("So i think. we should");
    expect(updates.at(-1)).toBe("So i think. we should");
    // The next phrase revises the previous phrase's end: here it runs on into one sentence.
    recognizer.hear(["so i think", "we should merge"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("So i think we should merge.");
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.state).toBe("inactive");
    expect(capture.text).toBe("So i think we should merge.");
    expect(capture.unpunctuated).toBeNull();
  });

  it("keeps earlier punctuation fixed and lets the model revise only the last words", async () => {
    punctuationFixture.punctuate = sentence;
    const seen: string[][] = [];
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    const first = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen";
    recognizer.hear([first]);
    await vi.advanceTimersByTimeAsync(0);
    punctuationFixture.punctuate = (words) => { seen.push([...words]); return sentence(words); };
    recognizer.hear([first, "nineteen twenty"]);
    await vi.advanceTimersByTimeAsync(0);
    // Sixteen words of context; the four before the new phrase are revised; earlier words stay.
    expect(seen[0]).toHaveLength(18);
    expect(seen[0]![0]).toBe("three");
    expect(capture.text).toBe("One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty.");
  });

  it("reads the typed draft's last words as context and never changes them", async () => {
    const seen: string[][] = [];
    punctuationFixture.punctuate = (words) => { seen.push([...words]); return words.map((word) => word.toUpperCase()); };
    const capture = new RecognitionCapture(stream, { lang: "en-US", before: "Fix the timer" });
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["and the tests"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual([["Fix", "the", "timer", "and", "the", "tests"]]);
    expect(capture.text).toBe("AND THE TESTS");
  });

  it("reads only the typed draft's unfinished sentence as context", async () => {
    const seen: string[][] = [];
    punctuationFixture.punctuate = (words) => { seen.push([...words]); return [...words]; };
    for (const before of ["Fixed the timer. Now the", "Fixed the timer."]) {
      const capture = new RecognitionCapture(stream, { lang: "en-US", before });
      capture.start();
      FakeSpeechRecognition.instances.at(-1)!.hear(["tests"]);
      await vi.advanceTimersByTimeAsync(0);
    }
    expect(seen).toEqual([["Now", "the", "tests"], ["tests"]]);
  });

  it("drops the punctuation of words the recognizer revises", async () => {
    punctuationFixture.punctuate = sentence;
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.hear(["so i think"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("So i think.");
    recognizer.hear(["so i thought"]);
    expect(capture.text).toBe("So i thought");
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("So i thought.");
  });

  it("starts a new sentence after a kept sentence end, whatever the model now makes of it", async () => {
    punctuationFixture.punctuate = (words) => words.map((word) => (word === "two" ? "two." : word));
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.hear(["one two three four five six"]);
    await vi.advanceTimersByTimeAsync(0);
    punctuationFixture.punctuate = (words) => [...words];
    recognizer.hear(["one two three four five six", "seven"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("one two. Three four five six seven");
  });

  it("Stop releases the microphone at once and waits for the last phrase's punctuation before ending", async () => {
    let answer: (() => void) | undefined;
    punctuationFixture.load = () => Promise.resolve({
      punctuate: (words) => new Promise<string[]>((resolve) => { answer = () => resolve(sentence(words)); }),
    });
    const track = { stop: vi.fn() };
    const capture = new RecognitionCapture({ getTracks: () => [track] } as unknown as MediaStream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    FakeSpeechRecognition.instances[0]!.hear(["merge it today"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(stopped).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalledOnce();
    expect(RecognitionCapture.idle()).toBeNull();
    answer!();
    await vi.advanceTimersByTimeAsync(0);
    expect(stopped).toHaveBeenCalledOnce();
    expect(capture.text).toBe("Merge it today.");
    expect(capture.unpunctuated).toBeNull();
  });

  it("punctuation not finished ten seconds after Stop ends with the words as recognized", async () => {
    punctuationFixture.load = () => Promise.resolve({ punctuate: () => new Promise<string[]>(() => undefined) });
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    FakeSpeechRecognition.instances[0]!.hear(["merge it today"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(9999);
    expect(stopped).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(stopped).toHaveBeenCalledOnce();
    expect(capture.text).toBe("merge it today");
    expect(capture.unpunctuated).toBe("failed");
    expect(capture.failure).toBeNull();
  });

  it("a model still loading holds Stop for three seconds at most, and says it was loading", async () => {
    punctuationFixture.load = () => new Promise(() => undefined);
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["merge it today"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(2999);
    expect(stopped).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(stopped).toHaveBeenCalledOnce();
    expect(capture.text).toBe("merge it today");
    expect(capture.unpunctuated).toBe("loading");
  });

  it("a model that cannot load leaves the words as recognized, marked unpunctuated", async () => {
    punctuationFixture.load = () => Promise.reject(new Error("no WebAssembly"));
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["merge it today"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(stopped).toHaveBeenCalledOnce();
    expect(capture.text).toBe("merge it today");
    expect(capture.unpunctuated).toBe("failed");
  });

  it("cancel while Stop waits for punctuation ends at once", async () => {
    punctuationFixture.load = () => new Promise(() => undefined);
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["merge it today"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(stopped).not.toHaveBeenCalled();
    capture.cancel();
    expect(stopped).toHaveBeenCalledOnce();
    expect(RecognitionCapture.idle()).toBeNull();
  });

  it.each(["cancel", "cancel after Stop", "timeout"])("%s while the model loads does not queue abandoned words ahead of the next capture", async (ending) => {
    let loaded!: (model: { punctuate: (words: readonly string[]) => Promise<string[]> }) => void;
    const loading = new Promise<{ punctuate: (words: readonly string[]) => Promise<string[]> }>((resolve) => { loaded = resolve; });
    const punctuate = vi.fn(async (words: readonly string[]) => sentence(words));
    punctuationFixture.load = () => loading;
    const abandoned = new RecognitionCapture(stream, { lang: "en-US" });
    abandoned.start();
    FakeSpeechRecognition.instances[0]!.hear(["discard these words"]);
    if (ending !== "cancel") abandoned.stop();
    if (ending === "timeout") await vi.advanceTimersByTimeAsync(3000);
    else abandoned.cancel();
    await vi.advanceTimersByTimeAsync(0);
    expect(abandoned.state).toBe("inactive");

    const next = new RecognitionCapture(stream, { lang: "en-US" });
    next.start();
    FakeSpeechRecognition.instances[1]!.hear(["fresh words"]);
    loaded({ punctuate });
    await vi.advanceTimersByTimeAsync(0);
    next.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(punctuate.mock.calls).toEqual([[["fresh", "words"]]]);
    expect(next.text).toBe("Fresh words.");
    expect(abandoned.text).toBe("discard these words");
  });

  it.each(["cancel", "timeout"])("in-flight punctuation cannot change a capture after %s", async (ending) => {
    let answer!: (words: string[]) => void;
    punctuationFixture.load = async () => ({ punctuate: () => new Promise<string[]>((resolve) => { answer = resolve; }) });
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    const updated = vi.fn();
    capture.onstop = stopped;
    capture.onupdate = updated;
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["words already landed"]);
    await vi.advanceTimersByTimeAsync(0);
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    if (ending === "cancel") capture.cancel();
    else await vi.advanceTimersByTimeAsync(10000);
    expect(capture.state).toBe("inactive");
    updated.mockClear();
    answer(["Words", "already", "landed."]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("words already landed");
    expect(stopped).toHaveBeenCalledOnce();
    expect(updated).not.toHaveBeenCalled();
  });

  it("Cancel ignores native words and errors while its recognizer is still ending", async () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.answersAbort = false;
    recognizer.hear(["discarded"]);
    await vi.advanceTimersByTimeAsync(0);
    const updated = vi.fn();
    capture.onupdate = updated;
    capture.cancel();
    recognizer.hear(["late cancelled words"]);
    recognizer.onerror?.({ error: "not-allowed" });
    expect(capture.text).toBe("discarded");
    expect(capture.failure).toBeNull();
    expect(updated).not.toHaveBeenCalled();
    recognizer.silence();
    expect(capture.state).toBe("inactive");
  });

  it("Stop accepts its final phrase before native end and ignores later words and refusal during punctuation", async () => {
    let answer!: (words: string[]) => void;
    punctuationFixture.load = async () => ({ punctuate: () => new Promise<string[]>((resolve) => { answer = resolve; }) });
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.answersStop = false;
    recognizer.hear([], "last");
    capture.stop();
    recognizer.hear(["last phrase"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("last phrase");
    recognizer.silence();
    recognizer.hear(["late replacement"]);
    recognizer.onerror?.({ error: "not-allowed" });
    expect(capture.text).toBe("last phrase");
    expect(capture.failure).toBeNull();
    expect(stopped).not.toHaveBeenCalled();
    answer(["Last", "phrase."]);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.text).toBe("Last phrase.");
    expect(capture.failure).toBeNull();
    expect(stopped).toHaveBeenCalledOnce();
  });

  it("other languages load no model and keep the recognizer's text", async () => {
    const load = vi.fn(punctuationFixture.load);
    punctuationFixture.load = load;
    const capture = new RecognitionCapture(stream, { lang: "de-DE" });
    capture.start();
    FakeSpeechRecognition.instances[0]!.hear(["guten morgen"]);
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(load).not.toHaveBeenCalled();
    expect(capture.text).toBe("guten morgen");
    expect(capture.unpunctuated).toBeNull();
  });

  it("Stop that the recognizer never answers still ends within three seconds with the words so far", async () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    recognizer!.hear(["kept"]);
    capture.stop();
    capture.stop();
    expect(recognizer!.stopped).toBe(1);
    await vi.advanceTimersByTimeAsync(2999);
    expect(stopped).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(stopped).toHaveBeenCalledOnce();
    expect(recognizer!.aborted).toBe(1);
    expect(capture.state).toBe("inactive");
    expect(capture.text).toBe("kept");
    expect(capture.failure).toBeNull();
  });

  it("restarts after silence, but five immediate ends in a row are a failure instead of a loop", () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.hear(["first"]);
    vi.advanceTimersByTime(5000);
    recognizer!.silence();
    expect(recognizer!.started).toBe(2);
    for (let count = 0; count < 4; count++) recognizer!.silence();
    expect(recognizer!.started).toBe(6);
    expect(stopped).not.toHaveBeenCalled();
    recognizer!.silence();
    expect(recognizer!.started).toBe(6);
    expect(stopped).toHaveBeenCalledOnce();
    expect(capture.failure).toBe("failed");
    expect(capture.text).toBe("first");
  });

  it("cancel aborts at once and ends with the recognizer's own end, once", () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersAbort = false;
    capture.cancel();
    capture.cancel();
    expect(recognizer!.aborted).toBe(1);
    expect(stopped).not.toHaveBeenCalled();
    recognizer!.silence();
    expect(recognizer!.started).toBe(1);
    expect(stopped).toHaveBeenCalledOnce();
    recognizer!.silence();
    expect(stopped).toHaveBeenCalledOnce();
  });

  it("cancel that the recognizer never answers still ends within three seconds", () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersAbort = false;
    capture.cancel();
    vi.advanceTimersByTime(2999);
    expect(stopped).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(stopped).toHaveBeenCalledOnce();
    recognizer!.silence();
    expect(recognizer!.started).toBe(1);
    expect(stopped).toHaveBeenCalledOnce();
  });

  it("idle() resolves once every capture asked to end has ended", async () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersAbort = false;
    const idle = vi.fn();
    expect(RecognitionCapture.idle()).toBeNull();
    capture.cancel();
    void RecognitionCapture.idle()!.then(idle);
    await Promise.resolve();
    expect(idle).not.toHaveBeenCalled();
    recognizer!.silence();
    await Promise.resolve();
    expect(idle).toHaveBeenCalledOnce();
    expect(RecognitionCapture.idle()).toBeNull();
  });

  it("cancel during Stop's wait for the last phrase aborts without waiting further", () => {
    const capture = new RecognitionCapture(stream, { lang: "en-US" });
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    capture.stop();
    capture.cancel();
    expect(recognizer!.aborted).toBe(1);
    return Promise.resolve().then(() => expect(stopped).toHaveBeenCalledOnce());
  });
});
