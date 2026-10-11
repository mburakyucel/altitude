import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import { ApiError } from "../data/api";
import Composer, { returnToComposer } from "./Composer";
import type { ComposerProps } from "./Composer";
import type { ImageSubmission } from "./ImageDraft";
import { ImageViewerHost, MessageImages } from "./MessageImages";
import { hostMicrophone, hostVoiceServer, installVoiceBrowser, speak } from "./voiceTest";

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
  vi.stubGlobal("fetch", vi.fn(async () => json({ ...capability, available, reason: available ? undefined : "Image decoder unavailable." })));
  return revoke;
}

/** This computer's speech service, answering `final` at the end; every other request reads image input. */
function hostVoice(final: string) {
  return hostVoiceServer({ final, fallback: async () => json(capability) }).install();
}

/** Start host voice and say half a second, so Send and Stop act on a listening capture. */
async function listen(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Start voice input" }));
  await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
  speak();
  await screen.findByText("Listening… Stop to add text, or Send.");
}

/** jsdom has no modal dialogs; opening only needs the dialog shown. */
function modal() {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  onTestFinished(() => { delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal; });
}

function Harness({ initial = "Explain this", ...props }: Partial<ComposerProps> & { initial?: string }) {
  const [text, setText] = useState(initial);
  return <Composer conversation={`project/${props.imageScope?.project ?? "alpha"}`} value={text} onChange={setText} onSubmit={() => undefined} ariaLabel="Message" placeholder="Message" imageScope={{ project: "alpha" }} {...props} />;
}

/**
 * A page's outbox (Conversation.tsx, Task.tsx): a sent message shows its copy, through view changes and remounts,
 * until the send is accepted. `keep` wraps a send so; only a reload or pairing again loses the outbox (`forget`).
 */
function outbox() {
  const shown = new Set<string>();
  return {
    shows: (copy: string) => shown.has(copy),
    forget: () => shown.clear(),
    keep: (submit: ComposerProps["onSubmit"]): ComposerProps["onSubmit"] => (text, accepted, images, copy) => {
      shown.add(copy!);
      return submit(text, () => { shown.delete(copy!); accepted(); }, images, copy);
    },
  };
}

