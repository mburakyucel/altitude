import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Desktop certificate-trust steps on the pair page, per system and browser, on a disposable plain-HTTP loopback API.
// The access trust row and the trust check are presentation fixtures; this supplies no TLS or OS trust acceptance.
test.use({ paired: false });

const NAME = "Altitude CA DEMO";
const AGENTS = {
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
  "mac-firefox": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
  linux: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  "linux-firefox": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
  other: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
} as const;

/** The real access observation, presented as an HTTPS installation whose certificate this browser does not trust. */
async function untrusted(page: Page, local: boolean) {
  await page.route("**/api/access", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(),
      trust: { local, https: true, check: true, certificate: { name: NAME } } } });
  });
  await page.route("**/api/trust", (route) => route.fulfill({ json: { challenge: "fixture-check" } }));
  await page.route("**/api/trust/*", (route) => route.fulfill({ json: { trusted: false } }));
}

function trustItem(page: Page) {
  return page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Trust Altitude’s certificate/ }) });
}

test.describe("hosting computer @desktop-only", () => {
  test.use({ userAgent: AGENTS.linux });
  test("offers alt trust first and keeps the manual steps a click away", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    await untrusted(page, true);
    await walk.open("/");
    const item = trustItem(page);
    const manual = item.getByText("Trust it by hand instead", { exact: true });
    const download = item.getByRole("link", { name: "Download the certificate", exact: true });
    await walk.state("01-hosting-computer", {
      visible: [item.getByText("~/.local/bin/alt trust", { exact: true }), manual, item.getByText("Not trusted yet", { exact: true }),
        page.getByRole("button", { name: "Check again", exact: true })],
      hidden: [download, page.getByLabel("Steps for")],
    });
    await walk.state("02-hosting-computer-by-hand", {
      action: () => manual.click(),
      visible: [download, page.getByLabel("Steps for"), item.getByText("chrome://certificate-manager", { exact: true })],
      hidden: [],
    });
  });
});

for (const [system, agent] of Object.entries(AGENTS)) {
  test.describe(`another ${system} desktop @desktop-only`, () => {
    test.use({ userAgent: agent });
    test(`shows the ${system} steps`, async ({ page }, info) => {
      const walk = walkthrough(page, info);
      await untrusted(page, false);
      await walk.open("/");
      const item = trustItem(page);
      await walk.state(`01-${system}`, {
        visible: [item.getByRole("link", { name: "Download the certificate", exact: true }), page.getByLabel("Steps for")],
        hidden: [item.getByText("~/.local/bin/alt trust", { exact: true })],
      });
      await expect(page.getByLabel("Steps for")).toHaveValue(system);
    });
  });
}
