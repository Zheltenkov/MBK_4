// Independent debt landing acceptance through the real widget and real page markup.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEBT_KEY = 'mbkDebtChat.sid';
const NORMAL_KEY = 'mbkAiChat.sid';
const QUESTION = 'Какая у вас общая сумма долга? Укажите сумму в рублях.';
const REGION = 'В каком городе и регионе вы живёте?';

function publicState({ history, mode = 'debt', fields = ['debts.total'], ...extra } = {}) {
  return {
    chat_history: history || [{ role: 'assistant', content: QUESTION }],
    questionnaire_policy: { version: 1, mode, reason: 'explicit_request' },
    quick_replies: [],
    plan: { action: 'ask', fields, matched: null, routes: [] },
    dialog_phase: 'collecting_debt_context',
    crm_lead: { status: 'not_sent' },
    crm_workflow: { need_site_consent: null },
    closed: false,
    ...extra,
  };
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

async function waitUntil(check, timeout = 2500) {
  const deadline = Date.now() + timeout;
  while (!check()) {
    if (Date.now() >= deadline) throw new Error('Debt UI condition timed out');
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

async function mount(t, {
  debtSid = null, normalSid = null, createState = publicState(),
  restoreState = publicState(), restoreStatus = 200, restoreSid = debtSid,
  busy = false, fetchImpl = null,
  url = 'http://127.0.0.1:8777/debt/',
} = {}) {
  const markup = readFileSync(join(HERE, '..', 'debt', 'index.html'), 'utf8');
  const source = readFileSync(join(HERE, '..', '_ai-chat.js'), 'utf8');
  const dom = new JSDOM(markup, { url, runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  const streams = [];
  t.after(() => { streams.forEach((stream) => stream.close()); window.close(); });
  const actualTimer = window.setTimeout.bind(window);
  // Keep the real recovery state machine, accelerating only its documented500ms poll.
  window.setTimeout = (handler, delay, ...args) => actualTimer(handler, delay === 500 ? 5 : delay, ...args);
  window.MBK_AI_CHAT_REVEAL_MS = 0;
  if (debtSid) window.localStorage.setItem(DEBT_KEY, debtSid);
  if (normalSid) window.localStorage.setItem(NORMAL_KEY, normalSid);
  window.fetch = async (urlValue, options = {}) => {
    const call = { url: String(urlValue), options, method: options.method || 'GET' };
    calls.push(call);
    if (fetchImpl) return fetchImpl(call, calls.length);
    if (call.method === 'POST') return response({ session_id: 'debt-new', state: createState });
    return response({ session_id: restoreSid, state: restoreState, turn_in_progress: busy }, restoreStatus);
  };
  class FakeEventSource {
    constructor(urlValue) {
      this.url = String(urlValue);
      this.closed = false;
      this.listeners = new Map();
      streams.push(this);
    }
    addEventListener(name, handler) {
      const callbacks = this.listeners.get(name) || [];
      callbacks.push(handler);
      this.listeners.set(name, callbacks);
    }
    close() { this.closed = true; }
    emit(name, value) {
      // Deliberately deliver queued events even after close to exercise generation guards.
      this.emitRaw(name, JSON.stringify(value));
    }
    emitRaw(name, data) {
      for (const handler of this.listeners.get(name) || []) handler({ data });
    }
  }
  window.EventSource = FakeEventSource;
  window.eval(source);
  const root = window.document.querySelector('#mbk-ai-chat');
  assert.ok(root);
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { window, root, calls, streams,
    posts: () => calls.filter((call) => call.method === 'POST'),
    gets: () => calls.filter((call) => call.method === 'GET'),
    feed: () => root.querySelector('.mbkc-feed-inner').textContent,
  };
}

function start(ui) {
  const button = ui.root.querySelector('[data-action="start-debt"]');
  assert.ok(button, 'The debt page needs an explicit start control');
  button.click();
}

function submit(ui, message) {
  const input = ui.root.querySelector('.mbkc-input');
  input.value = message;
  input.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.root.querySelector('.mbkc-inputbar').dispatchEvent(
    new ui.window.Event('submit', { bubbles: true, cancelable: true }),
  );
}

async function created(ui) {
  start(ui);
  await waitUntil(() => ui.feed().includes(QUESTION));
}

test('debt markup declares embedded scenario and page load does not allocate a session', async (t) => {
  const ui = await mount(t);
  assert.equal(ui.root.dataset.scenario, 'debt');
  assert.equal(ui.root.dataset.layout, 'embedded');
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.streams.length, 0);
});

test('start explicitly creates debt and renders the server first question without opening SSE', async (t) => {
  const ui = await mount(t);
  await created(ui);
  assert.equal(ui.posts().length, 1);
  const body = JSON.parse(ui.posts()[0].options.body);
  assert.equal(body.requested_scenario, 'debt');
  for (const key of ['anketa', 'context_expected', 'capability', 'phone', 'trusted_signal']) assert.equal(body[key], undefined);
  assert.equal(ui.posts()[0].options.credentials, 'same-origin');
  assert.equal(ui.streams.length, 0);
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), 'debt-new');
});

test('first submit creates once then sends its text once without a parallel greeting stream', async (t) => {
  const ui = await mount(t);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  assert.equal(ui.posts().length, 1);
  const url = new URL(ui.streams[0].url, ui.window.location.href);
  assert.equal(url.pathname, '/chat-api/session/debt-new/stream');
  assert.equal(url.searchParams.get('message'), '400000');
});

test('rapid start clicks and submit share one pending creation', async (t) => {
  const pending = deferred();
  const ui = await mount(t, { fetchImpl: () => pending.promise });
  start(ui);
  start(ui);
  submit(ui, '400000');
  assert.equal(ui.posts().length, 1);
  pending.resolve(response({ session_id: 'debt-new', state: publicState() }));
  await waitUntil(() => ui.feed().includes(QUESTION));
  assert.equal(ui.posts().length, 1);
  assert.ok(ui.streams.length <= 1);
});

test('a late superseded POST response cannot overwrite the current session or storage', async (t) => {
  const old = deferred();
  let postNumber = 0;
  const ui = await mount(t, { fetchImpl: (call) => {
    if (call.method !== 'POST') throw new Error('Unexpected read in creation race');
    postNumber += 1;
    return postNumber === 1 ? old.promise : response({ session_id: 'debt-current', state: publicState() });
  } });
  start(ui);
  // Dispatch the controller's public restart action while a previous async request is pending.
  // The synthetic control exercises cancellation even when its ordinary error UI is hidden.
  const restart = ui.window.document.createElement('button');
  restart.dataset.action = 'new-debt';
  ui.root.appendChild(restart);
  restart.click();
  restart.remove();
  await waitUntil(() => ui.window.localStorage.getItem(DEBT_KEY) === 'debt-current');
  let oldDecoded = false;
  old.resolve({ ok: true, status: 200, json: async () => {
    oldDecoded = true;
    return { session_id: 'debt-obsolete', state: publicState() };
  } });
  await waitUntil(() => oldDecoded);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), 'debt-current');
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  assert.equal(new URL(ui.streams[0].url, ui.window.location.href).pathname, '/chat-api/session/debt-current/stream');
  assert.equal(ui.posts().length, 2);
});

