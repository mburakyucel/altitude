import { expect, type Locator, type Page, type TestInfo } from "@playwright/test";
import { test } from "./fixtures";
import { doubleTap, viewer } from "./viewer";
import { walkthrough } from "./walkthrough";

// The project conversation with real private image storage; the engine answers from a deterministic fixture.
test.use({ serviceScript: "image-input-service.py" });
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "ignoreErrors" }); });

type Point = { x: number; y: number };
type Kind = "phone" | "desktop" | "wide" | "icon";
/** Four fictional pictures: a phone screenshot, a desktop screenshot, the widest picture a message takes, and an icon. */
const PICTURES: { name: string; width: number; height: number; kind: Kind }[] = [
  { name: "checkout-phone.png", width: 1170, height: 2532, kind: "phone" },
  { name: "dashboard.png", width: 1600, height: 1000, kind: "desktop" },
  { name: "timeline-wide.png", width: 8192, height: 3000, kind: "wide" },
  { name: "app-icon.png", width: 240, height: 160, kind: "icon" },
];
const caption = "Here is the checkout flow.";
const imageRead = (url: URL) => /^\/api\/images\/alpha\/[^/]+$/.test(url.pathname);

/** Draws a fictional picture locally, so nothing is fetched from outside the disposable service. */
async function picture(page: Page, { name, width, height, kind }: typeof PICTURES[number]) {
  const data = await page.evaluate(({ width, height, kind }) => {
    const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
    const c = canvas.getContext("2d")!;
    c.fillStyle = "#f8fafc"; c.fillRect(0, 0, width, height);
    if (kind === "phone") {
      c.fillStyle = "#1e293b"; c.fillRect(0, 0, width, 260);
      c.fillStyle = "#fff"; c.font = "bold 72px sans-serif"; c.fillText("Lumen checkout", 60, 190);
      for (let row = 0; row < 9; row++) {
        c.fillStyle = "#e2e8f0"; c.fillRect(60, 320 + row * 220, width - 120, 170);
        c.fillStyle = "#334155"; c.font = "48px sans-serif"; c.fillText(`Saved basket ${row + 1}`, 100, 420 + row * 220);
      }
      c.fillStyle = "#4f46e5"; c.fillRect(60, height - 240, width - 120, 150);
      c.fillStyle = "#fff"; c.font = "bold 56px sans-serif"; c.fillText("Pay now", width / 2 - 110, height - 145);
    } else if (kind === "desktop") {
      c.fillStyle = "#e2e8f0"; c.fillRect(0, 0, 300, height);
      c.fillStyle = "#172554"; c.font = "bold 40px sans-serif"; c.fillText("Lumen dashboard", 340, 80);
      for (let row = 0; row < 12; row++) {
        c.fillStyle = row % 2 ? "#f1f5f9" : "#fff"; c.fillRect(340, 130 + row * 66, width - 380, 66);
        c.fillStyle = "#475569"; c.font = "26px sans-serif"; c.fillText(`Order ${1040 + row}`, 360, 172 + row * 66);
      }
    } else if (kind === "wide") {
      c.fillStyle = "#94a3b8"; c.fillRect(0, height / 2 - 4, width, 8);
      for (let at = 0; at < width; at += 512) {
        c.fillStyle = "#16a34a"; c.fillRect(at + 40, height / 2 - 600 + (at / 512 % 4) * 120, 380, 240);
        c.fillStyle = "#0f172a"; c.font = "bold 96px sans-serif"; c.fillText(`Week ${at / 512 + 1}`, at + 60, height / 2 + 200);
      }
    } else {
      c.fillStyle = "#4f46e5"; c.beginPath(); c.roundRect(40, 20, 160, 120, 28); c.fill();
      c.fillStyle = "#fff"; c.font = "bold 80px sans-serif"; c.fillText("L", 98, 110);
    }
    return canvas.toDataURL("image/png").split(",")[1]!;
  }, { width, height, kind });
  return { name, mimeType: "image/png", buffer: Buffer.from(data, "base64") };
}

