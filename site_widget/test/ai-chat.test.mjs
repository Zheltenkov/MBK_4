// Тесты чипсов виджета AI-чата: разметка — копия blade-шаблона mbk-site,
// сеть и поток ответа подменены. Спека:
// docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = dirname(fileURLToPath(import.meta.url));
const WIDGET = readFileSync(join(here, '..', '_ai-chat.js'), 'utf8');
// Комментарии Blade вырезаются, остальное — обычный HTML.
const MARKUP = readFileSync(join(here, 'ai-chat-widget.blade.php'), 'utf8')
  .replace(/\{\{--[\s\S]*?--\}\}/g, '');

const GREETING = [
  { label: 'Получить деньги', value: 'Получить деньги' },
  { label: 'Другой вопрос', value: 'Другой вопрос' },
];

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// Настоящее ожидание — для тестов темпа показа пузырей (revealMs > 0);
// используют реальный таймер node:test, а не window (его паузы отключены
// по умолчанию через revealMs: 0 в mountWidget).
function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Опрос условия вместо фиксированной задержки — устойчиво к дрожанию таймеров.
async function waitUntil(check, { timeout = 2000, step = 5 } = {}) {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > timeout) throw new Error('waitUntil: время ожидания истекло');
    await wait(step);
  }
}

async function mountWidget({
  storedSid = null,
  restoredState = null,
  restoredOk = true,
  postOk = true,
  postStatus = 200,
  postBody = { session_id: 'sid-1' },
  open = true,
  fetchImpl = null,
  revealMs = 0,
} = {}) {
  const dom = new JSDOM(`<!doctype html><html><body>${MARKUP}</body></html>`, {
    url: 'https://www.mbk.ru/',
    runScripts: 'outside-only',
  });
  const { window } = dom;
  const streams = [];
  const calls = [];

  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closed = false;
      streams.push(this);
    }

    addEventListener(name, handler) {
      (this.listeners[name] ||= []).push(handler);
    }

    close() {
      this.closed = true;
    }

    emit(name, data) {
      // Ordinary legacy fixtures model the exact current backend envelope.
      // Explicit malformed-envelope regressions use emitRaw so this adapter cannot hide them.
      const sid = decodeURIComponent(new URL(this.url, 'https://www.mbk.ru/').pathname.split('/')[3]);
      const payload = name === 'state' && data && !Object.hasOwn(data, 'session_id') ?
        { ...data, session_id: sid } : data;
      this.emitRaw(name, JSON.stringify(payload));
    }

    emitRaw(name, data) {
      for (const handler of this.listeners[name] || []) {
        handler({ data });
      }
    }
  }

  window.EventSource = FakeEventSource;
  // Пауза между пузырями одного ответа — дело показа, в тестах она не нужна:
  // 0 отключает её для любого пузыря (см. revealDelay_ в _ai-chat.js).
  // Тесты темпа показа переопределяют её сами перед созданием виджета.
  window.MBK_AI_CHAT_REVEAL_MS = revealMs;
  window.fetch = async (url, options = {}) => {
      calls.push({ url, options });
      if (fetchImpl) return fetchImpl(url, options);
      if (options.method === 'POST') {
        return { ok: postOk, status: postStatus, json: async () => postBody };
      }
      if (restoredState) {
        return {
          ok: restoredOk,
          status: restoredOk ? 200 : 404,
          json: async () => ({ session_id: storedSid || postBody.session_id, state: restoredState, turn_in_progress: false }),
        };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    };
  if (storedSid) window.localStorage.setItem('mbkAiChat.sid', storedSid);

  window.eval(WIDGET);
  const root = window.document.getElementById('mbk-ai-chat');
  if (open) {
    root.querySelector('.mbkc-launcher').click();
    await flush();
    await flush();
  }
  return { window, root, streams, calls };
}

function answer(stream, { bubbles, quickReplies, state }) {
  const payload = { bubbles };
  if (quickReplies !== undefined) payload.quick_replies = quickReplies;
  stream.emit('bubbles', payload);
  if (state) stream.emit('state', { state });
  stream.emit('done', {});
}

function chips(root) {
  return [...root.querySelectorAll('.mbkc-chips .mbkc-chip')].map((button) => [
    button.textContent,
    button.getAttribute('data-value'),
  ]);
}

