import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecognitionCapture } from "./recognition";
import { FakeSpeechRecognition } from "./voiceTest";

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

  it("requests native punctuation when supported and preserves the recognizer's text", () => {
    class PunctuatingRecognition extends FakeSpeechRecognition {
      unspokenPunctuation = false;
    }
    vi.stubGlobal("SpeechRecognition", PunctuatingRecognition);
    const capture = new RecognitionCapture(stream, "en-US");
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0] as PunctuatingRecognition;
    expect(recognizer.unspokenPunctuation).toBe(true);
    recognizer.hear(["Hello, world!"], "Is this");
    expect(capture.text).toBe("Hello, world! Is this");
    recognizer.hear(["Hello, world!", "Is this ready?"]);
    capture.stop();
    expect(capture.text).toBe("Hello, world! Is this ready?");
  });

  it("keeps dictation unchanged when native punctuation is unavailable", () => {
    const capture = new RecognitionCapture(stream, "en-US");
    capture.start();
    const recognizer = FakeSpeechRecognition.instances[0]!;
    expect("unspokenPunctuation" in recognizer).toBe(false);
    recognizer.hear(["a fragment", "of one sentence"]);
    capture.stop();
    expect(capture.text).toBe("a fragment of one sentence");
  });

  it("Stop that the recognizer never answers still ends within three seconds with the words so far", () => {
    const capture = new RecognitionCapture(stream, "en-US");
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    recognizer!.hear(["kept"]);
    capture.stop();
    capture.stop();
    expect(recognizer!.stopped).toBe(1);
    vi.advanceTimersByTime(2999);
    expect(stopped).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(stopped).toHaveBeenCalledOnce();
    expect(recognizer!.aborted).toBe(1);
    expect(capture.state).toBe("inactive");
    expect(capture.text).toBe("kept");
    expect(capture.failure).toBeNull();
  });

  it("restarts after silence, but five immediate ends in a row are a failure instead of a loop", () => {
    const capture = new RecognitionCapture(stream, "en-US");
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

  it("cancel ends at once and ignores the recognizer's later end", () => {
    const capture = new RecognitionCapture(stream, "en-US");
    const stopped = vi.fn();
    capture.onstop = stopped;
    capture.start();
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    capture.cancel();
    expect(stopped).toHaveBeenCalledOnce();
    expect(recognizer!.aborted).toBe(1);
    recognizer!.silence();
    expect(recognizer!.started).toBe(1);
    expect(stopped).toHaveBeenCalledOnce();
  });
});
