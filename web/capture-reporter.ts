// CAPTURE=1 (ALTITUDE_UI_CAPTURE names the folder): each journey's Playwright video becomes a small GIF through
// altitude/capture.py. A capture never decides a journey; its outcome is recorded in captures.json beside the GIFs.
// See docs/DEVELOPMENT.md#validation-captures.
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import type { Reporter, TestCase, TestResult } from "@playwright/test/reporter";

export const captures = process.env.ALTITUDE_UI_CAPTURE ? resolve(process.env.ALTITUDE_UI_CAPTURE) : undefined;
const LIMIT = 12;              // captures one run keeps, as RUN_LIMIT in altitude/capture.py
const DESKTOP = 800;           // capture width of a wider page; a phone keeps its own width

type Video = { title: string; width: number; path: string };

export default class CaptureReporter implements Reporter {
  private videos: Video[] = [];
  onTestEnd(test: TestCase, result: TestResult) {
    const video = result.attachments.find(a => a.name === "video" && a.path);
    const project = test.parent.project();
    if (!video?.path || !project) return;
    const [, , file, ...titles] = test.titlePath();
    this.videos.push({ title: [project.name, basename(file ?? "", ".pw.ts"), ...titles].join(" "),
      width: Math.min(project.use.viewport?.width ?? DESKTOP, DESKTOP), path: video.path });
  }
  // Last in the reporter list, so the HTML report has copied each video before the per-test copy is removed.
  onEnd() {
    if (!captures) return;
    try {
      mkdirSync(captures, { recursive: true });
      writeFileSync(resolve(captures, "captures.json"), JSON.stringify(this.convert(captures), null, 2) + "\n");
    } catch (error) {
      console.error(`capture: none: ${(error as Error).message}`);
    }
    for (const video of this.videos) {
      try { rmSync(video.path, { force: true }); } catch { /* the journey's own output folder keeps it */ }
    }
  }
  private convert(folder: string) {
    const names = new Set<string>();
    return this.videos.sort((a, b) => a.title.localeCompare(b.title)).map((video, index) => {
      const stem = video.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80) || "journey";
      let name = `${stem}.gif`;
      for (let n = 2; names.has(name); n += 1) name = `${stem}-${n}.gif`;
      names.add(name);
      if (index >= LIMIT) return { title: video.title, none: `${LIMIT} captures per run` };
      try {
        const out = execFileSync("python3", ["-m", "altitude.capture", video.path, resolve(folder, name),
          "--width", String(video.width)], { cwd: resolve(".."), encoding: "utf8", timeout: 400_000 });
        const made = JSON.parse(out) as Record<string, unknown>;
        return made.none ? { title: video.title, none: made.none } : { title: video.title, file: name, ...made };
      } catch (error) {
        return { title: video.title, none: String((error as Error).message).slice(0, 400) };
      }
    });
  }
}
