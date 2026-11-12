{{-- AI-чат МБК: виджет в дизайне chat-handoff v2, работает через /chat-api/ (стек mbk_ai).
     Стили и логика — в общей сборке: from_verstala sass/modules/_!ai-chat.scss и js/modules/_ai-chat.js. --}}
<div id="mbk-ai-chat" data-mode="button" data-avatar="/app/img/chat-nika.png" data-operator-name="Ника">

    {{-- Режим 1: плавающая кнопка --}}
    <button type="button" class="mbkc-launcher" aria-label="Открыть чат с консультантом">
        <span class="mbkc-avatar mbkc-avatar--lg">
            <img src="/app/img/chat-nika.png" alt="Ника, специалист МБК">
            <span class="mbkc-online"></span>
            <span class="mbkc-badge"></span>
        </span>
        <span class="mbkc-launcher-text">
            <span class="mbkc-launcher-name">Ника</span><br>
            <span class="mbkc-launcher-hint">Написать нам...</span>
        </span>
    </button>

    {{-- Режимы 2–3: виджет / полный экран --}}
    <div class="mbkc-panel">

        {{-- Сайдбар (fullscreen, desktop) --}}
        <aside class="mbkc-sidebar">
            <div class="mbkc-sidebar-head">
                <div class="mbkc-sidebar-title">
                    <span>Сообщения</span>
                    <button type="button" class="mbkc-iconbtn mbkc-only-desktop" data-action="toggle-sidebar" title="Скрыть панель">
                        <svg class="mbkc-icon mbkc-icon--lg" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m15 9-3 3 3 3"/></svg>
                    </button>
                </div>
                <div class="mbkc-search">
                    <input type="text" placeholder="Поиск по сообщениям...">
                    <svg class="mbkc-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                </div>
                <div class="mbkc-tabs">
                    <button type="button" class="mbkc-tab mbkc-active" data-tab="chats">
                        <svg class="mbkc-icon mbkc-icon--sm" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Чаты
                    </button>
                    <button type="button" class="mbkc-tab" data-tab="files">
                        <svg class="mbkc-icon mbkc-icon--sm" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>Файлы
                    </button>
                </div>
            </div>
            <div class="mbkc-sidebar-body">
                <div data-pane="chats">
                    <div class="mbkc-chat-item">
                        <span class="mbkc-avatar mbkc-avatar--lg">
                            <img src="/app/img/chat-nika.png" alt="Ника">
                            <span class="mbkc-online"></span>
                        </span>
                        <span class="mbkc-chat-item-body">
                            <span class="mbkc-chat-item-name">Ника</span>
                            <span class="mbkc-chat-item-last">Онлайн-консультация</span>
                        </span>
                    </div>
                </div>
                <div data-pane="files" hidden>
                    <div class="mbkc-empty">Файлов пока нет</div>
                </div>
            </div>
        </aside>

        {{-- Основная колонка --}}
        <div class="mbkc-main">
            <div class="mbkc-header">
                <div class="mbkc-header-person">
                    <button type="button" class="mbkc-iconbtn mbkc-only-fs mbkc-only-desktop" data-action="toggle-sidebar" title="Показать/скрыть панель">
                        <svg class="mbkc-icon mbkc-icon--lg" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m13 9 3 3-3 3"/></svg>
                    </button>
                    <span class="mbkc-avatar mbkc-avatar--md">
                        <img src="/app/img/chat-nika.png" alt="Ника, специалист МБК">
                        <span class="mbkc-online"></span>
                    </span>
                    <div>
                        <div class="mbkc-header-name">Ника</div>
                        <div class="mbkc-header-role">Специалист МБК</div>
                    </div>
                </div>
                <div class="mbkc-header-actions">
                    {{-- Кнопки fullscreen-режима убраны по решению МБК 2026-08-17:
                         чат живёт только в компактном окне. --}}
                    <button type="button" class="mbkc-iconbtn" data-action="close" title="Закрыть">
                        <svg class="mbkc-icon" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>
                    </button>
                </div>
            </div>

            <div class="mbkc-feed-wrap">
                <div class="mbkc-feed">
                    <div class="mbkc-feed-inner" role="log" aria-live="polite" aria-relevant="additions text"></div>
                </div>
            </div>

            <form class="mbkc-inputbar">
                <div class="mbkc-inputbar-inner">
                    <input type="text" class="mbkc-input" placeholder="Введите сообщение..." autocomplete="off" aria-label="Ваше сообщение" enterkeyhint="send">
                    <button type="submit" class="mbkc-send" disabled aria-label="Отправить">
                        <svg class="mbkc-icon" viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/></svg>
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
