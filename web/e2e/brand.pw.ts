import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { fixtureProject } from "./fixture-data";
import { walkthrough } from "./walkthrough";

test("the Altitude mark names the product in both themes and serves as the tab and Home Screen icon", async ({ page, request }, info) => {
  const phone = info.project.name === "phone";
  const walk = walkthrough(page, info);
  const project = await fixtureProject(request);
  const brand = phone ? page.locator(".phone-title") : page.locator(".rail-brand");
  const mark = brand.locator("svg.brand-mark");

  for (const theme of ["light", "dark"]) {
    if (theme === "dark") await page.addInitScript(() => localStorage.setItem("altitude.theme", "dark"));
    await walk.open("/");
    await walk.state(`${theme}-needs-you`, { visible: [mark, brand.getByText("Altitude", { exact: true })], hidden: [] });
    // The tile follows the theme's accent; the glyph stays legible on it.
    const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim());
    expect(accent).toBe(theme === "dark" ? "#6366f1" : "#4f46e5");
    if (phone) {
      await expect(page.getByRole("heading", { name: "Altitude", level: 1 })).toBeVisible();
      // A project tab names the project instead; the mark is only on the global tabs.
      await walk.state(`${theme}-project`, {
        action: () => page.getByRole("link", { name: "Chat", exact: true }).click(),
        visible: [page.getByRole("heading", { name: project.name, level: 1 })],
        hidden: [page.locator(".phone-header .brand-mark")],
      });
    }
  }

  const icon = page.locator('link[rel="icon"][type="image/svg+xml"]');
  await expect(icon).toHaveAttribute("type", "image/svg+xml");
  for (const [href, type] of [[await icon.getAttribute("href"), "image/svg+xml"], [await page.locator('link[rel="apple-touch-icon"]').getAttribute("href"), "image/png"]]) {
    const response = await request.get(href!);
    expect(response.ok(), `${href} must be served`).toBe(true);
    expect(response.headers()["content-type"]).toContain(type);
  }
});

