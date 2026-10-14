import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { walkthrough } from "./walkthrough";

// Real installation records, strict archives and API writes. Native service/download effects are simulated.
// A manual supported recovery followed by an explicit retry is distinct from unattended upgrade acceptance.
test.use({ serviceScript: "update-recovery-service.py" });
test.setTimeout(90_000);

test("generated cache failure removes the spinner, preserves automatic history, and supports recovery then retry", async ({ page, request, service }, info) => {
  const walk = walkthrough(page, info);
  const notice = page.getByRole("status", { name: "New version" });
  const spinner = notice.getByText(/Installing Altitude v0.5.0/);
  await walk.open("/settings");
  await walk.state("update-01-automatic-running", { visible: [spinner], hidden: [notice.getByRole("button", { name: "Try again" })] });
  const initial = await (await request.get(`${service}/fixture/evidence`)).json();
  expect(initial.record.attempt.automatic).toBe(true);
  expect(initial.record.automatic_attempts).toEqual(["v0.5.0"]);
  expect(initial.strict_metadata).toBe("rejected");
  expect(initial.fence).toBe(true);
  expect(initial.paired_devices.length).toBeGreaterThan(0);

  const failed = await (await request.post(`${service}/fixture/finish-attempt`)).json();
  expect(failed.record.attempt.state).toBe("failed");
  expect(failed.record.attempt.automatic).toBe(true);
  expect(failed.strict_metadata).toBe("rejected");
  expect(failed.fence).toBe(false);
  expect(failed.current).toBe("versions/v0.4.0");
  await page.reload();
  await walk.state("update-02-failed-actionable", {
    visible: [notice.getByText(/The update to v0.5.0 did not finish/), notice.getByRole("button", { name: "Try again" })],
    hidden: [spinner, notice.getByText(/will install automatically/)],
  });

  // Calls the same public recovery operation as the hosting terminal; it does not overwrite receipt states.
  const recovered = await (await request.post(`${service}/fixture/recover`)).json();
  expect(recovered.strict_metadata).toBe("valid");
  expect(recovered.current).toBe("versions/v0.4.0");
  expect(recovered.record.automatic_attempts).toEqual(["v0.5.0"]);
  expect(recovered.record.attempt.state).toBe("failed");
  expect(recovered.retained).toBe(true);
  expect(recovered.certificate_retained).toBe(true);
  expect(recovered.projects_retained).toBe(true);
  expect(recovered.paired_devices).toEqual(initial.paired_devices);
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
  expect(success.retained).toBe(true);
  expect(success.certificate_retained).toBe(true);
  expect(success.projects_retained).toBe(true);
  expect(success.paired_devices).toEqual(initial.paired_devices);
  await page.reload();
  await walk.state("update-04-verified-simulated-success", { visible: [page.locator(".settings-version").getByText("v0.5.0", { exact: true })], hidden: [notice, spinner] });
});