test('кнопки приветствия рисуются под пузырями последним рядом ленты', async () => {
  const { root, streams } = await mountWidget();

  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/chat-api/session/sid-1/stream');
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });

  assert.deepEqual(chips(root), [
    ['Получить деньги', 'Получить деньги'],
    ['Другой вопрос', 'Другой вопрос'],
  ]);
  const feed = root.querySelector('.mbkc-feed-inner');
  assert.ok(feed.lastElementChild.classList.contains('mbkc-chips'));
});

test('без поля quick_replies виджет ведёт себя как раньше', async () => {
  const { root, streams } = await mountWidget();

  answer(streams[0], { bubbles: ['Добрый день!'] });

  assert.deepEqual(chips(root), []);
  assert.equal(root.querySelectorAll('.mbkc-row--in .mbkc-bubble').length, 1);
});

test('нажатие отправляет текст кнопки обычным сообщением и убирает ряд', async () => {
  const { root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });

  root.querySelectorAll('.mbkc-chip')[1].click();

  assert.equal(streams.length, 2);
  assert.equal(
    streams[1].url,
    '/chat-api/session/sid-1/stream?message=' + encodeURIComponent('Другой вопрос'),
  );
  assert.deepEqual(chips(root), []);
  const outgoing = [...root.querySelectorAll('.mbkc-row--out .mbkc-bubble > span')].map(
    (span) => span.textContent,
  );
  assert.deepEqual(outgoing, ['Другой вопрос']);
});

test('пока бот отвечает, нажатие ничего не отправляет и ряд остаётся', async () => {
  const { root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'], quick_replies: GREETING });

  root.querySelector('.mbkc-chip').click();

  assert.equal(streams.length, 1);
  assert.equal(chips(root).length, 2);
});

test('начал печатать — ряд пропадает, пустой ввод ряд не трогает', async () => {
  const { window, root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });
  const input = root.querySelector('.mbkc-input');

  input.value = '   ';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert.equal(chips(root).length, 2);

  input.value = 'у меня';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert.deepEqual(chips(root), []);
});

test('отправка из поля ввода убирает ряд', async () => {
  const { window, root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });
  const input = root.querySelector('.mbkc-input');
  input.value = 'Нужны деньги';

  root
    .querySelector('.mbkc-inputbar')
    .dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 2);
  assert.deepEqual(chips(root), []);
  assert.equal(input.value, '');
});

test('состояние с пустым списком кнопок убирает ряд', async () => {
  const { root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'], quick_replies: GREETING });

  streams[0].emit('state', { state: { chat_history: [{ role: 'assistant', content: 'Добрый день!' }], quick_replies: [] } });

  assert.deepEqual(chips(root), []);
});

test('после перезагрузки кнопки берутся из состояния сессии', async () => {
  const { root, streams } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [{ role: 'assistant', content: 'Добрый день!' }],
      quick_replies: GREETING,
    },
  });

  assert.equal(streams.length, 0);
  assert.deepEqual(
    chips(root).map(([label]) => label),
    ['Получить деньги', 'Другой вопрос'],
  );
});

test('после перезагрузки разговора с репликой клиента кнопок нет', async () => {
  const { root } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [
        { role: 'assistant', content: 'Добрый день!' },
        { role: 'user', content: 'Нужны деньги' },
      ],
      quick_replies: [],
    },
  });

  assert.deepEqual(chips(root), []);
});

test('мусорные элементы отбрасываются, кнопок не больше четырёх', async () => {
  const { root, streams } = await mountWidget();
  const junk = [
    null,
    'строка',
    { label: '', value: 'x' },
    { label: 'Без значения' },
    { label: 'Число', value: 5 },
    { label: '1', value: '1' },
    { label: '2', value: '2' },
    { label: '3', value: '3' },
    { label: '4', value: '4' },
    { label: '5', value: '5' },
  ];

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: junk });

  assert.deepEqual(
    chips(root).map(([label]) => label),
    ['1', '2', '3', '4'],
  );
});

test('не массив вместо списка — кнопок нет', async () => {
  const { root, streams } = await mountWidget();

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: { label: 'x', value: 'x' } });

  assert.deepEqual(chips(root), []);
});

test('текст кнопки — только текст, разметка не исполняется', async () => {
  const { root, streams } = await mountWidget();
  const label = '<img src=x onerror="window.hacked=1">';

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: [{ label, value: 'x' }] });

  assert.equal(root.querySelector('.mbkc-chip img'), null);
  assert.equal(root.querySelector('.mbkc-chip').textContent, label);
});