test("installation and bookmark metadata serves Climb icons with safe launcher cropping", async ({ page, request, browserName }, info) => {
  const walk = walkthrough(page, info);
  // Root-relative discovery works from a deep link as well as the landing page.
  await walk.open("/settings");
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveAttribute("sizes", "180x180");
  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
  const response = await request.get(manifestHref!);
  expect(response.ok()).toBe(true);
  expect(response.headers()["content-type"]).toContain("application/manifest+json");
  expect(response.headers()["cache-control"]).toBe("no-store");
  const manifest = await response.json();
  expect(manifest).toMatchObject({
    id: "/", name: "Altitude", short_name: "Altitude", start_url: "/", scope: "/", display: "standalone",
  });
  // Chromium parses the served manifest itself, rather than only our JSON reader.
  if (browserName === "chromium") {
    const session = await page.context().newCDPSession(page);
    const parsed = await session.send("Page.getAppManifest");
    expect(parsed.errors).toEqual([]);
    expect(parsed.url).toBe(new URL(manifestHref!, page.url()).href);
    await session.detach();
  }
  for (const size of [192, 512]) {
    expect(manifest.icons).toContainEqual(expect.objectContaining({ sizes: `${size}x${size}`, type: "image/png", purpose: "any" }));
  }
  expect(manifest.icons).toContainEqual(expect.objectContaining({ sizes: "512x512", purpose: "maskable" }));

  const icons: { src: string; sizes: string; purpose: string }[] = [
    { src: (await page.locator('link[rel="apple-touch-icon"]').getAttribute("href"))!, sizes: "180x180", purpose: "apple" },
    ...manifest.icons,
  ];
  for (const icon of icons) {
    const asset = await request.get(icon.src);
    expect(asset.ok(), icon.src).toBe(true);
    expect(asset.headers()["content-type"]).toContain("image/png");
    expect(asset.headers()["cache-control"]).toBe("no-store");
    const pixels = await page.evaluate(async (src) => {
      const image = new Image();
      image.src = src;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d")!;
      context.drawImage(image, 0, 0);
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
      let white = 0, maxRadius = 0, opaque = true;
      for (let i = 0; i < data.length; i += 4) {
        opaque &&= data[i + 3] === 255;
        if (data[i] > 240 && data[i + 1] > 240 && data[i + 2] > 240 && data[i + 3] > 240) {
          white++;
          const x = (i / 4 % canvas.width + .5) / canvas.width - .5;
          const y = (Math.floor(i / 4 / canvas.width) + .5) / canvas.height - .5;
          maxRadius = Math.max(maxRadius, Math.hypot(x, y));
        }
      }
      return { size: `${canvas.width}x${canvas.height}`, whiteFraction: white / (data.length / 4),
        maxRadius, opaque, corner: [...data.slice(0, 4)] };
    }, icon.src);
    expect(pixels.size, icon.src).toBe(icon.sizes);
    expect(pixels.whiteFraction, `${icon.src} contains the white glyph`).toBeGreaterThan(.08);
    expect(pixels.whiteFraction).toBeLessThan(.3);
    if (icon.purpose === "maskable" || icon.purpose === "apple") {
      expect(pixels.opaque).toBe(true);
      expect(pixels.corner).toEqual([79, 70, 229, 255]);
    }
    if (icon.purpose === "maskable") expect(pixels.maxRadius).toBeLessThanOrEqual(.4);
  }

  const icoHref = await page.locator('link[rel="icon"][sizes="16x16 32x32 48x48"]').getAttribute("href");
  const ico = await request.get(icoHref!);
  expect(ico.ok()).toBe(true);
  expect(ico.headers()["content-type"]).toMatch(/^image\/(vnd.microsoft.icon|x-icon)/);
  expect(ico.headers()["cache-control"]).toBe("no-store");
  const bytes = await ico.body();
  expect(bytes.readUInt16LE(2)).toBe(1); // ICO, not the server's HTML route fallback.
  expect(bytes.readUInt16LE(4)).toBe(3);
  expect([0, 1, 2].map((i) => [bytes[6 + i * 16], bytes[7 + i * 16]])).toEqual([[16, 16], [32, 32], [48, 48]]);

  // Review-only gallery uses the served files. These masks are geometry previews, not native UI.
  // Playwright aborts intercepted URLs ending in /favicon.ico; a query lets the preview decode it.
  await page.setContent(`<style>
    body { margin: 20px; font: 16px system-ui; color: #0f172a; background: #f8fafc; }
    main { display: flex; flex-wrap: wrap; gap: 20px; } figure { margin: 0; width: 145px; }
    img { display: block; width: 120px; height: 120px; margin-bottom: 8px; }
    .round { border-radius: 50%; } .square { border-radius: 25%; }
  </style><h1>Climb icon treatments</h1><main>
    <figure><img src="/altitude-mark.svg">SVG favicon</figure>
    <figure><img src="/favicon.ico?preview" style="width:48px;height:48px">ICO fallback (48px)</figure>
    <figure><img src="/apple-touch-icon.png" class="square">Apple touch icon</figure>
    <figure><img src="/icon-192.png">Android 192px</figure>
    <figure><img src="/icon-512.png">Android 512px</figure>
    <figure><img src="/icon-maskable-512.png">Maskable full bleed</figure>
    <figure><img src="/icon-maskable-512.png" class="round">Circular crop</figure>
    <figure><img src="/icon-maskable-512.png" class="square">Rounded square crop</figure>
    <figure><img src="/icon-maskable-512.png" style="clip-path:circle(40%)">Minimum safe circle</figure>
  </main>`);
  await page.locator("img").evaluateAll(async (images) => {
    await Promise.all(images.map((image) => (image as HTMLImageElement).decode()));
  });
  await walk.state("served-icon-treatments", { visible: [page.getByRole("heading", { name: "Climb icon treatments" })], hidden: [] });
});