/** Opens the project conversation and sends the four pictures in one message; returns its thumbnails. */
async function sendPictures(page: Page, info: TestInfo) {
  const capability = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/images/alpha");
  await walkthrough(page, info).open("/projects/alpha");
  expect((await capability).ok()).toBe(true);
  const field = page.getByRole("textbox", { name: "Message L3 about alpha" });
  await page.getByLabel("Choose images").setInputFiles(await Promise.all(PICTURES.map((one) => picture(page, one))));
  await expect(page.getByLabel("Selected images", { exact: true }).locator("img")).toHaveCount(4);
  await field.fill(caption);
  await page.locator(".composer").getByRole("button", { name: /^(Send|Queue)$/ }).click();
  const message = page.locator(".bubble").filter({ hasText: caption });
  const openers = PICTURES.map((one) => message.getByRole("button", { name: `Open image ${one.name}`, exact: true }));
  for (const opener of openers) await expect(opener.locator("img")).toHaveAttribute("src", /^blob:/);
  return { message, openers };
}

/** Where the current picture is headed, from its own style: the place an eased movement ends. */
async function placed(picture: Locator) {
  return picture.evaluate((node) => {
    const image = node as HTMLImageElement;
    const [x = 0, y = 0] = (image.style.transform.match(/-?[\d.]+/g) ?? []).map(Number);
    return { x, y, w: parseFloat(image.style.width), h: parseFloat(image.style.height), natural: image.naturalWidth };
  });
}

/** The fitted place a picture of this size has between the bars: whole, centred, and never above its own size. */
async function fitted(page: Page, size: { w: number; h: number }) {
  const v = viewer(page);
  const bar = (await v.dialog.locator(".image-viewer-bar").boundingBox())!;
  const band = (await v.dialog.locator(".image-viewer-band").boundingBox())!;
  const width = page.viewportSize()!.width;
  const side = width < 1024 ? 0 : 88;
  const room = { w: width - 2 * side, h: band.y - (bar.y + bar.height) };
  const s = Math.min(1, room.w / size.w, room.h / size.h);
  return { s, x: side + (room.w - size.w * s) / 2, y: bar.y + bar.height + (room.h - size.h * s) / 2, w: size.w * s, h: size.h * s, top: bar.y + bar.height, bottom: band.y };
}

/** Waits until nothing is easing and the dark is whole: the viewer at rest. */
async function settled(page: Page) {
  await expect.poll(() => viewer(page).dialog.evaluate((node) => [(node as HTMLElement).style.getPropertyValue("--moving"), (node as HTMLElement).style.getPropertyValue("--dim")]))
    .toEqual(["0ms", "1"]);
}

/** Real touches through Chromium's input pipeline, which become the same pointer events a phone sends. */
async function fingers(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", points: Point[]) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points.map((point, id) => ({ x: point.x, y: point.y, id })) });
  const pause = (ms: number) => ms ? page.waitForTimeout(ms) : Promise.resolve();
  return {
    /** One finger from a point to another in even steps; `hold` keeps it down at the end, `ms` slows each step. */
    async drag(from: Point, to: Point, { steps = 10, hold = false, ms = 0 } = {}) {
      await touch("touchStart", [from]);
      for (let n = 1; n <= steps; n++) {
        await pause(ms);
        await touch("touchMove", [{ x: from.x + (to.x - from.x) * n / steps, y: from.y + (to.y - from.y) * n / steps }]);
      }
      if (!hold) await touch("touchEnd", []);
    },
    lift: () => touch("touchEnd", []),
    /** Two fingers either side of a point, moving from one gap to another. */
    async pinch(at: Point, from: number, to: number, steps = 10) {
      const pair = (gap: number) => [{ x: at.x - gap / 2, y: at.y }, { x: at.x + gap / 2, y: at.y }];
      await touch("touchStart", pair(from));
      for (let n = 1; n <= steps; n++) await touch("touchMove", pair(from + (to - from) * n / steps));
      await touch("touchEnd", []);
    },
  };
}

const near = (actual: number, expected: number, label: string) => expect(Math.abs(actual - expected), `${label}: ${actual} ≈ ${expected}`).toBeLessThan(1.5);

