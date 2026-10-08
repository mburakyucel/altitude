/**
 * Decision alerts (issue #221). Every alert is shown through this worker's registration, which is
 * what Android requires and what web push uses next. The worker has no fetch handler: it caches
 * nothing, serves nothing offline, and holds no conversation text.
 */
self.addEventListener("install", () => self.skipWaiting());
// Claim the page that registered the worker, so its click handler reaches an already open Altitude.
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const client = open.find((candidate) => new URL(candidate.url).origin === self.location.origin);
      if (!client) return self.clients.openWindow(url);
      await client.focus();
      // The open app routes to the decision itself: a reload would lose an unsent reply.
      client.postMessage({ type: "alert-open", url });
    })(),
  );
});

/**
 * A push carries nothing: it only wakes this device, for a decision that newly needs the operator or for
 * one that no longer does. The worker asks Altitude what is waiting, names the project and task of each
 * new decision, and closes the banners whose decision has left the queue. `altitude-alerts` keeps the
 * tags already announced, so a later push repeats none of them.
 */
const ANNOUNCED = "altitude-alerts";
const ANY_DECISION = "altitude-decision"; // shown when Altitude is out of reach; stands for any waiting decision

self.addEventListener("push", (event) => event.waitUntil(alertWaiting()));

async function alertWaiting() {
  const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const shown = await self.registration.getNotifications();
  // A page on screen alerts for itself and closes what it no longer lists.
  if (open.some((client) => client.visibilityState === "visible")) return acknowledge(shown);
  const waiting = await pending();
  if (!waiting) {
    // The push may announce a decision or clear one, so a banner already shown stays and none is added;
    // with none shown, the phone says a decision is waiting and no more.
    if (shown.length) return acknowledge(shown);
    return self.registration.showNotification("A decision needs you", {
      body: "Open Altitude to read it.", tag: ANY_DECISION, data: { url: "/" },
    });
  }
  const tags = new Set(waiting.map((decision) => decision.tag));
  const kept = [];
  for (const banner of shown) {
    if (banner.tag === ANY_DECISION ? tags.size : tags.has(banner.tag)) kept.push(banner);
    else banner.close(); // answered, withdrawn or superseded since it was shown
  }
  const announced = await remembered();
  // A decision whose task is still moving is neither shown nor recorded, so it alerts once the task rests.
  const fresh = waiting.filter((decision) => !decision.held && !announced.includes(decision.tag));
  await remember(waiting.filter((decision) => !decision.held || announced.includes(decision.tag))
    .map((decision) => decision.tag));
  if (!fresh.length) return acknowledge(kept);
  for (const decision of fresh) {
    await self.registration.showNotification(`${decision.project} needs a decision`, {
      body: decision.title, tag: decision.tag, data: { url: decision.url },
    });
  }
}

/**
 * Every push must show a notification, or Safari ends the subscription after three. With nothing new to
 * say, a banner still standing is shown again in place without a sound; with none, a silent one is shown
 * and closed at once, so no invented decision stays on the device.
 */
async function acknowledge(kept) {
  const [banner] = kept;
  if (banner) {
    return self.registration.showNotification(banner.title, {
      body: banner.body, tag: banner.tag, data: banner.data, silent: true,
    });
  }
  await self.registration.showNotification("Altitude", { tag: "altitude-quiet", silent: true });
  for (const quiet of await self.registration.getNotifications({ tag: "altitude-quiet" })) quiet.close();
}

/** What Altitude says is waiting, or null while this device cannot reach it. */
async function pending() {
  try {
    const response = await fetch("/api/overview", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) return null;
    const rows = (await response.json()).queue || [];
    return rows.filter((row) => row.id).map((row) => {
      const query = new URLSearchParams({ question: row.id }); // the link Needs you uses for that ask
      if (row.revision !== undefined && row.revision !== null) query.set("revision", String(row.revision));
      return {
        project: row.project,
        title: row.title || row.slug,
        held: Boolean(row.alert_held),
        tag: `${row.project}:${row.slug}:${row.group_id || row.id}`, // the decision, not its revision
        url: `/projects/${row.project}/tasks/${row.slug}?${query}`,
      };
    });
  } catch {
    return null; // no route to Altitude, or it did not answer in time
  }
}

async function remembered() {
  const stored = await (await caches.open(ANNOUNCED)).match("/announced");
  return stored ? await stored.json() : [];
}

async function remember(tags) {
  await (await caches.open(ANNOUNCED)).put("/announced", new Response(JSON.stringify(tags)));
}
