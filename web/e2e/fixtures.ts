import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { expect, test as base } from "@playwright/test";

type Service = { url: string; device: string; certificate?: string; pairing?: string; output?: string; machine?: string };

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
    let started = false;
    try {
      const ready = await Promise.race([
        once(lines, "line"),
        exited.then(() => { throw new Error(`Disposable service exited: ${stderr}`); }),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(`Disposable service did not start: ${stderr}`)), 10_000); }),
      ]);
      clearTimeout(timer);
      started = true;
      const data = JSON.parse(ready[0] as string);
      expect(data.disposable).toBe(true);
      expect(new URL(data.url).hostname).toBe("127.0.0.1");
      await use(data as Service);
      expect(child.exitCode, `Disposable service died during the test: ${stderr}`).toBeNull();
      expect(child.signalCode, "Disposable service was killed during the test").toBeNull();
    } finally {
      clearTimeout(timer);
      lines.close();
      if (child.exitCode === null) child.kill("SIGTERM");
      const forced = setTimeout(() => child.kill("SIGKILL"), 3_000);
      try { await exited; } finally { clearTimeout(forced); }
      // A service that never started fails with its own startup error, which a cleanup check would replace.
      if (started) {
        expect(child.exitCode, `Disposable service must finish cleanup within three seconds: ${stderr}`).toBe(0);
        expect(stderr, "The isolated server must not hide failed background workflows").toBe("");
      }
    }
  },
  service: async ({ altitude }, use) => { await use(altitude.url); },
  baseURL: async ({ service }, use) => { await use(service); },
  // API calls made by a spec itself act as a paired device.
  request: async ({ playwright, altitude }, use) => {
    const request = await playwright.request.newContext({ baseURL: altitude.url,
      extraHTTPHeaders: altitude.machine ? { "X-Altitude-Key": altitude.machine } : { Cookie: `altitude_device=${altitude.device}` } });
    await use(request);
    await request.dispose();
  },
  // Walkthroughs never wait on the internet: a request beyond loopback fails at once (a stalled third-party
  // stylesheet once held a reload's load event past the test limit). The route also keeps interception on
  // for the whole test, because Chromium strands a request issued while Playwright disables interception
  // after a page's last route expires or is removed (the voice Send with image lost its POST under
  // parallel runs). Playwright continues loopback requests itself, without calling a test handler.
  context: async ({ context, altitude, paired }, use) => {
    if (paired) await context.addCookies([{ name: "altitude_device", value: altitude.device, url: altitude.url }]);
    await context.route(/^https?:\/\/(?!127\.0\.0\.1[:/])/, route => route.abort());
    await use(context);
  },
});
