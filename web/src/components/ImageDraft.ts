import { useEffect, useRef, useState } from "react";
import { api, ImageCapabilitySchema } from "../data/api";
import type { ImageCapability, ImageSend } from "../data/api";
import type { ImagePreview } from "./MessageImages";

export interface ImageScope { project: string; task?: string; engine?: string | null }
export interface ImageSubmission extends ImageSend { previews: ImagePreview[] }
interface Selection extends ImagePreview { file: File; key: string }
const LIMITS = { max_count: 4, max_bytes: 10 << 20, max_total_bytes: 20 << 20 };
export const IMAGE_HELP = "PNG, JPEG or static WebP. Up to 4 images, 10 MiB each, 20 MiB total; 25 megapixels and 8192 pixels per side.";

/** Selection stays in this mounted conversation; only Send reads it for admission. */
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
            const problem = await new Promise<string>((resolve) => {
              const image = new Image();
              const unreadable = `${file.name} could not be read. Export a PNG, JPEG or static WebP and try again.`;
              image.onerror = () => resolve(unreadable);
              image.onload = () => resolve(!image.naturalWidth || !image.naturalHeight ? unreadable
                : image.naturalWidth > limits.max_dimension || image.naturalHeight > limits.max_dimension || image.naturalWidth * image.naturalHeight > limits.max_pixels
                  ? `${file.name} exceeds 25 megapixels or 8192 pixels per side. Choose a smaller image.` : "");
              image.src = url;
            });
            if (!current()) return;
            pendingPreview.current = null;
            if (problem) { URL.revokeObjectURL(url); failure = problem; }
            else replace([...selection.current, { name: file.name, key: crypto.randomUUID(), file, url }]);
          }
        }
        if (current()) setError(failure);
      } finally {
        if (current()) { checkActive.current = false; setChecking(false); }
      }
    },
    remove(key: string) { replace(selection.current.filter((image) => image.key !== key)); setError(""); },
    clear() { replace([]); setError(""); },
    async submission(): Promise<ImageSubmission> {
      const originals = [...selection.current];
      const images = await Promise.all(originals.map((image) => new Promise<{ name: string; data: string }>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error(`Could not read ${image.name}. Choose it again.`));
        reader.onload = () => resolve({ name: image.name, data: String(reader.result).split(",")[1] ?? "" });
        reader.readAsDataURL(image.file);
      })));
      return { request_id: crypto.randomUUID(), images, previews: originals.map(({ name, file }, index) => ({ name, url: `data:${file.type};base64,${images[index]!.data}` })) };
    },
  };
}
