# Группа МБК в GitLab — проверенный доступ и карта репозиториев

Проверено 02.11.2026: [группа mbk](https://gitlab.mbk-credit.ru/mbk), group ID 3.
Вход выполнен под v.zheltenkov в браузере. Список группы получен полностью
через same-origin GET API с действующей browser session, включая подгруппу
landings: 17 проектов, семь основных и десять лендингов.
Пароль, cookies и одноразовые ссылки в проект не сохранялись.
Это подтверждение чтения, не проверка права push/поставки.

## Основные проекты

| Проект | ID на новом хосте | Default branch | Что проверено |
|---|---|---|---|
| [mbk_ai](https://gitlab.mbk-credit.ru/mbk/mbk_ai) | 4 | main | Конфигурация запуска, custom/vendor-src с Python-кодом бота; прочитан core.py |
| [crm-mbk](https://gitlab.mbk-credit.ru/mbk/crm-mbk) | 2 | main | Дерево Bitrix/Laravel, прочитан lara/app/Services/Classification/CrmLeadProfileMapper.php |
| [mbk-site](https://gitlab.mbk-credit.ru/mbk/mbk-site) | 1 | master | Дерево сайта, прочитан www/resources/from_verstala/src/js/modules/_ai-chat.js |
| [mbk-partners](https://gitlab.mbk-credit.ru/mbk/mbk-partners) | 3 | master | Laravel-каталог www, прочитан www/routes/api.php; бизнес-правила ещё не разобраны |
| [mbk-telega-bot](https://gitlab.mbk-credit.ru/mbk/mbk-telega-bot) | 7 | master | Виден в полном списке группы, содержимое отдельно не изучалось |
| [okb-checker](https://gitlab.mbk-credit.ru/mbk/okb-checker) | 6 | main | Виден в полном списке группы, содержимое отдельно не изучалось |
| [max](https://gitlab.mbk-credit.ru/mbk/max) | 5 | main | Виден в полном списке группы, назначение отдельно не изучалось |

## Лендинги

Все в `mbk/landings`, default branch master; в этом проходе проверены
доступность в группе и метаданные, не код каждого лендинга.

| Проект | ID | Ссылка |
|---|---|---|
| loan-v1 | 8 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/loan-v1) |
| loan-v2 | 9 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/loan-v2) |
| loan-v3 | 10 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/loan-v3) |
| refinance-v1 | 11 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/refinance-v1) |
| refinance-v2 | 12 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/refinance-v2) |
| refinance-v3 | 13 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/refinance-v3) |
| urgentsale | 14 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/urgentsale) |
| buyout | 15 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/buyout) |
| stoimost-kv | 16 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/stoimost-kv) |
| stoimost-kv2 | 17 | [открыть](https://gitlab.mbk-credit.ru/mbk/landings/stoimost-kv2) |

## Свидетельства чтения кода

Файлы получены HTTP 200 по зафиксированным commit SHA, не только открыты карточки:

| Проект / ветка | Commit SHA | Проверенный файл |
|---|---|---|
| mbk_ai/main | 18903d23cb20864a4d7f95d6a6d28e6e88952eef | custom/vendor-src/core.py, 2368 строк |
| crm-mbk/main | fea1dd57919189d7ab80d32e4f1033d5361cb2fd | lara/app/Services/Classification/CrmLeadProfileMapper.php, 177 строк |
| mbk-site/master | a014de226cecb269af36f3b158a4a0d518d4b1bd | www/resources/from_verstala/src/js/modules/_ai-chat.js, 802 строки |
| mbk-partners/master | 41fcf638f49f5bd4d284a7bc74ae3a66948912a6 | www/routes/api.php, 58 строк |

Количество строк учитывает завершающий перевод строки как split-элемент.
У mbk_ai основной Python-код находится внутри custom/vendor-src, а не в корне.
Зафиксированный main бота — коммит от 05.10.2026; он не доказывает наличие
поздних локальных исправлений. Состояние dev/MR и установленной версии
production надо сопоставлять отдельно; в этом проходе они не проверялись.

## Что изменилось для продолжения работ

1. Можно читать фактические серверные мапперы/валидаторы/классификацию CRM
   вместо предположения по локальным Python-схемам.
2. Можно проверить правила программы партнёров, дедупликацию/лимиты и форму
   выдачи офферов. Наличие репозитория не доказывает текущие строки правил в БД,
   коммерческие договорённости, цены и доступность покупателя.
3. Можно сопоставить context/form-id/виджет и vendor-src. Нужна адресная
   сверка с нашими файлами перед любой поставкой, без перезаписи чужих правок.

Старые project ID, MR-ссылки и user ID со старого хоста нельзя автоматически
использовать на новом. Например, mbk_ai здесь ID 4, а не исторический 13.
Browser-session API работает в текущей авторизованной сессии; старый PAT ранее
получал 401 и этим входом не исправлялся. Постоянный API-токен не создавался.
Никакие изменения репозиториев, MR или настроек доступа не выполнялись.