test('закрытый диалог кнопок не показывает', async () => {
  const { root, streams } = await mountWidget();

  streams[0].emit('state', { state: { chat_history: [], closed: true, quick_replies: [] } });
  streams[0].emit('bubbles', { bubbles: ['Спасибо!'], quick_replies: GREETING });

  assert.deepEqual(chips(root), []);
});

/* ---------- создание и восстановление сессии ---------- */

test('открытие чата создаёт сессию POST-запросом и сохраняет sid в localStorage', async () => {
  const { window, root, streams, calls } = await mountWidget();

  const posts = calls.filter((c) => c.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/chat-api/session');
  assert.equal(posts[0].options.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(posts[0].options.body), { utm: null });

  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-1');
  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/chat-api/session/sid-1/stream');
  assert.ok(root.querySelector('.mbkc-feed-inner [data-typing]'), 'до ответа показан индикатор набора');
});

test('сбой создания сессии (500) показывает системное сообщение с повтором', async () => {
  const { window, root, calls } = await mountWidget({ postOk: false, postStatus: 500 });

  const system = root.querySelector('.mbkc-system');
  assert.ok(system);
  assert.ok(system.textContent.includes('Не удалось подключиться к чату.'));
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), null);
  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 1);

  system.querySelector('button').click();
  await flush();
  await flush();

  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 2);
  assert.ok(root.querySelector('.mbkc-system'), 'после провалившегося повтора сообщение появляется снова');
});

test('лимит новых диалогов (429) показывает отдельное сообщение', async () => {
  const { root } = await mountWidget({ postOk: false, postStatus: 429 });

  const system = root.querySelector('.mbkc-system');
  assert.ok(system.textContent.includes('Превышен лимит новых диалогов. Попробуйте немного позже.'));
});

test('сохранённый sid восстанавливает историю без создания новой сессии', async () => {
  const { window, root, streams, calls } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [
        { role: 'assistant', content: 'Добрый день!' },
        { role: 'user', content: 'Нужны деньги' },
      ],
    },
  });

  assert.equal(streams.length, 0);
  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/chat-api/session/sid-9');
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-9');

  const rows = [...root.querySelectorAll('.mbkc-row--in .mbkc-bubble > span, .mbkc-row--out .mbkc-bubble > span')].map(
    (span) => span.textContent,
  );
  assert.deepEqual(rows, ['Добрый день!', 'Нужны деньги']);
});

test('в истории учитываются только реплики user и assistant', async () => {
  const { root } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [
        { role: 'assistant', content: 'Добрый день!' },
        { role: 'system', content: 'служебная запись' },
        { role: 'user', content: 'Нужны деньги' },
      ],
    },
  });

  const texts = [...root.querySelectorAll('.mbkc-feed-inner .mbkc-bubble > span')].map((span) => span.textContent);
  assert.deepEqual(texts, ['Добрый день!', 'Нужны деньги']);
});

test('восстановленная сессия без истории открывает поток заново тем же sid', async () => {
  const { streams, calls } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: { chat_history: [] },
  });

  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/chat-api/session/sid-9/stream');
});

test('закрытый восстановленный диалог сохраняет sid до явного начала нового', async () => {
  const { window, root, streams, calls } = await mountWidget({
    storedSid: 'sid-old',
    restoredState: { closed: true, chat_history: [{ role: 'assistant', content: 'Было' }] },
  });

  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-old');
  assert.equal(streams.length, 0);
  const restart = root.querySelector('[data-action="new-debt"]');
  assert.ok(restart);
  restart.click();
  await waitUntil(() => calls.filter((c) => c.options.method === 'POST').length === 1 && streams.length === 1);
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-1');
});

test('недоступный сохранённый sid требует ручного действия без автоматического POST', async () => {
  const { window, root, streams, calls } = await mountWidget({ storedSid: 'sid-broken' });

  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-broken');
  assert.equal(streams.length, 0);
  assert.ok(root.querySelector('[data-action="recover-debt"]'));
  assert.ok(root.querySelector('[data-action="new-debt"]'));
});

