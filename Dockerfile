FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала requirements — кэшируется отдельным слоем.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime-код приложения. Копируем явным allowlist, чтобы в образ случайно
# не попадали eval-наборы, legacy UI, локальные скрипты или будущие dev-файлы.
# Новый пакет надо добавлять сюда руками; за этим следит
# tests/test_dockerfile.py, иначе пропуск виден только падением контейнера.
COPY assistant_contracts.py ./
COPY attribution.py ./
COPY config.py ./
COPY context_attachment.py ./
COPY context_policy.py ./
COPY context_projection.py ./
COPY core.py ./
COPY crm_dialog_workflow.py ./
COPY debt_policy.py ./
COPY debt_workflow.py ./
COPY entry_routing.py ./
COPY lead_intake_coordinator.py ./
COPY llm_agent.py ./
COPY logger.py ./
COPY main.py ./
COPY names_ru.py ./
COPY observability.py ./
COPY pii_masking.py ./
COPY policy_voice.py ./
COPY policy_voice_critic.py ./
COPY policy_content.py ./
COPY policy_grounded_voice.py ./
COPY policy_semantics.py ./
COPY policy_semantic_analysis.py ./
COPY policy_factual_audit.py ./
COPY dialogue_frame.py ./
COPY prompts.py ./
COPY quick_replies.py ./
COPY session_store.py ./
COPY state.py ./
COPY transport.py ./
COPY usage_tracker.py ./
COPY utils.py ./
# Ядро квалификации. matrix.yaml обязателен — без него движок не поднимется.
COPY routing/ ./routing/
COPY delivery/ ./delivery/
COPY geo/ ./geo/
COPY interpretation/ ./interpretation/
COPY analytics/ ./analytics/
COPY storage/ ./storage/
COPY customer_context/ ./customer_context/
COPY onloader/ ./onloader/

# Logs пишутся в volume, директорию создаём заранее.
RUN mkdir -p logs/sessions && \
    groupadd --system mbk && \
    useradd --system --gid mbk --no-create-home mbk && \
    chown -R mbk:mbk /app
USER mbk

EXPOSE 8000

# Опенер с пустым ProxyHandler, а не urlopen: голый urlopen молча читает
# системные HTTP_PROXY/HTTPS_PROXY. Прокси у нас нужен ровно одному адресату —
# зарубежному провайдеру модели, — и стоит администратору прописать его
# системно, как вопрос про собственный localhost уедет за границу. Ответа не
# будет, docker посчитает контейнер больным и уронит исправную выкатку.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.build_opener(u.ProxyHandler({})).open('http://localhost:8000/api/health', timeout=3).status == 200 else 1)"

# Пока SessionStore находится в памяти процесса, worker обязан быть один: иначе
# создание сессии и следующий SSE-запрос могут попасть в разные независимые store.
# Масштабировать workers можно только после подключения общего Redis/БД repository.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--access-log"]