test('a failed first POST preserves the typed draft and does not silently resend', async (t) => {
  const pending = deferred();
  const ui = await mount(t, { fetchImpl: () => pending.promise });
  submit(ui, 'Мой долг 400000 рублей');
  assert.equal(ui.root.querySelector('.mbkc-input').value, 'Мой долг 400000 рублей');
  pending.reject(new Error('PRIVATE_CREATE_ERROR'));
  await waitUntil(() => ui.root.querySelector('.mbkc-status [data-action="start-debt"]'));
  assert.equal(ui.root.querySelector('.mbkc-input').value, 'Мой долг 400000 рублей');
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.streams.length, 0);
  assert.ok(!ui.feed().includes('PRIVATE_CREATE_ERROR'));
});

test('a normal widget id and query parameters do not start or seed the debt chat', async (t) => {
  const ui = await mount(t, { normalSid: 'normal-private',
    url: 'http://127.0.0.1:8777/debt/?phone=70000000001&sid=normal-private&goal=credit' });
  assert.equal(ui.calls.length, 0);
  await created(ui);
  assert.equal(ui.window.localStorage.getItem(NORMAL_KEY), 'normal-private');
  const body = JSON.parse(ui.posts()[0].options.body);
  assert.equal(body.phone, undefined);
  assert.equal(body.sid, undefined);
  assert.equal(body.goal, undefined);
});

