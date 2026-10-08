// Evidence comes from the effective shared configuration, never a spec-specific launch override.
import { readFileSync, writeFileSync } from "node:fs";
import type { FullConfig, FullResult, Reporter, TestCase, TestResult } from "@playwright/test/reporter";

export default class BrowserEvidence implements Reporter {
  private evidence: Record<string, unknown> = {};
  onBegin(config: FullConfig) {
    if (!process.env.ALTITUDE_BROWSER_EVIDENCE) return;
    this.evidence = {
      playwright: JSON.parse(readFileSync("node_modules/@playwright/test/package.json", "utf8")).version,
      projects: config.projects.map(({ name, use }) => ({ name, browserName: use.browserName,
        channel: use.channel, headless: use.headless, launch: {
          chromiumSandbox: use.launchOptions?.chromiumSandbox, args: use.launchOptions?.args,
          timeout: use.launchOptions?.timeout, config: use.launchOptions?.env?.XDG_CONFIG_HOME,
        } })),
      tests: [],
    };
  }
  onTestEnd(test: TestCase, result: TestResult) {
    (this.evidence.tests as unknown[] | undefined)?.push({ title: test.titlePath(), status: result.status,
      attachments: result.attachments.filter(a => a.name === "preflight").map(a => a.body?.toString()) });
  }
  onEnd(result: FullResult) {
    if (process.env.ALTITUDE_BROWSER_EVIDENCE) {
      writeFileSync(process.env.ALTITUDE_BROWSER_EVIDENCE, JSON.stringify({ ...this.evidence, status: result.status }, null, 2));
    }
  }
}
