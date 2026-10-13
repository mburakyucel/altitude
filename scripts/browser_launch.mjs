// The actual-image Linux lane and its finite protection preflight share this contract.
export const browserLaunchOptions = Object.freeze({
  channel: "chromium", chromiumSandbox: true, headless: true, timeout: 30000,
});
