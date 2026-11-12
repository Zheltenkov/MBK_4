-- Переписка с клиентом целиком.
--
-- Заказчик попросил складывать все диалоги в его базу, «чтобы потом просто
-- было их смотреть». Отдельная таблица, а не payload в analytics_events: там
-- по контракту нет персональных данных (см. analytics/events.py), а реплика
-- клиента — это свободный текст, в котором есть и имя, и город.
--
-- Номер телефона в текст не попадает: бот заменяет его на солёный отпечаток
-- `[телефон a1b2c3d4e5f6]` до записи (см. analytics/transcripts.py). Отпечаток
-- одинаков для всех написаний одного номера, поэтому диалог с лидом в CRM
-- связать можно, а сам номер из базы не достать.
--
-- CI нет, релизы ручные: накатывается вручную один раз.
--   docker compose exec -T postgres psql -U mbk_app -d mbk_analytics \
--     < analytics/migrations/003_dialog_messages.sql

CREATE TABLE IF NOT EXISTS dialog_messages (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Именно с зоной: сервер в UTC, заказчик считает в МСК.
    occurred_at timestamptz NOT NULL DEFAULT now(),
    session_id  text        NOT NULL,
    -- Номер хода и место реплики внутри хода. Порядок восстанавливается по
    -- ним, а не по времени: пузыри одного ответа пишутся одной меткой.
    turn        integer     NOT NULL,
    position    integer     NOT NULL,
    role        text        NOT NULL CHECK (role IN ('client', 'assistant')),
    message     text        NOT NULL,
    -- Повторённый или переигранный ход не должен раздваивать переписку.
    UNIQUE (session_id, turn, position)
);

-- Основной способ чтения: «покажи диалог целиком, по порядку».
CREATE INDEX IF NOT EXISTS dialog_messages_session_idx
    ON dialog_messages (session_id, turn, position);

-- Второй: «что было вчера».
CREATE INDEX IF NOT EXISTS dialog_messages_time_idx
    ON dialog_messages (occurred_at DESC);

COMMENT ON TABLE dialog_messages IS
    'Переписка бота с клиентом. Телефон заменён солёным отпечатком до записи.';
COMMENT ON COLUMN dialog_messages.turn IS
    'Номер хода диалога: одна реплика клиента и ответные пузыри бота.';
COMMENT ON COLUMN dialog_messages.position IS
    'Порядок реплики внутри хода, начиная с реплики клиента.';

-- Таблица заводится ради чтения дата-инженером — доступ выдаётся сразу.
-- (В 002 права на будущие таблицы намеренно не раздавались оптом.)
--
-- Через DO, а не голым GRANT: роль создаёт миграция 002, а она накатывается
-- руками — ей нужен пароль. На чистом стенде роли ещё нет, и отказ GRANT
-- обрушил бы весь накат (migrate идёт с ON_ERROR_STOP=1, backend ждёт его
-- успеха). Права выдадутся при следующем накате, уже после 002.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_ro') THEN
        GRANT SELECT ON dialog_messages TO analytics_ro;
        GRANT SELECT ON SEQUENCE dialog_messages_id_seq TO analytics_ro;
    ELSE
        RAISE NOTICE
            'роль analytics_ro не найдена — права на dialog_messages не выданы';
    END IF;
END
$$;

-- Диалог целиком одной строкой, как его читают глазами:
--   SELECT session_id,
--          min(occurred_at) AS started_at,
--          string_agg(
--              CASE role WHEN 'client' THEN 'Клиент: ' ELSE 'Бот: ' END || message,
--              E'\n' ORDER BY turn, position
--          ) AS dialog
--   FROM dialog_messages
--   GROUP BY session_id
--   ORDER BY started_at DESC;
--
-- Срок хранения этой таблицы — решение заказчика: бот сам её не чистит.
-- Удаление старше N дней:
--   DELETE FROM dialog_messages WHERE occurred_at < now() - interval '90 days';
