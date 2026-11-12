-- Аналитические события MBK v4.
--
-- Таблицу читает дата-инженер заказчика: SSH-туннель на 127.0.0.1:5432, роль
-- analytics_ro, только SELECT и только по этой таблице. Персональных данных
-- здесь нет по контракту (см. analytics/events.py: PERSONAL_DATA_KEYS), связь с
-- клиентом — исключительно через session_id.
--
-- CI нет, релизы ручные: накатывается вручную один раз.
--   docker compose exec -T postgres psql -U mbk_app -d mbk_analytics \
--     < analytics/migrations/001_analytics_events.sql

CREATE TABLE IF NOT EXISTS analytics_events (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Именно с зоной: сервер в UTC, заказчик считает в МСК.
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event       text        NOT NULL,
    session_id  text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Событие «раз за сессию» (старт чата, пройденная анкета) не должно
    -- посчитаться дважды, если ход переигран или запрос повторён. NULL
    -- допускается многократно — turn_completed возникает каждый ход и ключа
    -- не имеет.
    dedup_key   text        UNIQUE
);

-- Основной разрез отчётов: «сколько событий X за период».
CREATE INDEX IF NOT EXISTS analytics_events_event_time_idx
    ON analytics_events (event, occurred_at DESC);

-- Склейка воронки одного разговора.
CREATE INDEX IF NOT EXISTS analytics_events_session_idx
    ON analytics_events (session_id, occurred_at);

-- Разрезы по маршруту и шагу анкеты живут в payload.
CREATE INDEX IF NOT EXISTS analytics_events_payload_idx
    ON analytics_events USING gin (payload);

COMMENT ON TABLE analytics_events IS
    'Аналитические события бота. Без персональных данных: связь с клиентом только через session_id.';
COMMENT ON COLUMN analytics_events.dedup_key IS
    'Ключ идемпотентности для событий, которые могут повториться при ретрае.';
