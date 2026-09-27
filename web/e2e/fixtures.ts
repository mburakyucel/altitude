import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { expect, test as base } from "@playwright/test";

type Service = { url: string; device: string };

/** Every spec owns a disposable real API/storage process serving this checkout's built bundle. Its browser is
 * paired with that service unless a spec sets `paired: false` to walk the pairing screen itself. */
export const test = base.extend<{ altitude: Service; service: string; scenario: string; single: boolean; serviceScript: string; paired: boolean }>({
  scenario: ["acceptance", { option: true }],
  single: [false, { option: true }],
  serviceScript: ["", { option: true }],
  paired: [true, { option: true }],
  altitude: async ({ scenario, single, serviceScript }, use) => {
    const script = serviceScript || (["acceptance", "tasks"].includes(scenario) ? "acceptance-service.py" : `project-${scenario}-service.py`);
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
      await use({ url: data.url as string, device: data.device as string });
      expect(child.exitCode, `Disposable service died during the test: ${stderr}`).toBeNull();
      expect(child.signalCode, "Disposable service was killed during the test").toBeNull();
    } finally {
      clearTimeout(timer);
      lines.close();
      if (child.exitCode === null) child.kill("SIGTERM");
      const forced = setTimeout(() => child.kill("SIGKILL"), 3_000);
      try { await exited; } finally { clearTimeout(forced); }
      expect(child.exitCode, `Disposable service must finish cleanup within three seconds: ${stderr}`).toBe(0);
      expect(stderr, "The isolated server must not hide failed background workflows").toBe("");
    }
  },
  service: async ({ altitude }, use) => { await use(altitude.url); },
  baseURL: async ({ service }, use) => { await use(service); },
  // API calls made by a spec itself act as a paired device.
  request: async ({ playwright, altitude }, use) => {
    const request = await playwright.request.newContext({ baseURL: altitude.url, extraHTTPHeaders: { Cookie: `altitude_device=${altitude.device}` } });
    await use(request);
    await request.dispose();
  },
  // Chromium strands a request issued while Playwright disables request interception after a page's
  // last route expires or is removed (the voice Send with image lost its POST under parallel runs).
  // This never-matching route keeps interception on for the whole test; Playwright continues
  // unmatched requests itself, without calling a test handler.
  context: async ({ context, altitude, paired }, use) => {
    if (paired) await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    await context.route("altitude-e2e:keep-interception", () => undefined);
    await use(context);
  },
});