test('reload restores only the authoritative debt history and sends no new request', async (t) => {
  const ui = await mount(t, { debtSid: 'debt-owned', normalSid: 'normal-owned' });
  await waitUntil(() => ui.feed().includes(QUESTION));
  assert.equal(ui.gets().length, 1);
  assert.equal(ui.gets()[0].url, '/chat-api/session/debt-owned');
  assert.equal(ui.gets()[0].options.credentials, 'same-origin');
  assert.equal(ui.posts().length, 0);
  assert.equal(ui.streams.length, 0);
  assert.equal(ui.window.localStorage.getItem(NORMAL_KEY), 'normal-owned');
});

test('restored server-known contact is not followed by a new questionnaire or local phone prompt', async (t) => {
  const text = 'Сведения собраны. Отправка заявки не подтверждена.';
  const ui = await mount(t, { debtSid: 'debt-known-contact', restoreState: publicState({
    fields: [], dialog_phase: 'collected_held', facts: { client: { phone: '[скрыто]' } },
    history: [{ role: 'assistant', content: text }],
  }) });
  await waitUntil(() => ui.feed().includes(text));
  assert.equal(ui.posts().length, 0);
  assert.equal(ui.streams.length, 0);
  assert.ok(!ui.feed().includes('На какой номер'));
});

for (const [label, options] of [
  ['normal mode', { restoreState: publicState({ questionnaire_policy: null }) }],
  ['consumer policy mode', { restoreState: publicState({ questionnaire_policy: { version: 1, mode: 'standard' } }) }],
  ['foreign response id', { restoreSid: 'another-debt' }],
  ['unknown policy version', { restoreState: publicState({ questionnaire_policy: { version: 99, mode: 'debt' } }) }],
  ['forbidden session', { restoreStatus: 403 }],
  ['untyped worker progress', { busy: 'false' }],
]) {
  test(`restore rejects ${label} without automatic session creation`, async (t) => {
    const ui = await mount(t, { debtSid: 'debt-owned', ...options });
    await waitUntil(() => ui.root.querySelector('.mbkc-system'));
    assert.equal(ui.posts().length, 0);
    assert.equal(ui.streams.length, 0);
    assert.ok(!ui.feed().includes(QUESTION));
  });
}

test('a busy restored worker is polled within a finite budget and never replays or creates', async (t) => {
  const ui = await mount(t, { debtSid: 'debt-busy', busy: true });
  await waitUntil(() => ui.root.querySelector('[data-action="recover-debt"]'));
  assert.equal(ui.gets().length, 3);
  assert.equal(ui.posts().length, 0);
  assert.equal(ui.streams.length, 0);
});

test('network interruption reconciles by GET and never repeats the message', async (t) => {
  const history = [
    { role: 'assistant', content: QUESTION }, { role: 'user', content: '400000' },
    { role: 'assistant', content: REGION },
  ];
  const ui = await mount(t, { restoreSid: 'debt-new', restoreState: publicState({ history, fields: ['client.region_class'] }) });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('error', { message: 'RAW_PRIVATE_ERROR' });
  await waitUntil(() => ui.gets().length === 1 && ui.feed().includes(REGION));
  assert.equal(ui.streams[0].closed, true);
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.posts().length, 1);
  assert.ok(!ui.feed().includes('RAW_PRIVATE_ERROR'));
  assert.equal(ui.root.querySelector('.mbkc-input').value, '');
});

