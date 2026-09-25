// Fictional editorial data for the actual production web app. No service state is read.
export const now = '2026-09-07T16:00:00Z';
const ago = minutes => new Date(Date.parse(now) - minutes * 60_000).toISOString();
const message = (id, role, text, minutes) => ({ id, role, text, at: ago(minutes) });
const task = (slug, title, engine, minutes) => ({
  slug, title, state: 'running', attempt: 1, session_id: `fixture-${slug}`,
  l2_engine: engine, dispatched: ago(minutes), updated: ago(2),
  live: { state: 'running', context_percent: 24, edits: 6, agent: { status: 'working' } },
  files: {}, events: [], messages: [], report_json: null,
});
export function fixtures() {
  const compatibility = task('client-compatibility', 'Preserve client compatibility', 'codex', 34);
  compatibility.messages = [
    message('c1', 'l2', 'I am covering the v1 response contract and the index-switch boundary. Existing clients will keep the same request and response shape.', 22),
    message('c2', 'burak', 'Keep pagination tokens valid across the cutover. Clients must not restart an in-flight search.', 18),
    message('c3', 'l2', 'I will bind each token to its index generation. New searches can move to v2 while existing cursors finish on v1. The tests will cross the switch in both directions.', 17),
  ];
  const activity = 'Contract tests pass. I am checking rollback across index generations.';
  compatibility.activity = {
    generation: compatibility.session_id, state: 'available',
    commentary: { id: 'fixture-activity', text: activity, at: ago(0.25), time_kind: 'source' },
    observation: { at: ago(0.25), label: 'Assistant output' },
  };
  const backfill = task('resumable-backfill', 'Build resumable index backfill', 'claude', 32);
  backfill.messages = [
    message('b1', 'l2', 'Can a retried batch rewrite a document that already reached the new index?', 20),
    message('b2', 'l3', 'Use the recorded rule: upsert by document ID and source revision. A replay must not replace a newer revision. Resume with that contract.', 19),
  ];
  const performance = task('performance-baseline', 'Measure search latency baseline', 'codex', 30);
  const tasks = [compatibility, backfill, performance];
  const history = [
    { role: 'user', text: 'Move Atlas to a versioned search index. Keep the v1 API stable and p95 below 200 ms. Agree the rollout constraints before starting.', trigger: 'chat', turn_id: 't1', at: ago(65) },
    { role: 'assistant', text: 'Keep the response contract unchanged; bind cursors to an index generation. Backfills use document ID and source revision so retries are safe. Establish latency on the current index before comparing v2.', trigger: 'chat', turn_id: 't1', engine: 'codex', at: ago(64) },
    { role: 'user', text: 'Agreed. Start compatibility, backfill and the performance baseline independently. Hold the rollout until we have the results.', trigger: 'chat', turn_id: 't2', at: ago(35) },
    { role: 'assistant', text: 'Three L2 owners are working in separate worktrees.\n\n**Compatibility** covers the v1 contract and cursors. **Backfill** implements resumable batches. **Performance** measures the existing index while those changes are built.\n\nI will use their reports to coordinate rollout readiness. You can steer each owner directly from its task.', trigger: 'chat', turn_id: 't2', engine: 'codex', at: ago(30) },
  ];
  return {
    setup: { project: 'atlas', status: 'ready', steps: [], operation: null },
    images: {
      available: true, max_count: 4, max_bytes: 10 * 1024 * 1024,
      max_total_bytes: 20 * 1024 * 1024, max_pixels: 25_000_000, max_dimension: 8192,
    },
    voice: { backend: 'browser', selection: 'fixture-browser', url: '', model: '', key_set: false },
    overview: {
      projects: [
        { name: 'atlas', managed: true, counts: { running: 3, blocked: 0 }, l3: { session_id: 'fixture-l3' } },
        { name: 'harbor', managed: true, counts: { blocked: 0 }, l3: { session_id: 'fixture-harbor-l3' } },
      ],
      queue: [],
      wip: { per_project: { atlas: 3 }, machine: 3, waiting: [] },
      quota: { known: false }, operator: 'Alex', now,
      engines: [
        { engine: 'codex', label: 'Codex', week: 36, known: true, at: ago(1) },
        { engine: 'claude', label: 'Claude', week: 42, known: true, at: ago(1) },
      ],
    },
    project: { name: 'atlas', tasks, archive: [], decisions: [], l3: { session_id: 'fixture-l3', turns: 14 }, busy: false },
    chat: { history, busy: false, active: null, queued: [], engine: null },
    tasks,
    transcript: {
      project: 'atlas', slug: compatibility.slug, engine: 'codex', session_id: compatibility.session_id,
      cursor: 6, redaction: 'Fictional fixture data; no provider logs are read.',
      events: [
        { seq: 0, source: 'platform', kind: 'boundary', type: 'state', at: ago(34), text: 'queued → running' },
        { seq: 1, source: 'codex', kind: 'message', type: 'assistant', role: 'assistant', at: ago(18), text: 'The cursor currently holds an offset. I am checking where the index generation can travel with it.' },
        { seq: 2, source: 'codex', kind: 'command', type: 'command', tool: 'shell', tool_use_id: 'fixture-command', at: ago(16), text: 'rg -n "cursor|generation" src/search', summary: 'rg -n "cursor|generation" src/search', output: 'src/search/cursor.ts:12: export function decodeCursor(token)\nsrc/search/index.ts:48: const generation = activeIndex()', status: 'completed' },
        { seq: 3, source: 'codex', kind: 'message', type: 'assistant', role: 'assistant', at: ago(15), text: 'The compatibility boundary is small: decode the token, select its generation, preserve the response shape. I will cover old cursors, new searches and rollback in the contract suite.' },
        { seq: 4, source: 'codex', kind: 'command', type: 'command', tool: 'shell', tool_use_id: 'fixture-tests', at: ago(12), text: 'pnpm test -- search-contract', summary: 'pnpm test -- search-contract', output: '18 contract tests passed', status: 'completed' },
        { seq: 5, source: compatibility.l2_engine, kind: 'message', type: 'assistant', role: 'assistant', at: ago(0.25), text: activity },
      ],
    },
  };
}

// A later moment in the same fictional migration: engineering needs a product decision.
export function askRetention(data) {
  const task = data.tasks.find(t => t.slug === 'resumable-backfill');
  const question = {
    project: 'atlas', slug: task.slug, title: task.title,
    id: 'retention-window', revision: 1, anchor_id: 'retention-question',
    kind: 'asks', asked_by: 'l3', audience: 'operator', status: 'open', state: 'blocked',
    asked: now, since: now, resolution: null,
    question: 'How long should we keep the old index for rollback?',
    options: [
      { key: 'seven', label: '7 days', text: 'Keep the old index for seven days.' },
      { key: 'thirty', label: '30 days', text: 'Keep the old index for thirty days.' },
    ],
    recommended_key: 'seven',
    recommendation: {
      text: 'Keep it for seven days.', label: '7 days',
      why: 'Covers the pilot and a full traffic cycle. Thirty days gives a longer rollback window but keeps both indexes on disk.',
    },
  };
  task.state = 'blocked';
  task.question = question;
  task.questions = [question];
  task.question_group = { id: question.id, revision: 1, anchor_id: question.anchor_id, questions: [question] };
  task.messages.push({ id: question.anchor_id, role: 'l2', text: question.question, at: now });
  data.overview.queue = [question];
  data.overview.projects[0].counts = { running: 2, blocked: 1, needs_you: 1 };
  data.overview.wip = { per_project: { atlas: 2 }, machine: 2, waiting: [] };
  return question;
}
