// Render the built application with shareable fixtures, without running altd or an engine.
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { fixtures, askRetention, now } from './fixtures.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(resolve(root, 'web/package.json'));
const { chromium, devices, expect } = require('@playwright/test');
const images = resolve(root, 'web/ui-artifacts/readme/images');
const evidence = process.env.README_EVIDENCE_DIR || resolve(root, 'web/ui-artifacts/readme');
await mkdir(images, { recursive: true });
await mkdir(evidence, { recursive: true });
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  const relative = path.startsWith('/assets/') ? `web/dist${path}` : 'web/dist/index.html';
  try {
    const body = await readFile(resolve(root, relative));
    const type = { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.html': 'text/html' }[extname(relative)];
    res.writeHead(200, { 'Content-Type': type || 'application/octet-stream' }).end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({
  channel: 'chromium', chromiumSandbox: false,
  env: { ...process.env, XDG_CONFIG_HOME: resolve(evidence, 'browser-config') },
});
try {
  for (const phone of [false, true]) {
    const name = phone ? 'phone' : 'desktop';
    const context = await browser.newContext({
      ...(phone ? devices['Pixel 7'] : {}),
      viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      deviceScaleFactor: 2, locale: 'en-US', timezoneId: 'UTC', colorScheme: 'light',
      ...(phone ? { recordVideo: { dir: resolve(evidence, 'video'), size: { width: 390, height: 844 } } } : {}),
    });
    const page = await context.newPage();
    await page.clock.setFixedTime(new Date(now));
    const data = fixtures();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
    await context.route('**/api/**', async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path === '/api/changes') return route.fulfill({ status: 204 });
      let body;
      if (path === '/api/overview') body = data.overview;
      else if (path === '/api/setup/atlas') body = data.setup;
      else if (path === '/api/images/atlas') body = data.images;
      else if (path === '/api/voice') body = data.voice;
      else if (path === '/api/project/atlas') body = data.project;
      else if (path === '/api/chat/atlas') body = data.chat;
      else if (path.startsWith('/api/task/atlas/')) body = data.tasks.find(t => t.slug === path.split('/').at(-1));
      else if (path === '/api/transcript/atlas/client-compatibility') body = data.transcript;
      else if (path === '/api/l2/message' && request.method() === 'POST') {
        const sent = request.postDataJSON();
        const task = data.tasks.find(t => t.slug === sent.slug);
        const message = { id: 'fixture-sent', role: 'burak', text: sent.text, at: now };
        task.messages.push(message);
        body = { ok: true, message };
      }
      else if (path === '/api/decide' && request.method() === 'POST') {
        const sent = request.postDataJSON();
        expect(sent).toEqual({ project: 'atlas', slug: 'resumable-backfill', question_id: 'retention-window', revision: 1, option_key: 'seven' });
        const task = data.tasks.find(t => t.slug === sent.slug);
        task.question.response = { text: 'Keep the old index for seven days.', at: now, message_id: 'retention-answer' };
        data.overview.queue = [];
        body = { question: task.question };
      }
      if (!body) {
        errors.push(`Unexpected fixture request: ${request.method()} ${path}`);
        return route.fulfill({ status: 404, json: { error: 'No fixture' } });
      }
      return route.fulfill({ json: body });
    });
    async function capture(file, publish = true) {
      await page.evaluate(() => document.fonts.ready);
      await expect(page.locator('body')).not.toContainText('Could not load');
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`${file}: horizontal overflow`);
      await page.screenshot({ path: resolve(publish ? images : evidence, `${file}-${name}.png`) });
      // Give the phone recording time to show each real interface state.
      if (phone) await page.waitForTimeout(1400);
    }
    // The phone capture uses a recent conversation excerpt so it starts at a complete message.
    // The endpoint returns a history window; no component or layout is changed for capture.
    if (phone) data.chat.history = data.chat.history.slice(-2);
    await page.goto(`${origin}/projects/atlas`);
    await expect(page.getByRole('region', { name: 'Conversation', exact: true })).toContainText('Three L2 owners');
    await capture('project');
    if (phone) {
      await page.getByRole('link', { name: 'Work', exact: true }).click();
      await expect(page.getByRole('region', { name: 'Work', exact: true })).toContainText('Preserve client compatibility');
      await capture('work');
    }
    await page.getByRole('link', { name: /^Preserve client compatibility ·/ }).click();
    await expect(page.getByRole('region', { name: 'Task conversation' })).toContainText('bind each token');
    if (!phone) await expect(page.getByRole('region', { name: 'Live session', exact: true })).toContainText('compatibility boundary');
    await capture('task');
    await page.getByRole('textbox', { name: 'Message the L2', exact: true }).fill('Include the rollback case in the PR notes.');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Task conversation' })).toContainText('Include the rollback case');
    await expect(page.getByRole('textbox', { name: 'Message the L2', exact: true })).toHaveValue('');
    await capture('task-message-sent', false);
    if (phone) {
      await page.getByRole('link', { name: 'Live session', exact: true }).click();
      await expect(page.getByRole('region', { name: 'Live session', exact: true })).toContainText('compatibility boundary');
      await capture('session');
    }
    const question = askRetention(data);
    await page.goto(`${origin}/`);
    await expect(page.getByRole('heading', { name: 'Needs you', exact: true })).toBeVisible();
    await expect(page.getByText(question.question, { exact: true })).toBeVisible();
    await capture('decision');
    await page.getByRole('button', { name: /7 days/ }).click();
    await expect(page.getByRole('button', { name: 'Send 1 answer', exact: true })).toBeEnabled();
    await capture('decision-selected', false);
    await page.getByRole('button', { name: 'Send 1 answer', exact: true }).click();
    await expect(page.getByText('Nothing needs you.', { exact: true })).toBeVisible();
    await capture('decision-sent', false);
    if (errors.length) throw new Error(errors.join('\n'));
    await context.close();
    if (phone) await page.video().saveAs(resolve(images, 'phone-walkthrough.webm'));
    console.log(`${name}: project → work → task → direct message → session → decision → answer; no console errors or viewport overflow`);
  }
} finally {
  await browser.close();
  await new Promise(done => server.close(done));
}
