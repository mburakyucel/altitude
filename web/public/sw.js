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
