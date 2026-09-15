import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../data/api";
import Composer from "./Composer";
import type { ComposerProps } from "./Composer";
import type { ImageSubmission } from "./ImageDraft";
import { MessageImages } from "./MessageImages";
import { installVoiceBrowser } from "./voiceTest";

const capability = { available: true, max_count: 4, max_bytes: 10 << 20, max_total_bytes: 20 << 20, max_pixels: 25_000_000, max_dimension: 8192 };
const image = () => new File(["fictional raster"], "screen.png", { type: "image/png" });
let capabilityRead: Promise<unknown> | undefined;
const json = (body: unknown, status = 200) => {
  const response = new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  if (body && typeof body === "object" && "available" in body) {
    const parsed = response.json();
    capabilityRead = parsed;
    response.json = () => parsed;
  }
  return response;
};

function browser(available = true) {
  capabilityRead = undefined;
  const revoke = vi.fn();
  let next = 0;
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL() { return `blob:image-${++next}`; }
    static override revokeObjectURL = revoke;
  });
  vi.stubGlobal("Image", class {
    naturalWidth = 640; naturalHeight = 360;
    onload: (() => void) | null = null;
    set src(_url: string) { queueMicrotask(() => this.onload?.()); }
  });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("transcribe") ? json({ text: "the spoken explanation" }) : json({ ...capability, available, reason: available ? undefined : "Image decoder unavailable." })));
  return revoke;
}

function Harness({ initial = "Explain this", ...props }: Partial<ComposerProps> & { initial?: string }) {
  const [text, setText] = useState(initial);
  return <Composer conversation={`project/${props.imageScope?.project ?? "alpha"}`} value={text} onChange={setText} onSubmit={() => undefined} ariaLabel="Message" placeholder="Message" imageScope={{ project: "alpha" }} {...props} />;
}