test('сетевая ошибка восстановления сохраняет sid и позволяет повторить только GET', async () => {
  const { window, root, streams, calls } = await mountWidget({
    storedSid: 'sid-broken',
    fetchImpl: async (url, options = {}) => {
      if (options.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({ session_id: 'sid-1' }) };
      }
      throw new Error('network down');
    },
  });

  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-broken');
  assert.equal(streams.length, 0);
  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  root.querySelector('[data-action="recover-debt"]').click();
  await waitUntil(() => calls.filter((c) => c.options.method !== 'POST').length === 2);
  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.equal(streams.length, 0);
});

test('повторное открытие чата не создаёт вторую сессию', async () => {
  const { root, streams, calls } = await mountWidget();

  root.querySelector('.mbkc-launcher').click();
  await flush();

  assert.equal(calls.filter((c) => c.options.method === 'POST').length, 1);
  assert.equal(streams.length, 1);
});

/* ---------- поток ответа (SSE) ---------- */

test('текст стрима собирается по чанкам без метки времени, пока не пришли пузыри', async () => {
  const { root, streams } = await mountWidget();
  const stream = streams[0];

  stream.emit('chunk', {}); // без text — ничего не меняет
  assert.ok(root.querySelector('.mbkc-feed-inner [data-typing]'));

  stream.emit('chunk', { text: 'При' });
  assert.equal(root.querySelector('.mbkc-feed-inner [data-typing]'), null, 'первый непустой чанк убирает набор текста');
  stream.emit('chunk', { text: 'вет!' });

  const draft = root.querySelector('.mbkc-row--in .mbkc-bubble');
  assert.equal(draft.querySelector('span').textContent, 'Привет!');
  assert.equal(draft.querySelector('.mbkc-meta'), null, 'у черновика нет метки времени');
});

test('финальные пузыри заменяют черновик стрима', async () => {
  const { root, streams } = await mountWidget();
  const stream = streams[0];

  stream.emit('chunk', { text: 'Черновик' });
  stream.emit('bubbles', { bubbles: ['Готовый ответ'] });

  const bubbles = [...root.querySelectorAll('.mbkc-row--in .mbkc-bubble')];
  assert.equal(bubbles.length, 1);
  assert.equal(bubbles[0].querySelector('span').textContent, 'Готовый ответ');
  assert.ok(bubbles[0].querySelector('.mbkc-meta'), 'у финального пузыря есть метка времени');
});

test('done без пузырей оставляет собранный текст стрима обычным сообщением без метки времени', async () => {
  const { root, streams } = await mountWidget();
  const stream = streams[0];

  stream.emit('chunk', { text: 'Ответ без финальных пузырей' });
  stream.emit('done', {});

  const bubbles = [...root.querySelectorAll('.mbkc-row--in .mbkc-bubble')];
  assert.equal(bubbles.length, 1);
  assert.equal(bubbles[0].querySelector('span').textContent, 'Ответ без финальных пузырей');
  assert.equal(bubbles[0].querySelector('.mbkc-meta'), null);
});

