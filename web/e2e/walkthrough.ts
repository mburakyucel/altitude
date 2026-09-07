import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

/** Projects run the same steps at both widths. Issue #195: prove removals as well as appearances. */
export function walkthrough(page: Page, info: TestInfo) {
  return {
    async open(route: string) {
      const response = await page.goto(route);
      expect(response?.ok(), `Route ${route} must load`).toBe(true);
    },
    async state(name: string, options: {
      action?: () => Promise<unknown>;
      visible: Locator[];
      hidden: Locator[];
    }) {
      await test.step(name, async () => {
        await options.action?.();
        for (const locator of options.visible) await expect(locator).toBeVisible();
        for (const locator of options.hidden) await expect(locator).toBeHidden();
        const path = info.outputPath(`${name}.png`);
        await page.screenshot({ path, fullPage: true, animations: "disabled" });
        await info.attach(name, { path, contentType: "image/png" });
      });
    },
  };
}
