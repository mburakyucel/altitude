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
