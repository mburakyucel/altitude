import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Real installation records, strict archives and API writes. Native service/download effects are simulated.
// An explicit retry after a failed automatic attempt is distinct from unattended upgrade acceptance.
test.use({ serviceScript: "update-recovery-service.py" });
test.setTimeout(90_000);

test("a failed automatic update shows its cause, preserves automatic history, and an explicit retry succeeds beside generated caches", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const notice = page.getByRole("status", { name: "New version" });
  const spinner = notice.getByText(/Installing Altitude v0.5.0/);
  await walk.open("/settings");
  await walk.state("update-01-automatic-running", { visible: [spinner], hidden: [notice.getByRole("button", { name: "Try again" })] });
  const initial = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(initial.record.attempt.automatic).toBe(true);
  expect(initial.record.automatic_attempts).toEqual(["v0.5.0"]);
  expect(initial.installed_metadata).toBe("valid");
  expect(initial.caches).toBe(1);
  expect(initial.fence).toBe(true);
  expect(initial.paired_devices.length).toBeGreaterThan(0);

  const failed = await (await request.post(`${service}/fixture/finish-attempt`)).json();
  expect(failed.record.attempt.state).toBe("failed");
  expect(failed.record.attempt.automatic).toBe(true);
  expect(failed.record.attempt.error).toBe("Archive checksum mismatch; the installed application is unchanged.");
  expect(failed.fence).toBe(false);
  expect(failed.current).toBe("versions/v0.4.0");
  await page.reload();
  await walk.state("update-02-failed-actionable", {
    visible: [notice.getByText("The update to v0.5.0 did not finish: Archive checksum mismatch; the installed application is unchanged. Altitude v0.4.0 keeps running."),
      notice.getByRole("button", { name: "Try again" })],
    hidden: [spinner, notice.getByText(/installs automatically/)],
  });

  // The release is published again with its real checksum; the failed attempt and its history stay as they were.
  const republished = await (await request.post(`${service}/fixture/republish`)).json();
  expect(republished.record.automatic_attempts).toEqual(["v0.5.0"]);
  expect(republished.record.attempt.state).toBe("failed");
  await walk.state("update-03-explicit-retry", {
    action: () => notice.getByRole("button", { name: "Try again" }).click(),
    visible: [spinner], hidden: [notice.getByRole("button", { name: "Try again" })],
  });
  const retry = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(retry.record.attempt.automatic).toBe(false);
  expect(retry.record.attempt.id).not.toBe(initial.record.attempt.id);
  expect(retry.detached).toBe(2);
  const success = await (await request.post(`${service}/fixture/finish-attempt`)).json();
  expect(success.record.attempt.state).toBe("succeeded");
  expect(success.record.attempt.automatic).toBe(false);
  expect(success.record.automatic_attempts).toEqual(["v0.5.0"]);
  expect(success.current).toBe("versions/v0.5.0");
  expect(success.caches).toBe(1);
  expect(success.installed_metadata).toBe("valid");
  expect(success.retained).toBe(true);
  expect(success.certificate_retained).toBe(true);
  expect(success.projects_retained).toBe(true);
  expect(success.paired_devices).toEqual(initial.paired_devices);
  await page.reload();
  await walk.state("update-04-verified-simulated-success", { visible: [page.locator(".settings-version").getByText("v0.5.0", { exact: true })], hidden: [notice, spinner] });
});
