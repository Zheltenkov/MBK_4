import { useEffect, useRef, useState } from 'react';
import { endSession, getSessionState, getSessionUsage, streamMessage } from '../api.js';

export default function ChatView({ sessionId, initialState, onReset }) {
  const [messages, setMessages] = useState(initialState?.chat_history || []);
  const [streamingBuffer, setStreamingBuffer] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  // Результат обращения в маршрутизирующий микросервис: ссылка либо отказ.
  const [delivery, setDelivery] = useState(initialState?.delivery || null);
  const [crmWorkflow, setCrmWorkflow] = useState(initialState?.crm_workflow || null);
  const [crmLead, setCrmLead] = useState(initialState?.crm_lead || null);
  const [runtime, setRuntime] = useState(initialState?.runtime || null);
  const [error, setError] = useState(null);
  const [showUsage, setShowUsage] = useState(false);
  const [usage, setUsage] = useState(null);
  // Кнопки под последней репликой бота (спека чипсов 14.09.2026): приходят в
  // событии bubbles и в состоянии сессии; нажатие — обычное сообщение.
  const [quickReplies, setQuickReplies] = useState(initialState?.quick_replies || []);
  const crmSubmitted = crmWorkflow?.status === 'submitted';
  const isCompleted = delivery?.status === 'completed' || crmSubmitted;
  const hasCrmStatus = crmWorkflow?.status && crmWorkflow.status !== 'not_started';
  // Панель разбора — для нас, а не для клиента. Номер лида, итоговый JSON,
  // режим отправки и расход токенов ему видеть незачем: 10.08.2026 на боевом
  // стенде он увидел бы «Тестовый лид создан в CRM: 3450947».
  const isDebug = Boolean(runtime?.debug_panel);
  // Ссылка от CRM — единственное из этого блока, что адресовано клиенту.
  const thanksUrl = crmWorkflow?.thanks_url || null;
  const showStatusBar = isDebug ? Boolean(delivery || hasCrmStatus || showUsage) : Boolean(thanksUrl);

  const cancelRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const hasStartedOpeningRef = useRef(messages.length > 0);

  // Авто-старт opening, если истории ещё нет.
  useEffect(() => {
    if (!hasStartedOpeningRef.current) {
      hasStartedOpeningRef.current = true;
      runStream(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Авто-скролл к низу при новых сообщениях / стриме.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, streamingBuffer, delivery]);

  // Загрузка usage по запросу.
  useEffect(() => {
    if (!showUsage) return;
    let cancelled = false;
    getSessionUsage(sessionId).then((u) => { if (!cancelled) setUsage(u); }).catch(() => {});
    return () => { cancelled = true; };
  }, [showUsage, sessionId, messages.length]);

  // Safe retry CRM идёт в фоне уже после завершения SSE-хода. Пока workflow
  // pending, короткий polling синхронизирует открытый виджет: иначе лид и
  // thanks_url появлялись на backend, а клиент видел старое состояние до
  // перезагрузки или следующего сообщения.
  useEffect(() => {
    const pending = ['qualification_retry_pending', 'submission_retry_pending'].includes(crmWorkflow?.status);
    if (!pending) return undefined;
    let cancelled = false;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const result = await getSessionState(sessionId);
        if (cancelled || !result?.state) return;
        const state = result.state;
        if (state.chat_history) setMessages(state.chat_history);
        if (Array.isArray(state.quick_replies)) setQuickReplies(state.quick_replies);
        if (state.delivery) setDelivery(state.delivery);
        if (state.crm_workflow) setCrmWorkflow(state.crm_workflow);
        if (state.crm_lead) setCrmLead(state.crm_lead);
        if (state.runtime) setRuntime(state.runtime);
      } catch {
        // Следующий polling повторит чтение; временный сетевой сбой не должен
        // превращаться в новую красную ошибку поверх уже принятой анкеты.
      } finally {
        refreshing = false;
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [crmWorkflow?.status, sessionId]);

  const runStream = (userMessage) => {
    setError(null);
    setIsStreaming(true);
    setStreamingBuffer('');
    if (userMessage) {
      setQuickReplies([]);
      setMessages((m) => [...m, { role: 'user', content: userMessage }]);
    }
    const cancel = streamMessage(sessionId, userMessage || null, {
      onChunk: ({ text }) => setStreamingBuffer((b) => b + text),
      onBubbles: ({ bubbles, quick_replies: chips }) => {
        // Сервер прислал финальную разбивку — заменяем накопленный буфер
        // на готовые пузыри (учитывают guard_bubbles).
        setStreamingBuffer('');
        setMessages((m) => [...m, ...bubbles.map((c) => ({ role: 'assistant', content: c }))]);
        if (bubbles.length) setQuickReplies(Array.isArray(chips) ? chips : []);
      },
      onState: ({ state, delivery: deliveryResult }) => {
        if (deliveryResult) setDelivery(deliveryResult);
        if (state?.chat_history) setMessages(state.chat_history);
        if (Array.isArray(state?.quick_replies)) setQuickReplies(state.quick_replies);
        if (state?.crm_workflow) setCrmWorkflow(state.crm_workflow);
        if (state?.crm_lead) setCrmLead(state.crm_lead);
        if (state?.runtime) setRuntime(state.runtime);
      },
      onError: ({ message }) => {
        setError(message || 'Ошибка');
        setIsStreaming(false);
        setStreamingBuffer('');
      },
      onDone: () => {
        setIsStreaming(false);
      },
    });
    cancelRef.current = cancel;
  };

  const handleSend = (e) => {
    e?.preventDefault();
    const text = inputRef.current?.value?.trim();
    if (!text || isStreaming || isCompleted) return;
    inputRef.current.value = '';
    runStream(text);
  };

  // Live-разбивка буфера на пузыри по \n\n. Последний пузырь — «растущий».
  const streamingBubbles = streamingBuffer
    ? streamingBuffer.split(/\n\n+/).map((s) => s.trim()).filter(Boolean)
    : [];

  const handleDownload = async () => {
    try {
      const u = await getSessionUsage(sessionId);
      const payload = {
        session_id: sessionId,
        chat_history: messages,
        delivery,
        crm_workflow: crmWorkflow,
        crm_lead: crmLead,
        runtime,
        usage: u,
        exported_at: new Date().toISOString(),
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mbk_dialog_${sessionId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || String(err));
    }
  };

  const handleEnd = async () => {
    cancelRef.current?.();
    try { await endSession(sessionId); } catch {}
    onReset();
  };

  return (
    <div className="h-full flex flex-col">
      {isDebug && runtime?.crm_workflow_enabled && (
        <CrmRuntimeBanner runtime={runtime} />
      )}

      {/* Status bar: результат маршрутизации / usage toggle */}
      {showStatusBar && (
        <div className="mx-auto w-full max-w-3xl px-4 pt-3 space-y-2">
          {isDebug && crmWorkflow?.status === 'awaiting_site_consent' && (
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-800">
              CRM-квалификация завершена. Ожидается явный ответ клиента на предложение консультации.
            </div>
          )}
          {crmWorkflow?.status === 'submitted' && (isDebug || thanksUrl) && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              {isDebug
                ? `✅ Тестовый лид создан в CRM${crmLead?.lead_id ? `: ${crmLead.lead_id}` : '.'}`
                : 'Ваше решение готово.'}
              {thanksUrl && (
                <a
                  href={thanksUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="ml-2 inline-flex rounded-md bg-emerald-700 px-3 py-1.5 font-medium text-white hover:bg-emerald-800"
                >
                  Перейти к следующему шагу
                </a>
              )}
            </div>
          )}
          {isDebug && crmWorkflow?.status === 'gated_local' && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              CRM не вызывалась: данные не прошли тестовый allowlist. Показан только локальный JSON.
            </div>
          )}
          {isDebug && ['qualification_error', 'submission_error', 'outcome_unknown'].includes(crmWorkflow?.status) && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              CRM-передача завершилась технической ошибкой. Автоматический повтор заблокирован.
            </div>
          )}
          {isDebug && delivery?.status === 'link' && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              ✅ Ссылка отправлена клиенту{delivery.mocked ? ' (мок-режим, микросервис не подключён)' : ''}.
            </div>
          )}
          {isDebug && isCompleted && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              ✅ Опрос завершён. Ниже показан JSON, который будет передаваться дальше.
            </div>
          )}
          {/* Отказ и техническую ошибку клиенту проговаривает сам бот — здесь
              они дублировались бы плашкой на служебном языке. */}
          {isDebug && delivery?.status === 'no_fit' && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Подходящего решения не нашлось{delivery.message ? `: ${delivery.message}` : '.'}
            </div>
          )}
          {isDebug && delivery?.status === 'error' && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              Не удалось получить ссылку из-за технической ошибки
              {delivery.message ? `: ${delivery.message}` : '. Попробуйте повторить позже.'}
            </div>
          )}
          {isDebug && showUsage && (
            <UsagePanel usage={usage} />
          )}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-4 space-y-3">
          {messages.length === 0 && !streamingBuffer && (
            <div className="text-center text-sm text-slate-400 py-12">
              Открываю чат…
            </div>
          )}
          {messages.map((m, i) => (
            <ChatBubble key={i} role={m.role} text={m.content} />
          ))}
          {streamingBubbles.map((b, i) => (
            <ChatBubble key={`s${i}`} role="assistant" text={b} streaming={i === streamingBubbles.length - 1} />
          ))}
          {!isStreaming && !isCompleted && quickReplies.length > 0 && (
            <QuickReplies items={quickReplies} onPick={(value) => runStream(value)} />
          )}
          {/* Ход начинается с разбора реплики: он занимает несколько секунд, и
              всё это время поток открыт, а показывать ещё нечего. Без этого
              индикатора клиент смотрел в пустой чат и решал, что бот завис.
              Индикатор из v3 включался только ПОСЛЕ ответа — там разбор и шёл
              после стрима. */}
          {isStreaming && !streamingBuffer && <TypingIndicator />}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {isDebug && isCompleted && delivery?.request && (
            <CompletedSurveyJson value={delivery.request} />
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-slate-200 bg-white">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          <form onSubmit={handleSend} className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              rows={1}
              placeholder={isCompleted ? 'Опрос завершён' : isStreaming ? 'Бот отвечает…' : 'Напишите сообщение…'}
              disabled={isCompleted || isStreaming}
              className="input-base resize-none min-h-[44px] max-h-32"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              onInput={(e) => {
                if (e.currentTarget.value.trim()) setQuickReplies([]);
              }}
            />
            <button type="submit" disabled={isStreaming || isCompleted} className="btn-primary h-11">
              ↑
            </button>
          </form>
          <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
            <div className="flex gap-3">
              {isDebug && (
                <>
                  <button onClick={() => setShowUsage((s) => !s)} className="hover:text-slate-600">
                    {showUsage ? 'Скрыть расходы' : 'Расходы и токены'}
                  </button>
                  <button onClick={handleDownload} className="hover:text-slate-600">Скачать диалог</button>
                </>
              )}
            </div>
            <button onClick={handleEnd} className="hover:text-slate-600">Завершить сессию</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CrmRuntimeBanner({ runtime }) {
  const isTestMode = runtime.crm_submission_mode === 'test';
  const canCreate = runtime.crm_lead_intake_enabled && runtime.crm_test_phone_configured;
  return (
    <div className="border-b border-amber-200 bg-amber-50">
      <div className="mx-auto w-full max-w-3xl px-4 py-2 text-xs text-amber-900">
        <span className="font-semibold">
          {isTestMode ? 'Боевая CRM — тестовый режим.' : 'CRM workflow включён.'}
        </span>{' '}
        {isTestMode && canCreate
          ? `Реальный лид может быть создан только для «${runtime.crm_test_full_name}» и разрешённого тестового номера.`
          : 'Создание лидов заблокировано конфигурацией.'}
      </div>
    </div>
  );
}

function CompletedSurveyJson({ value }) {
  const [copied, setCopied] = useState(false);
  const formatted = JSON.stringify(value, null, 2);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(formatted);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section
      className="bubble-fade-in overflow-hidden rounded-xl border border-emerald-200 bg-slate-950 text-slate-100 shadow-sm"
      aria-labelledby="survey-json-title"
    >
      <div className="flex items-center justify-between border-b border-slate-700 px-4 py-3">
        <div>
          <h2 id="survey-json-title" className="text-sm font-semibold">Итоговый JSON</h2>
          <p className="mt-0.5 text-xs text-slate-400">Незапрошенные поля сохранены как null</p>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-md border border-slate-600 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
        >
          {copied ? 'Скопировано' : 'Копировать'}
        </button>
      </div>
      <pre className="max-h-96 overflow-auto p-4 text-xs leading-relaxed">
        <code>{formatted}</code>
      </pre>
    </section>
  );
}

function ChatBubble({ role, text, streaming }) {
  const isUser = role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} bubble-fade-in`}>
      <div
        className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-brand-600 text-white rounded-br-sm'
            : 'bg-white border border-slate-200 text-slate-800 rounded-bl-sm'
        }`}
      >
        {text}
        {streaming && <span className="inline-block w-1 h-4 ml-0.5 bg-slate-400 animate-pulse" />}
      </div>
    </div>
  );
}

