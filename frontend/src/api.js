// API-клиент к FastAPI-бэкенду. Все запросы идут на тот же origin через nginx-proxy в проде
// или Vite-proxy в dev. SSE через нативный EventSource.

const SESSION_KEY = 'mbk_session_id';

export function loadSessionId() {
  try { return sessionStorage.getItem(SESSION_KEY); } catch { return null; }
}

export function saveSessionId(id) {
  try { sessionStorage.setItem(SESSION_KEY, id); } catch { /* приватный режим */ }
}

export function clearSessionId() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* */ }
}

async function jsonFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    credentials: 'same-origin',
    ...options,
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = res.statusText; }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export async function login(username, password) {
  return jsonFetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export async function getAuthStatus() {
  return jsonFetch('/api/auth/me');
}

export async function logout() {
  return jsonFetch('/api/auth/logout', { method: 'POST' });
}

export async function getHealth() {
  return jsonFetch('/api/health');
}

// Рекламные метки страницы, с которой открыт чат. Читаются один раз, при
// загрузке, и кладутся в sessionStorage: клиент может открыть чат не сразу, а
// после перезагрузки страницы метки в адресе уже не будет.
//
// Если чат встроен во фрейм, лендинг обязан передать метки в адрес фрейма —
// собственный адрес родителя фрейму недоступен. document.referrer в этом случае
// как раз и указывает на лендинг.
const UTM_KEY = 'mbk_utm';
const UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];

function captureUtm() {
  try {
    const stored = sessionStorage.getItem(UTM_KEY);
    if (stored) return JSON.parse(stored);
  } catch { /* приватный режим */ }

  const marks = {};
  try {
    const params = new URLSearchParams(window.location.search);
    for (const name of UTM_PARAMS) {
      const value = params.get(name);
      if (value) marks[name] = value;
    }
    marks.landing = document.referrer || window.location.href;
  } catch { /* нестандартное окружение — метки не обязательны */ }

  try { sessionStorage.setItem(UTM_KEY, JSON.stringify(marks)); } catch { /* */ }
  return marks;
}

const utm = captureUtm();

export async function createSession(anketa) {
  return jsonFetch('/api/session', {
    method: 'POST',
    body: JSON.stringify({ anketa, utm }),
  });
}

export async function getSessionState(sessionId) {
  return jsonFetch(`/api/session/${sessionId}`);
}

export async function getSessionUsage(sessionId) {
  return jsonFetch(`/api/session/${sessionId}/usage`);
}

export async function endSession(sessionId) {
  return jsonFetch(`/api/session/${sessionId}/end`, { method: 'POST' });
}

/**
 * Открывает SSE-стрим. Если message пустой/null, бэкенд понимает это как opening (если
 * история пуста). Иначе — обычный ход.
 *
 * callbacks: { onChunk, onBubbles, onState, onError, onDone }
 * Возвращает функцию-cancel для досрочного закрытия.
 */
export function streamMessage(sessionId, message, callbacks) {
  const url = new URL(`/api/session/${sessionId}/stream`, window.location.origin);
  if (message && message.trim()) url.searchParams.set('message', message);
  const es = new EventSource(url.toString());
  let applicationErrorReceived = false;

  es.addEventListener('chunk', (e) => {
    try { callbacks.onChunk?.(JSON.parse(e.data)); } catch (err) { console.warn('bad chunk', err); }
  });
  es.addEventListener('bubbles', (e) => {
    try { callbacks.onBubbles?.(JSON.parse(e.data)); } catch (err) { console.warn('bad bubbles', err); }
  });
  es.addEventListener('state', (e) => {
    try { callbacks.onState?.(JSON.parse(e.data)); } catch (err) { console.warn('bad state', err); }
  });
  // Серверное событие error (отличается от нативной ошибки соединения).
  es.addEventListener('error', (e) => {
    // Если у события есть .data — это сервер прислал прикладную ошибку.
    if (e.data) {
      applicationErrorReceived = true;
      try { callbacks.onError?.(JSON.parse(e.data)); } catch { callbacks.onError?.({ message: 'unknown server error' }); }
    }
    // Иначе это транспортная ошибка EventSource (например, обрыв). Обработаем в onerror.
  });
  es.addEventListener('done', () => {
    callbacks.onDone?.();
    es.close();
  });

  // Транспортная ошибка соединения.
  es.onerror = () => {
    // Custom SSE event:error уже доставил безопасный серверный текст. Браузер
    // следом вызывает нативный onerror для того же события — второй generic
    // callback затирал конкретное сообщение.
    if (applicationErrorReceived) {
      es.close();
      return;
    }
    // Если соединение закрылось нормально (после done), readyState=CLOSED — это ОК.
    if (es.readyState === EventSource.CLOSED) return;
    callbacks.onError?.({ message: 'Соединение прервано. Попробуйте ещё раз.' });
    es.close();
  };

  return () => es.close();
}
