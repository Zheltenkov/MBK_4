"""Concrete rejection feedback reaches the bounded semantic speaker repair."""
from __future__ import annotations

from io import BytesIO
import json

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_grounded_voice
from tests.test_policy_grounded_voice import semantic_raw


@pytest.mark.parametrize("draft,frame,diagnoses,final", [
    (
        "Алименты и возмещение вреда сохраняются после банкротства.",
        DialogueFrame(None, "collected_held", mode="debt"),
        ("overbroad_harm_exception", "жизни или здоровью"),
        "Алименты и требования о возмещении вреда жизни или здоровью сохраняются.",
    ),
    (
        "Записала 400000 рублей.",
        DialogueFrame("debts.total", "collecting_debt_context", mode="debt", confirming=True, require_question=True),
        ("unconfirmed_amount_assertion", "приблизительную сумму"),
        "Какую точную общую сумму долга вы знаете?",
    ),
    (
        "Есть ли сейчас активные просрочки по платежам?",
        DialogueFrame(None, "collected_held", mode="debt"),
        ("unallowed_collection", "единственного разрешённого"),
        "Можем обсудить ваш вопрос здесь.",
    ),
    (
        "Банкротство означает полное освобождение от всех долгов. Есть ли сейчас просрочки по платежам?",
        DialogueFrame(None, "collected_held", mode="debt"),
        ("unconditional_discharge", "unallowed_collection", "текущие платежи"),
        "Результат банкротства зависит от условий закона и конкретных обстоятельств.",
    ),
])
def test_deterministic_rejection_diagnoses_are_sent_to_the_second_real_adapter_call(
    monkeypatch, draft, frame, diagnoses, final,
):
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)
    replies = [draft, semantic_raw(), final, semantic_raw()]
    bodies = []

    def open_request(settings, request):
        # Intercept transport only. Prompt construction, parsing and validation remain real.
        bodies.append(json.loads(request.data))
        assert len(bodies) <= len(replies), "Repair must remain within two speaker/analysis pairs"
        return BytesIO(json.dumps({"choices": [{"message": {"content": replies[len(bodies) - 1]}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": "Помогите разобраться."}, config,
        frame=frame, fallback="Запасной ответ.",
    )

    assert [body["model"] for body in bodies] == [config.model, config.extractor_model] * 2
    repair = "\n".join(message["content"] for message in bodies[2]["messages"] if message["role"] == "system")
    assert all(diagnosis in repair for diagnosis in diagnoses)
    assert draft not in repair
    assert result.spoken.text == final
    assert result.spoken.source == "model"
    assert result.spoken.asked_field == frame.allowed_field
