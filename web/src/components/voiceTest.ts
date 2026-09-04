import { vi } from "vitest";

/** Browser voice primitives for route/component tests; emits one AAC/mp4 blob when stopped. */
export class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static isTypeSupported(type: string) {
    return type === "audio/mp4";
  }

  state: RecordingState = "inactive";
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  stopCalls = 0;

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType ?? "audio/mp4";
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.stopCalls += 1;
    this.state = "inactive";
    queueMicrotask(() => {
      this.ondataavailable?.({ data: new Blob(["aac recording"], { type: this.mimeType }) });
      this.onstop?.();
    });
  }
}

export function installVoiceBrowser() {
  FakeMediaRecorder.instances = [];
  const track = { stop: vi.fn() };
  const mediaStream = { getTracks: () => [track] } as unknown as MediaStream;
  const getUserMedia = vi.fn(async () => mediaStream);
  const voiceNavigator = Object.create(window.navigator) as Navigator;
  Object.defineProperty(voiceNavigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  vi.stubGlobal("navigator", voiceNavigator);
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  return { getUserMedia, track };
}
