/**
 * @fileoverview AI-чат МБК — клиент виджета (разметка в
 * ai-chat-widget.blade.php, подключается только на главной; на остальных
 * страницах модуль сразу выходит).
 *
 * Протокол: POST /chat-api/session -> {session_id} + HttpOnly-кука;
 * GET /chat-api/session/{id}/stream[?message=...] -> SSE
 *   (chunk|bubbles|state|done|error);
 *   bubbles может нести quick_replies: [{label, value}] — кнопки под
 *   репликой бота;
 * GET /chat-api/session/{id} -> состояние с chat_history (восстановление).
 * /chat-api/ проксируется nginx'ом в стек mbk_ai
 * (см. docker/nginx/conf.d/default.conf).
 */

/** @private {string} Базовый путь REST/SSE API чата. */
const API = '/chat-api';

/** @private {string} Ключ localStorage, под которым хранится id сессии. */
const STORE_KEY = 'mbkAiChat.sid';
const DEBT_STORE_KEY = 'mbkDebtChat.sid';

/** @private {number} Предел длины отправляемого сообщения. */
const MAX_LEN = 2000;

/* Кнопки под репликой бота: бот присылает их полем quick_replies события
   bubbles и в состоянии сессии. Нажатие уходит обычным сообщением с текстом
   кнопки — тем же путём, что набранный текст. Поле ввода не блокируется. */
/** @private {number} Не больше стольки кнопок показывается под репликой. */
const MAX_CHIPS = 4;

/* Несколько пузырей одного ответа показываются по одному с паузой и
   индикатором печати между ними — иначе видно, что все реплики от машины
   и пришли одним залпом. Пауза перед пузырём — по длине его текста: база +
   мс/символ, с потолком. window.MBK_AI_CHAT_REVEAL_MS переопределяет паузу
   тем же числом для любого пузыря — тесты ставят туда 0. */
/** @private {number} База паузы перед следующим пузырём, мс. */
const REVEAL_BASE_MS = 500;
/** @private {number} Надбавка к паузе за каждый символ пузыря, мс. */
const REVEAL_PER_CHAR_MS = 30;
/** @private {number} Потолок паузы перед одним пузырём, мс. */
const REVEAL_MAX_MS = 2000;

/**
 * Клиент AI-чата: сессия, поток ответа (SSE) и разметка ленты сообщений
 * внутри корневого контейнера #mbk-ai-chat.
 */
class AiChatWidget {
  /**
   * @param {!HTMLElement} root Корневой контейнер виджета (#mbk-ai-chat).
   */
  constructor(root) {
    /** @private {!HTMLElement} */
    this.root_ = root;
    this.debtPage_ = root.dataset.scenario === 'debt';
    this.isDebt_ = this.debtPage_;
    // Session storage belongs to the surface that created this chat.
    this.storeKey_ = this.debtPage_ ? DEBT_STORE_KEY : STORE_KEY;
    this.viewportBound_ = false;
    this.requestGeneration_ = 0;
    this.recoveryTimer_ = null;
    this.followBottom_ = true;
    this.viewportSequence_ = 0;
    this.viewportAdjusting_ = false;
    this.debtHistoryLength_ = 0;
    this.pendingDebtTurn_ = null;
    this.onloaderState_ = null;
    this.onloaderExpected_ = typeof root.mbkResolveOnloader === 'function';
    this.onloaderBootstrapAttempted_ = false;
    this.onloaderUncertain_ = false;
    this.renderedOnloaderOffer_ = null;

    /** @private {!Object<string, ?Element>} Ссылки на узлы разметки виджета. */
    this.els_ = {
      launcher: root.querySelector('.mbkc-launcher'),
      badge: root.querySelector('.mbkc-badge'),
      feed: root.querySelector('.mbkc-feed'),
      feedInner: root.querySelector('.mbkc-feed-inner'),
      input: root.querySelector('.mbkc-input'),
      send: root.querySelector('.mbkc-send'),
      form: root.querySelector('.mbkc-inputbar'),
      lastMsg: root.querySelector('.mbkc-chat-item-last'),
      tabs: root.querySelectorAll('.mbkc-tab'),
      tabPanes: {
        chats: root.querySelector('[data-pane="chats"]'),
        files: root.querySelector('[data-pane="files"]'),
      },
    };

    /**
     * Состояние диалога.
     * @private {{
     *   sid: ?string,
     *   es: ?EventSource,
     *   busy: boolean,
     *   started: boolean,
     *   unread: number,
     *   draft: ?{row: !Element, textEl: !Element},
     *   draftText: string,
     *   gotBubbles: boolean,
     *   closed: boolean,
     *   revealing: boolean,
     *   revealTimer: ?number,
     *   revealSeq: number,
     *   pendingFinish: boolean,
     * }}
     */
    this.state_ = {
      sid: null,
      es: null,
      busy: false, // идёт стрим ответа
      // сессия инициализирована (история загружена или создана новая)
      started: false,
      unread: 0,
      draft: null, // элемент пузыря, наполняемый чанками
      draftText: '',
      gotBubbles: false,
      closed: false,
      revealing: false, // идёт показ пузырей одного ответа по одному
      revealTimer: null, // id паузы перед следующим пузырём
      revealSeq: 0, // снимок сверяют отложенные показы; смена — отмена
      // done/error пришли раньше, чем достоказались пузыри
      pendingFinish: false,
      recovering: false,
    };

    this.bindEvents_();
    this.setBusyUi_(false);
    this.bindViewport_();
    if (this.debtPage_) {
      this.root_.dataset.mode = 'widget';
      this.bindViewport_();
      const stored = this.loadSid_();
      if (stored) {
        this.state_.sid = stored;
        this.recoverDebt_();
      }
    }
  }

  /* ---------- утилиты ---------- */

  /**
   * Текущее время в формате ЧЧ:ММ для метки сообщения.
   * @return {string}
   * @private
   */
  now_() {
    const d = new Date();
    return ('0' + d.getHours()).slice(-2) + ':' +
        ('0' + d.getMinutes()).slice(-2);
  }

  /** @private */
  scrollDown_() {
    this.els_.feed.scrollTop = this.els_.feed.scrollHeight;
    if (this.isDebt_) this.followBottom_ = true;
  }

