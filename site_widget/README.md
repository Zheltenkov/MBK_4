# Виджет AI-чата mbk-site — рабочая копия

Файлы — копии из репозитория заказчика `mbk/mbk-site` с правкой чипсов
(спека `docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md`).
В поставку `mbk_ai` папка не входит: `sync_vendor.py` её не перечисляет.

| Файл здесь | Путь в mbk-site | Копия снята с |
|---|---|---|
| `_ai-chat.js` | `www/resources/from_verstala/src/js/modules/_ai-chat.js` | blob `f151e68a95b807effd4eed230f5f9708fb0d1c20`, коммит `13d5a961` |
| `_!ai-chat.scss` | `www/resources/from_verstala/src/sass/modules/_!ai-chat.scss` | blob `1c7c4f2673b34c12fe39a2d428b361f2219074b4`, коммит `7f3b16c7` |
| `test/ai-chat-widget.blade.php` | `www/resources/views/ai-chat-widget.blade.php` | blob `6cd909912e19580cc59bb77626471672e5c95b19`, коммит `7f3b16c7` |

`master` mbk-site на момент копии — `e308abc7`. Нетронутая копия лежит в
истории нашего репозитория коммитом «Копия виджета AI-чата из mbk-site без
правок»; правка — следующими коммитами, её видно `git diff`.

## Контракт с ботом

- Событие `bubbles`: `{"bubbles": [...], "quick_replies": [{"label", "value"}]}`.
  Поле есть, только когда кнопки есть.
- Состояние сессии (`GET /chat-api/session/<id>`, событие `state`): поле
  `quick_replies` всегда, пустой список — кнопок нет.
- Нажатие отправляет `value` обычным сообщением.

## Тесты

```bash
cd site_widget
npm install
npm test
```

## Поставка — только после утверждения человека

1. Сверить `blob_id` обоих файлов на `master` mbk-site с таблицей выше. Если
   файл изменился — сначала перенести их правку сюда и перезапустить тесты.
2. Создать ветку `dev` от `master` и коммит двух файлов
   (`_ai-chat.js`, `_!ai-chat.scss`) по путям из таблицы.
3. MR `dev → master`, `assignee_id` и `reviewer_ids` — vasin (id=2). В
   описании: контракт выше; что пересобрать — `gulp build` в
   `www/resources/from_verstala` (обновятся `www/public/app/js/app.js` и
   `app.css`); как проверить — открыть чат на главной, под приветствием две
   кнопки, нажатие отправляет текст кнопки, ряд пропадает при вводе текста.
4. Сборку и выкатку сайта делает Андрей.
