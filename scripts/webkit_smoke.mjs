// Finite prerequisite check for the validation image's emulated-iPhone lane (#667).
// Run inside `alt task validate` after installing web dependencies; this is desktop WebKit, not iOS acceptance.
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { webkit, devices } = require("@playwright/test");
const output = process.argv[2];
if (!output) throw new Error("usage: node scripts/webkit_smoke.mjs RESULT.json");
const result = {
  passed: false, scope: "Linux desktop WebKit emulated iPhone prerequisite",
  playwright: require("@playwright/test/package.json").version,
  executable: webkit.executablePath(), user: process.getuid(),
};
const server = createServer((_, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end('<!doctype html><meta name="viewport" content="width=device-width"><title>Fictional</title>' +
    '<h1>Fictional project</h1><button onclick="this.textContent=\'Ready\'">Check</button>');
});
let browser;
const deadline = setTimeout(() => {
  writeFileSync(output, JSON.stringify({ ...result, error: "60-second timeout" }, null, 2) + "\n");
  process.exit(1);
}, 60000);
try {
  assert.notEqual(result.user, 0, "validation runs as a non-root user");
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await webkit.launch({ headless: true, timeout: 30000 });
  result.version = browser.version();
  const context = await browser.newContext({ ...devices["iPhone 15"], viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  result.heading = await page.locator("h1").innerText();
  assert.equal(result.heading, "Fictional project");
  await page.getByRole("button", { name: "Check", exact: true }).tap();
  await page.getByRole("button", { name: "Ready", exact: true }).waitFor();
  result.viewport = page.viewportSize();
  result.passed = true;
} catch (error) {
  result.error = String(error);
} finally {
  await browser?.close();
  server.close();
  clearTimeout(deadline);
}
writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
console.log(`WebKit prerequisite ${result.passed ? "passed" : "FAILED"}; evidence in ${output}`);
process.exit(result.passed ? 0 : 1);