test("a picture opens fitted between the bars, zooms toward the tap, moves on and closes back to its thumbnail", async ({ page, browserName }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const { message, openers } = await sendPictures(page, info);
  const v = viewer(page);
  await message.scrollIntoViewIfNeeded();
  await walk.state("01-message-thumbnails", { visible: openers, hidden: [v.dialog] });
  if (phone) {
    // Four pictures sit two by two on the phone.
    const [a, , c] = await Promise.all(openers.map((one) => one.boundingBox()));
    near(c!.x, a!.x, "third thumbnail under the first");
  }

  const actions = phone ? [v.dialog.getByRole("button", { name: "Download image", exact: true })]
    : ["Zoom image", "Copy image", "Download image", "Open in new tab"].map((name) => v.dialog.getByRole("button", { name, exact: true }));
  await walk.state("02-viewer-fitted", { action: async () => { await (phone ? openers[0]!.tap() : openers[0]!.click()); await settled(page); },
    visible: [v.dialog.getByText("checkout-phone.png", { exact: true }), v.dialog.getByText(/^You · /), v.count("1 / 4"), v.close, v.picture, ...actions,
      v.dialog.getByRole("button", { name: "Next image", exact: true })],
    hidden: [v.dialog.getByRole("button", { name: "Previous image", exact: true }), v.zoomed, v.dialog.getByRole("status", { name: "Loading image…" })] });
  await expect(v.close).toBeFocused();
  await expect(page.getByRole("dialog", { name: "Image checkout-phone.png", exact: true })).toBeVisible();
  const fit = await fitted(page, { w: 1170, h: 2532 });
  const start = await placed(v.picture);
  near(start.w, fit.w, "fitted width"); near(start.x, fit.x, "fitted left"); near(start.y, fit.y, "fitted top");
  expect(start.y).toBeGreaterThanOrEqual(fit.top - 0.5);
  expect(start.y + start.h).toBeLessThanOrEqual(fit.bottom + 0.5);

  // Double-tap zooms toward the tapped point: the pixel under the finger stays under it.
  const at = { x: fit.x + fit.w * 0.5, y: fit.y + fit.h * 0.3 };
  const pixel = { x: (at.x - start.x) / fit.s, y: (at.y - start.y) / fit.s };
  await walk.state("03-zoomed-toward-tap", { action: async () => { await doubleTap(page, phone, at.x, at.y); await settled(page); },
    visible: [v.zoomed, ...phone ? [] : [v.dialog.getByRole("button", { name: "Fit image", exact: true })]], hidden: [] });
  const zoomed = await placed(v.picture);
  // A phone screenshot fitted at about a third of its size zooms to its own pixels.
  near(zoomed.w, 1170, "double-tap shows the picture's own pixels");
  near(zoomed.x + pixel.x * zoomed.w / 1170, at.x, "tapped point across");
  near(zoomed.y + pixel.y * zoomed.w / 1170, at.y, "tapped point down");

  // Panning stops at the picture's edges: its top meets the bar, and its left side the screen's edge when it is
  // wider than the screen, as on the phone; a picture narrower than the screen stays centred across it.
  const pan = async (from: Point, to: Point) => {
    if (phone) await (await fingers(page)).drag(from, to);
    else { await page.mouse.move(from.x, from.y); await page.mouse.down(); await page.mouse.move(to.x, to.y, { steps: 10 }); await page.mouse.up(); }
    await settled(page);
  };
  // Real touches need Chromium's input protocol; the emulated iPhone lane walks the rest.
  if (browserName === "chromium") {
    await walk.state("04-panned-to-corner", { action: async () => {
      for (let n = 0; n < 4; n++) await pan({ x: 60, y: fit.top + 100 }, { x: 360, y: fit.top + 600 });
    }, visible: [v.zoomed], hidden: [] });
    const corner = await placed(v.picture);
    const width = page.viewportSize()!.width;
    near(corner.x, corner.w > width ? 0 : (width - corner.w) / 2, "left edge at the screen's edge, or centred");
    near(corner.y, fit.top, "top edge under the bar");
  }

  // A second double-tap returns to fit.
  await doubleTap(page, phone, fit.x + fit.w / 2, fit.y + fit.h / 2);
  await settled(page);
  await expect(v.zoomed).toBeHidden();
  near((await placed(v.picture)).w, fit.w, "back to the fitted width");

  // The next picture: the arrow on the desktop, a key, or the strip a finger drags on the phone.
  const next = v.dialog.getByRole("button", { name: "Next image", exact: true });
  await walk.state("05-next-picture", { action: async () => { await next.click(); await settled(page); },
    visible: [v.dialog.getByText("dashboard.png", { exact: true }), v.count("2 / 4"), v.dialog.getByRole("button", { name: "Previous image", exact: true })], hidden: [v.dialog.getByText("checkout-phone.png", { exact: true })] });
  await page.keyboard.press("ArrowRight");
  await settled(page);
  await expect(v.count("3 / 4")).toBeVisible();

  // The widest picture a message takes, 8192 pixels across, fits the screen's width and still zooms.
  const wide = await fitted(page, { w: 8192, h: 3000 });
  await expect(v.picture).toHaveJSProperty("complete", true);
  const wideFit = await placed(v.picture);
  expect(wideFit.natural).toBe(8192);
  near(wideFit.w, wide.w, "the wide picture fits across");
  await walk.state("06-widest-picture", { visible: [v.dialog.getByText("timeline-wide.png", { exact: true }), v.picture], hidden: [v.zoomed] });
  await doubleTap(page, phone, wide.x + wide.w / 2, wide.y + wide.h / 2);
  await settled(page);
  near((await placed(v.picture)).w, wide.w * 4, "double-tap zooms a tiny fit four times");
  await walk.state("07-widest-picture-zoomed", { visible: [v.zoomed], hidden: [] });
  await page.keyboard.press("0");
  await settled(page);

  // A picture smaller than the screen shows at its own size.
  await page.keyboard.press("ArrowRight");
  await settled(page);
  await walk.state("08-small-picture-own-size", { visible: [v.count("4 / 4"), v.picture], hidden: [next] });
  near((await placed(v.picture)).w, 240, "the icon at its own width");

  // Escape closes; focus returns to the thumbnail that opened the viewer.
  await walk.state("09-closed-to-thumbnail", { action: () => page.keyboard.press("Escape"), visible: openers, hidden: [v.dialog] });
  await expect(openers[0]!).toBeFocused();
});

