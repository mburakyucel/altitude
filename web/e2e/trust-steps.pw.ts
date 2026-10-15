import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Desktop certificate-trust steps on the pair page, per system and browser, on a disposable plain-HTTP loopback API,
// in a desktop window and a narrow one. The access trust row and the trust check are presentation fixtures; this
// supplies no TLS or OS trust acceptance.
test.use({ paired: false, hasTouch: false, isMobile: false });

const NAME = "Altitude CA DEMO";
const AGENTS = {
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
  linux: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  "linux-firefox": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
  other: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
} as const;
/** The step only each system's list has. */
const MARKS = {
  mac: /^In Keychain Access, open “Altitude CA DEMO”, expand Trust/,
  linux: /^Choose Local certificates › Custom › Installed by you/,
  "linux-firefox": /^Tick Trust this CA to identify websites/,
  other: /^Import it as a trusted authority in this browser or system/,
} as const;

/** The real access observation, presented as an HTTPS installation whose certificate this browser does not trust
 * until `trusted` says so. `local` is the hosting computer, where alt trust works. */
async function untrusted(page: Page, local: boolean, trusted = () => false) {
  await page.route("**/api/access", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(),
      trust: { local, https: true, check: true, command: local, certificate: { name: NAME } } } });
  });
  await page.route("**/api/trust", (route) => route.fulfill({ json: { challenge: "fixture-check" } }));
  await page.route("**/api/trust/*", (route) => route.fulfill({ json: { trusted: trusted() } }));
}

function trustItem(page: Page) {
  return page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Trust Altitude’s certificate/ }) });
}

test.describe("hosting computer", () => {
  test.use({ userAgent: AGENTS.linux });
  test("offers alt trust first, keeps the manual steps a click away and leaves once trusted", async ({ page }, info) => {
    const walk = walkthrough(page, info);
    let trusted = false;
    await untrusted(page, true, () => trusted);
    await walk.open("/");
    const item = trustItem(page);
    const command = item.getByText("~/.local/bin/alt trust", { exact: true });
    const manual = item.getByText("Trust it by hand instead", { exact: true });
    const download = item.getByRole("link", { name: "Download the certificate", exact: true });
    const again = page.getByRole("button", { name: "Check again", exact: true });
    await walk.state("01-hosting-computer", {
      visible: [command, manual, item.getByText("Not trusted yet", { exact: true }), again],
      hidden: [download, page.getByLabel("Steps for"), page.getByRole("status")],
    });
    await walk.state("02-hosting-computer-by-hand", {
      action: () => manual.click(),
      visible: [download, page.getByLabel("Steps for"), item.getByText("chrome://certificate-manager", { exact: true })],
      hidden: [],
    });
    trusted = true;
    await walk.state("03-hosting-computer-trusted", {
      action: () => again.click(),
      visible: [page.getByRole("heading", { name: /^Pair this browser/ })],
      hidden: [command, manual, download, again],
    });
  });
});

for (const [system, agent] of Object.entries(AGENTS) as [keyof typeof AGENTS, string][]) {
  test.describe(`another ${system} desktop`, () => {
    test.use({ userAgent: agent });
    test(`shows the ${system} steps and switches with Steps for`, async ({ page }, info) => {
      const walk = walkthrough(page, info);
      await untrusted(page, false);
      await walk.open("/");
      const item = trustItem(page);
      const choose = page.getByLabel("Steps for");
      const others = Object.entries(MARKS).filter(([key]) => key !== system).map(([, mark]) => item.getByText(mark));
      await walk.state(`01-${system}`, {
        visible: [item.getByRole("link", { name: "Download the certificate", exact: true }), choose, item.getByText(MARKS[system]),
          item.getByRole("link", { name: "Certificate guide", exact: true })],
        hidden: [item.getByText("~/.local/bin/alt trust", { exact: true }), page.getByRole("status"), ...others],
      });
      await expect(choose).toHaveValue(system);
      const next = system === "linux" ? "linux-firefox" : "linux";
      await walk.state(`02-${system}-switched-to-${next}`, {
        action: () => choose.selectOption(next),
        visible: [item.getByText(MARKS[next])],
        hidden: [item.getByText(MARKS[system])],
      });
    });
  });
}
