import { expect, type Locator, type Page } from "@playwright/test";

/** The open image viewer's parts, located inside its dialog. */
export function viewer(page: Page) {
  const dialog = page.getByRole("dialog").filter({ has: page.locator(".image-viewer-stage") });
  return {
    dialog,
    // The pictures either side of the current one are hidden from the accessibility tree while a swipe shows them.
    picture: dialog.getByRole("img"),
    zoomed: dialog.locator(".image-viewer-stage[data-zoomed]"),
    close: dialog.getByRole("button", { name: "Close image", exact: true }),
    count: (text: string) => dialog.getByText(text, { exact: true }),
  };
}

/** Width of the current picture on screen, in CSS pixels. */
export const shownWidth = (picture: Locator) => picture.evaluate((node) => node.getBoundingClientRect().width);

/** Two quick taps on the phone, or two clicks with a mouse, at a point of the screen. */
export async function doubleTap(page: Page, phone: boolean, x: number, y: number) {
  for (let n = 0; n < 2; n++) {
    if (phone) await page.touchscreen.tap(x, y);
    else await page.mouse.click(x, y);
  }
}

/** Zooms the current picture as each width offers it: a double tap at its centre on the phone, the Zoom button on the desktop. */
export async function zoomIn(page: Page, phone: boolean) {
  const v = viewer(page);
  await expect(v.picture).toHaveJSProperty("complete", true);
  const fitted = await shownWidth(v.picture);
  if (phone) {
    const box = (await v.picture.boundingBox())!;
    await doubleTap(page, true, box.x + box.width / 2, box.y + box.height / 2);
  } else await v.dialog.getByRole("button", { name: "Zoom image", exact: true }).click();
  await expect(v.zoomed).toBeVisible();
  await expect.poll(() => shownWidth(v.picture)).toBeGreaterThan(fitted);
}
