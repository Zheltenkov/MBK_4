#!/usr/bin/env bash
# Сквозной smoke-тест API. Запускается локально после `docker compose up -d`.
# Проверяет: создание сессии → opening через SSE → один turn → расход токенов.

set -euo pipefail

BASE="${BASE:-http://localhost:8000}"

say() { printf "\n\033[1;36m%s\033[0m\n" "$*"; }

say "1) Health"
curl -fsS "$BASE/api/health" | python3 -m json.tool

say "2) Создаём сессию с анкетой"
SESSION_ID=$(curl -fsS -X POST "$BASE/api/session" \
    -H 'Content-Type: application/json' \
    -d '{
        "anketa": {
            "full_name": "Иван Тестов",
            "phone": "+79991234567",
            "desired_amount": 500000,
            "has_car": true,
            "has_current_loans": false,
            "marital_status": "женат",
            "has_dependents": true,
            "asset_type": "Недвижимость"
        }
    }' | python3 -c 'import sys, json; print(json.load(sys.stdin)["session_id"])')
echo "session_id = $SESSION_ID"

say "3) Получаем opening через SSE (бот начнёт сам). Curl покажет события."
curl -fsSN "$BASE/api/session/$SESSION_ID/stream" | head -40 || true

say "4) Отдаём реплику клиента и читаем поток"
curl -fsSN -G "$BASE/api/session/$SESSION_ID/stream" \
    --data-urlencode "message=У меня Тойота РАВ 2019 года, без кредитов." | head -40 || true

say "5) Текущее состояние"
curl -fsS "$BASE/api/session/$SESSION_ID" | python3 -m json.tool | head -30

say "6) Расход токенов и стоимость"
curl -fsS "$BASE/api/session/$SESSION_ID/usage" | python3 -m json.tool

say "7) Глобально по всем сессиям"
curl -fsS "$BASE/api/usage/global" | python3 -m json.tool

say "Готово. Если в шагах 3–4 видишь event: chunk / bubbles / state — backend жив."