async function select(files: File[] = [image()]) {
  // Request start does not mean the capability body has reached React yet.
  await act(async () => { await capabilityRead; });
  fireEvent.change(screen.getByLabelText("Choose images"), { target: { files } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add images" })).toBeEnabled());
  await screen.findByLabelText("Selected images");
}

describe("image draft admission", () => {
  it("voice navigation retains image bytes and the original retry callback without clearing another draft", async () => {
    browser();
    installVoiceBrowser();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("transcribe")) { await gate; return json({ text: "spoken" }); }
      return json(capability);
    }));
    const original = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission) => {
      if (original.mock.calls.length === 1) throw new TypeError("Lost image receipt");
    });
    const destination = vi.fn();
    const user = userEvent.setup();
    const first = render(<Harness onSubmit={original} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    render(<Harness initial="Other unsent recovery" onSubmit={destination} />);
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    await act(async () => release());
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not confirm send.");
    expect(original).toHaveBeenCalledOnce();
    const sent = original.mock.calls[0]?.[2];
    expect(sent?.images).toEqual([{ name: "screen.png", data: btoa("fictional raster") }]);
    expect(sent?.previews[0]?.url).toBe(`data:image/png;base64,${btoa("fictional raster")}`);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    expect(original).toHaveBeenCalledTimes(2);
    expect(original.mock.calls[1]?.[2]).toBe(sent);
    expect(destination).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Other unsent recovery");
  });

  it("keeps selection local, removes the strip and URLs, and leaves text intact", async () => {
    const revoke = browser();
    const user = userEvent.setup();
    render(<Harness />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    await select();
    expect(fetch).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Remove image screen.png" }));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
  });

  it("freezes an unconfirmed immutable send and retries the same identity and bytes", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission) => { if (onSubmit.mock.calls.length === 1) throw new TypeError("connection lost"); });
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not confirm send.");
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add images" })).toBeDisabled();
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    expect(onSubmit.mock.calls[1]?.[2]).toBe(onSubmit.mock.calls[0]?.[2]);
    expect(onSubmit.mock.calls[0]?.[2]?.images?.[0]).toEqual({ name: "screen.png", data: btoa("fictional raster") });
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.queryByLabelText("Selected images")).toBeNull();
  });

  it("restores a refused draft and its images, with a fresh identity for an edited retry", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission) => { if (onSubmit.mock.calls.length === 1) throw new ApiError(422, "Choose a smaller image."); });
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not sent. Choose a smaller image.");
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.getByLabelText("Selected images")).toBeVisible();
    await user.type(screen.getByRole("textbox"), " more");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit.mock.calls[1]?.[0]).toBe("Explain this more");
    expect(onSubmit.mock.calls[1]?.[2]?.request_id).not.toBe(onSubmit.mock.calls[0]?.[2]?.request_id);
  });

  it("rejects format, count, per-file and aggregate excess without replacing valid selections", async () => {
    browser();
    render(<Harness />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await select([new File(["svg"], "drawing.svg", { type: "image/svg+xml" })]);
    expect(screen.getByRole("alert")).toHaveTextContent("Choose PNG, JPEG or static WebP.");
    const large = new File(["x"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(large, "size", { value: (10 << 20) + 1 });
    await select([large]);
    expect(screen.getByRole("alert")).toHaveTextContent("exceeds 10 MiB");
    const ten = new File(["x"], "ten.png", { type: "image/png" });
    Object.defineProperty(ten, "size", { value: 10 << 20 });
    await select([ten, ten]);
    expect(screen.getByRole("alert")).toHaveTextContent("exceed 20 MiB total");
    fireEvent.click(screen.getByRole("button", { name: "Remove image ten.png" }));
    await select([image(), image(), image(), image()]);
    expect(screen.getAllByRole("button", { name: "Remove image screen.png" })).toHaveLength(4);
    expect(screen.getByRole("alert")).toHaveTextContent("Up to 4 images");
  });

  it("allows image-only sends; unavailable input leaves ordinary text usable", async () => {
    browser();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    const view = render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.clear(screen.getByRole("textbox"));
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0]).toBe("");
    view.unmount();
    browser(false);
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "Add images" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Image input unavailable. Image decoder unavailable.");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("keeps previews through voice cancel, and sends only a nonempty transcript with images", async () => {
    browser();
    installVoiceBrowser();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    expect(screen.queryByRole("button", { name: "Add images" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(screen.getByLabelText("Selected images")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0]).toBe("Explain this the spoken explanation");
    expect(onSubmit.mock.calls[0]?.[2]?.images).toHaveLength(1);
  });

  it("releases local previews on navigation and a late admission cannot restore another draft", async () => {
    const revoke = browser();
    let refuse: (error: Error) => void = () => undefined;
    const onSubmit = () => new Promise<void>((_resolve, reject) => { refuse = reject; });
    const user = userEvent.setup();
    const view = render(<Harness key="alpha" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Sending images…");
    view.rerender(<Harness key="beta" imageScope={{ project: "beta" }} />);
    refuse(new ApiError(422, "refused"));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Explain this"));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
  });

  it("recovers an unconfirmed image caption after leaving, without persisting or resending the image", async () => {
    browser();
    const onSubmit = vi.fn(async () => { throw new TypeError("Lost receipt"); });
    const user = userEvent.setup();
    const view = render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Could not confirm send.", { exact: false });
    view.unmount();
    render(<Harness initial="" onSubmit={onSubmit} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the conversation before sending again.");
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(sessionStorage.getItem("altitude.submitted:project/alpha")).toContain("Explain this");
    expect(sessionStorage.getItem("altitude.submitted:project/alpha")).not.toContain("fictional raster");
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("keeps selected images editable while Stop holds sending", async () => {
    browser();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} sendDisabled />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(screen.getByRole("textbox"), " correction{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove image screen.png" }));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
  });

  it.each(["", null])("voice Send with transcript %s retains images and sends nothing", async (transcript) => {
    browser(); installVoiceBrowser();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("transcribe")
      ? transcript === null ? json({ error: "unavailable" }, 503) : json({ text: transcript })
      : json(capability)));
    const onSubmit = vi.fn(); const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await select();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.getByLabelText("Selected images")).toBeVisible();
  });

  it.each([false, true])("late image decoding cannot repopulate another scope (remounted=%s)", async (remount) => {
    const revoke = browser();
    const view = render(<Harness key="alpha" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await select();
    let loaded = () => undefined;
    vi.stubGlobal("Image", class {
      naturalWidth = 640; naturalHeight = 360;
      onload: (() => void) | null = null;
      set src(_url: string) { loaded = () => { this.onload?.(); }; }
    });
    fireEvent.change(screen.getByLabelText("Choose images"), { target: { files: [new File(["late"], "late.png", { type: "image/png" })] } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    view.rerender(<Harness key={remount ? "beta" : "alpha"} imageScope={{ project: "beta" }} />);
    await act(async () => { loaded(); });
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
    expect(revoke).toHaveBeenCalledWith("blob:image-2");
  });

  it.each([false, true])("an older text refusal survives newer image admission (recovery write fails: %s)", async (writeFails) => {
    browser();
    let refuseText: (error: Error) => void = () => undefined;
    let acceptImage: () => void = () => undefined;
    const onSubmit = vi.fn((_text: string, _accepted: () => void, images?: ImageSubmission) => images
      ? new Promise<void>((resolve) => { acceptImage = resolve; })
      : new Promise<void>((_resolve, reject) => { refuseText = reject; }));
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "Send" }));
    await select(); await user.type(screen.getByRole("textbox"), "Caption for the image");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    await act(async () => { refuseText(new ApiError(409, "Earlier text was refused")); });
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByText("Sending images…")).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    if (writeFails) vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
    await act(async () => { acceptImage(); });
    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.getByRole("alert")).toHaveTextContent("Not sent.");
    if (writeFails) expect(screen.getByRole("alert")).toHaveTextContent("Recovery could not be updated.");
  });
});

describe("private image reads", () => {
  it("distinguishes denied/missing reads and Retry repeats only that image request", async () => {
    browser();
    let status = 403;
    vi.stubGlobal("fetch", vi.fn(async () => json({}, status)));
    const user = userEvent.setup();
    render(<MessageImages project="alpha" images={[{ id: "abc", name: "screen.png", mime_type: "image/png", size: 12, width: 2, height: 2, source_message_id: "message" }]} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Image access denied.");
    status = 404;
    await user.click(screen.getByRole("button", { name: "Retry image screen.png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Image unavailable.");
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/images/alpha/abc", "/api/images/alpha/abc"]);
  });
});