function QuickReplies({ items, onPick }) {
  return (
    <div className="flex flex-wrap gap-2 bubble-fade-in">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onPick(item.value)}
          className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:border-brand-600"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function TypingIndicator({ label }) {
  return (
    <div className="flex items-center gap-2 text-xs text-slate-400 px-2">
      <span className="flex gap-1">
        <span className="typing-dot w-1.5 h-1.5 bg-slate-400 rounded-full" />
        <span className="typing-dot w-1.5 h-1.5 bg-slate-400 rounded-full" />
        <span className="typing-dot w-1.5 h-1.5 bg-slate-400 rounded-full" />
      </span>
      {label && <span>{label}</span>}
    </div>
  );
}

function UsagePanel({ usage }) {
  if (!usage) return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
      Загружаю расходы…
    </div>
  );
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
      <div className="flex justify-between font-medium text-slate-800">
        <span>Всего: {usage.total_tokens.toLocaleString('ru')} токенов</span>
        <span>${usage.total_cost_usd.toFixed(4)}</span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-slate-500">
        {Object.entries(usage.by_role || {}).map(([role, v]) => (
          <div key={role} className="flex justify-between">
            <span>{translateRole(role)}:</span>
            <span>{v.prompt_tokens + v.completion_tokens} ток / ${v.cost_usd.toFixed(4)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function translateRole(r) {
  return { conversation: 'разговор', extraction: 'разбор', opening: 'старт' }[r] || r;
}
