"""Состояние диалога переживает перезапуск процесса при любом раскладе.

27.08.2026, бой: backend ушёл в crash-loop. `core._site_consent_followup`
записал в `state["crm_workflow"]` отметку `offer_link_sent` — прямо в словарь,
мимо модели, — а `CrmDialogState` объявлена с `extra="forbid"` и такого поля не
знала. Единственная сессия, дошедшая до ожидания согласия, валила
`_read_workflow_state` при каждом обращении: и у планировщика на старте, и на
следующем ходе клиента. Состояние чистили руками.

Дефект держался на разрыве авторства: модель состояния живёт в
`crm_dialog_workflow`, а пишет в тот же словарь `core`. Ни один тест не гонял
состояние через сериализацию после того, как ссылка на офер выдана, — поэтому
здесь два слоя проверки, а не один.

**Статический слой** обходит исходники и собирает все ключи, которые код
записывает в словари состояния напрямую. Он поймает следующую такую запись,
кем бы она ни была сделана, — даже если поведенческий тест на неё не написан.

**Поведенческий слой** проваривает каждый статус автомата через настоящий цикл
«сохранили в базу → процесс умер → подняли заново» и требует, чтобы автомат
после этого продолжал работать.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

import core
import crm_dialog_workflow
from crm_dialog_workflow import (
    CRM_WORKFLOW_STATE_KEY,
    CrmDialogState,
    CrmDialogStatus,
)
from lead_intake_coordinator import CRM_LEAD_STATE_KEY, LeadSubmissionState
from tests.test_crm_dialog_workflow import (  # noqa: F401 — переиспользуем стенд
    FakeCrmClient,
    _session,
    _workflow,
)


ROOT = pathlib.Path(__file__).resolve().parent.parent

# Словари состояния и модели, которые их разбирают при восстановлении.
STATE_MODELS = {
    CRM_WORKFLOW_STATE_KEY: CrmDialogState,
    CRM_LEAD_STATE_KEY: LeadSubmissionState,
}

# Имена локальных переменных, в которые код кладёт эти словари, прежде чем
# писать в них. Список короткий намеренно: он описывает сложившуюся практику, а
# не пытается угадать все возможные.
LOCAL_ALIASES = {
    CRM_WORKFLOW_STATE_KEY: {"workflow", "crm_workflow"},
    CRM_LEAD_STATE_KEY: {"lead_state", "crm_lead"},
}


def _source_files() -> list[pathlib.Path]:
    skip = {
        "tests", "dist", "logs", "node_modules", ".venv313", "__pycache__",
        ".git", ".claude", ".pytest_cache", "frontend", "evaluation",
    }
    return [
        path
        for path in ROOT.rglob("*.py")
        if not any(part in skip for part in path.relative_to(ROOT).parts)
    ]


def _written_keys(state_key: str) -> dict[str, set[str]]:
    """Ключи, записываемые в словарь состояния, по файлам-источникам.

    Два случая записи, оба встречались в коде:

        state["crm_workflow"]["offer_link_sent"] = True   — цепочкой
        workflow["offer_link_sent"] = True                — через переменную
    """
    aliases = LOCAL_ALIASES[state_key]
    found: dict[str, set[str]] = {}

    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — файл сломан, это ловит линт
            continue

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                key = _assigned_key(target, state_key, aliases)
                if key is not None:
                    found.setdefault(str(path.relative_to(ROOT)), set()).add(key)
    return found


def _assigned_key(target: ast.expr, state_key: str, aliases: set[str]) -> str | None:
    if not isinstance(target, ast.Subscript):
        return None
    if not (isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str)):
        return None
    base = target.value
    # workflow["..."] = ...
    if isinstance(base, ast.Name) and base.id in aliases:
        return target.slice.value
    # state["crm_workflow"]["..."] = ...
    if (
        isinstance(base, ast.Subscript)
        and isinstance(base.slice, ast.Constant)
        and base.slice.value == state_key
    ):
        return target.slice.value
    return None


# --------------------------------------------------------------------------- #
# Статический слой: чего код пишет в состояние
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state_key", sorted(STATE_MODELS))
def test_no_module_writes_a_field_the_schema_does_not_know(state_key):
    """Любая запись в состояние обязана быть полем модели.

    Именно этого теста не хватало 27.08.2026: `offer_link_sent` писался из
    core.py, а объявлен был только в crm_dialog_workflow.py — и разошлись они
    молча.
    """
    known = set(STATE_MODELS[state_key].model_fields)
    unknown = {
        path: sorted(keys - known)
        for path, keys in _written_keys(state_key).items()
        if keys - known
    }

    assert not unknown, (
        f"В словарь состояния «{state_key}» пишутся поля, которых нет в "
        f"{STATE_MODELS[state_key].__name__}: {unknown}. Модель объявлена с "
        "extra=\"forbid\", поэтому такая сессия не переживёт перезапуск: "
        "разбор упадёт при восстановлении и уронит обработку хода."
    )


def test_the_scanner_would_have_caught_the_incident():
    """Страховка на сам сканер: без неё он мог бы молча ничего не находить."""
    keys = _written_keys(CRM_WORKFLOW_STATE_KEY)

    assert "core.py" in keys, "сканер перестал видеть записи из core.py"
    assert "offer_link_sent" in keys["core.py"]


@pytest.mark.parametrize("state_key", sorted(STATE_MODELS))
def test_every_model_field_survives_a_json_round_trip(state_key):
    """Состояние уезжает в базу через JSON — типы обязаны это переживать."""
    model = STATE_MODELS[state_key]
    dumped = json.loads(json.dumps(model().model_dump(mode="json")))

    assert model.model_validate(dumped) == model()


# --------------------------------------------------------------------------- #
# Поведенческий слой: перезапуск процесса на каждом статусе
# --------------------------------------------------------------------------- #
def _restart(session):
    """Пересобирает сессию так, как это делает восстановление из базы.

    Через JSON намеренно: в базе лежит именно он, и подмена объектов в памяти
    скрыла бы половину класса ошибок — от несериализуемого значения до ключа,
    которого нет в схеме.
    """
    session.state = json.loads(json.dumps(session.state, default=str))
    return session


@pytest.mark.parametrize("status", list(CrmDialogStatus))
def test_a_session_in_any_status_survives_a_restart(status):
    """Ни один статус автомата не должен ронять разбор состояния."""
    session = _session()
    session.state[CRM_WORKFLOW_STATE_KEY] = CrmDialogState(
        status=status
    ).model_dump(mode="json")

    restored = _restart(session)

    assert crm_dialog_workflow._read_workflow_state(restored).status == status


def test_the_offer_link_mark_survives_a_restart(monkeypatch):
    """Тот самый случай: ссылка выдана, процесс перезапущен, автомат жив.

    Дословный сценарий инцидента 27.08.2026 — от паузы до обращения к автомату
    после рестарта. До правки падало на последней строке.
    """
    monkeypatch.setenv("MBK_SITE_CONSENT_PAUSE_ENABLED", "1")
    client = FakeCrmClient(
        need_site_consent=True, thanks_url="https://info.mbk.ru/bfl-region"
    )
    session = _session()
    workflow = _workflow(client, [])
    workflow.advance(session, "")

    bubbles = core._site_consent_followup(session.state)
    assert bubbles, "ссылка на офер не выдана — сценарий не воспроизведён"
    assert session.state[CRM_WORKFLOW_STATE_KEY]["offer_link_sent"] is True

    restored = _restart(session)

    state = crm_dialog_workflow._read_workflow_state(restored)
    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert state.offer_link_sent is True
    # Планировщик истечений ходит по тем же сессиям на старте процесса.
    assert workflow.retry_pending(restored) is None
    assert workflow.recover_interrupted(restored) is None


def test_a_confirmed_consent_after_a_restart_still_sends_one_lead(monkeypatch):
    """Клиент жмёт кнопку уже после перезапуска — лид уходит, и ровно один."""
    monkeypatch.setenv("MBK_SITE_CONSENT_PAUSE_ENABLED", "1")
    client = FakeCrmClient(
        need_site_consent=True, thanks_url="https://info.mbk.ru/bfl-region"
    )
    session = _session()
    workflow = _workflow(client, [])
    workflow.advance(session, "")
    core._site_consent_followup(session.state)

    state = workflow.confirm_site_consent(_restart(session))

    assert state.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1


def test_an_unknown_field_in_stored_state_is_still_refused():
    """Строгость схемы остаётся: тест защищает поле, а не отменяет запрет.

    Соблазн «починить» такой инцидент через extra="ignore" велик, а цена —
    потерянные молча поля состояния. Правильный путь один: объявить поле.
    """
    session = _session()
    session.state[CRM_WORKFLOW_STATE_KEY] = {
        "status": CrmDialogStatus.AWAITING_SITE_CONSENT.value,
        "какое_то_новое_поле": True,
    }

    with pytest.raises(Exception):
        crm_dialog_workflow._read_workflow_state(session)
