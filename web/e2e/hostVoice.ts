import type { Page, Route } from "@playwright/test";

/*
 * The fixture host for host voice walkthroughs: an overlaid /api/voice with its setup state, and live
 * recordings that answer each chunk from the samples heard, so no model runs and nothing leaves the page.
 * `offline` drops every live request as a lost connection would; `forget` makes the host answer 410 for the
 * recording it knows, as after an idle drop or a restart, so the page opens a new one and replays; `holdAudio`
 * holds the chunks' answers; `live` replaces the words answered while listening.
 */

type HostState = Record<string, unknown>;
export const READY = { state: "ready", download_bytes: 698435338 };

/** The fixture host: settings, setup actions and live recordings. */
export async function fixtureHost(page: Page, initial: HostState = READY) {
  const host = {
    state: initial,
    backend: "host",
    samples: 0,
    chunks: [] as number[],
    requests: [] as string[],
    refuse: null as null | { status: number; error: string },
    failAudio: null as null | { status: number; error: string },
    holdFinal: null as null | Promise<void>,
    /** Replaces the final words; `finals` counts final requests as they arrive, before `holdFinal`. */
    final: null as null | string,
    finals: 0,
    /** Answers the final request with this error once `holdFinal` settles. */
    failFinal: null as null | { status: number; error: string },
    /** Holds every chunk's answer until it settles, as a slow host would. */
    holdAudio: null as null | Promise<void>,
    /** The words answered while listening, in place of "check" and "check the build". */
    live: null as null | string,
    offline: false,
    forget: false,
    opened: 0,
    id: "",
  };
  const settings = () => ({ backend: host.backend, selection: `fixture-${host.backend}`, host: host.state });
  await page.route((url) => url.pathname === "/api/voice", async (route: Route) => {
    if (route.request().method() === "POST") host.backend = JSON.parse(route.request().postData() ?? "{}").backend;
    await route.fulfill({ json: settings() });
  });
  await page.route((url) => url.pathname === "/api/voice/host", async (route) => {
    const { action } = JSON.parse(route.request().postData() ?? "{}");
    host.requests.push(action);
    host.state = action === "setup" ? { state: "setting-up", download_bytes: 698435338, done_bytes: 0 } : { state: "absent", download_bytes: 698435338 };
    await route.fulfill({ json: settings() });
  });
  await page.route((url) => url.pathname.startsWith("/api/voice/live"), async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/cancel")) {
      host.requests.push(path.replace(/^\/api\/voice\/live/, "live"));
      return route.fulfill({ json: { ok: true } });
    }
    if (host.offline) return route.abort("internetdisconnected");
    host.requests.push(path.replace(/^\/api\/voice\/live/, "live"));
    if (path === "/api/voice/live") {
      if (host.refuse) return route.fulfill({ status: host.refuse.status, json: { error: host.refuse.error } });
      host.samples = 0;
      host.chunks = [];
      host.opened += 1;
      host.id = host.opened === 1 ? "fixture" : `fixture-${host.opened}`;
      return route.fulfill({ json: { id: host.id, owner: "fixture-device" } });
    }
    if (host.forget || !path.startsWith(`/api/voice/live/${host.id}/`)) {
      host.forget = false;
      host.id = "";
      return route.fulfill({ status: 410, json: { error: "Voice stopped: this recording has ended." } });
    }
    if (host.failAudio) return route.fulfill({ status: host.failAudio.status, json: { error: host.failAudio.error } });
    await host.holdAudio;
    const samples = (route.request().postDataBuffer()?.length ?? 0) / 2;
    host.samples += samples;
    host.chunks.push(samples);
    const final = new URL(route.request().url()).searchParams.get("final") === "1";
    if (final) {
      host.finals += 1;
      await host.holdFinal;
      if (host.failFinal) return route.fulfill({ status: host.failFinal.status, json: { error: host.failFinal.error } });
      return route.fulfill({ json: { text: host.final ?? `Heard ${host.chunks.length} chunks.`, final: true } });
    }
    return route.fulfill({ json: { text: host.live ?? (host.chunks.length > 1 ? "check the build" : "check") } });
  });
  return host;
}