test('an uncommitted interrupted turn restores its text in RAM without automatic replay', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  assert.equal(ui.root.querySelector('.mbkc-input').value, '');
  ui.streams[0].emit('error', {});
  await waitUntil(() => ui.gets().length === 1 && ui.root.querySelector('.mbkc-input').value === '400000');
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), 'debt-new');
  assert.ok(!Object.values(ui.window.localStorage).includes('400000'));
});

test('recovery preserves a draft the user edited while the previous turn was pending', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  const input = ui.root.querySelector('.mbkc-input');
  input.value = 'Уточню сумму позже';
  input.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.streams[0].emit('error', {});
  await waitUntil(() => ui.gets().length === 1 && ui.streams[0].closed);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(input.value, 'Уточню сумму позже');
  assert.equal(ui.streams.length, 1);
});

test('a later canonical accepted turn clears only its own restored pending draft', async (t) => {
  let reads = 0;
  const committed = publicState({ history: [
    { role: 'assistant', content: QUESTION }, { role: 'user', content: '400000' },
    { role: 'assistant', content: REGION },
  ] });
  const ui = await mount(t, { fetchImpl: (call) => {
    if (call.method === 'POST') return response({ session_id: 'debt-new', state: publicState() });
    reads += 1;
    return response({ session_id: 'debt-new', state: reads === 1 ? publicState() : committed, turn_in_progress: false });
  } });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('error', {});
  await waitUntil(() => ui.root.querySelector('.mbkc-input').value === '400000');
  const recover = ui.window.document.createElement('button');
  recover.dataset.action = 'recover-debt';
  ui.root.appendChild(recover);
  recover.click();
  recover.remove();
  await waitUntil(() => reads === 2 && ui.feed().includes(REGION));
  assert.equal(ui.root.querySelector('.mbkc-input').value, '');
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.posts().length, 1);
});

test('another tab user text cannot acknowledge this tab interrupted message', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new', restoreState: publicState({ history: [
    { role: 'assistant', content: QUESTION }, { role: 'user', content: 'Сообщение из другой вкладки' },
    { role: 'assistant', content: 'Состояние обновилось' },
  ] }) });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('error', {});
  await waitUntil(() => ui.gets().length === 1 && ui.root.querySelector('.mbkc-input').value === '400000');
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.posts().length, 1);
});

test('the current completed SSE can acknowledge its masked contact text', async (t) => {
  const ui = await mount(t);
  await created(ui);
  submit(ui, '+70000000471');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('state', { session_id: 'debt-new', state: publicState({ fields: [], history: [
    { role: 'assistant', content: QUESTION }, { role: 'user', content: '[скрыто]' },
    { role: 'assistant', content: 'Сведения собраны. Отправка заявки не подтверждена.' },
  ] }) });
  ui.streams[0].emit('done', {});
  await waitUntil(() => ui.streams[0].closed);
  assert.equal(ui.root.querySelector('.mbkc-input').value, '');
  assert.equal(ui.gets().length, 0);
  assert.equal(ui.streams.length, 1);
});

test('submitting twice while a stream is pending never duplicates the message', async (t) => {
  const ui = await mount(t);
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  submit(ui, '400000');
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.posts().length, 1);
});

test('done without a committed state uses GET reconciliation instead of replay', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('done', {});
  await waitUntil(() => ui.gets().length === 1);
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.streams.length, 1);
  assert.equal(ui.streams[0].closed, true);
});

