import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import VoiceComposer, { combineDraft } from "./VoiceComposer";
import { FakeMediaRecorder, installVoiceBrowser } from "./voiceTest";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function Harness({
  initial = "",
  onSubmit = vi.fn(async () => {}),
}: {
  initial?: string;
  onSubmit?: (text: string) => void | Promise<void>;
}) {
  const [value, setValue] = useState(initial);
  return (
    <VoiceComposer
      value={value}
      onChange={setValue}
      onSubmit={onSubmit}
      ariaLabel="Test message"
      placeholder="Write a message"
    />
  );
}

async function recordOne(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Start voice recording" }));
  await screen.findByRole("button", { name: "Stop voice recording" });
  await user.click(screen.getByRole("button", { name: "Stop voice recording" }));
}

describe("VoiceComposer", () => {
  it("keeps the draft separate until Edit / insert and restores focus at the end", async () => {
    installVoiceBrowser();
    const fetchMock = vi.fn(async () => jsonResponse({ text: "dictated follow-up" }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Harness initial="Existing draft." />);

    await recordOne(user);

    const review = await screen.findByRole("region", { name: "Voice transcript review" });
    const input = screen.getByLabelText("Test message");
    expect(review).toHaveTextContent("dictated follow-up");
    expect(screen.getByLabelText("Voice transcript")).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("button", { name: "Voice transcript awaiting review" })).toBeDisabled();
    expect(input).toHaveValue("Existing draft.");
    expect(screen.getByRole("button", { name: "Edit / insert" })).toHaveFocus();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/transcribe",
      expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }),
    );

    await user.click(screen.getByRole("button", { name: "Edit / insert" }));
    expect(input).toHaveValue("Existing draft. dictated follow-up");
    await waitFor(() => expect(input).toHaveFocus());
    expect((input as HTMLTextAreaElement).selectionStart).toBe(
      "Existing draft. dictated follow-up".length,
    );
  });

  it("never sends on transcription alone and sends the combined text only on the explicit action", async () => {
    installVoiceBrowser();
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ text: "spoken ending" })));
    const onSubmit = vi.fn(async () => {});
    const user = userEvent.setup();
    render(<Harness initial="Typed beginning" onSubmit={onSubmit} />);

    await recordOne(user);
    await screen.findByRole("region", { name: "Voice transcript review" });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByLabelText("Test message"), { key: "Enter", metaKey: true });
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledWith("Typed beginning spoken ending");
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Voice transcript review" })).toBeNull(),
    );
    expect(screen.getByLabelText("Test message")).toHaveFocus();
  });

  it("announces recording and transcribing, and cancel leaves the draft usable", async () => {
    const voice = installVoiceBrowser();
    const fetchMock = vi.fn();
    const onSubmit = vi.fn(async () => {});
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Harness initial="Do not lose this" onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Recording… press Stop");
    expect(screen.getByText("0:00")).toHaveAttribute("aria-hidden", "true");
    fireEvent.keyDown(screen.getByLabelText("Test message"), { key: "Enter", metaKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));

    const input = screen.getByLabelText("Test message");
    expect(input).toHaveValue("Do not lose this");
    await waitFor(() => expect(input).toHaveFocus());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(voice.track.stop).toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("draft is unchanged");
  });

  it("announces transcription while the server is working", async () => {
    installVoiceBrowser();
    let finish: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Transcribing your recording");
    expect(screen.getByRole("button", { name: "Cancel voice input" })).toBeEnabled();

    await act(async () => finish?.(jsonResponse({ text: "ready now" })));
    expect(await screen.findByRole("region", { name: "Voice transcript review" })).toHaveTextContent(
      "ready now",
    );
  });

  it("keeps typing available after permission denial and transcription failure", async () => {
    const voice = installVoiceBrowser();
    voice.getUserMedia.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: "Whisper is asleep" }, 503)));
    const user = userEvent.setup();
    render(<Harness initial="Safe draft" />);

    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Allow it for Altitude in Safari settings");
    expect(screen.getByLabelText("Test message")).toHaveValue("Safe draft");

    await recordOne(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Whisper is asleep");
    expect(screen.getByLabelText("Test message")).toBeEnabled();
    expect(screen.getByLabelText("Test message")).toHaveValue("Safe draft");
  });

  it("explains the HTTPS prerequisite while leaving the composer functional", async () => {
    installVoiceBrowser();
    vi.stubGlobal("isSecureContext", false);
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByRole("status")).toHaveTextContent("Voice input needs HTTPS");
    const microphone = screen.getByRole("button", { name: "Voice input unavailable" });
    expect(microphone).toHaveAttribute("aria-disabled", "true");
    await user.type(screen.getByLabelText("Test message"), "typing still works");
    expect(screen.getByLabelText("Test message")).toHaveValue("typing still works");
  });

  it("submits an ordinary draft with the documented keyboard shortcut", async () => {
    installVoiceBrowser();
    const onSubmit = vi.fn(async () => {});
    render(<Harness initial="keyboard message" onSubmit={onSubmit} />);

    fireEvent.keyDown(screen.getByLabelText("Test message"), { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("keyboard message"));
  });

  it("makes Stop idempotent while browser data and stop events are queued", async () => {
    installVoiceBrowser();
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ text: "one result" })));
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    const stop = await screen.findByRole("button", { name: "Stop voice recording" });
    fireEvent.click(stop);
    fireEvent.click(stop);

    expect(FakeMediaRecorder.instances[0]?.stopCalls).toBe(1);
    expect(await screen.findByRole("region", { name: "Voice transcript review" })).toHaveTextContent(
      "one result",
    );
  });

  it("keeps a failed voice Send editable and clears its alert after insertion", async () => {
    installVoiceBrowser();
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ text: "still recoverable" })));
    const onSubmit = vi.fn(async () => { throw new Error("offline"); });
    const user = userEvent.setup();
    render(<Harness initial="Original" onSubmit={onSubmit} />);

    await recordOne(user);
    await screen.findByRole("region", { name: "Voice transcript review" });
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("transcript is still here");

    await user.click(screen.getByRole("button", { name: "Edit / insert" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Test message")).toHaveValue("Original still recoverable");
  });

  it("preserves whitespace drafts and respects an existing separator", () => {
    expect(combineDraft("   ", "dictated")).toBe("   dictated");
    expect(combineDraft("first\n", "dictated")).toBe("first\ndictated");
    expect(combineDraft("first", "dictated")).toBe("first dictated");
  });
});
