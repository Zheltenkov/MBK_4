// The existing widget presents an authenticated backend offer without a phone or browser authority.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {JSDOM} from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const SID = 'same-owned-chat';
const KEY = 'mbkAiChat.sid';
const CAP = 'RAM_ONLY_SYNTHETIC_CAPABILITY';
const history = [{role: 'user', content: 'Ранее заданный вопрос'}, {role: 'assistant', content: 'Сохраняем этот диалог'}];
const offer = {offer_id: 'offer-local', version: '2', consent_version: 'local-v3', partner_name: 'Тестовая компания'};
const metadata = (status = 'awaiting_consent', extra = {}) => ({status, mode: 'local', contact_known: true,
  offer, presentation_id: 'presentation-1', ...extra});
const state = (onloader = metadata(), extra = {}) => ({chat_history: history, closed: false,
  quick_replies: [], questionnaire_policy: null, onloader, ...extra});
const envelope = (current = state(), sid = SID) => ({session_id: sid, state: current, turn_in_progress: false});
const response = (body, status = 200) => ({ok: status < 300, status, json: async () => body});

async function waitUntil(check) {
  const deadline = Date.now() + 1500;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('Onloader UI condition timed out');
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

async function mount(t, {stored = true, host = false, fetchImpl, current = state()} = {}) {
  const markup = readFileSync(join(HERE, 'ai-chat-widget.blade.php'), 'utf8').replace(/\{\{--[\s\S]*?--\}\}/g, '');
  const dom = new JSDOM(markup, {url: 'https://www.mbk.ru/', runScripts: 'outside-only'});
  const {window} = dom, calls = [], streams = [], minted = [];
  const root = window.document.querySelector('#mbk-ai-chat');
  if (stored) window.localStorage.setItem(KEY, SID);
  window.localStorage.setItem('mbkDebtChat.sid', 'other-surface');
  if (host) root.mbkResolveOnloader = async (sid) => { minted.push(sid); return CAP; };
  window.MBK_AI_CHAT_REVEAL_MS = 0;
  window.fetch = async (url, options = {}) => {
    const call = {url: String(url), method: options.method || 'GET', options}; calls.push(call);
    return fetchImpl ? fetchImpl(call) : response(envelope(current));
  };
  class Source {
    constructor(url) {this.url = url; this.listeners = {}; streams.push(this);}
    addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
    close() {this.closed = true;}
    emit(name, payload) {for (const fn of this.listeners[name] || []) fn({data: JSON.stringify(payload)});}
  }
  window.EventSource = Source;
  t.after(() => window.close());
  window.eval(readFileSync(join(HERE, '..', '_ai-chat.js'), 'utf8'));
  root.querySelector('.mbkc-launcher').click();
  await waitUntil(() => root.querySelector('.mbkc-onloader') || root.querySelector('.mbkc-status button'));
  await new Promise((resolve) => setTimeout(resolve, 0));
  return {root, window, calls, streams, minted, posts: () => calls.filter((call) => call.method === 'POST'),
    feed: () => root.querySelector('.mbkc-feed-inner').textContent};
}

function submit(ui, text) {
  ui.root.querySelector('.mbkc-input').value = text;
  ui.root.querySelector('form').dispatchEvent(new ui.window.Event('submit', {bubbles: true, cancelable: true}));
}

test('reload restores the same offer, SID and prior history without minting or allocating', async (t) => {
  const ui = await mount(t, {host: true});
  assert.equal(ui.calls.length, 1); assert.equal(ui.posts().length, 0); assert.deepEqual(ui.minted, []);
  assert.equal(ui.window.localStorage.getItem(KEY), SID);
  assert.equal(ui.window.localStorage.getItem('mbkDebtChat.sid'), 'other-surface');
  assert.match(ui.feed(), /Ранее заданный вопрос/); assert.match(ui.feed(), /Повторно вводить телефон не нужно/);
  assert.match(ui.feed(), /Локальная проверка/);
});

for (const decision of ['accept', 'decline']) {
  test(`explicit ${decision} sends the exact presentation once in the existing session`, async (t) => {
    let release;
    const ui = await mount(t, {fetchImpl: (call) => call.method === 'GET' ? response(envelope()) :
      new Promise((resolve) => {release = resolve;})});
    const button = ui.root.querySelector(`[data-action="onloader-${decision}"]`);
    button.click();
    await waitUntil(() => ui.posts().length === 1);
    // The first command is already in flight. A second click cannot invalidate it and send another.
    button.click();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(ui.posts().length, 1);
    assert.equal(ui.posts()[0].url, `/chat-api/session/${SID}/onloader/decision`);
    assert.deepEqual(JSON.parse(ui.posts()[0].options.body), {presentation_id: 'presentation-1', offer_id: offer.offer_id,
      offer_version: offer.version, consent_version: offer.consent_version, decision});
    release(response(envelope(state(metadata(decision === 'accept' ? 'queued' : 'declined')))));
    await waitUntil(() => !ui.root.querySelector('[data-action="onloader-accept"]'));
    assert.equal(ui.posts().length, 1);
  });
}

test('typed yes is only a chat turn and never consent or a legacy consent cookie', async (t) => {
  const ui = await mount(t);
  submit(ui, 'Да'); assert.equal(ui.streams.length, 1); assert.equal(ui.posts().length, 0);
  ui.streams[0].emit('state', {session_id: SID, state: state(metadata(), {crm_workflow: {site_consent: true}})});
  ui.streams[0].emit('done', {});
  assert.doesNotMatch(ui.window.document.cookie, /mbk_chat_consent/);
});

test('lost decision response performs one canonical GET and no decision replay', async (t) => {
  let reads = 0;
  const ui = await mount(t, {fetchImpl: (call) => {
    if (call.method === 'POST') throw new Error('lost response');
    return response(envelope(state(metadata(++reads === 1 ? 'awaiting_consent' : 'outcome_unknown'))));
  }});
  ui.root.querySelector('[data-action="onloader-accept"]').click();
  await waitUntil(() => ui.root.querySelector('[data-action="onloader-reconcile"]'));
  assert.equal(ui.posts().length, 1); assert.equal(reads, 2);
  assert.equal(ui.root.querySelector('[data-action="onloader-accept"]'), null);
});

test('lost acknowledgement followed by canonical queued clears recovery controls without repeating a command', async (t) => {
  let reads = 0;
  const ui = await mount(t, {host: true, fetchImpl: (call) => {
    if (call.method === 'POST') throw new Error('committed response lost');
    return response(envelope(state(metadata(++reads === 1 ? 'awaiting_consent' : 'queued'))));
  }});
  ui.root.querySelector('[data-action="onloader-accept"]').click();
  await waitUntil(() => reads === 2 && ui.feed().includes('Тестовая передача поставлена в очередь'));
  assert.equal(ui.root.querySelector('[data-action="onloader-accept"]'), null);
  assert.equal(ui.root.querySelector('[data-action="onloader-reconcile"]'), null);
  assert.equal(ui.root.querySelector('[data-action="onloader-refresh"]'), null);
  assert.equal(ui.posts().length, 1);
});

test('manual reconcile observes the same operation without resending the decision', async (t) => {
  const ui = await mount(t, {current: state(metadata('outcome_unknown')), fetchImpl: (call) =>
    response(envelope(state(metadata(call.method === 'POST' ? 'queued' : 'outcome_unknown'))))});
  ui.root.querySelector('[data-action="onloader-reconcile"]').click();
  await waitUntil(() => ui.posts().length === 1);
  assert.equal(ui.posts()[0].url, `/chat-api/session/${SID}/onloader/reconcile`);
  assert.deepEqual(JSON.parse(ui.posts()[0].options.body), {});
});

test('new host-backed chat holds before minting and uses no opening stream or persistent capability', async (t) => {
  const ui = await mount(t, {stored: false, host: true, fetchImpl: (call) => {
    if (call.url.endsWith('/onloader')) return response(envelope());
    return response(envelope(state(metadata('pending', {contact_known: false, offer: undefined, presentation_id: undefined}))));
  }});
  await waitUntil(() => ui.root.querySelector('[data-action="onloader-accept"]'));
  assert.equal(ui.posts().length, 2); assert.deepEqual(ui.minted, [SID]);
  assert.equal(JSON.parse(ui.posts()[0].options.body).onloader_expected, true);
  assert.deepEqual(JSON.parse(ui.posts()[1].options.body), {capability: CAP});
  assert.equal(ui.streams.length, 0); assert.equal(ui.window.localStorage.getItem(KEY), SID);
  assert.doesNotMatch(ui.window.document.documentElement.outerHTML + JSON.stringify(ui.window.localStorage), new RegExp(CAP));
});

test('existing normal history attaches through the host without allocating a different session', async (t) => {
  const ui = await mount(t, {host: true, fetchImpl: (call) => response(envelope(call.method === 'GET' ? state(null) : state()))});
  await waitUntil(() => ui.root.querySelector('[data-action="onloader-accept"]'));
  assert.deepEqual(ui.minted, [SID]); assert.equal(ui.posts().length, 1);
  assert.match(ui.feed(), /Ранее заданный вопрос/); assert.equal(ui.window.localStorage.getItem(KEY), SID);
});

test('public offer markup stays text and supplies no executable elements', async (t) => {
  const ui = await mount(t, {current: state(metadata('awaiting_consent', {offer: {...offer, partner_name: '<img src=x onerror=alert(1)>'}}))});
  assert.match(ui.feed(), /<img src=x/); assert.equal(ui.root.querySelector('.mbkc-onloader img'), null);
});

test('closed chat budget disables typing while explicit offer actions remain available', async (t) => {
  const ui = await mount(t, {current: state(metadata(), {closed: true})});
  assert.equal(ui.root.querySelector('.mbkc-input').disabled, true);
  assert.equal(ui.root.querySelector('[data-action="onloader-accept"]').disabled, false);
});

test('a decision response for another SID cannot replace history, offer or the stored identity', async (t) => {
  let reads = 0;
  const ui = await mount(t, {fetchImpl: (call) => {
    if (call.method === 'GET') { reads++; return response(envelope()); }
    return response(envelope(state(metadata('queued'), {chat_history: [{role: 'assistant', content: 'FOREIGN_SESSION_SENTINEL'}]}), 'foreign-session'));
  }});
  ui.root.querySelector('[data-action="onloader-accept"]').click();
  await waitUntil(() => reads === 2 && ui.root.querySelector('[data-action="onloader-reconcile"]'));
  assert.doesNotMatch(ui.feed(), /FOREIGN_SESSION_SENTINEL/);
  assert.equal(ui.window.localStorage.getItem(KEY), SID);
  assert.equal(ui.posts().length, 1);
});

test('editing a consent button dataset cannot change the canonical offered recipient or decision tuple', async (t) => {
  const ui = await mount(t, {fetchImpl: (call) => response(envelope(state(metadata(call.method === 'POST' ? 'queued' : 'awaiting_consent'))))});
  const button = ui.root.querySelector('[data-action="onloader-accept"]');
  button.dataset.offerId = 'browser-picked-recipient'; button.dataset.consentVersion = 'browser-picked-version';
  button.click(); await waitUntil(() => ui.posts().length === 1);
  const posted = JSON.parse(ui.posts()[0].options.body);
  assert.equal(posted.offer_id, offer.offer_id); assert.equal(posted.consent_version, offer.consent_version);
  assert.equal(posted.presentation_id, 'presentation-1');
});

test('pending source failures keep the same held chat with a manual refresh and no opening stream', async (t) => {
  const pending = state(metadata('pending', {contact_known: false, offer: undefined, presentation_id: undefined}));
  const ui = await mount(t, {stored: false, host: true, fetchImpl: (call) => {
    if (call.url.endsWith('/onloader')) throw new Error('source unavailable');
    return response(envelope(pending));
  }});
  await waitUntil(() => ui.calls.filter((call) => call.method === 'GET').length === 1 &&
    ui.root.querySelector('[data-action="onloader-refresh"]') && !ui.root.querySelector('[data-action="onloader-refresh"]').disabled);
  assert.equal(ui.streams.length, 0); assert.equal(ui.posts().length, 2);
  assert.equal(ui.root.querySelector('.mbkc-input').disabled, true);
  assert.equal(ui.window.localStorage.getItem(KEY), SID);
});

test('decline with an unknown backend outcome remains a refusal with a manual read action', async (t) => {
  const ui = await mount(t, {current: state(metadata('outcome_unknown', {decision: 'decline'}))});
  assert.match(ui.feed(), /Отказ сохранён/);
  assert.match(ui.feed(), /Подтверждение обработки пока не получено/);
  assert.equal(ui.root.querySelector('[data-action="onloader-accept"]'), null);
  assert.ok(ui.root.querySelector('[data-action="onloader-reconcile"]'));
  assert.equal(ui.posts().length, 0);
});

for (const current of [state(metadata('awaiting_consent', {contact_known: false})), state(metadata('awaiting_consent', {mode: 'external'}))]) {
  test('malformed or unverified public offer cannot display a consent control', async (t) => {
    const ui = await mount(t, {current});
    assert.equal(ui.root.querySelector('[data-action="onloader-accept"]'), null);
    assert.equal(ui.posts().length, 0);
  });
}
