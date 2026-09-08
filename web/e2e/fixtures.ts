import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { expect, test as base } from "@playwright/test";

/** Every spec owns a disposable real API/storage process serving this checkout's built bundle. */
export const test = base.extend<{ service: string; scenario: string; single: boolean }>({
  scenario: ["acceptance", { option: true }],
  single: [false, { option: true }],
  service: async ({ scenario, single }, use) => {
    const script = ["acceptance", "tasks"].includes(scenario) ? "acceptance-service.py" : `project-${scenario}-service.py`;
    const child = spawn("python3", [`e2e/${script}`, ...(single ? ["single"] : scenario === "tasks" ? ["tasks"] : [])], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stderr = "";
    child.stderr.on("data", (data) => { stderr = (stderr + data).slice(-20_000); });
    const lines = createInterface({ input: child.stdout });
    const exited = once(child, "exit");
    let timer: NodeJS.Timeout | undefined;
    try {
      const ready = await Promise.race([
        once(lines, "line"),
        exited.then(() => { throw new Error(`Disposable service exited: ${stderr}`); }),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(`Disposable service did not start: ${stderr}`)), 10_000); }),
      ]);
      clearTimeout(timer);
      const data = JSON.parse(ready[0] as string);
      expect(data.disposable).toBe(true);
      expect(new URL(data.url).hostname).toBe("127.0.0.1");
      await use(data.url as string);
      expect(child.exitCode, `Disposable service died during the test: ${stderr}`).toBeNull();
      expect(child.signalCode, "Disposable service was killed during the test").toBeNull();
    } finally {
      clearTimeout(timer);
      lines.close();
      if (child.exitCode === null) child.kill("SIGTERM");
      const forced = setTimeout(() => child.kill("SIGKILL"), 3_000);
      try { await exited; } finally { clearTimeout(forced); }
      expect(child.exitCode, "Disposable service must finish cleanup within three seconds").toBe(0);
      expect(stderr, "The isolated server must not hide failed background workflows").toBe("");
    }
  },
  baseURL: async ({ service }, use) => { await use(service); },
});
