-- Доступ дата-инженера заказчика.
--
-- Отдельная роль, а не пользователь приложения: чтение не должно уметь ничего
-- менять, и его отзыв не должен задевать бота. Пароль подставляется при
-- накате, в репозиторий не попадает.
--
--   docker compose exec -T postgres psql -U mbk_app -d mbk_analytics \
--     -v ro_password='<пароль>' \
--     < analytics/migrations/002_analytics_readonly_role.sql
--
-- Пароль передаётся БЕЗ обрамляющих одинарных кавычек: их добавит сам psql в
-- :'ro_password'. 03.08.2026 он был передан с кавычками, они вошли в пароль, и
-- дата-инженер получал «password authentication failed» с виду верным паролем.
--
-- Проверять пароль надо СНАРУЖИ контейнера. Внутри действует
--   host all all 127.0.0.1/32 trust
-- то есть `docker exec ... psql -h 127.0.0.1` пускает вообще без пароля и
-- ничего не доказывает. Настоящая проверка — подключение по сети compose:
--   docker run --rm --network mbk_default -e PGPASSWORD='<пароль>' \
--     postgres:17-alpine psql -h mbk-postgres -U analytics_ro -d mbk_analytics \
--     -c 'SELECT count(*) FROM analytics_events'
--
-- Подключение с его стороны (5432 наружу не опубликован):
--   ssh -N -L 5432:127.0.0.1:5432 <пользователь>@<адрес сервера>
--   psql "host=127.0.0.1 dbname=mbk_analytics user=analytics_ro"

-- Без DO-блока намеренно: psql НЕ подставляет свои переменные внутри
-- dollar-quoted строк, поэтому :ro_password там остался бы литералом.
-- \gexec выполняет то, что вернул SELECT, а :'ro_password' экранируется psql.
SELECT format('CREATE ROLE analytics_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_ro')
\gexec

ALTER ROLE analytics_ro PASSWORD :'ro_password';

GRANT CONNECT ON DATABASE mbk_analytics TO analytics_ro;
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON analytics_events TO analytics_ro;
-- pg_dump читает last_value последовательности и без этого падает на
-- «permission denied for sequence». Права на чтение счётчика ничего не
-- открывают сверх самой таблицы, зато позволяют выгружать привычным
-- инструментом, а не только через \copy.
GRANT SELECT ON SEQUENCE analytics_events_id_seq TO analytics_ro;

-- Права на будущие таблицы намеренно НЕ выдаются через ALTER DEFAULT PRIVILEGES:
-- каждая новая таблица может содержать что-то, чего постороннему видеть не
-- следует, поэтому доступ к ней обсуждается отдельно.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
