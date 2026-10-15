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

// A store that cannot be read or written still ends in a banner, one that names nothing, so a failure never
// reveals more than this device chose nor leaves a push without the banner Safari requires.
self.addEventListener("push", (event) => event.waitUntil(alertWaiting().catch(() =>
  self.registration.showNotification("A decision needs you", { tag: ANY_DECISION, data: { url: "/" } }))));

/**
 * This device's preferences, which its Settings page keeps beside the announced keys: what a banner reveals
 * (`task`, `project` or `none`), the pace Altitude wakes it at (`every` seconds, decisions in between arriving
 * together) and whether it sounds. A device that never chose names the project and task, every time, aloud.
 */
async function preferences() {
  const stored = await (await caches.open(ANNOUNCED)).match("/preferences");
  const chosen = stored ? await stored.json() : {};
  return {
    reveal: ["project", "none"].includes(chosen.reveal) ? chosen.reveal : "task",
    every: Number(chosen.every) || 0,
    sound: chosen.sound !== false,
  };
}

/** One decision's banner: "<project> needs a decision" over its task name, as much of that as `reveal` allows. */
function named(decision, still, reveal, silent) {
  const needs = still ? "still needs" : "needs";
  return [reveal === "none" ? `A decision ${needs} you` : `${decision.project} ${needs} a decision`, {
    body: reveal === "task" ? decision.title : undefined, tag: decision.tag, data: { url: decision.url }, silent,
  }];
}

/** One line per decision, the projects alone, or nothing, as `reveal` allows. */
function lines(decisions, reveal) {
  if (reveal === "none") return undefined;
  return (reveal === "task" ? decisions.map((decision) => `${decision.project} · ${decision.title}`)
    : [...new Set(decisions.map((decision) => decision.project))]).join("\n");
}

async function alertWaiting() {
  const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const waiting = await pending();
  if (waiting) {
    const tags = new Set(waiting.map((decision) => decision.tag));
    for (const banner of await self.registration.getNotifications()) {
      // Answered, withdrawn, superseded or asked again since it was shown; a generic banner is named below or no
      // longer true. This comes before reading the device's store, so a store that fails still clears them.
      if (banner.tag === ANY_DECISION || !tags.has(banner.tag)) banner.close();
    }
  }
  const chosen = await preferences();
  // With Altitude on screen the alert still shows, without a sound.
  const silent = !chosen.sound || open.some((client) => client.visibilityState === "visible");
  if (!waiting) {
    return self.registration.showNotification("A decision needs you", {
      body: "Altitude is out of reach, so this alert can't name it.", tag: ANY_DECISION, data: { url: "/" }, silent,
    });
  }
  const announced = await remembered();
  // A decision whose task is still moving is neither shown nor recorded, so it alerts once the task rests.
  const ready = waiting.filter((decision) => !decision.held);
  const fresh = ready.filter((decision) => !announced.includes(decision.tag));
  await remember(waiting.filter((decision) => !decision.held || announced.includes(decision.tag))
    .map((decision) => decision.tag));
  if (chosen.every && fresh.length > 1) {
    // Decisions that arrived while Altitude waited out this device's pace come together, opening Needs you.
    return self.registration.showNotification(`${fresh.length} decisions need you`, {
      body: lines(fresh, chosen.reveal), tag: fresh[0].tag, data: { url: "/" }, silent,
    });
  }
  for (const decision of fresh) {
    await self.registration.showNotification(...named(decision, false, chosen.reveal, silent));
  }
  if (fresh.length) return;
  // Several wakes delivered together, or handed back between the wake and this read: the push still shows,
  // silently, and says what waits rather than that nothing is new.
  const [decision] = ready;
  if (ready.length === 1) {
    return self.registration.showNotification(...named(decision, true, chosen.reveal, true));
  }
  const [title, body] = ready.length
    ? ["Still waiting for you", lines(ready, chosen.reveal) ?? `${ready.length} decisions`]
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
      // An operator step has no task: it names only that it is one and opens Needs you, which shows it (decisions.ts).
      decisions.set(row.alert_key, {
        project: row.project,
        title: row.slug === null ? "A command to run" : row.title || row.slug,
        held: Boolean(row.alert_held),
        tag: row.alert_key,
        url: row.slug === null ? "/" : `/projects/${row.project}/tasks/${row.slug}?${query}`,
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
