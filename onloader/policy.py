"""Deterministic local onloader dialogue and closed public projection."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from pydantic import ValidationError
from debt_policy import parse_policy

from .contracts import OnloaderState

STATE_KEY = "onloader"
CHAT_KEY = "onloader_dialogue"
# These whole replies are an elliptical answer to the active offer, not arbitrary negation.
_COLLOQUIAL_OFFER_NO = re.compile(
    r"(?:не\s*,?\s+(?:пока|сейчас)\s+не\s+надо|(?:пока|сейчас)\s+нет)[.!]?", re.I,
)
_TRANSFER_REFUSAL = re.compile(
    r"(?:я\s+)?(?:(?:сейчас|пока)\s+)?не\s+(?:хочу|буду|готов[а]?)\s+"
    r"(?:(?:сейчас|пока)\s+)?передавать"
    r"(?:\s+(?:(?:мои|свои|эти|мой|свой|этот)\s+)?(?:данные|сведения|контакты?|телефон|номер(?:\s+телефона)?|заявку))?"
    r"(?:\s+(?:сейчас|пока))?", re.I,
)


def _explicit_transfer_refusal(message: str, *, independent_clauses: bool) -> bool:
    """Read whole independent own-choice clauses without interpreting quoted speech."""
    # Keep supported direct refusals for a retained presentation, without
    # extending the new clause-resolution path to unavailable offers.
    if _TRANSFER_REFUSAL.fullmatch(message.strip().rstrip(".!… ")):
        return True
    if not independent_clauses:
        return False
    unquoted = re.sub(r"""«[^»]*»|"[^"]*"|'[^']*'""", "[quoted]", message)
    for sentence in re.findall(r"[^.!?…;]+[.!?…;]?", unquoted):
        # A conditional or reported sentence cannot supply an independent decision.
        if "?" in sentence or ":" in sentence or re.search(r"\b(?:если|допустим|предположим|бы)\b", sentence, re.I):
            continue
        for clause in re.split(r",\s*но\s+", sentence, flags=re.I):
            clause = re.sub(r"^\s*но\s+", "", clause, flags=re.I).strip().rstrip(".!…; ")
            if _TRANSFER_REFUSAL.fullmatch(clause):
                return True
    return False


def chat_declined(state: Mapping[str, object]) -> bool:
    """A conversational preference is separate from the backend's consent decision."""
    current = read_state(state)
    preference = state.get(CHAT_KEY)
    return bool(current is not None and current.decision is None and isinstance(preference, dict)
                and preference.get("presentation_id") == current.presentation_id
                and preference.get("declined") is True)


def read_state(state: Mapping[str, object]) -> OnloaderState | None:
    try:
        return OnloaderState.model_validate(state.get(STATE_KEY))
    except ValidationError:
        return None


def contact_known(state: Mapping[str, object]) -> bool:
    # This is an independently authorized presence flag, never a fake phone fact.
    return read_state(state) is not None


def dialogue_active(state: Mapping[str, object]) -> bool:
    return STATE_KEY in state and (not contact_known(state) or parse_policy(state) is None)


def public_metadata(state: Mapping[str, object]) -> dict[str, object] | None:
    if STATE_KEY not in state:
        return None
    current = read_state(state)
    if current is None:
        raw = state.get(STATE_KEY)
        status = raw.get("status") if isinstance(raw, dict) else None
        return {"status": status if isinstance(status, str) and status in {"pending", "stale", "forbidden", "unavailable"}
                else "unavailable", "mode": "local", "contact_known": False}
    return {
        "status": current.status, "mode": "local", "contact_known": True,
        "offer": current.offer.model_dump(), "presentation_id": current.presentation_id,
        "decision": current.decision,
    }


