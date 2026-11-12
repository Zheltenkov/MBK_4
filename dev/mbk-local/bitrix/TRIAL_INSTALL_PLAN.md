# Штатная новая trial-установка: проверенный сценарий

Выполнено 06.11.2026 в отдельном scope. Первоначальный Git-pinned runtime
дошёл до coupon_activation.php; его дату/ключ/лицензионные проверки не меняли.
Новая trial-копия установлена официальным мастером, с собственной schema и
volume. Main26.150.0, CRM26.100.200, 90модулей и локальный administrator.
Native CCrmLead::Add создал реальный website lead1; доказательства в STATUS.md.

Официальная документация описывает два источника — BitrixSetup и архив —
и quick installation с локальным .settings.php:
https://docs.1c-bitrix.ru/pages/get-started/install-distr.html

Документация отдельно описывает шаг регистрации demo и регистрационные поля.
Необязательность регистрации подтверждена реальной формой CheckLicenseKey:
checkbox снят, имя/фамилия/email регистрации не отправлялись. Обновления
пропущены штатным skip, administrator с invalid email создан локально.
Design/demo-content wizard не требуется серверному сценарию и не завершался.

## Что проверить до запуска

1. Сохранить официальный URL, время скачивания, hash архива и фактические
   версии main/crm/currency. Это другой source provenance, не parity старого Git.
2. Читать только открытый installer flow: entrypoint → выбор языка/редакции →
   лицензионное соглашение → регистрация → system check → DB → InstallDB →
   локальный administrator → solution wizard. Обфускацию/ключи не декодировать.
3. Для каждого шага фиксировать входные поля, обязательность, CSRF/session,
   локальные DB/file writes, network endpoints и next-step response.
4. Source-backed native DB writes ожидаются через штатный InstallDB:
   таблицы, modules/options, tasks/groups/languages/sites, native handlers.
   Нельзя механически применять предыдущий минимальный seed или считать
   registrations пустыми после официальной установки. Фактический handler/
   agent/event/workflow inventory должен пройти review перед native Add.
5. Generated DB/admin credentials остаются только в ignored local env/config.
   Локальный admin может иметь synthetic имя/invalid.example email, пока
   мастер сохраняет их только локально. Внешнюю регистрацию с выдуманными
   персональными данными не отправлять. Если обязательна — показать точные
   поля/официальный endpoint родителю для вопроса пользователю.
6. Новый сервис только internal network/no host ports/SMTP false; никаких
   customer init hooks. Для штатной установки разрешать запись только в
   собственный новый filesystem/schema. После установки перейти в RO mode.
7. Мастер проверять HTTP-клиентом внутри изоляции без хостового JS/browser:
   содержимое HTML может включать внешние scripts/telemetry. Не добавлять
   исходящий доступ ради автоматического завершения system check.

## Критерий результата

Штатно установленный trial kernel ещё не означает лида. Следом выполняются
реальные CUserTypeEntity/CUserFieldEnum, original CCrmLead::Add и чтение
lead + UF/FM/address из native ORM/UserFieldManager с фактическими enum IDs.
Только этот combined proof разрешает claim genuine_bitrix_runtime и handoff
единого lead ID реальной локальной партнёрке.
