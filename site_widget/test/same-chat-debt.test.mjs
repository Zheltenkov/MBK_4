// Same widget, session, history and owner surface through a server-selected debt policy.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const SID = 'normal-owned';
const KEY = 'mbkAiChat.sid';
const DEBT_KEY = 'mbkDebtChat.sid';
const GREETING = 'Здравствуйте. Чем помочь?';
const DEBT_QUESTION = 'Какая у вас общая сумма долга?';
const REQUEST = 'Хочу списать свои долги';
const OLD_HISTORY = [
  { role: 'assistant', content: GREETING },
  { role: 'user', content: 'Тестовый клиент, контакт уже известен' },
  { role: 'assistant', content: 'Какой у вас вопрос?' },
];

function state({ debt = false, mode = 'debt', history = OLD_HISTORY, ...extra } = {}) {
  return { chat_history: history, questionnaire_policy: debt ? { version: 1, mode, reason: 'explicit_request' } : null,
    quick_replies: [], closed: false, facts: { client: { phone: '[телефон известен]' } }, ...extra };
}

function switched(mode = 'debt') {
  return state({ debt: true, mode, history: [...OLD_HISTORY,
    { role: 'user', content: REQUEST }, { role: 'assistant', content: DEBT_QUESTION }] });
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

async function waitUntil(check) {
  const deadline = Date.now() + 1500;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('Same-chat UI condition timed out');
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

async function mount(t, { restore = state(), status = 200, fetchImpl, revealMs = 0 } = {}) {
  const markup = readFileSync(join(HERE, 'ai-chat-widget.blade.php'), 'utf8').replace(/\{\{--[\s\S]*?--\}\}/g, '');
  const source = readFileSync(join(HERE, '..', '_ai-chat.js'), 'utf8');
  const dom = new JSDOM(markup, { url: 'https://www.mbk.ru/', runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [], streams = [], revealCallbacks = [];
  window.localStorage.setItem(KEY, SID);
  window.localStorage.setItem(DEBT_KEY, 'auxiliary-unrelated');
  window.document.cookie = 'mbk_session_grant=synthetic-normal-owner; path=/';
  window.MBK_AI_CHAT_REVEAL_MS = revealMs;
  const timer = window.setTimeout.bind(window);
  window.setTimeout = (handler, delay, ...args) => {
    if (revealMs && delay === revealMs) revealCallbacks.push(() => handler(...args));
    return timer(handler, delay === 500 ? 5 : delay, ...args);
  };
  window.fetch = async (url, options = {}) => {
    const call = { url: String(url), method: options.method || 'GET', options };
    calls.push(call);
    if (fetchImpl) return fetchImpl(call, calls.length);
    if (call.method === 'POST') return response({ session_id: 'new-normal', state: state({ history: [] }) });
    return response({ session_id: SID, state: restore, turn_in_progress: false }, status);
  };
  class FakeEventSource {
    constructor(url) { this.url = String(url); this.listeners = {}; this.closed = false; streams.push(this); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    close() { this.closed = true; }
    emit(name, payload) {
      for (const callback of this.listeners[name] || []) callback({ data: JSON.stringify(payload) });
    }
  }
  window.EventSource = FakeEventSource;
  t.after(() => { streams.forEach((stream) => stream.close()); window.close(); });
  window.eval(source);
  const root = window.document.querySelector('#mbk-ai-chat');
  root.querySelector('.mbkc-launcher').click();
  await waitUntil(() => calls.length && (!root.querySelector('.mbkc-input').disabled ||
    root.querySelector('[data-action="recover-debt"], [data-action="new-debt"]')));
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { window, root, calls, streams, revealCallbacks,
    feed: () => root.querySelector('.mbkc-feed-inner').textContent,
    posts: () => calls.filter((call) => call.method === 'POST'),
  };
}

function submit(ui, text) {
  ui.root.querySelector('.mbkc-input').value = text;
  ui.root.querySelector('.mbkc-input').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.root.querySelector('form').dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
}

function promote(ui, mode = 'debt') {
  submit(ui, REQUEST);
  const stream = ui.streams.at(-1);
  stream.emit('bubbles', { bubbles: [DEBT_QUESTION] });
  stream.emit('state', { session_id: SID, state: switched(mode) });
  stream.emit('done', {});
  return stream;
}

function sameIdentity(ui) {
  assert.equal(ui.window.localStorage.getItem(KEY), SID);
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), 'auxiliary-unrelated');
  assert.equal(ui.window.document.cookie, 'mbk_session_grant=synthetic-normal-owner');
  assert.equal(ui.posts().length, 0);
}

test('sidebar tabs use the hidden attribute while preserving their visible pane', async (t) => {
  const ui = await mount(t);
  const files = ui.root.querySelector('[data-pane="files"]');
  const chats = ui.root.querySelector('[data-pane="chats"]');
  assert.equal(files.hidden, true);
  assert.equal(files.hasAttribute('style'), false);
  ui.root.querySelector('[data-tab="files"]').click();
  assert.equal(files.hidden, false);
  assert.equal(chats.hidden, true);
  ui.root.querySelector('[data-tab="chats"]').click();
  assert.equal(files.hidden, true);
  assert.equal(chats.hidden, false);
});

test('normal widget restores an already-debt session through its original key and owner surface', async (t) => {
  const ui = await mount(t, { restore: switched() });
  sameIdentity(ui);
  assert.equal(ui.streams.length, 0);
  assert.ok(ui.feed().includes(OLD_HISTORY[1].content));
  assert.equal(ui.feed().split(DEBT_QUESTION).length - 1, 1);
  assert.ok(!ui.feed().includes('номер можно'));
});

for (const mode of ['debt', 'clarify', 'cancelled']) {
  test(`live server policy ${mode} keeps the same identity and canonical history`, async (t) => {
    const ui = await mount(t);
    promote(ui, mode);
    sameIdentity(ui);
    assert.equal(ui.streams.length, 1);
    assert.ok(ui.streams[0].url.startsWith(`/chat-api/session/${SID}/stream?message=`));
    assert.equal(ui.feed().split(GREETING).length - 1, 1);
    assert.equal(ui.feed().split(DEBT_QUESTION).length - 1, 1);
    assert.equal(ui.feed().split(REQUEST).length - 1, 1);
  });
}

test('promotion ignores late old chunks or consent fields', async (t) => {
  const ui = await mount(t);
  const old = promote(ui);
  old.emit('chunk', { text: 'STALE_OLD_CHUNK' });
  old.emit('bubbles', { bubbles: ['STALE_OLD_BUBBLE'] });
  old.emit('state', { session_id: SID, state: state({ crm_workflow: { need_site_consent: true } }) });
  submit(ui, '400000');
  assert.equal(ui.streams.length, 2);
  old.emit('done', {});
  old.emit('error', {});
  assert.equal(ui.streams[1].closed, false);
  assert.ok(!ui.feed().includes('STALE_OLD'));
  sameIdentity(ui);
});

test('a queued normal reveal callback cannot add a stale bubble after server policy promotion', async (t) => {
  const ui = await mount(t, { revealMs: 17 });
  submit(ui, REQUEST);
  const stream = ui.streams[0];
  stream.emit('bubbles', { bubbles: [DEBT_QUESTION, 'STALE_DELAYED_BUBBLE'] });
  assert.equal(ui.revealCallbacks.length, 1);
  stream.emit('state', { session_id: SID, state: switched() });
  ui.revealCallbacks[0]();
  stream.emit('done', {});
  assert.ok(!ui.feed().includes('STALE_DELAYED_BUBBLE'));
  assert.equal(ui.feed().split(DEBT_QUESTION).length - 1, 1);
  sameIdentity(ui);
});

test('promotion happens before ordinary consent-cookie handling', async (t) => {
  const ui = await mount(t);
  submit(ui, REQUEST);
  ui.streams[0].emit('state', { session_id: SID,
    state: { ...switched(), crm_workflow: { need_site_consent: true } } });
  ui.streams[0].emit('done', {});
  sameIdentity(ui);
});

test('an interrupted first debt turn discovers persisted policy by GET without replay or allocation', async (t) => {
  let reads = 0;
  const ui = await mount(t, { fetchImpl: (call) => {
    assert.equal(call.method, 'GET');
    return response({ session_id: SID, state: ++reads === 1 ? state() : switched(), turn_in_progress: false });
  } });
  submit(ui, REQUEST);
  const old = ui.streams[0];
  old.emit('error', {});
  await waitUntil(() => ui.calls.length === 2 || ui.streams.length > 1);
  assert.equal(ui.streams.length, 1, 'An interrupted message must not be replayed while its policy is unknown');
  await waitUntil(() => ui.feed().includes(DEBT_QUESTION));
  sameIdentity(ui);
  assert.equal(ui.streams.length, 1);
  assert.equal(old.closed, true);
  submit(ui, '400000');
  assert.equal(ui.streams.length, 2);
  assert.ok(ui.streams[1].url.endsWith('message=400000'));
});

test('a latched debt session rejects a later ordinary state instead of resuming credit questions', async (t) => {
  const ui = await mount(t);
  promote(ui);
  submit(ui, '400000');
  ui.streams[1].emit('state', { session_id: SID, state: state({ history: [...OLD_HISTORY,
    { role: 'assistant', content: 'UNEXPECTED_CREDIT_QUESTION' }] }) });
  assert.ok(!ui.feed().includes('UNEXPECTED_CREDIT_QUESTION'));
  await waitUntil(() => ui.root.querySelector('[data-action="recover-debt"]'));
  assert.ok(!ui.feed().includes('UNEXPECTED_CREDIT_QUESTION'));
  assert.equal(ui.root.querySelector('.mbkc-input').disabled, true);
  sameIdentity(ui);
});

test('a busy transition recovery is bounded and retains the original session', async (t) => {
  let reads = 0;
  const ui = await mount(t, { fetchImpl: () => response({ session_id: SID,
    state: ++reads === 1 ? state() : switched(), turn_in_progress: reads > 1 }) });
  submit(ui, REQUEST);
  ui.streams[0].emit('error', {});
  await waitUntil(() => ui.root.querySelector('[data-action="recover-debt"]'));
  assert.equal(reads, 4);
  assert.equal(ui.streams.length, 1);
  sameIdentity(ui);
});

for (const status of [403, 404, 500]) {
  test(`known normal session restore ${status} remains manual and preserves its key`, async (t) => {
    const ui = await mount(t, { status });
    sameIdentity(ui);
    assert.equal(ui.streams.length, 0);
    assert.ok(ui.root.querySelector('[data-action="recover-debt"]'));
    assert.ok(ui.root.querySelector('[data-action="new-debt"]'));
  });
}

test('a closed restored session creates a fresh ordinary dialog only after an explicit action', async (t) => {
  const ui = await mount(t, { restore: state({ debt: true, closed: true }) });
  sameIdentity(ui);
  ui.root.querySelector('[data-action="new-debt"]').click();
  await waitUntil(() => ui.posts().length === 1 && ui.streams.length === 1);
  assert.equal(JSON.parse(ui.posts()[0].options.body).requested_scenario, undefined);
  assert.equal(ui.window.localStorage.getItem(KEY), 'new-normal');
  assert.equal(ui.window.localStorage.getItem(DEBT_KEY), 'auxiliary-unrelated');
});

for (const kind of ['missing-id', 'invalid-history', 'unknown-policy-version']) {
  test(`${kind} transition envelope is reconciled before applying data or consent`, async (t) => {
    const ui = await mount(t);
    submit(ui, REQUEST);
    const incoming = state({ debt: true, history: [{ role: 'assistant', content: 'PRIVATE_REJECTED_STATE' }],
      crm_workflow: { need_site_consent: true } });
    if (kind === 'invalid-history') incoming.chat_history = 'malformed';
    if (kind === 'unknown-policy-version') incoming.questionnaire_policy.version = 999;
    const envelope = { state: incoming };
    if (kind !== 'missing-id') envelope.session_id = SID;
    ui.streams[0].emit('state', envelope);
    await waitUntil(() => ui.calls.length === 2);
    assert.ok(!ui.feed().includes('PRIVATE_REJECTED_STATE'));
    sameIdentity(ui);
  });
}

test('a wrong-session debt promotion is rejected before history or consent is applied', async (t) => {
  const ui = await mount(t, { restore: state() });
  submit(ui, REQUEST);
  ui.streams[0].emit('state', { session_id: 'other-session', state: state({ debt: true,
    history: [{ role: 'assistant', content: 'PRIVATE_OTHER_SESSION' }], crm_workflow: { need_site_consent: true } }) });
  await waitUntil(() => ui.calls.length === 2);
  assert.ok(!ui.feed().includes('PRIVATE_OTHER_SESSION'));
  sameIdentity(ui);
});

test('debt words alone cannot promote the client when server policy stays ordinary', async (t) => {
  const ui = await mount(t);
  submit(ui, REQUEST);
  ui.streams[0].emit('state', { session_id: SID, state: state({ history: [...OLD_HISTORY,
    { role: 'user', content: REQUEST }, { role: 'assistant', content: 'Что вас интересует?' }] }) });
  ui.streams[0].emit('done', {});
  submit(ui, 'Продолжим');
  ui.streams[1].emit('chunk', { text: 'ORDINARY_STREAM_STILL_ACTIVE' });
  assert.ok(ui.feed().includes('ORDINARY_STREAM_STILL_ACTIVE'));
  sameIdentity(ui);
});