  /**
   * @param {string} pathsMarkup Разметка <path> внутри SVG.
   * @return {!SVGElement}
   * @private
   */
  svgIcon_(pathsMarkup) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.setAttribute('viewBox', '0 0 24 24');
    el.setAttribute('class', 'mbkc-icon mbkc-icon--sm mbkc-check');
    el.innerHTML = pathsMarkup;
    return el;
  }

  /**
   * Текст -> DOM с кликабельными ссылками (без innerHTML для текста).
   * @param {!Node} target
   * @param {string} text
   * @private
   */
  linkify_(target, text) {
    const re = /(https?:\/\/[^\s<>"')\]]+)/g;
    let idx = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > idx) {
        target.appendChild(
            document.createTextNode(text.slice(idx, m.index)));
      }
      const a = document.createElement('a');
      a.href = m[1];
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = m[1];
      target.appendChild(a);
      idx = m.index + m[1].length;
    }
    if (idx < text.length) {
      target.appendChild(document.createTextNode(text.slice(idx)));
    }
  }

  /* ---------- рендер сообщений ---------- */

  /**
   * @return {!HTMLElement}
   * @private
   */
  avatarEl_() {
    const wrap = document.createElement('div');
    wrap.className = 'mbkc-avatar mbkc-avatar--sm';
    const img = document.createElement('img');
    img.src = this.root_.dataset.avatar;
    img.alt = this.root_.dataset.operatorName;
    const dot = document.createElement('span');
    dot.className = 'mbkc-online';
    wrap.appendChild(img);
    wrap.appendChild(dot);
    return wrap;
  }

  /**
   * @param {string} role 'user' или 'bot'.
   * @param {string} text
   * @param {boolean=} withTime По умолчанию true; false — черновик без
   *     метки времени.
   * @param {boolean=} trackUnread По умолчанию true; false — не трогать бейдж
   *     непрочитанных и превью последнего сообщения (уже посчитаны отдельно,
   *     как при показе пузырей одного ответа по одному — см. bumpUnread_).
   * @return {{row: !HTMLElement, textEl: !HTMLElement}}
   * @private
   */
  addMessage_(role, text, withTime, trackUnread) {
    const follow = !this.isDebt_ || role === 'user' || this.nearBottom_();
    const row = document.createElement('div');
    row.className = 'mbkc-row ' +
        (role === 'user' ? 'mbkc-row--out' : 'mbkc-row--in');
    if (role !== 'user') row.appendChild(this.avatarEl_());

    const bubble = document.createElement('div');
    bubble.className = 'mbkc-bubble';
    const p = document.createElement('span');
    this.linkify_(p, text);
    bubble.appendChild(p);

    if (withTime !== false) {
      const meta = document.createElement('div');
      meta.className = 'mbkc-meta';
      const t = document.createElement('span');
      t.textContent = this.now_();
      meta.appendChild(t);
      if (role === 'user') {
        meta.appendChild(this.svgIcon_(
            '<path d="m4 12 4 4 6-6"/><path d="m10 12 4 4 6-6"/>'));
      }
      bubble.appendChild(meta);
    }

    row.appendChild(bubble);
    this.els_.feedInner.appendChild(row);
    if (follow) this.scrollDown_();

    if (role !== 'user' && trackUnread !== false) this.bumpUnread_(text);
    return {row, textEl: p};
  }

  /**
   * Бейдж непрочитанных и превью последнего сообщения в сайдбаре — считаются
   * по факту получения ответа бота, а не по видимому появлению пузыря в
   * ленте: при показе пузырей одного ответа по одному это отдельный вызов,
   * до начала пауз (см. revealBubbles_), иначе счётчик отставал бы от них.
   * @param {string} text
   * @private
   */
  bumpUnread_(text) {
    if (this.els_.lastMsg) this.els_.lastMsg.textContent = text;
    if (this.root_.dataset.mode === 'button') {
      this.state_.unread++;
      this.els_.badge.textContent = String(this.state_.unread);
      this.els_.badge.classList.add('mbkc-visible');
    }
  }

  /**
   * @param {string} text
   * @param {(function(): void)=} retry Если задан, рисуется кнопка «Повторить».
   * @private
   */
  addSystem_(text, retry) {
    const el = document.createElement('div');
    el.className = 'mbkc-system' + (retry ? ' mbkc-system--error' : '');
    el.appendChild(document.createTextNode(text + (retry ? ' ' : '')));
    if (retry) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Повторить';
      b.addEventListener('click', () => {
        el.remove();
        retry();
      });
      el.appendChild(b);
    }
    this.els_.feedInner.appendChild(el);
    this.scrollDown_();
  }

  /** @private */
  showTyping_() {
    this.hideTyping_();
    const row = document.createElement('div');
    row.className = 'mbkc-row mbkc-row--in';
    row.dataset.typing = '1';
    row.appendChild(this.avatarEl_());
    const bubble = document.createElement('div');
    bubble.className = 'mbkc-bubble mbkc-typing';
    bubble.innerHTML = '<span></span><span></span><span></span>';
    row.appendChild(bubble);
    this.els_.feedInner.appendChild(row);
    this.scrollDown_();
  }

  /** @private */
  hideTyping_() {
    const t = this.els_.feedInner.querySelector('[data-typing]');
    if (t) t.remove();
  }

  /* ---------- показ пузырей одного ответа по одному ---------- */

  /**
   * Пауза перед показом пузыря — соразмерна длине его текста (база + мс за
   * символ, с потолком). window.MBK_AI_CHAT_REVEAL_MS переопределяет её
   * фиксированным числом для любого пузыря — так тесты отключают паузы (0).
   * @param {string} text
   * @return {number}
   * @private
   */
  revealDelay_(text) {
    const override = (typeof window !== 'undefined') ?
      window.MBK_AI_CHAT_REVEAL_MS : undefined;
    if (typeof override === 'number') return override;
    return Math.min(REVEAL_MAX_MS,
        REVEAL_BASE_MS + (text || '').length * REVEAL_PER_CHAR_MS);
  }

  /**
   * Отменяет ещё не показанные пузыри текущего ответа, если они есть:
   * снимает таймер паузы и сдвигает revealSeq, поэтому колбэк отложенного
   * шага, уже поставленный в очередь, при срабатывании увидит несовпадение
   * и ничего не сделает. Вызывается и при открытии нового потока (новое
   * сообщение уходит боту только когда клиент не busy, то есть прошлый
   * ответ уже целиком показан), и в начале обработки нового события bubbles
   * (защита от повторного bubbles в рамках одного потока) — так недопоказанные
   * пузыри старого ответа не всплывают поверх нового.
   * @private
   */
  cancelPendingReveal_() {
    if (this.state_.revealTimer) {
      clearTimeout(this.state_.revealTimer);
      this.state_.revealTimer = null;
    }
    this.state_.revealSeq++;
    this.state_.revealing = false;
    this.state_.pendingFinish = false;
  }

  /**
   * Показывает пузыри одного ответа по одному: первый — сразу (он заменяет
   * черновик потока), следующие — после паузы с индикатором печати в ней.
   * После последнего — кнопки-подсказки и разблокировка ввода.
   * @param {!Array<string>} bubbles
   * @param {*} quickReplies
   * @private
   */
  revealBubbles_(bubbles, quickReplies) {
    const seq = this.state_.revealSeq;
    this.state_.revealing = true;

    const showNext = (index) => {
      // отменено новым ответом/потоком
      if (this.state_.revealSeq !== seq) return;

      if (this.state_.draft) {
        this.state_.draft.row.remove();
        this.state_.draft = null;
        this.state_.draftText = '';
      }
      this.hideTyping_();
      this.addMessage_('bot', bubbles[index], true, false);

      if (index === bubbles.length - 1) {
        this.state_.revealing = false;
        this.renderQuickReplies_(quickReplies);
        /* Ввод разблокируется только когда оба условия выполнены: это
           последний пузырь ЛОГИЧЕСКИ (событие done/error уже пришло — иначе
           бот ещё может прислать больше) и он же последний ВИЗУАЛЬНО (мы его
           только что дорисовали). Если done уже случился, пока рисовались
           пузыри, finishStream_ отложил его этим флагом — довершаем здесь. */
        if (this.state_.pendingFinish) {
          this.state_.pendingFinish = false;
          this.state_.busy = false;
          this.setBusyUi_(false);
          if (this.root_.dataset.mode !== 'button') this.els_.input.focus();
        }
        return;
      }

      this.showTyping_();
      this.state_.revealTimer = setTimeout(() => {
        this.state_.revealTimer = null;
        showNext(index + 1);
      }, this.revealDelay_(bubbles[index + 1]));
    };

    showNext(0);
  }

  /* ---------- быстрые ответы (чипсы) ---------- */

  /** @private */
  clearQuickReplies_() {
    const rows = this.els_.feedInner.querySelectorAll('.mbkc-chips');
    for (let i = 0; i < rows.length; i++) rows[i].remove();
  }

  /**
   * @param {*} items Список {label, value} из quick_replies; мусор
   *     отбрасывается.
   * @private
   */
  renderQuickReplies_(items) {
    this.clearQuickReplies_();
    if (this.state_.closed || !Array.isArray(items)) return;
    const row = document.createElement('div');
    row.className = 'mbkc-chips';
    for (let i = 0; i < items.length && row.childNodes.length < MAX_CHIPS;
      i++) {
      const item = items[i];
      if (!item || typeof item.label !== 'string' ||
        typeof item.value !== 'string') continue;
      if (!item.label.trim() || !item.value.trim()) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mbkc-chip';
      button.textContent = item.label;
      button.setAttribute('data-value', item.value);
      if (this.isDebt_) button.disabled = this.state_.busy || this.state_.recovering || this.state_.closed;
      row.appendChild(button);
    }
    if (!row.childNodes.length) return;
    this.els_.feedInner.appendChild(row);
    this.scrollDown_();
  }

  /* ---------- сессия ---------- */

  /**
   * @param {string} sid
   * @private
   */
  saveSid_(sid) {
    try {
      localStorage.setItem(this.storeKey_, sid);
    } catch (e) {}
  }

  /**
   * @return {?string}
   * @private
   */
  loadSid_() {
    try {
      return localStorage.getItem(this.storeKey_);
    } catch (e) {
      return null;
    }
  }

  /** @private */
  dropSid_() {
    try {
      localStorage.removeItem(this.storeKey_);
    } catch (e) {}
  }

  /**
   * Рекламная атрибуция посетителя — тем же $.utmParam/$.utmLanding, что и
   * анкеты (_defaults.js). Бот сохранит её в сессии и положит в лид для CRM.
   * @return {?Object}
   * @private
   */
  collectUtm_() {
    if (typeof $ === 'undefined' || !$.utmParam) return null;
    /* Куки счётчиков — сырыми значениями, как шлют анкетные формы
       (_send-form.js: ga/ym): без них чат-лид выпадает из сквозной
       аналитики. */
    const jar = (typeof Cookies !== 'undefined' && Cookies.get) ?
      Cookies.get() : {};
    return {
      utm_source: $.utmParam('utm_source') || '',
      utm_medium: $.utmParam('utm_medium') || '',
      utm_campaign: $.utmParam('utm_campaign') || '',
      utm_term: $.utmParam('utm_term') || '',
      utm_content: $.utmParam('utm_content') || '',
      landing: ($.utmLanding && $.utmLanding()) || '',
      ga_client_id: jar._ga || '',
      ym_client_id: jar._ym_uid || '',
    };
  }

  /**
   * @return {!Promise<!Object>}
   * @private
   */
  createSession_() {
    const body = {utm: this.collectUtm_()};
    if (this.debtPage_) body.requested_scenario = 'debt';
    if (this.onloaderExpected_) body.onloader_expected = true;
    return fetch(API + '/session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
      credentials: 'same-origin',
    }).then((r) => {
      if (r.status === 429) throw new Error('rate');
      if (this.isDebt_ && r.status === 503) throw new Error('unavailable');
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    }).then((data) => {
      if (this.debtPage_) this.validateDebtEnvelope_(data);
      return data;
    });
  }

  /**
   * @param {string} sid
   * @return {!Promise<!Object>}
   * @private
   */
  restoreSession_(sid) {
    return fetch(API + '/session/' + encodeURIComponent(sid), {
      credentials: 'same-origin',
    }).then((r) => {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    });
  }

  /**
   * Инициализация диалога при первом открытии:
   * либо восстановление истории, либо новая сессия с приветствием.
   * @private
   */
  startDialog_() {
    if (this.state_.busy || (this.state_.started && !this.isDebt_)) return;
    const stored = this.state_.sid || this.loadSid_();
    if (stored) {
      this.state_.sid = stored;
      this.recoverDebt_(true);
      return;
    }
    if (this.debtPage_) return this.startDebtDialog_();
    this.stopDebtRequest_();
    const generation = this.requestGeneration_;
    this.state_.busy = true;
    this.setBusyUi_(true);
    this.createSession_().then((data) => {
      if (generation !== this.requestGeneration_) return;
      this.validateSessionIdentity_(data);
      this.state_.sid = data.session_id;
      this.saveSid_(data.session_id);
      if (this.onloaderExpected_) {
        if (data.turn_in_progress !== false || !data.state || !data.state.onloader) throw new Error('onloader');
        this.applyServerState_(data.state);
        this.bootstrapOnloader_(true);
        return;
      }
      if (data.state && data.state.onloader) {
        this.applyServerState_(data.state);
        this.debtReady_();
        return;
      }
      if (this.hasDebtPolicy_(data.state)) {
        this.applyServerState_(data.state);
        this.debtReady_();
        return;
      }
      this.state_.started = true;
      this.openStream_(null);
    }).catch((error) => {
      if (generation !== this.requestGeneration_) return;
      this.state_.busy = false;
      this.setBusyUi_(false);
      this.addSystem_(error.message === 'rate' ?
        'Превышен лимит новых диалогов. Попробуйте немного позже.' :
        'Не удалось подключиться к чату.', () => this.startDialog_());
    });
  }

  /* ---------- встроенная долговая консультация ---------- */

  /** Public offer data is a closed projection. Authority remains in the owning backend. */
  readOnloaderState_(state) {
    const value = state && state.onloader;
    if (value === undefined || value === null) {
      if (this.onloaderState_) throw new Error('onloader');
      return null;
    }
    const statuses = ['pending', 'awaiting_consent', 'declined', 'signal_pending', 'queued',
      'rejected', 'outcome_unknown', 'stale', 'forbidden', 'unavailable'];
    if (!value || value.mode !== 'local' || !statuses.includes(value.status) ||
        typeof value.contact_known !== 'boolean') throw new Error('onloader');
    const result = {status: value.status, mode: 'local', contact_known: value.contact_known};
    if (value.decision !== undefined && value.decision !== null && !['accept', 'decline'].includes(value.decision)) throw new Error('onloader');
    result.decision = value.decision || null;
    if (value.offer !== undefined && value.offer !== null) {
      const offer = value.offer;
      for (const key of ['offer_id', 'version', 'consent_version', 'partner_name']) {
        if (typeof offer[key] !== 'string' || !offer[key].trim() || offer[key].length > 160) throw new Error('onloader');
      }
      result.offer = {offer_id: offer.offer_id, version: offer.version,
        consent_version: offer.consent_version, partner_name: offer.partner_name};
    }
    if (typeof value.presentation_id === 'string' && value.presentation_id.length <= 160) result.presentation_id = value.presentation_id;
    if (value.status === 'awaiting_consent' && (!result.offer || !result.presentation_id || !result.contact_known)) throw new Error('onloader');
    return result;
  }

  renderOnloader_() {
    const value = this.onloaderState_;
    this.renderedOnloaderOffer_ = null;
    if (!value) return;
    const card = document.createElement('section');
    card.className = 'mbkc-onloader';
    card.setAttribute('aria-label', 'Передача текущей заявки');
    const text = (content, className) => {
      const element = document.createElement('p');
      if (className) element.className = className;
      element.textContent = content;
      card.appendChild(element);
    };
    const action = (name, label) => {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.action = name; button.textContent = label;
      button.disabled = this.state_.busy || this.state_.recovering;
      card.appendChild(button);
    };
    text('Локальная проверка · передача моделируется', 'mbkc-onloader-mode');
    if (value.status === 'awaiting_consent' && !this.onloaderUncertain_) {
      text('Контакт уже есть в анкете. Повторно вводить телефон не нужно.');
      text('Разрешаю передать сведения текущей заявки компании «' + value.offer.partner_name + '» для консультации.');
      text('Версия согласия ' + value.offer.consent_version);
      this.renderedOnloaderOffer_ = {sid: this.state_.sid, presentation_id: value.presentation_id,
        offer_id: value.offer.offer_id, offer_version: value.offer.version, consent_version: value.offer.consent_version};
      action('onloader-accept', 'Разрешаю передачу');
      action('onloader-decline', 'Не передавать');
    } else {
      const labels = {
        pending: 'Проверяем текущую заявку.', declined: 'Отказ сохранён. Передача остановлена.',
        queued: 'Согласие сохранено. Тестовая передача поставлена в очередь.',
        signal_pending: 'Результат передачи ещё проверяется.', outcome_unknown: 'Результат передачи пока неизвестен.',
        rejected: 'Предложение сейчас недоступно.', stale: 'Сведения о предложении нужно обновить.',
        forbidden: 'Не удалось подтвердить доступ к текущей заявке.', unavailable: 'Проверка заявки сейчас недоступна.',
        awaiting_consent: 'Результат предыдущего действия не подтверждён. Сначала проверьте состояние.',
      };
      text(value.decision === 'decline' && ['signal_pending', 'outcome_unknown'].includes(value.status) ?
        'Отказ сохранён. Подтверждение обработки пока не получено.' : labels[value.status]);
      if (['signal_pending', 'outcome_unknown'].includes(value.status) || this.onloaderUncertain_) action('onloader-reconcile', 'Проверить результат');
      if (typeof this.root_.mbkResolveOnloader === 'function' && (this.onloaderUncertain_ ||
          ['pending', 'stale', 'forbidden', 'unavailable', 'rejected'].includes(value.status))) action('onloader-refresh', 'Обновить предложение');
    }
    this.els_.feedInner.appendChild(card);
  }

  /** Minting is supplied by authenticated host glue and held only in this promise chain. */
  bootstrapOnloader_(alreadyBusy) {
    if (!this.state_.sid || (!alreadyBusy && (this.state_.busy || this.state_.recovering))) return;
    if (typeof this.root_.mbkResolveOnloader !== 'function') return;
    this.onloaderBootstrapAttempted_ = true;
    this.onloaderOperation_('', null, (sid) => Promise.resolve(this.root_.mbkResolveOnloader(sid)).then((capability) => {
      if (typeof capability !== 'string' || !capability || capability.length > 4096) throw new Error('onloader');
      return {capability};
    }));
  }

  decideOnloader_(decision) {
    const value = this.onloaderState_, shown = this.renderedOnloaderOffer_;
    if (this.state_.busy || this.state_.recovering || this.onloaderUncertain_ || !shown ||
        !value || value.status !== 'awaiting_consent' || !value.contact_known ||
        shown.sid !== this.state_.sid || shown.presentation_id !== value.presentation_id ||
        shown.offer_id !== value.offer.offer_id || shown.offer_version !== value.offer.version ||
        shown.consent_version !== value.offer.consent_version) return;
    const {sid, ...binding} = shown;
    this.onloaderOperation_('decision', {...binding, decision});
  }

  /** A failed decision is observed by GET. Its command is never automatically replayed. */
  onloaderOperation_(operation, body, prepare) {
    if (!this.state_.sid || (!prepare && (this.state_.busy || this.state_.recovering))) return;
    this.stopDebtRequest_();
    const generation = this.requestGeneration_, sid = this.state_.sid;
    this.state_.busy = true; this.state_.recovering = false; this.setBusyUi_(true);
    Promise.resolve().then(() => prepare ? prepare(sid) : body).then((payload) => {
      if (generation !== this.requestGeneration_ || sid !== this.state_.sid) return null;
      return fetch(API + '/session/' + encodeURIComponent(sid) + '/onloader' + (operation ? '/' + operation : ''), {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
    }).then((response) => {
      if (!response || generation !== this.requestGeneration_) return null;
      if (!response.ok) throw new Error('http ' + response.status);
      return response.json();
    }).then((data) => {
      if (!data || generation !== this.requestGeneration_ || sid !== this.state_.sid) return;
      this.validateSessionIdentity_(data, sid);
      if (data.turn_in_progress !== false || !data.state || !data.state.onloader) throw new Error('onloader');
      if (!operation) this.onloaderUncertain_ = false;
      this.applyServerState_(data.state); this.debtReady_();
    }).catch(() => {
      if (generation !== this.requestGeneration_) return;
      if (operation === 'decision') this.onloaderUncertain_ = true;
      this.recoverDebt_();
    });
  }

  /** Keep a user's scroll position while they read older messages. */
  nearBottom_() {
    const feed = this.els_.feed;
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  }

  /** The visual viewport follows the on-screen keyboard without focusing it on load. */
  bindViewport_() {
    if (this.viewportBound_) return;
    this.viewportBound_ = true;
    const update = () => {
      const follow = this.followBottom_;
      const top = this.els_.feed.scrollTop;
      const sequence = ++this.viewportSequence_;
      this.viewportAdjusting_ = true;
      const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      if (height > 0) document.documentElement.style.setProperty('--mbkc-viewport-height', height + 'px');
      const topOffset = window.visualViewport ? window.visualViewport.offsetTop : 0;
      document.documentElement.style.setProperty('--mbkc-viewport-top', (topOffset || 0) + 'px');
      const afterLayout = () => {
        if (sequence !== this.viewportSequence_) return;
        if (follow) this.scrollDown_();
        else this.els_.feed.scrollTop = top;
        this.followBottom_ = follow;
        this.viewportAdjusting_ = false;
      };
      if (window.requestAnimationFrame) window.requestAnimationFrame(afterLayout);
      else setTimeout(afterLayout, 0);
    };
    this.els_.feed.addEventListener('scroll', () => {
      if (!this.viewportAdjusting_) this.followBottom_ = this.nearBottom_();
    });
    window.addEventListener('resize', update);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', update);
      window.visualViewport.addEventListener('scroll', update);
    }
    update();
  }

  /** A saved id selects a session. The owning HttpOnly cookie authorizes the read. */
  validateDebtEnvelope_(data, expectedSid) {
    this.validateSessionIdentity_(data, expectedSid);
    this.validateDebtState_(data.state);
  }

  validateSessionIdentity_(data, expectedSid) {
    if (!data || typeof data.session_id !== 'string' ||
        !/^[a-zA-Z0-9_-]{1,128}$/.test(data.session_id) ||
        (expectedSid && data.session_id !== expectedSid)) throw new Error('session');
  }

  hasDebtPolicy_(state) {
    return !!state && state.questionnaire_policy !== undefined && state.questionnaire_policy !== null;
  }

  validateDebtState_(state) {
    const policy = state && state.questionnaire_policy;
    if (!policy || policy.version !== 1 ||
        !['debt', 'clarify', 'cancelled'].includes(policy.mode) ||
        !Array.isArray(state.chat_history)) throw new Error('scenario');
  }

  /** Replace optimistic/partial UI only with the server's canonical history. */
  applyServerState_(state) {
    const onloader = this.readOnloaderState_(state);
    if (this.isDebt_ || this.hasDebtPolicy_(state)) {
      this.validateDebtState_(state);
      // Server policy promotes transport inside the existing identity and history.
      this.isDebt_ = true;
      this.bindViewport_();
    } else if (!state || !Array.isArray(state.chat_history)) {
      throw new Error('session');
    }
    const feed = this.els_.feed;
    const follow = this.nearBottom_();
    const oldTop = feed.scrollTop;
    this.cancelPendingReveal_();
    this.els_.feedInner.textContent = '';
    for (const item of state.chat_history) {
      if (item && ['user', 'assistant'].includes(item.role) && typeof item.content === 'string') {
        this.addMessage_(item.role === 'user' ? 'user' : 'bot', item.content, true, false);
      }
    }
    this.state_.closed = state.closed === true;
    this.state_.started = true;
    this.onloaderState_ = onloader;
    // A canonical terminal outcome resolves this tab's transport uncertainty.
    if (onloader && ['queued', 'declined', 'rejected'].includes(onloader.status)) this.onloaderUncertain_ = false;
    this.confirmDebtTurn_(state, false);
    this.debtHistoryLength_ = state.chat_history.length;
    if (onloader) {
      this.clearQuickReplies_();
      this.renderOnloader_();
    } else this.renderQuickReplies_(state.quick_replies || []);
    const welcome = this.root_.querySelector('.mbkc-welcome');
    if (welcome) welcome.hidden = true;
    if (follow) this.scrollDown_();
    else {
      feed.scrollTop = oldTop;
      this.followBottom_ = false;
    }
  }

  /** All status text and action labels are local text nodes. */
  debtStatus_(message, actions) {
    const previous = this.root_.querySelector('.mbkc-status');
    if (previous) previous.remove();
    if (!message) return;
    const status = document.createElement('div');
    status.className = 'mbkc-system mbkc-status';
    status.setAttribute('role', 'status');
    status.appendChild(document.createTextNode(message));
    for (const action of actions || []) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.action = action.action;
      button.textContent = action.label;
      status.appendChild(button);
    }
    this.els_.feedInner.appendChild(status);
    this.scrollDown_();
  }

  stopDebtRequest_() {
    this.requestGeneration_++;
    if (this.recoveryTimer_) clearTimeout(this.recoveryTimer_);
    this.recoveryTimer_ = null;
    if (this.state_.es) this.state_.es.close();
    this.state_.es = null;
    this.cancelPendingReveal_();
    this.hideTyping_();
  }

  debtReady_() {
    this.state_.busy = false;
    this.state_.recovering = false;
    this.hideTyping_();
    this.setBusyUi_(false);
    const restored = this.restoreDebtDraft_();
    if (this.state_.closed) {
      this.debtStatus_('Диалог завершён.', [{action: 'new-debt', label: 'Начать новый диалог'}]);
    } else if (restored) {
      this.debtStatus_('Ответ на сообщение не подтверждён. Текст сохранён в поле ввода. Проверьте историю перед отправкой.');
    }
  }

  /** Keep an unconfirmed text in this tab's input, never in persistent storage or an automatic retry. */
  restoreDebtDraft_() {
    const pending = this.pendingDebtTurn_;
    if (!pending || this.els_.input.value.trim()) return false;
    this.els_.input.value = pending.text;
    pending.restored = true;
    this.setBusyUi_(this.state_.busy);
    return true;
  }

  /** A GET needs matching text. The current successful SSE turn also handles masked contact text. */
  confirmDebtTurn_(state, ownCompletedTurn) {
    const pending = this.pendingDebtTurn_;
    if (!pending) return;
    const turns = state.chat_history.slice(pending.historyLength).filter((item) => item && item.role === 'user');
    if (!turns.some((item) => item.content === pending.text) && !(ownCompletedTurn && turns.length)) return;
    if (pending.restored && this.els_.input.value === pending.text) this.els_.input.value = '';
    this.pendingDebtTurn_ = null;
  }

  debtFailure_(error) {
    this.state_.busy = false;
    this.state_.recovering = !!this.state_.sid;
    this.restoreDebtDraft_();
    this.hideTyping_();
    this.setBusyUi_(false);
    const messages = {
      rate: 'Сейчас слишком много новых диалогов. Попробуйте немного позже.',
      unavailable: 'Консультация сейчас недоступна. Попробуйте позже.',
      'http 403': 'Не удалось открыть сохранённый диалог. Можно начать новый.',
      'http 401': 'Не удалось открыть сохранённый диалог. Можно начать новый.',
      'http 404': 'Сохранённый диалог больше недоступен. Можно начать новый.',
      scenario: 'Не удалось восстановить сохранённый диалог. Можно начать новый диалог.',
      session: 'Не удалось подтвердить сохранённый диалог. Можно начать новый.',
    };
    const text = messages[error.message] || 'Связь с чатом прервалась. Проверьте историю перед продолжением.';
    const actions = this.state_.sid ? [
      {action: 'recover-debt', label: 'Проверить историю'},
      {action: 'new-debt', label: 'Начать новый диалог'},
    ] : [{action: 'start-debt', label: 'Попробовать снова'}];
    this.debtStatus_(text, actions);
  }

  /** POST is made only after Start or first Submit. It already contains the first question. */
  startDebtDialog_(firstMessage) {
    if (this.state_.busy || this.state_.recovering || this.state_.closed) return;
    if (this.state_.sid) return;
    this.stopDebtRequest_();
    const generation = this.requestGeneration_;
    this.state_.busy = true;
    this.setBusyUi_(true);
    this.debtStatus_('Подключаемся к консультации…');
    this.createSession_().then((data) => {
      if (generation !== this.requestGeneration_) return;
      this.state_.sid = data.session_id;
      this.saveSid_(data.session_id);
      this.applyServerState_(data.state);
      this.debtReady_();
      if (firstMessage && this.sendText_(firstMessage) && this.els_.input.value.trim() === firstMessage) {
        this.els_.input.value = '';
      }
    }).catch((error) => {
      if (generation === this.requestGeneration_) this.debtFailure_(error);
    });
  }

  /** Explicit restart retains a draft but never resends an interrupted message. */
  newDebtDialog_() {
    this.stopDebtRequest_();
    this.dropSid_();
    this.state_.sid = null;
    this.state_.started = false;
    this.state_.closed = false;
    this.state_.busy = false;
    this.state_.recovering = false;
    this.pendingDebtTurn_ = null;
    this.debtHistoryLength_ = 0;
    this.onloaderState_ = null;
    this.onloaderBootstrapAttempted_ = false;
    this.onloaderUncertain_ = false;
    this.renderedOnloaderOffer_ = null;
    this.isDebt_ = this.debtPage_;
    this.els_.feedInner.textContent = '';
    if (this.debtPage_) this.startDebtDialog_();
    else this.startDialog_();
  }

  /** Reconcile by GET only. A pending server turn is checked at most three times. */
  recoverDebt_(allowOpening) {
    if (!this.state_.sid) return;
    this.stopDebtRequest_();
    const generation = this.requestGeneration_;
    const sid = this.state_.sid;
    this.state_.busy = true;
    this.state_.recovering = true;
    this.setBusyUi_(true);
    this.debtStatus_('Проверяем историю диалога…');
    const read = (remaining) => {
      if (generation !== this.requestGeneration_) return;
      this.restoreSession_(sid).then((data) => {
        if (generation !== this.requestGeneration_) return;
        this.validateSessionIdentity_(data, sid);
        if (typeof data.turn_in_progress !== 'boolean') throw new Error('session');
        this.applyServerState_(data.state);
        if (data.turn_in_progress === true) {
          if (remaining > 0) {
            this.debtStatus_('Ответ ещё готовится…');
            this.recoveryTimer_ = setTimeout(() => read(remaining - 1), 500);
          } else {
            this.state_.busy = false;
            this.setBusyUi_(false);
            this.debtStatus_('Ответ ещё готовится. Проверьте историю чуть позже.', [
              {action: 'recover-debt', label: 'Проверить историю'},
            ]);
          }
          return;
        }
        if (!this.onloaderState_ && this.onloaderExpected_ && !this.onloaderBootstrapAttempted_) {
          this.bootstrapOnloader_(true);
          return;
        }
        this.debtReady_();
        if (allowOpening && !this.isDebt_ && !this.onloaderState_ && !data.state.closed &&
            !data.state.chat_history.length && !this.pendingDebtTurn_) this.openStream_(null);
      }).catch((error) => {
        if (generation === this.requestGeneration_) this.debtFailure_(error);
      });
    };
    read(2);
  }

  openDebtStream_(message) {
    this.stopDebtRequest_();
    this.pendingDebtTurn_ = {text: message, historyLength: this.debtHistoryLength_, restored: false};
    const generation = this.requestGeneration_;
    this.state_.busy = true;
    this.setBusyUi_(true);
    this.showTyping_();
    const sid = this.state_.sid;
    const url = API + '/session/' + encodeURIComponent(sid) + '/stream?message=' + encodeURIComponent(message);
    const es = new EventSource(url);
    this.state_.es = es;
    let committed = false;
    let responseState = null;
    const current = () => this.state_.es === es && generation === this.requestGeneration_;
    es.addEventListener('bubbles', (event) => {
      if (!current() || committed) return;
      try {
        const payload = JSON.parse(event.data);
        if (!Array.isArray(payload.bubbles)) return;
        this.hideTyping_();
        for (const text of payload.bubbles) {
          if (typeof text === 'string') this.addMessage_('bot', text);
        }
      } catch (error) {}
    });
    es.addEventListener('state', (event) => {
      if (!current()) return;
      try {
        const payload = JSON.parse(event.data);
        if (payload.session_id !== sid) throw new Error('session');
        this.applyServerState_(payload.state);
        responseState = payload.state;
        committed = true;
      } catch (error) {
        this.recoverDebt_();
      }
    });
    es.addEventListener('done', () => {
      if (!current()) return;
      if (!committed) return this.recoverDebt_();
      this.confirmDebtTurn_(responseState, true);
      es.close();
      this.state_.es = null;
      this.debtReady_();
    });
    es.addEventListener('error', () => {
      if (!current()) return;
      // A GET can observe a committed answer. Replaying this message cannot.
      this.recoverDebt_();
    });
  }

  /* ---------- SSE ---------- */

  /**
   * Открывает SSE-поток ответа бота.
   * @param {?string} message null — пустое подключение (бот сам поздоровается).
   * @private
   */
  openStream_(message) {
    if (this.isDebt_ || this.onloaderState_) return this.openDebtStream_(message);
    if (this.state_.es) {
      this.state_.es.close();
      this.state_.es = null;
    }
    this.cancelPendingReveal_();
    const generation = ++this.requestGeneration_;
    if (message) this.pendingDebtTurn_ = {text: message, historyLength: this.debtHistoryLength_, restored: false};
    this.state_.busy = true;
    this.state_.gotBubbles = false;
    this.state_.draft = null;
    this.state_.draftText = '';
    this.setBusyUi_(true);
    this.showTyping_();

    let url = API + '/session/' + encodeURIComponent(this.state_.sid) +
        '/stream';
    if (message) url += '?message=' + encodeURIComponent(message);

    const es = new EventSource(url);
    this.state_.es = es;
    let responseState = null;
    const current = () => this.state_.es === es && generation === this.requestGeneration_;

    es.addEventListener('chunk', (ev) => {
      if (!current() || responseState) return;
      let text = '';
      try {
        text = JSON.parse(ev.data).text || '';
      } catch (e) {}
      if (!text) return;
      if (!this.state_.draft) {
        this.hideTyping_();
        this.state_.draft = this.addMessage_('bot', '', false);
      }
      this.state_.draftText += text;
      this.state_.draft.textEl.textContent = this.state_.draftText;
      this.scrollDown_();
    });

    es.addEventListener('bubbles', (ev) => {
      if (!current() || responseState) return;
      let payload = {};
      try {
        payload = JSON.parse(ev.data) || {};
      } catch (e) {}
      const bubbles = payload.bubbles || [];
      if (!bubbles.length) return;
      this.state_.gotBubbles = true;
      /* Новый ответ отменяет недопоказанные пузыри прежнего — на случай
         повторного bubbles в рамках одного потока. */
      this.cancelPendingReveal_();
      /* Бейдж и превью последнего сообщения — по факту получения всего
         ответа, не по темпу его показа в ленте (см. bumpUnread_). */
      for (let i = 0; i < bubbles.length; i++) this.bumpUnread_(bubbles[i]);
      this.revealBubbles_(bubbles, payload.quick_replies || []);
    });

    es.addEventListener('state', (ev) => {
      if (!current()) return;
      try {
        const payload = JSON.parse(ev.data);
        if (payload.session_id !== this.state_.sid) throw new Error('session');
        const state = payload.state;
        this.applyServerState_(state);
        this.state_.draft = null;
        this.state_.draftText = '';
        responseState = state;
        if (this.isDebt_ || this.onloaderState_) return;
        /* Нужно согласие по офер-странице: кладём session_id (ID лида у бота)
           в куку. Клиент уйдёт на нашу офер-страницу того же домена, нажмёт
           «согласен» — кука уедет с запросом кнопки, сайт (finishAutoConfirm)
           опознает чатовый лид и форварднёт боту. Кросс-доменная Tilda куку
           не видит — там id едет GET-параметром в самой ссылке. */
        const wf = state && state.crm_workflow;
        if (wf && wf.need_site_consent === true && this.state_.sid) {
          document.cookie = 'mbk_chat_consent=' +
              encodeURIComponent(this.state_.sid) +
              '; path=/; max-age=1800; samesite=lax';
        }
        if (state && Array.isArray(state.quick_replies) &&
          !state.quick_replies.length) {
          this.clearQuickReplies_();
        }
        if (state && state.closed) {
          this.state_.closed = true;
          this.clearQuickReplies_();
          this.debtStatus_('Диалог завершён.', [{action: 'new-debt', label: 'Начать новый диалог'}]);
        }
      } catch (e) {
        this.recoverDebt_();
      }
    });

    es.addEventListener('done', () => {
      if (!current()) return;
      if (this.isDebt_ || this.onloaderState_) {
        if (!responseState) return this.recoverDebt_();
        this.confirmDebtTurn_(responseState, true);
        es.close();
        this.state_.es = null;
        this.debtReady_();
        return;
      }
      if (responseState) this.confirmDebtTurn_(responseState, true);
      this.finishStream_();
    });

    es.addEventListener('error', () => {
      if (!current()) return;
      // The server may have pinned debt policy before the connection was lost.
      this.recoverDebt_();
    });
  }

  /**
   * Завершает текущий SSE-поток и возвращает интерфейс в готовность к вводу.
   * @private
   */
  finishStream_() {
    if (this.state_.es) {
      this.state_.es.close();
      this.state_.es = null;
    }
    /* Пузыри ответа ещё показываются по одному — доводит revealBubbles_;
       она снимет busy и разблокирует ввод, когда покажет последний. */
    if (this.state_.revealing) {
      this.state_.pendingFinish = true;
      return;
    }
    this.hideTyping_();
    /* если финальные пузыри так и не пришли, оставляем стрим-черновик как
       есть */
    if (this.state_.draft && this.state_.gotBubbles) {
      this.state_.draft.row.remove();
    }
    this.state_.draft = null;
    this.state_.busy = false;
    this.setBusyUi_(false);
    if (this.root_.dataset.mode !== 'button') this.els_.input.focus();
  }

  /* ---------- отправка ---------- */

  /**
   * @param {boolean} busy
   * @private
   */
  setBusyUi_(busy) {
    const waiting = this.onloaderState_ && ['pending', 'stale', 'forbidden', 'unavailable'].includes(this.onloaderState_.status);
    this.els_.send.disabled = busy || this.state_.recovering || this.state_.closed || waiting || !this.els_.input.value.trim();
    this.els_.input.disabled = !!this.state_.closed || !!this.state_.recovering || !!waiting;
    for (const action of this.root_.querySelectorAll('[data-action^="onloader-"]')) action.disabled = !!busy || this.state_.recovering;
    if (this.isDebt_) {
      this.root_.setAttribute('aria-busy', busy ? 'true' : 'false');
      for (const start of this.root_.querySelectorAll('[data-action="start-debt"]')) {
        start.disabled = !!busy || !!this.state_.recovering || !!this.state_.sid;
      }
      for (const chip of this.root_.querySelectorAll('.mbkc-chip')) {
        chip.disabled = !!busy || !!this.state_.recovering || !!this.state_.closed;
      }
    }
  }

  /**
   * Одна дорога сообщения для поля ввода и для кнопок.
   * @param {string} raw
   * @return {boolean} false, если отправлять нельзя: пусто, идёт ответ,
   *     диалог закрыт, нет сессии.
   * @private
   */
  sendText_(raw) {
    let text = (raw || '').trim();
    if (this.isDebt_ && text && !this.state_.started && !this.state_.sid && !this.state_.busy) {
      this.startDebtDialog_(text.slice(0, MAX_LEN));
      return false;
    }
    if (!text || this.state_.busy || this.state_.closed ||
      this.state_.recovering || !this.state_.sid) return false;
    if (text.length > MAX_LEN) text = text.slice(0, MAX_LEN);
    this.clearQuickReplies_();
    this.addMessage_('user', text);
    this.openStream_(text);
    return true;
  }

  /** @private */
  submit_() {
    if (this.sendText_(this.els_.input.value)) this.els_.input.value = '';
  }

  /* ---------- режимы ---------- */

  /**
   * @param {string} mode 'button' | 'widget' | 'fullscreen'.
   * @private
   */
  setMode_(mode) {
    this.root_.dataset.mode = mode;
    if (mode !== 'button') {
      this.state_.unread = 0;
      this.els_.badge.classList.remove('mbkc-visible');
      this.startDialog_();
      setTimeout(() => {
        this.els_.input.focus();
        this.scrollDown_();
      }, 50);
    }
  }

  /* ---------- обработчики ---------- */

  /** @private */
  bindEvents_() {
    if (this.els_.launcher) this.els_.launcher.addEventListener('click', () => this.setMode_('widget'));

    this.root_.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      if (this.isDebt_ && action === 'start-debt') this.startDebtDialog_();
      else if (action === 'onloader-accept') this.decideOnloader_('accept');
      else if (action === 'onloader-decline') this.decideOnloader_('decline');
      else if (action === 'onloader-refresh') this.bootstrapOnloader_();
      else if (action === 'onloader-reconcile') this.onloaderOperation_('reconcile', {});
      else if (action === 'recover-debt') this.recoverDebt_();
      else if (action === 'new-debt') this.newDebtDialog_();
      else if (action === 'close') this.setMode_('button');
      else if (action === 'expand') this.setMode_('fullscreen');
      else if (action === 'minimize') this.setMode_('widget');
      else if (action === 'toggle-sidebar') {
        this.root_.classList.toggle('mbkc-sidebar-collapsed');
      }
    });

    this.els_.form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      this.submit_();
    });
    this.els_.input.addEventListener('input', () => {
      if (this.pendingDebtTurn_) this.pendingDebtTurn_.restored = false;
      /* Человек начал печатать сам — кнопки больше не предлагаем. */
      if (this.els_.input.value.trim()) this.clearQuickReplies_();
      this.setBusyUi_(this.state_.busy);
    });

    this.els_.feedInner.addEventListener('click', (ev) => {
      const chip = ev.target.closest('.mbkc-chip');
      if (!chip) return;
      this.sendText_(chip.getAttribute('data-value'));
    });
    this.els_.input.setAttribute('maxlength', String(MAX_LEN));

    this.els_.tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        this.els_.tabs.forEach((t) => t.classList.remove('mbkc-active'));
        tab.classList.add('mbkc-active');
        const pane = tab.dataset.tab;
        Object.keys(this.els_.tabPanes).forEach((k) => {
          if (this.els_.tabPanes[k]) {
            this.els_.tabPanes[k].hidden = k !== pane;
          }
        });
      });
    });
  }
}

const root = document.getElementById('mbk-ai-chat');
if (root) {
  new AiChatWidget(root);
}
