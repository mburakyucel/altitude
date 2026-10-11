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
      // The open app routes to the decision itself: a reload would lose an unsent reply. The route goes first,
      // so a window the platform will not focus still shows the decision when the tap brings it forward.
      client.postMessage({ type: "alert-open", url });
      try {
        await client.focus();
      } catch {
        await self.clients.openWindow(url);
      }
    })(),
  );
});

/**
 * A push carries nothing and is sent only when a decision newly needs the operator: first asked, or asked again
 * after they replied. The worker asks Altitude what is waiting, closes the banners whose decision has left the
 * queue, and names the project and task of each new decision. Altitude keys each decision (`alert_key`) and
 * renews the key only when it is asked again after a reply, so this worker need not have seen it leave;
 * `altitude-alerts` keeps the keys already announced, so a later push repeats none.
 *
 * Every push shows a notification, because Safari ends a subscription after three that show none, and
 * none is a bare "Altitude": WebKit keeps a banner it is asked to close within 30 seconds, and a tag never
 * replaces an earlier banner on iOS, so whatever is shown stays and must say something true.
 */
const ANNOUNCED = "altitude-alerts";
const ANY_DECISION = "altitude-decision"; // shown when Altitude is out of reach; stands for any waiting decision

self.addEventListener("push", (event) => event.waitUntil(alertWaiting()));

async function alertWaiting() {
  const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  // With Altitude on screen the alert still shows, without a sound.
  const silent = open.some((client) => client.visibilityState === "visible");
  const waiting = await pending();
  if (!waiting) {
    return self.registration.showNotification("A decision needs you", {
      body: "Altitude is out of reach, so this alert can't name it.", tag: ANY_DECISION, data: { url: "/" }, silent,
    });
  }
  const tags = new Set(waiting.map((decision) => decision.tag));
  for (const banner of await self.registration.getNotifications()) {
    // Answered, withdrawn, superseded or asked again since it was shown; a generic banner is named below or no
    // longer true.
    if (banner.tag === ANY_DECISION || !tags.has(banner.tag)) banner.close();
  }
  const announced = await remembered();
  // A decision whose task is still moving is neither shown nor recorded, so it alerts once the task rests.
  const ready = waiting.filter((decision) => !decision.held);
  const fresh = ready.filter((decision) => !announced.includes(decision.tag));
  await remember(waiting.filter((decision) => !decision.held || announced.includes(decision.tag))
    .map((decision) => decision.tag));
  for (const decision of fresh) {
    await self.registration.showNotification(`${decision.project} needs a decision`, {
      body: decision.title, tag: decision.tag, data: { url: decision.url }, silent,
    });
  }
  if (fresh.length) return;
  // Several wakes delivered together, or handed back between the wake and this read: the push still shows,
  // silently, and says what waits rather than that nothing is new.
  const [decision] = ready;
  if (ready.length === 1) {
    return self.registration.showNotification(`${decision.project} still needs a decision`, {
      body: decision.title, tag: decision.tag, data: { url: decision.url }, silent: true,
    });
  }
  const [title, body] = ready.length
    ? ["Still waiting for you", ready.map((waits) => `${waits.project} · ${waits.title}`).join("\n")]
    : waiting.length
      ? ["No decision needs you now", "L3 or the task's owner is handling it first."]
      : ["No decision needs you now", "It was settled before this alert arrived."];
  return self.registration.showNotification(title, {
    body, tag: "altitude-nothing-new", data: { url: "/" }, silent: true,
  });
}

/** What Altitude says is waiting, one entry per decision (a question group), or null while this device cannot
 * reach it. */
async function pending() {
  try {
    const response = await fetch("/api/overview", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) return null;
    const decisions = new Map();
    for (const row of (await response.json()).queue || []) {
      if (!row.alert_key || decisions.has(row.alert_key)) continue; // reviews, faults and stops alert no one
      const query = new URLSearchParams({ question: row.id }); // the link Needs you uses for that ask
      if (row.revision !== undefined && row.revision !== null) query.set("revision", String(row.revision));
      decisions.set(row.alert_key, {
        project: row.project,
        title: row.title || row.slug,
        held: Boolean(row.alert_held),
        tag: row.alert_key,
        url: `/projects/${row.project}/tasks/${row.slug}?${query}`,
      });
    }
    return [...decisions.values()];
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