test("@chromium fingers and the mouse: swipe follows the finger, pinch zooms, swipe down and a click outside close", async ({ page }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const { openers } = await sendPictures(page, info);
  const v = viewer(page);
  await openers[1]!.click();
  await settled(page);
  const size = page.viewportSize()!;
  const middle = { x: size.width / 2, y: size.height / 2 };
  if (phone) {
    const hand = await fingers(page);
    // A short, slow drag springs back; a drag past a third of the screen moves on, the next picture beside it.
    await hand.drag(middle, { x: middle.x - 60, y: middle.y }, { ms: 40 });
    await settled(page);
    await expect(v.count("2 / 4")).toBeVisible();
    await walk.state("01-swipe-follows-finger", { action: () => hand.drag(middle, { x: middle.x - size.width * 0.45, y: middle.y }, { hold: true }),
      visible: [v.dialog.locator(".image-viewer-slide img").nth(1)], hidden: [] });
    await expect(v.dialog.locator(".image-viewer-slide")).toHaveCount(3);
    await hand.lift();
    await settled(page);
    await expect(v.count("3 / 4")).toBeVisible();
    // Past the first or last picture the strip gives a third of the finger's travel and springs back.
    await page.keyboard.press("ArrowLeft"); await settled(page);
    await page.keyboard.press("ArrowLeft"); await settled(page);
    await hand.drag(middle, { x: middle.x + 150, y: middle.y }, { hold: true });
    // Chromium hands the page a finger's moves once per frame, so the last one arrives with the next frame.
    await expect.poll(() => v.dialog.locator(".image-viewer-slide").first().evaluate((node) => (node as HTMLElement).style.transform)).toBe("translateX(50px)");
    await hand.lift();
    await settled(page);
    await expect(v.count("1 / 4")).toBeVisible();

    // Pinching out zooms around the fingers; past the limit it settles back on release.
    await page.keyboard.press("ArrowRight"); await settled(page);
    const fit = await fitted(page, { w: 1600, h: 1000 });
    await walk.state("02-pinch-zoomed", { action: async () => { await hand.pinch(middle, 80, 240); await settled(page); }, visible: [v.zoomed], hidden: [] });
    near((await placed(v.picture)).w, fit.w * 3, "pinched three times wider");
    await hand.pinch(middle, 40, 360);
    await settled(page);
    near((await placed(v.picture)).w, 1600 * Math.max(4 * fit.s, 2), "settles at the most zoom");
    await page.keyboard.press("0"); await settled(page);

    // Swiping down shrinks the picture and fades the dark with the finger; letting go past the threshold closes.
    await walk.state("03-swipe-down-follows", { action: () => hand.drag(middle, { x: middle.x, y: middle.y + 180 }, { hold: true }), visible: [v.picture], hidden: [] });
    await expect.poll(() => v.dialog.evaluate((node) => (node as HTMLElement).style.getPropertyValue("--dim"))).toBe("0.5");
    await hand.lift();
    await walk.state("04-swiped-down-closed", { visible: [openers[1]!], hidden: [v.dialog] });
    await expect(openers[1]!).toBeFocused();
  } else {
    // A trackpad pinch arrives as a wheel with Control; a mouse notch zooms in steps toward the pointer.
    const fit = await fitted(page, { w: 1600, h: 1000 });
    await page.mouse.move(middle.x, middle.y);
    await walk.state("01-wheel-zoomed", { action: async () => { await page.mouse.wheel(0, -100); await settled(page); }, visible: [v.zoomed], hidden: [] });
    near((await placed(v.picture)).w, fit.w * 1.25, "one notch");
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, -40);
    await page.keyboard.up("Control");
    await settled(page);
    near((await placed(v.picture)).w, fit.w * 1.25 * Math.exp(0.4), "trackpad pinch");
    // Keys: + and - zoom, arrows pan while zoomed, 0 fits.
    await page.keyboard.press("+"); await settled(page);
    const before = await placed(v.picture);
    await page.keyboard.press("ArrowRight"); await settled(page);
    expect((await placed(v.picture)).x).toBeLessThan(before.x);
    await expect(v.count("2 / 4")).toBeVisible();
    await page.keyboard.press("0"); await settled(page);
    await expect(v.zoomed).toBeHidden();
    // A click on the picture keeps it; a click on the dark space beside it closes, as a backdrop does.
    await page.mouse.click(middle.x, middle.y);
    await expect(v.dialog).toBeVisible();
    await walk.state("02-click-outside-closed", { action: () => page.mouse.click(40, middle.y + 200), visible: [openers[1]!], hidden: [v.dialog] });
    await expect(openers[1]!).toBeFocused();
  }
});