test('разрыв связи на реплике клиента проверяет тот же sid через GET без повторной отправки', async () => {
  const { root, streams, calls } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'] });
  streams[0].emit('done', {});

  const input = root.querySelector('.mbkc-input');
  input.value = 'Нужен займ';
  root.querySelector('.mbkc-inputbar').dispatchEvent(new (root.ownerDocument.defaultView.Event)('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 2);
  streams[1].emit('error', {});

  await waitUntil(() => root.querySelector('[data-action="recover-debt"]'));
  assert.equal(calls.filter((call) => call.options.method !== 'POST').length, 1);
  assert.equal(streams[1].closed, true);
  root.querySelector('[data-action="recover-debt"]').click();
  await waitUntil(() => calls.filter((call) => call.options.method !== 'POST').length === 2);
  assert.equal(streams.length, 2);
  assert.equal(calls.filter((call) => call.options.method === 'POST').length, 1);
});

test('разрыв связи на приветствии восстанавливает тот же sid и ручной GET работает', async () => {
  const { window, root, streams, calls } = await mountWidget();

  streams[0].emit('error', {});

  await waitUntil(() => root.querySelector('[data-action="recover-debt"]'));
  root.querySelector('[data-action="recover-debt"]').click();
  await waitUntil(() => calls.filter((call) => call.options.method !== 'POST').length === 2);
  assert.equal(streams.length, 1);
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-1');
  assert.equal(calls.filter((call) => call.options.method === 'POST').length, 1);
});

test('частичный ответ при разрыве тоже сверяется через GET без повторного SSE', async () => {
  const { root, streams, calls } = await mountWidget();

  streams[0].emit('chunk', { text: 'Успел сказать немного' });
  streams[0].emit('error', {});

  await waitUntil(() => root.querySelector('[data-action="recover-debt"]'));
  assert.equal(streams[0].closed, true);
  assert.equal(calls.filter((call) => call.options.method !== 'POST').length, 1);
  assert.equal(calls.filter((call) => call.options.method === 'POST').length, 1);
  assert.equal(streams.length, 1);
});

test('во время стриминга ссылка в тексте не становится кликабельной', async () => {
  const { root, streams } = await mountWidget();

  streams[0].emit('chunk', { text: 'Заявка тут: https://mbk.ru/offer' });

  const draft = root.querySelector('.mbkc-row--in .mbkc-bubble span');
  assert.equal(draft.querySelector('a'), null);
  assert.equal(draft.textContent, 'Заявка тут: https://mbk.ru/offer');
});

test('ссылка в финальном пузыре становится кликабельной', async () => {
  const { root, streams } = await mountWidget();

  streams[0].emit('bubbles', { bubbles: ['Заявка тут: https://mbk.ru/offer вот и всё'] });

  const bubble = root.querySelector('.mbkc-row--in .mbkc-bubble span');
  const link = bubble.querySelector('a');
  assert.ok(link);
  assert.equal(link.href, 'https://mbk.ru/offer');
  assert.equal(link.target, '_blank');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(bubble.textContent, 'Заявка тут: https://mbk.ru/offer вот и всё');
});

test('текст пузыря бота не исполняется как разметка', async () => {
  const { root, streams } = await mountWidget();
  const payload = '<img src=x onerror="window.hacked=1">';

  streams[0].emit('bubbles', { bubbles: [payload] });

  // .mbkc-row--in содержит и служебный аватар с настоящим <img> — проверяем
  // отсутствие картинки именно внутри пузыря с текстом.
  assert.equal(root.querySelector('.mbkc-row--in .mbkc-bubble img'), null);
  assert.equal(root.querySelector('.mbkc-row--in .mbkc-bubble span').textContent, payload);
});

/* ---------- отправка сообщений ---------- */

test('пустая строка и строка из пробелов не отправляются', async () => {
  const { window, root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'] });
  const input = root.querySelector('.mbkc-input');

  input.value = '   ';
  root.querySelector('.mbkc-inputbar').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 1, 'новый поток не открылся');
  assert.equal(root.querySelectorAll('.mbkc-row--out').length, 0);
  assert.equal(input.value, '   ', 'поле не очищается при неудачной отправке');
});

test('текст длиннее предела обрубается перед отправкой', async () => {
  const { window, root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'] });
  streams[0].emit('done', {});
  const input = root.querySelector('.mbkc-input');
  const long = 'а'.repeat(2005);

  input.value = long;
  root.querySelector('.mbkc-inputbar').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  const sent = root.querySelector('.mbkc-row--out .mbkc-bubble span').textContent;
  assert.equal(sent.length, 2000);
  assert.equal(streams[1].url, '/chat-api/session/sid-1/stream?message=' + encodeURIComponent('а'.repeat(2000)));
});

test('пока бот отвечает, повторная отправка через форму не проходит', async () => {
  const { window, root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'] });
  streams[0].emit('done', {});
  const input = root.querySelector('.mbkc-input');

  input.value = 'Первое сообщение';
  root.querySelector('.mbkc-inputbar').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(streams.length, 2);

  input.value = 'Второе сообщение';
  root.querySelector('.mbkc-inputbar').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 2, 'второй поток не открылся, пока идёт первый');
  assert.equal(root.querySelectorAll('.mbkc-row--out').length, 1);
  assert.equal(input.value, 'Второе сообщение', 'поле не очищается при неудачной отправке');
});

test('закрытый диалог не принимает новые сообщения и блокирует поле ввода', async () => {
  const { window, root, streams } = await mountWidget();
  // Событие state само не блокирует поле — disabled выставляется внутри
  // setBusyUI, которую перечитывает только завершение потока (done).
  streams[0].emit('state', { state: { chat_history: [], closed: true } });
  streams[0].emit('done', {});
  const input = root.querySelector('.mbkc-input');

  assert.equal(input.disabled, true);
  assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-1');

  input.value = 'Ещё вопрос';
  root.querySelector('.mbkc-inputbar').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 1, 'сообщение после закрытия не отправлено');
});

test('кнопка отправки блокируется на пустом поле и при ответе бота', async () => {
  const { root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'] });
  streams[0].emit('done', {});
  const input = root.querySelector('.mbkc-input');
  const send = root.querySelector('.mbkc-send');

  assert.equal(send.disabled, true);

  input.value = 'Текст';
  input.dispatchEvent(new (root.ownerDocument.defaultView.Event)('input', { bubbles: true }));
  assert.equal(send.disabled, false);

  input.value = '';
  input.dispatchEvent(new (root.ownerDocument.defaultView.Event)('input', { bubbles: true }));
  assert.equal(send.disabled, true);
});

test('поле ввода получает атрибут maxlength равный пределу сообщения', async () => {
  const { root } = await mountWidget();
  assert.equal(root.querySelector('.mbkc-input').getAttribute('maxlength'), '2000');
});

/* ---------- окно чата и вспомогательный интерфейс ---------- */

test('кнопка закрытия возвращает виджет в режим плавающей кнопки', async () => {
  const { root } = await mountWidget();
  assert.equal(root.dataset.mode, 'widget');

  root.querySelector('[data-action="close"]').click();

  assert.equal(root.dataset.mode, 'button');
});

test('переключение вкладок сайдбара показывает соответствующую панель', async () => {
  const { root } = await mountWidget();
  const [chatsTab, filesTab] = root.querySelectorAll('.mbkc-tab');

  filesTab.click();

  assert.ok(filesTab.classList.contains('mbkc-active'));
  assert.equal(chatsTab.classList.contains('mbkc-active'), false);
  assert.equal(root.querySelector('[data-pane="files"]').hidden, false);
  assert.equal(root.querySelector('[data-pane="chats"]').hidden, true);
});

test('сообщения, пришедшие в режиме кнопки, копятся в бейдже непрочитанных', async () => {
  const { root, streams } = await mountWidget();

  root.querySelector('[data-action="close"]').click();
  assert.equal(root.dataset.mode, 'button');

  streams[0].emit('bubbles', { bubbles: ['Привет!', 'Как вас зовут?'] });

  const badge = root.querySelector('.mbkc-badge');
  assert.equal(badge.textContent, '2', 'счётчик растёт на каждый пузырь ответа, а не на одно событие');
  assert.ok(badge.classList.contains('mbkc-visible'));
  assert.equal(root.querySelector('.mbkc-chat-item-last').textContent, 'Как вас зовут?');
});

test('согласие на офер-странице кладёт sid в куку', async () => {
  const { root, streams } = await mountWidget();

  streams[0].emit('state', {
    state: { chat_history: [], crm_workflow: { need_site_consent: true } },
  });

  assert.ok(root.ownerDocument.cookie.includes('mbk_chat_consent=sid-1'));
});

/* ---------- темп показа пузырей одного ответа (2026-09-25) ---------- */
// Живой человек печатает реплики одну за другой, а не все сразу; ниже —
// тесты на паузу и индикатор набора между пузырями. mountWidget по умолчанию
// ставит window.MBK_AI_CHAT_REVEAL_MS = 0 (см. хвост _ai-chat.js), эти тесты
// сами просят ненулевую паузу через revealMs, чтобы застать промежуточное
// состояние — недопоказанный ряд пузырей.

test('пузыри одного ответа показываются по одному, не все сразу', async () => {
  const { root, streams } = await mountWidget({ revealMs: 30 });
  const stream = streams[0];
  // :not(.mbkc-typing) — у индикатора набора те же классы .mbkc-row--in
  // .mbkc-bubble, но три пустых <span> для точек анимации, не текст пузыря.
  const bubbleTexts = () =>
    [...root.querySelectorAll('.mbkc-row--in .mbkc-bubble:not(.mbkc-typing) > span')].map((s) => s.textContent);

  stream.emit('bubbles', { bubbles: ['Первый.', 'Второй.', 'Третий.'] });
  stream.emit('done', {});

  assert.deepEqual(bubbleTexts(), ['Первый.'], 'первый пузырь виден сразу, остальные — ещё нет');

  await waitUntil(() => bubbleTexts().length === 3);
  assert.deepEqual(bubbleTexts(), ['Первый.', 'Второй.', 'Третий.']);
});

test('в паузе между пузырями виден индикатор печати, после последнего — пропадает', async () => {
  const { root, streams } = await mountWidget({ revealMs: 40 });
  const stream = streams[0];
  const shown = () => root.querySelectorAll('.mbkc-row--in .mbkc-bubble:not(.mbkc-typing)').length;

  stream.emit('bubbles', { bubbles: ['Первый.', 'Второй.'] });
  stream.emit('done', {});

  assert.ok(root.querySelector('.mbkc-feed-inner [data-typing]'), 'индикатор виден в паузе перед вторым пузырём');

  await waitUntil(() => shown() === 2);
  assert.equal(root.querySelector('.mbkc-feed-inner [data-typing]'), null, 'после последнего пузыря индикатора нет');
});

test('кнопки-подсказки и разблокировка ввода — только после последнего пузыря', async () => {
  const { root, streams } = await mountWidget({ revealMs: 40 });
  const stream = streams[0];
  const submitViaForm = () => {
    root
      .querySelector('.mbkc-inputbar')
      .dispatchEvent(new root.ownerDocument.defaultView.Event('submit', { bubbles: true, cancelable: true }));
  };

  stream.emit('bubbles', { bubbles: ['Первый.', 'Второй.'], quick_replies: GREETING });
  stream.emit('done', {}); // done приходит следом за bubbles ещё до конца пауз показа

  assert.deepEqual(chips(root), [], 'кнопки не появляются раньше последнего пузыря');

  const input = root.querySelector('.mbkc-input');
  input.value = 'Пока рано';
  submitViaForm();
  assert.equal(streams.length, 1, 'пока не показан последний пузырь, отправка не проходит');

  await waitUntil(() => chips(root).length === 2);
  assert.deepEqual(chips(root), [
    ['Получить деньги', 'Получить деньги'],
    ['Другой вопрос', 'Другой вопрос'],
  ]);

  input.value = 'Теперь можно';
  submitViaForm();
  assert.equal(streams.length, 2, 'после последнего пузыря отправка снова работает');
});

test('новый ответ отменяет недопоказанные пузыри прежнего', async () => {
  const { root, streams } = await mountWidget({ revealMs: 80 });
  const stream = streams[0];

  stream.emit('bubbles', { bubbles: ['Первый.', 'Второй.'] });
  // «Второй.» ещё не показан — идёт пауза перед ним; новый ответ приходит раньше
  // Тот же открытый поток уточняет bubbles до окончательного done.
  stream.emit('bubbles', { bubbles: ['Новый ответ.'] });
  stream.emit('done', {});

  await wait(250); // заметно дольше отменённой паузы (80 мс)

  const texts = [...root.querySelectorAll('.mbkc-row--in .mbkc-bubble:not(.mbkc-typing) > span')].map(
    (s) => s.textContent,
  );
  assert.deepEqual(texts, ['Первый.', 'Новый ответ.'], '«Второй.» не должен всплыть поверх нового ответа');
});

for (const [label, envelope] of [
  ['missing sid', { state: { chat_history: [{ role: 'assistant', content: 'MUST_NOT_RENDER' }], crm_workflow: { need_site_consent: true } } }],
  ['foreign sid', { session_id: 'foreign-sid', state: { chat_history: [{ role: 'assistant', content: 'MUST_NOT_RENDER' }], crm_workflow: { need_site_consent: true } } }],
  ['missing canonical history', { session_id: 'sid-1', state: { crm_workflow: { need_site_consent: true } } }],
]) {
  test(`normal state rejects ${label} and reconciles the same session with GET`, async () => {
    const { root, streams, calls, window } = await mountWidget();
    // Raw emission bypasses the legacy helper's valid-envelope defaults.
    streams[0].emitRaw('state', JSON.stringify(envelope));
    assert.ok(!root.querySelector('.mbkc-feed-inner').textContent.includes('MUST_NOT_RENDER'));
    assert.ok(!root.ownerDocument.cookie.includes('mbk_chat_consent='));
    await waitUntil(() => calls.some((call) => call.url === '/chat-api/session/sid-1' && !call.options.method));
    assert.equal(streams[0].closed, true);
    assert.equal(streams.length, 1);
    assert.equal(calls.filter((call) => call.options.method === 'POST').length, 1);
    assert.equal(window.localStorage.getItem('mbkAiChat.sid'), 'sid-1');
    assert.ok(!root.querySelector('.mbkc-feed-inner').textContent.includes('MUST_NOT_RENDER'));
    assert.ok(!root.ownerDocument.cookie.includes('mbk_chat_consent='));
  });
}