def reply(state: Mapping[str, object]) -> str:
    metadata = public_metadata(state) or {}
    status = metadata.get("status")
    current = read_state(state)
    if current is not None and current.decision == "decline" and status in {"signal_pending", "outcome_unknown"}:
        return "Отказ сохранён. Подтверждение обработки пока не получено. Результат можно проверить кнопкой ниже."
    if status == "awaiting_consent":
        assert current is not None
        if chat_declined(state):
            return "Хорошо, можем просто продолжить разговор здесь."
        return (
            f"Номер уже есть в анкете. Разрешаете передать сведения заявки "
            f"компании «{current.offer.partner_name}» для консультации? "
            "Выберите ответ под сообщением. Сейчас это тестовая передача."
        )
    return {
        "pending": "Проверяем данные текущего обращения. Дождитесь результата проверки.",
        "signal_pending": "Решение обрабатывается. Проверьте статус передачи.",
        "queued": "Согласие сохранено. В тесте запрос принят в обработку.",
        "declined": "Отказ сохранён. Передача по этому предложению остановлена.",
        "rejected": "В тесте запрос отклонён. Нужно проверить данные обращения.",
        "outcome_unknown": "Результат передачи пока неизвестен. Проверьте статус, чтобы узнать результат.",
        "stale": "Предложение устарело. Обновите его кнопкой ниже.",
        "forbidden": "Доступ к текущему обращению не подтверждён. Откройте его через сайт партнёра.",
    }.get(str(status), "Данные обращения сейчас недоступны. Повторите проверку через сайт партнёра.")


def prepare(state: dict[str, Any], *, interpret_choice: bool = True) -> dict[str, Any]:
    new_state = deepcopy(state)
    current = read_state(new_state)
    message = str(state.get("_last_user_message") or "").strip()
    if interpret_choice and current is not None and current.decision is None and message:
        # This closes persuasion, without inventing a consent operation or receipt.
        if re.search(r"^\s*(?:я\s+)?передумал[а]?\b|^\s*хочу\s+продолжить\b", message, re.I):
            new_state.pop(CHAT_KEY, None)
        elif (re.fullmatch(r"не\s+передавать[.!]?", message, re.I)
              or _explicit_transfer_refusal(message, independent_clauses=current.status == "awaiting_consent")
              or (current.status == "awaiting_consent" and _COLLOQUIAL_OFFER_NO.fullmatch(message))
              # An elliptical first-person refusal refers only to the awaiting offer.
              # Whole-message matching excludes quoted, conditional and other-operation text.
              or (current.status == "awaiting_consent" and re.fullmatch(
                  r"(?:понял[а]?\s*,\s*)?"
                  r"(?:(?:я(?:\s+(?:сейчас|пока))?|(?:сейчас|пока)(?:\s+я)?)\s+)?"
                  r"(?:не\s+(?:хочу|буду|готов[а]?)\s+(?:(?:сейчас|пока)\s+)?подтверждать"
                  r"|подтверждать\s+(?:(?:сейчас|пока)\s+)?не\s+(?:хочу|буду|готов[а]?))[.!]?",
                  message, re.I))
              or re.search(r"^\s*(?:я\s+)?(?:нет(?:[,.! ]|$)|не\s+разрешаю\b|"
                           r"не\s+буду\s+нажимать\b|отказываюсь\b)", message, re.I)):
            new_state[CHAT_KEY] = {"presentation_id": current.presentation_id, "declined": True}
    # The onloader has an existing application and no contact-collection plan.
    new_state["plan"] = {"action": "onloader", "fields": []}
    new_state.pop("_plan_object", None)
    new_state["dialog_phase"] = "onloader"
    return new_state


def commit(state: dict[str, Any], message: str, spoken_reply: str | None = None) -> dict[str, Any]:
    new_state = prepare(state, interpret_choice=not state.get("_semantic_onloader_choice", False))
    new_state.pop("_semantic_onloader_choice", None)
    new_state.pop("_policy_spoken", None)
    new_state["message_count"] = state.get("message_count", 0) + 1
    history = new_state.setdefault("chat_history", [])
    if message:
        history.append({"role": "user", "content": message})
    response = spoken_reply or reply(new_state)
    if not history or history[-1] != {"role": "assistant", "content": response}:
        history.append({"role": "assistant", "content": response})
    return {"current_state": new_state, "messages": [response]}