test("the viewer reads the picture on screen and one each side, shows the thumbnail while it reads, and lets go of the rest", async ({ page }, info) => {
  await page.addInitScript(() => {
    // Counts the object URLs the page holds, so closing the viewer can be seen letting go of its pictures.
    const live = new Set<string>();
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (object) => { const url = create(object); live.add(url); return url; };
    URL.revokeObjectURL = (url) => { live.delete(url); revoke(url); };
    (window as unknown as { live: Set<string> }).live = live;
  });
  const walk = walkthrough(page, info);
  const { openers } = await sendPictures(page, info);
  const v = viewer(page);
  const ids = await Promise.all(openers.map(async (opener) => (await opener.locator("xpath=..").getAttribute("data-image"))!.split("/")[1]!));
  const held = () => page.evaluate(() => (window as unknown as { live: Set<string> }).live.size);
  const before = await held();
  const reads: string[] = [];
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => imageRead(url), async (route) => {
    reads.push(decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()!));
    await gate;
    await route.continue();
  });

  // While its own read is on its way the viewer shows the thumbnail's picture, and the actions wait.
  await walk.state("01-reading-shows-thumbnail", { action: () => openers[1]!.click(),
    visible: [v.dialog.getByRole("status", { name: "Loading image…" }), v.picture, v.count("2 / 4")], hidden: [] });
  await expect(v.picture).toHaveAttribute("data-placeholder", "true");
  await expect(v.dialog.getByRole("button", { name: "Download image", exact: true })).toBeDisabled();
  expect(reads).toEqual([ids[1]]);
  release();
  await walk.state("02-read-in", { visible: [v.picture], hidden: [v.dialog.getByRole("status", { name: "Loading image…" })] });
  await expect(v.dialog.getByRole("button", { name: "Download image", exact: true })).toBeEnabled();
  // Then one picture each side, so a swipe never waits.
  await expect.poll(() => [...reads].sort()).toEqual([ids[1], ids[0], ids[2]].sort());
  await expect.poll(held).toBe(before + 3);

  // Moving on reads the next neighbour and lets go of the picture two steps behind.
  await page.keyboard.press("ArrowRight");
  await expect(v.count("3 / 4")).toBeVisible();
  await expect.poll(() => reads.length).toBe(4);
  expect(reads[3]).toBe(ids[3]);
  await expect.poll(held).toBe(before + 3);

  // A page in the background keeps only the picture on screen, and reads its neighbours again when it returns.
  const visibility = (state: "hidden" | "visible") => page.evaluate((state) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
  await visibility("hidden");
  await expect.poll(held).toBe(before + 1);
  await visibility("visible");
  await expect.poll(() => reads.length).toBe(6);
  expect(reads.slice(4).sort()).toEqual([ids[1], ids[3]].sort());
  await expect.poll(held).toBe(before + 3);

  // Closing lets go of everything the viewer read; a later opening reads again.
  await page.keyboard.press("Escape");
  await expect(v.dialog).toBeHidden();
  await expect.poll(held).toBe(before);
});

