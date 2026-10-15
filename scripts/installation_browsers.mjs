// One visit in Playwright's Chromium or Firefox for the Linux VM lane's trust phase (installation_lifecycle.py
// Lifecycle.trust), with the browser's own defaults: Chromium reads the account's NSS database from HOME, Firefox the
// one in its profile folder. Prints JSON: whether the page loaded or the browser's certificate error, and whether
// First run appeared.
//   node installation_browsers.mjs PLAYWRIGHT_CORE chromium|firefox PROFILE ADDRESS
const [core, name, profile, address] = process.argv.slice(2);
const browsers = await import(core);
const context = await browsers[name].launchPersistentContext(profile);
const page = context.pages()[0] ?? await context.newPage();
const result = { browser: name, executable: browsers[name].executablePath(), address: address.replace(/#.*/, "#…") };
try {
  await page.goto(address);
  result.loaded = true;
  result.first_run = await page.getByRole("heading", { name: "Welcome to Altitude" })
    .waitFor({ timeout: 30_000 }).then(() => true, () => false);
} catch (error) {
  result.loaded = false;
  result.error = error.message.split("\n")[0];
}
await context.close();
console.log(JSON.stringify(result));