test('authoritative SSE history replaces provisional bubbles exactly once', async (t) => {
  const ui = await mount(t);
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  const stream = ui.streams[0];
  stream.emit('bubbles', { bubbles: [REGION] });
  stream.emit('state', { session_id: 'debt-new', state: publicState({
    fields: ['client.region_class'], history: [
      { role: 'assistant', content: QUESTION }, { role: 'user', content: '400000' },
      { role: 'assistant', content: REGION },
    ],
  }) });
  stream.emit('done', {});
  await waitUntil(() => stream.closed);
  const messages = [...ui.root.querySelectorAll('.mbkc-bubble > span')].map((node) => node.textContent);
  assert.equal(messages.filter((message) => message === REGION).length, 1);
  assert.equal(messages.filter((message) => message === '400000').length, 1);
  assert.equal(ui.gets().length, 0);
});

test('closed old SSE callbacks cannot replace the recovered current history', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  const old = ui.streams[0];
  old.emit('error', {});
  await waitUntil(() => old.closed && ui.gets().length === 1);
  old.emit('bubbles', { bubbles: ['STALE_PRIVATE_HISTORY'] });
  old.emit('state', { session_id: 'other-session', state: publicState({
    history: [{ role: 'assistant', content: 'STALE_PRIVATE_HISTORY' }],
  }) });
  assert.ok(!ui.feed().includes('STALE_PRIVATE_HISTORY'));
  assert.equal(ui.posts().length, 1);
});

test('wrong-session live state is rejected before displaying its history', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('state', { session_id: 'other-session', state: publicState({
    history: [{ role: 'assistant', content: 'FOREIGN_PRIVATE_HISTORY' }],
  }) });
  await waitUntil(() => ui.streams[0].closed);
  assert.ok(!ui.feed().includes('FOREIGN_PRIVATE_HISTORY'));
  assert.equal(ui.posts().length, 1);
});

test('live state without a session id is reconciled before applying its history', async (t) => {
  const ui = await mount(t, { restoreSid: 'debt-new' });
  await created(ui);
  submit(ui, '400000');
  await waitUntil(() => ui.streams.length === 1);
  ui.streams[0].emit('state', { state: publicState({
    history: [{ role: 'assistant', content: 'UNBOUND_PRIVATE_HISTORY' }],
  }) });
  assert.ok(!ui.feed().includes('UNBOUND_PRIVATE_HISTORY'));
  await waitUntil(() => ui.gets().length === 1);
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.streams[0].closed, true);
});

test('unsafe history, text and quick-reply labels remain text nodes', async (t) => {
  const attack = '<img src=x onerror="window.INJECTED=true">';
  const ui = await mount(t, { debtSid: 'debt-xss', restoreState: publicState({
    history: [{ role: 'assistant', content: attack }, { role: 'user', content: '<svg onload="window.INJECTED=true">' }],
    quick_replies: [{ label: attack, value: attack }],
  }) });
  await waitUntil(() => ui.feed().includes(attack));
  assert.equal(ui.window.INJECTED, undefined);
  assert.equal(ui.root.querySelectorAll('.mbkc-bubble > span img,.mbkc-bubble > span svg[onload],.mbkc-chips img').length, 0);
  assert.ok(ui.root.querySelector('.mbkc-chip').textContent.includes('<img'));
});

test('feature-off creation error stays manual and preserves the ordinary widget key', async (t) => {
  const ui = await mount(t, { normalSid: 'normal-owned', fetchImpl: () => response({ detail: 'scenario_unavailable' }, 503) });
  start(ui);
  await waitUntil(() => ui.root.querySelector('.mbkc-system'));
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.streams.length, 0);
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), null);
  assert.equal(ui.window.localStorage.getItem(NORMAL_KEY), 'normal-owned');
});

test('malformed initial response never invents or stores a session id', async (t) => {
  const ui = await mount(t, { fetchImpl: () => response({ state: publicState() }) });
  start(ui);
  await waitUntil(() => ui.root.querySelector('.mbkc-system'));
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), null);
  assert.equal(ui.streams.length, 0);
});