async function select(files: File[] = [image()]) {
  // Request start does not mean the capability body has reached React yet.
  await act(async () => { await capabilityRead; });
  fireEvent.change(screen.getByLabelText("Choose images"), { target: { files } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add images" })).toBeEnabled());
  await screen.findByLabelText("Selected images");
}

describe("image draft admission", () => {
  it("voice navigation sends image bytes through the original callback, and its failure leaves another draft alone", async () => {
    browser();
    installVoiceBrowser();
    const server = hostVoice("spoken");
    const original = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission) => {
      if (original.mock.calls.length === 1) throw new TypeError("Lost image receipt");
    });
    const destination = vi.fn();
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    const first = render(<Harness onSubmit={keep(original)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await listen(user);
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    render(<Harness initial="Other unsent recovery" onSubmit={keep(destination)} shows={shows} />);
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    await act(async () => server.reconnect());
    await waitFor(() => expect(original).toHaveBeenCalledOnce());
    const sent = original.mock.calls[0]?.[2];
    expect(sent?.images).toEqual([{ name: "screen.png", data: btoa("fictional raster") }]);
    expect(sent?.previews[0]?.url).toBe(`data:image/png;base64,${btoa("fictional raster")}`);
    // The conversation shows the failure on its message; the field keeps what was typed since.
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Other unsent recovery"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(destination).not.toHaveBeenCalled();
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

  it("a refused message's words and images edited and sent again go under a fresh identity", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission, _copy?: string) => { if (onSubmit.mock.calls.length === 1) throw new ApiError(422, "Choose a smaller image."); });
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    render(<Harness conversation="project/edited-images" onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    // The message shows the refusal; the field and picker stay as Send left them until Edit.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    const [text, , sent] = onSubmit.mock.calls[0]!;
    await act(async () => returnToComposer("project/edited-images", text, sent!.previews));
    await screen.findByLabelText("Selected images");
    await user.type(screen.getByRole("textbox"), " more");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit.mock.calls[1]?.[0]).toBe("Explain this more");
    expect(onSubmit.mock.calls[1]?.[2]?.images).toEqual(sent!.images);
    expect(onSubmit.mock.calls[1]?.[2]?.request_id).not.toBe(sent!.request_id);
  });

  it("where the message shows its own failure, Send hands the images to it and Edit returns them to the picker", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission, _copy?: string) => { throw new ApiError(422, "refused"); });
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    render(<Harness conversation="project/edit-images" onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const [, , submission, copy] = onSubmit.mock.calls[0]!;
    expect(copy).toEqual(expect.any(String));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.queryByText("Sending images…")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    await user.type(screen.getByRole("textbox"), "Typed since");
    await act(async () => returnToComposer("project/edit-images", "Explain this", submission!.previews));
    expect(screen.getByRole("textbox")).toHaveValue("Explain this\nTyped since");
    const strip = await screen.findByLabelText("Selected images");
    expect(strip.querySelectorAll("img")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
  });

  it("where the message shows its own state, the next message sends while images are still on their way", async () => {
    browser();
    let accept!: () => void;
    const onSubmit = vi.fn((_text: string, accepted: () => void, images?: ImageSubmission) =>
      images ? new Promise<void>((resolve) => { accept = () => { accepted(); resolve(); }; }) : Promise.resolve());
    const user = userEvent.setup();
    render(<Harness conversation="project/next-during-images" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    await user.type(screen.getByRole("textbox"), "And this next");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit.mock.calls[1]![0]).toBe("And this next");
    // Images chosen meanwhile stay when the first message is accepted.
    await select();
    await act(async () => accept());
    expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(1);
  });

  it("where the message shows its own state, Send hands its images over at once and the next message follows in order", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, accepted: () => void, _images?: ImageSubmission) => { accepted(); });
    const user = userEvent.setup();
    render(<Harness conversation="project/send-order" initial="" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.type(screen.getByRole("textbox"), "With the image");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    await user.type(screen.getByRole("textbox"), "Words only");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit.mock.calls.map(([text, , images]) => [text, images?.images?.length])).toEqual([["With the image", 1], ["Words only", undefined]]);
  });

  it("names an image it cannot read when it is chosen, keeping the others", async () => {
    browser();
    const read = FileReader.prototype.readAsDataURL;
    const spy = vi.spyOn(FileReader.prototype, "readAsDataURL").mockImplementation(function (this: FileReader, blob: Blob) {
      if ((blob as File).name === "broken.png") { setTimeout(() => this.onerror?.(new ProgressEvent("error") as ProgressEvent<FileReader>)); return; }
      read.call(this, blob);
    });
    try {
      render(<Harness conversation="project/unreadable" />);
      await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
      await select([image(), new File(["fictional raster"], "broken.png", { type: "image/png" })]);
      expect(await screen.findByRole("alert")).toHaveTextContent("broken.png could not be read.");
      expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(1);
    } finally { spy.mockRestore(); }
  });

  it("Edit changes nothing when the message's images do not fit beside the picker's, and says why", async () => {
    browser();
    render(<Harness conversation="project/full-picker" initial="" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select([image(), image(), image(), image()]);
    const preview = { name: "earlier.png", url: "data:image/png;base64,iVBORw0KGgo=" };
    let returned = true;
    await act(async () => { returned = returnToComposer("project/full-picker", "Earlier words", [preview]); });
    expect(returned).toBe(false);
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(4);
    expect(screen.getByRole("alert")).toHaveTextContent("Up to 4 images per message. Remove an image to add another.");
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
    hostVoice("the spoken explanation");
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await listen(user);
    expect(screen.queryByRole("button", { name: "Add images" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(screen.getByLabelText("Selected images")).toBeVisible();
    await listen(user);
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0]).toBe("Explain this the spoken explanation");
    expect(onSubmit.mock.calls[0]?.[2]?.images).toHaveLength(1);
  });

  it("releases local previews once sent, and a late refusal after navigation cannot restore another draft", async () => {
    const revoke = browser();
    let refuse: (error: Error) => void = () => undefined;
    const onSubmit = () => new Promise<void>((_resolve, reject) => { refuse = reject; });
    const user = userEvent.setup();
    const view = render(<Harness key="alpha" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    // The message's bubble shows the images from Send; the picker lets go of its own previews.
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
    view.rerender(<Harness key="beta" initial="Beta draft" imageScope={{ project: "beta" }} />);
    await act(async () => refuse(new ApiError(422, "refused")));
    expect(screen.getByRole("textbox")).toHaveValue("Beta draft");
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("recovers an unconfirmed image caption after a reload, without persisting or resending the image", async () => {
    browser();
    const onSubmit = vi.fn(async () => { throw new TypeError("Lost receipt"); });
    const { shows, forget, keep } = outbox();
    const user = userEvent.setup();
    const view = render(<Harness onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    view.unmount();
    // A reload loses the message that showed the caption.
    forget();
    render(<Harness initial="" onSubmit={keep(onSubmit)} shows={shows} />);
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
    const server = hostVoice(transcript ?? "");
    const onSubmit = vi.fn(); const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await select();
    await listen(user);
    await waitFor(() => expect(server.audio).toHaveLength(1));
    if (transcript === null) server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
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
});

describe("private image reads", () => {
  it("distinguishes denied/missing reads and Retry repeats only that image request", async () => {
    browser();
    let status = 403;
    vi.stubGlobal("fetch", vi.fn(async () => json({}, status)));
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={[{ id: "abc", name: "screen.png", mime_type: "image/png", size: 12, width: 2, height: 2, source_message_id: "message" }]} /></ImageViewerHost>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Image access denied.");
    status = 404;
    await user.click(screen.getByRole("button", { name: "Retry image screen.png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Image unavailable.");
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/images/alpha/abc", "/api/images/alpha/abc"]);
  });

  it("keeps an open viewer and its zoom when the opener's row is replaced, then focuses the replacement", async () => {
    const revoke = browser();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("fictional raster", { headers: { "Content-Type": "image/png" } })));
    modal();
    const user = userEvent.setup();
    const images = [{ id: "abc", name: "queued.png", mime_type: "image/png", size: 16, width: 2, height: 2, source_message_id: "message" }];
    // Queued and stored rows are separate subtrees, as in the project conversation.
    const Page = ({ admitted }: { admitted: boolean }) => <ImageViewerHost scope="/projects/alpha">
      {admitted ? <div className="bubble"><MessageImages project="alpha" images={images} /></div> : <ul><li><MessageImages project="alpha" images={images} /></li></ul>}
    </ImageViewerHost>;
    const view = render(<Page admitted={false} />);
    await user.click(await screen.findByRole("button", { name: "Open image queued.png" }));
    await user.click(screen.getByRole("button", { name: "Zoom image" }));
    const dialog = screen.getByRole("dialog", { name: "Image queued.png" });
    view.rerender(<Page admitted />);
    await screen.findByRole("button", { name: "Open image queued.png" });
    expect(screen.getByRole("dialog", { name: "Image queued.png" })).toBe(dialog);
    expect(screen.getByRole("button", { name: "Fit image" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(2); // The viewer reuses its bytes; only the new row reads.
    await user.click(screen.getByRole("button", { name: "Close image" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open image queued.png" })).toHaveFocus();
    const before = revoke.mock.calls.length;
    view.rerender(<ImageViewerHost scope="/projects/beta"><p /></ImageViewerHost>);
    expect(revoke.mock.calls.length).toBeGreaterThan(before);
  });

  it("closes the viewer when the page scope changes", async () => {
    browser();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("fictional raster", { headers: { "Content-Type": "image/png" } })));
    modal();
    const user = userEvent.setup();
    const images = [{ id: "abc", name: "screen.png", mime_type: "image/png", size: 16, width: 2, height: 2, source_message_id: "message" }];
    const view = render(<ImageViewerHost scope="/projects/alpha"><MessageImages project="alpha" images={images} /></ImageViewerHost>);
    await user.click(await screen.findByRole("button", { name: "Open image screen.png" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    view.rerender(<ImageViewerHost scope="/projects/alpha/tasks/other"><MessageImages project="alpha" images={images} /></ImageViewerHost>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    view.rerender(<ImageViewerHost scope="/projects/alpha"><MessageImages project="alpha" images={images} /></ImageViewerHost>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
