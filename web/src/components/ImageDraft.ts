import { useEffect, useRef, useState } from "react";
import { api, ImageCapabilitySchema } from "../data/api";
import type { ImageCapability, ImageSend } from "../data/api";
import type { ImagePreview } from "./MessageImages";

export interface ImageScope { project: string; task?: string; engine?: string | null }
export interface ImageSubmission extends ImageSend { previews: ImagePreview[] }
/** A chosen image, read once when chosen, so Send takes the selection at once. */
interface Selection extends ImagePreview { file: File; key: string; data: string }
const LIMITS = { max_count: 4, max_bytes: 10 << 20, max_total_bytes: 20 << 20 };
export const IMAGE_HELP = "PNG, JPEG or static WebP. Up to 4 images, 10 MiB each, 20 MiB total; 25 megapixels and 8192 pixels per side.";

/** Selection stays in this mounted conversation until Send takes it. */
export function useImageDraft(scope?: ImageScope) {
  const [selected, setSelected] = useState<Selection[]>([]);
  const selection = useRef(selected);
  const [error, setError] = useState("");
  const [capability, setCapability] = useState<ImageCapability | null>(null);
  const [checking, setChecking] = useState(false);
  const checkActive = useRef(false);
  const pendingPreview = useRef<string | null>(null);
  const generation = useRef(0);
  const currentScope = useRef("");
  currentScope.current = `${scope?.project ?? ""}:${scope?.task ?? ""}`;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    selection.current = [];
    setSelected([]); setError(""); setChecking(false); checkActive.current = false;
    return () => {
      mounted.current = false;
      generation.current++;
      selection.current.forEach((image) => URL.revokeObjectURL(image.url));
      if (pendingPreview.current) URL.revokeObjectURL(pendingPreview.current);
      pendingPreview.current = null;
    };
  }, [scope?.project, scope?.task]);
  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    setCapability(null);
    void api<ImageCapability>(`/api/images/${scope.project}${scope.task ? `?task=${scope.task}` : ""}`, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setCapability(ImageCapabilitySchema.parse(value));
    }).catch(() => {
      if (!controller.signal.aborted) setCapability({ ...LIMITS, max_pixels: 25_000_000, max_dimension: 8192, available: false, reason: "Could not check image input. Try again later." });
    });
    return () => controller.abort();
  }, [scope?.project, scope?.task, scope?.engine ?? ""]);

  const replace = (next: Selection[]) => {
    selection.current.filter((old) => !next.includes(old)).forEach((old) => URL.revokeObjectURL(old.url));
    selection.current = next;
    setSelected(next);
  };
  return {
    selected, error, setError, capability, checking,
    async add(files: File[]) {
      if (!mounted.current || !scope || checkActive.current || !files.length) return;
      if (!capability?.available) { setError(`Image input unavailable. ${capability?.reason ?? "Checking image input…"}`); return; }
      const started = generation.current;
      const owner = currentScope.current;
      const current = () => mounted.current && generation.current === started && currentScope.current === owner;
      checkActive.current = true; setChecking(true);
      let failure = "";
      const limits = capability;
      try {
        for (const file of files) {
          if (!current()) return;
          if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
            failure = "Choose PNG, JPEG or static WebP. Export other formats as PNG or JPEG.";
          } else if (selection.current.length >= limits.max_count) failure = "Up to 4 images per message. Remove an image to add another.";
          else if (file.size > limits.max_bytes) failure = `${file.name} exceeds 10 MiB. Choose a smaller image.`;
          else if (selection.current.reduce((sum, image) => sum + image.file.size, file.size) > limits.max_total_bytes) failure = "Images exceed 20 MiB total. Remove an image or choose smaller copies.";
          else if (!file.size) failure = `${file.name} is empty. Choose another image.`;
          else {
            const url = URL.createObjectURL(file);
            pendingPreview.current = url;
            const unreadable = `${file.name} could not be read. Export a PNG, JPEG or static WebP and try again.`;
            let width = 0;
            let height = 0;
            let problem = await new Promise<string>((resolve) => {
              const image = new Image();
              image.onerror = () => resolve(unreadable);
              image.onload = () => {
                // The size shapes its thumbnail from Sending on, so the saved picture lands in the same place.
                width = image.naturalWidth;
                height = image.naturalHeight;
                resolve(!width || !height ? unreadable
                  : width > limits.max_dimension || height > limits.max_dimension || width * height > limits.max_pixels
                    ? `${file.name} exceeds 25 megapixels or 8192 pixels per side. Choose a smaller image.` : "");
              };
              image.src = url;
            });
            const data = problem ? null : await new Promise<string | null>((resolve) => {
              const reader = new FileReader();
              reader.onerror = () => resolve(null);
              reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
              reader.readAsDataURL(file);
            });
            if (!current()) return;
            pendingPreview.current = null;
            if (!problem && data === null) problem = unreadable;
            if (problem || data === null) { URL.revokeObjectURL(url); failure = problem; }
            else replace([...selection.current, { name: file.name, key: crypto.randomUUID(), file, url, data, width, height }]);
          }
        }
        if (current()) setError(failure);
      } finally {
        if (current()) { checkActive.current = false; setChecking(false); }
      }
    },
    remove(key: string) { replace(selection.current.filter((image) => image.key !== key)); setError(""); },
    clear() { replace([]); setError(""); },
    /** Edit returns a failed message's images to the picker from the copies its bubble kept; nothing changes and
        the picker says why when they do not fit beside the current selection. */
    restore(previews: ImagePreview[]): boolean {
      const files = previews.map(({ name, url, width, height }) => {
        const [head = "", data = ""] = url.split(",");
        return { data, width, height, file: new File([Uint8Array.from(atob(data), (char) => char.charCodeAt(0))], name, { type: head.slice(5, head.indexOf(";")) }) };
      });
      const problem = selection.current.length + files.length > LIMITS.max_count ? "Up to 4 images per message. Remove an image to add another."
        : [...selection.current, ...files].reduce((sum, { file }) => sum + file.size, 0) > LIMITS.max_total_bytes
          ? "Images exceed 20 MiB total. Remove an image or choose smaller copies." : "";
      if (problem) { setError(problem); return false; }
      replace([...selection.current, ...files.map(({ file, data, width, height }) => ({ name: file.name, key: crypto.randomUUID(), file, data, width, height, url: URL.createObjectURL(file) }))]);
      return true;
    },
    /** The selection as one message: its images and the previews its bubble shows. */
    submission(): ImageSubmission {
      const chosen = selection.current;
      return { request_id: crypto.randomUUID(), images: chosen.map(({ name, data }) => ({ name, data })),
        previews: chosen.map(({ name, file, data, width, height }) => ({ name, url: `data:${file.type};base64,${data}`, width, height })) };
    },
  };
}
