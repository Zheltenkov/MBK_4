-- Состояние диалогов. Отдельная таблица, а не расширение аналитики: здесь
-- лежат персональные данные (имя, телефон, адреса), и режим доступа к ним
-- принципиально другой.
--
--   docker compose -f docker-compose.edge.yml exec -T postgres \
--     psql -U mbk_app -d mbk_analytics < storage/migrations/001_sessions.sql
--
-- Зачем вообще: до этого сессии жили только в памяти процесса, и рестарт
-- backend — то есть каждая выкатка — стирал все активные разговоры. Отдельно
-- опасен at-most-once автомат отправки лида: его статус (not_sent → in_flight
-- → submitted) хранится внутри session state, и потеря записи между in_flight
-- и submitted означала повторный лид в CRM.

CREATE TABLE IF NOT EXISTS sessions (
    id                  text PRIMARY KEY,
    created_at          timestamptz NOT NULL,
    last_active         timestamptz NOT NULL,
    has_started_opening boolean     NOT NULL DEFAULT false,
    anketa              jsonb,
    state               jsonb       NOT NULL,
    usage               jsonb       NOT NULL DEFAULT '[]'::jsonb
);

-- Восстановление после рестарта и удаление по сроку хранения — оба запроса
-- идут по времени последней активности.
CREATE INDEX IF NOT EXISTS sessions_last_active_idx ON sessions (last_active);

-- Явный запрет для роли дата-инженера. Миграция аналитики выдаёт ей SELECT
-- пофамильно, а не через ALTER DEFAULT PRIVILEGES, поэтому по умолчанию доступа
-- сюда нет. REVOKE стоит здесь как страховка на случай, если права когда-нибудь
-- начнут выдавать шире: в этой таблице персональные данные клиентов, и
-- выгружать её наружу нельзя.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_ro') THEN
        REVOKE ALL ON sessions FROM analytics_ro;
    END IF;
END
$$;