test("the actions: share or download on the phone, copy, download and a new tab on the desktop; an unsent picture has none", async ({ page, context }, info) => {
  const phone = info.project.name === "phone";
  await page.addInitScript((phone) => {
    // The share sheet belongs to the phone's system; the walkthrough records what the page hands it.
    const shared: string[] = [];
    (window as unknown as { shared: string[] }).shared = shared;
    if (!phone) return;
    Object.defineProperty(navigator, "canShare", { configurable: true, value: (data: ShareData) => Boolean(data.files?.length) });
    Object.defineProperty(navigator, "share", { configurable: true, value: async (data: ShareData) => { shared.push(...(data.files ?? []).map((file) => `${file.name} ${file.type}`)); } });
  }, phone);
  const walk = walkthrough(page, info);
  const { openers } = await sendPictures(page, info);
  const v = viewer(page);
  await (phone ? openers[1]!.tap() : openers[1]!.click());
  await settled(page);
  const button = (name: string) => v.dialog.getByRole("button", { name, exact: true });
  if (phone) {
    await walk.state("01-phone-share", { visible: [button("Share image"), v.close], hidden: [button("Download image"), button("Copy image"), button("Open in new tab")] });
    await button("Share image").tap();
    await expect.poll(() => page.evaluate(() => (window as unknown as { shared: string[] }).shared)).toEqual(["dashboard.png image/png"]);
  } else {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await walk.state("01-desktop-copied", { action: () => button("Copy image").click(),
      visible: [v.dialog.getByRole("status").filter({ hasText: "Image copied" })], hidden: [button("Share image")] });
    expect(await page.evaluate(async () => (await navigator.clipboard.read())[0]!.types)).toContain("image/png");
    const download = page.waitForEvent("download");
    await button("Download image").click();
    expect((await download).suggestedFilename()).toBe("dashboard.png");
    const popup = page.waitForEvent("popup");
    await button("Open in new tab").click();
    const tab = await popup;
    await tab.waitForLoadState();
    expect(new URL(tab.url()).pathname).toMatch(/^\/api\/images\/alpha\/[^/]+$/);
    await tab.close();
  }
  await page.keyboard.press("Escape");
  await expect(v.dialog).toBeHidden();

  // A picked picture opens from the composer before it is sent, with nothing to act on yet.
  await page.getByLabel("Choose images").setInputFiles(await picture(page, { ...PICTURES[3]!, name: "draft.png" }));
  const draft = page.getByLabel("Selected images", { exact: true }).getByRole("button", { name: "Open image draft.png", exact: true });
  await walk.state("02-unsent-picture", { action: () => draft.click(),
    visible: [v.dialog.getByText("draft.png", { exact: true }), v.dialog.getByText("Not sent yet", { exact: true }), v.close],
    hidden: [button("Download image"), button("Share image"), button("Copy image"), v.dialog.getByText("1 / 1")] });
  await v.close.click();
  await expect(v.dialog).toBeHidden();
  await expect(draft).toBeFocused();

  // Reduced motion: opening, moving and closing land at once.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openers[0]!.click();
  const moving = () => v.dialog.evaluate((node) => (node as HTMLElement).style.getPropertyValue("--moving"));
  expect(await moving()).toBe("0ms");
  await button("Next image").click();
  await expect(v.count("2 / 4")).toBeVisible();
  expect(await moving()).toBe("0ms");
  await page.keyboard.press("Escape");
  await expect(v.dialog).toBeHidden();
});
