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
 * A push carries nothing: it only wakes this device. The worker then asks Altitude what is waiting and
 * names the project and task; a phone that cannot reach Altitude says a decision is waiting and no more.
 * `altitude-alerts` keeps the tags already announced, so a later push repeats none of them.
 */
const ANNOUNCED = "altitude-alerts";

self.addEventListener("push", (event) => event.waitUntil(alertWaiting()));

async function alertWaiting() {
  const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  // A page on screen alerts for itself, and shows the decision the operator is already reading.
  if (open.some((client) => client.visibilityState === "visible")) return;
  const waiting = await pending();
  if (!waiting) {
    return self.registration.showNotification("A decision needs you", {
      body: "Open Altitude to read it.", tag: "altitude-decision", data: { url: "/" },
    });
  }
  const announced = await remembered();
  await remember(waiting.map((decision) => decision.tag));
  for (const decision of waiting.filter((decision) => !announced.includes(decision.tag))) {
    await self.registration.showNotification(`${decision.project} needs a decision`, {
      body: decision.title, tag: decision.tag, data: { url: decision.url },
    });
  }
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
