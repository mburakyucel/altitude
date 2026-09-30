// Launch Playwright's Chromium with its own sandbox against a local fictional page and record the protections it
// keeps: `node scripts/browser_sandbox.mjs RESULT.json` from a checkout with web/ installed. Exits 1 unless Chromium
// reports itself adequately sandboxed and every renderer runs unprivileged in its own user and PID namespaces under
// a seccomp filter. See docs/DEVELOPMENT.md#browser-verification.
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, readdirSync, readlinkSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = createRequire(new URL("../web/package.json", import.meta.url))("@playwright/test");
const output = process.argv[2];
if (!output) throw new Error("usage: node scripts/browser_sandbox.mjs RESULT.json");

const server = createServer((_, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end("<!doctype html><title>Fictional</title><h1>Fictional project</h1>");
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const profile = mkdtempSync(join(tmpdir(), "browser-sandbox-"));
const options = { channel: "chromium", chromiumSandbox: true, headless: true, timeout: 30000 };
const status = (pid) => readFileSync(`/proc/${pid}/status`, "utf8");
const field = (text, name) => new RegExp(`^${name}:\\s+(\\S+)`, "m").exec(text)?.[1];
const own = (pid, ns) => readlinkSync(`/proc/${pid}/ns/${ns}`) !== readlinkSync(`/proc/self/ns/${ns}`);
let result;
try {
  const context = await chromium.launchPersistentContext(profile, options);
  try {
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const heading = await page.textContent("h1");
    const sandbox = await context.newPage();
    await sandbox.goto("chrome://sandbox");
    const report = await sandbox.innerText("body");
    const renderers = readdirSync("/proc").filter((pid) => /^\d+$/.test(pid)).flatMap((pid) => {
      try {
        if (!readFileSync(`/proc/${pid}/cmdline`, "utf8").includes("--type=renderer")) return [];
        const text = status(pid);
        return [{ uid: field(text, "Uid"), seccomp: field(text, "Seccomp"), no_new_privs: field(text, "NoNewPrivs"),
                  own_user_namespace: own(pid, "user"), own_pid_namespace: own(pid, "pid") }];
      } catch {
        return [];  // a renderer that exited while being read
      }
    });
    const version = context.browser()?.version() ?? null;
    const kept = report.includes("You are adequately sandboxed.") && renderers.length > 0 && renderers.every((r) =>
      r.uid !== "0" && r.seccomp === "2" && r.own_user_namespace && r.own_pid_namespace);
    result = { passed: kept && heading === "Fictional project", version, options, heading, sandbox: report, renderers,
               user: field(status("self"), "Uid") };
  } finally {
    await context.close();
  }
} catch (error) {
  result = { passed: false, options, error: String(error) };
} finally {
  server.close();
  rmSync(profile, { recursive: true, force: true });
}
writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
console.log(`browser sandbox ${result.passed ? "kept" : "NOT kept"}; evidence in ${output}`);
process.exit(result.passed ? 0 : 1);
