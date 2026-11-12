"""The task frame filters collection acts while preserving free conversation."""
from __future__ import annotations

from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame, constrain_collection
from interpretation.fastpath import parse_expected_answer
from routing import get_matrix


def test_a_known_company_contact_purpose_remains_an_explanation_after_refusal():
    frame = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent", conversation_declined=True,
                          known_fields=frozenset({"client.phone"}), question_goal="offer_information_purpose")
    text = "Контакт нужен компании, чтобы связаться с вами и проконсультировать по обращению."
    result = constrain_collection(text, frame)
    assert result.text == text and not result.removed_fields and result.asked_field is None
    for forbidden in (
        "Контакт нужен компании, оставьте номер телефона.",
        "Без номера продолжить разговор не получится.",
        "Если передумаете, сможем продолжить разговор.",
    ):
        assert constrain_collection(forbidden, frame).text == ""


@pytest.mark.parametrize("text", [
    "Чтобы сохранить ваши ответы в анкете, нужен номер телефона.",
    "Для сохранения ответов нужен телефон.",
    "Телефон нужен, чтобы сохранить ответы.",
    "Номер необходим для записи данных анкеты.",
])
def test_contact_cannot_be_required_for_saving_answers(text):
    frame = DialogueFrame("client.phone", "collecting_contacts", require_question=True)
    result = constrain_collection(text, frame)
    assert result.text == "" and result.pressure_removed


@pytest.mark.parametrize("text", [
    "Номер нужен для оформления анкеты. Ответы сохраняются и без него.",
    "Телефон не нужен для сохранения ответов.",
    "Чтобы сохранить ответы, телефон не требуется.",
    "Номер не будет нужен для сохранения ответов.",
])
def test_storage_explanation_preserves_legitimate_application_purpose_and_denials(text):
    frame = DialogueFrame("client.phone", "collecting_contacts")
    assert constrain_collection(text, frame).text == text


@pytest.mark.parametrize("goal", ["debt_amount_purpose", "debt_explanation"])
def test_debt_purpose_defers_collection_without_losing_the_pending_plan(goal):
    frame = DialogueFrame("client.region_class", "collecting_debt_context",
                          known_fields=frozenset({"debts.total"}), question_goal=goal)
    answer = "От размера долга зависит, какой путь имеет смысл обсуждать."
    result = constrain_collection(answer + " В каком городе вы живёте?", frame)
    assert frame.expected == "client.region_class" and frame.allowed_field is None
    assert result.text == answer and result.asked_field is None
    assert result.removed_fields == ("client.region_class",)
    resumed = replace(frame, question_goal=None)
    assert resumed.allowed_field == "client.region_class"
    assert constrain_collection("В каком городе вы живёте?", resumed).asked_field == "client.region_class"


@pytest.mark.parametrize("text", [
    "Хотите, объясню, зачем нужен телефон?",
    "Вы спрашиваете, зачем уточнять сумму долга?",
    "Хотите, объясню разницу?",
    "Нет, номер повторно вводить не нужно.",
])
def test_explanatory_questions_keep_their_wording_after_phone_refusal(text):
    frame = DialogueFrame(None, "contact_declined_held", refused_fields=frozenset({"client.phone"}))
    result = constrain_collection(text, frame)
    assert result.text == text and not result.removed_fields and result.asked_field is None


@pytest.mark.parametrize("question,path", [
    ("Подскажите, какая у вас сейчас общая сумма долгов?", "debts.total"),
    ("Сейчас есть активные просрочки по платежам?", "debts.overdue_duration"),
    ("Подскажите, сколько месяцев уже есть просрочки?", "debts.overdue_duration"),
    ("Оставите контакт для связи?", "client.phone"),
    ("В каком городе вы живёте?", "client.region_class"),
])
def test_only_the_forbidden_collection_clause_is_removed(question, path):
    answer = "Есть разные способы обсудить вашу ситуацию."
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total", "client.region_class"}))
    result = constrain_collection(answer + "\n\n" + question, frame)
    assert result.text == answer and path in result.removed_fields


@pytest.mark.parametrize("answer", ["Нет, просрочек нет.", "Нет, нет просрочек.", "Нету, просрочек нет."])
def test_a_reinforced_negative_overdue_answer_closes_the_actual_field(answer):
    updates = parse_expected_answer(answer, ["debts.overdue_duration"], get_matrix())
    assert updates == [{"path":"debts.overdue_duration", "value":"no_overdue", "confidence":"high"}]


def test_a_compound_or_uncertain_negative_is_not_truncated_into_a_fact():
    for answer in ("Нет, наверное просрочек нет.", "Нет, просрочек нет, но есть долги 500000.", "Нет, просрочек нет?"):
        assert parse_expected_answer(answer, ["debts.overdue_duration"], get_matrix()) is None


def test_missing_progress_uses_one_model_recovery_and_preserves_the_human_answer(monkeypatch):
    import llm_agent
    import policy_voice
    from config import AppConfig
    replies = iter(["Сумму вы уже сообщили.", "Сумму вы уже сообщили.\n\nВ каком городе вы сейчас живёте?"])
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        return next(replies)
    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame("client.region_class", "collecting_debt_context", require_question=True)
    result = policy_voice.render({}, AppConfig(openrouter_api_key="unused"), expected=frame.expected,
        status=frame.status, fallback="В каком городе и регионе вы живёте?", frame=frame,
        fallback_question=frame.expected)
    assert result.text == "Сумму вы уже сообщили.\n\nВ каком городе вы сейчас живёте?"
    assert result.asked_field == "client.region_class"
    assert result.reason == "model_advisory_revised" and len(calls) == 2


def test_debt_purpose_is_a_response_goal_before_the_next_intake_question():
    import prompts
    frame = DialogueFrame("client.region_class", "collecting_debt_context",
                          known_fields=frozenset({"debts.total"}), question_goal="debt_amount_purpose")
    instruction = prompts.policy_turn_instruction(frame)
    assert instruction.index("Сейчас человек спрашивает") < instruction.index("НУЖНО ЗАКРЫТЬ")
    assert "от размера долга зависит, какой путь имеет смысл" in instruction.casefold()
    assert "Следующий вопрос анкеты пока отложи" in instruction


def test_a_region_confirmation_keeps_its_own_subject_in_the_instruction():
    import prompts
    instruction = prompts.policy_turn_instruction(DialogueFrame(
        "client.region_class", "collecting_debt_context", confirming=True,
        known_fields=frozenset({"debts.total"}),
    ))
    assert "Проясни только его" in instruction
    assert "остаток долга одним числом" not in instruction


def test_a_question_about_offer_information_gets_a_purpose_before_button_instructions():
    import prompts
    instruction = prompts.policy_turn_instruction(DialogueFrame(
        None, "onloader", mode="onloader", offer_present=True, offer_status="awaiting_consent",
        question_goal="offer_information_purpose",
    ))
    assert instruction.index("Объясни назначение по существу") < instruction.index("Разрешение на передачу выбирается кнопкой")


@pytest.mark.parametrize("text,valid", [
    ("Разрешение приняла.", False),
    ("Приняла ваше разрешение.", False),
    ("Разрешение не приняла, его подтверждают кнопкой.", True),
    ("Я не приняла ваше разрешение.", True),
])
def test_consent_acknowledgement_keeps_subject_verb_order_and_negation(text, valid):
    import policy_voice
    assert policy_voice.validate_reply(text, None) is valid


@pytest.mark.parametrize("text", [
    "Нужен кредит 400000 рублей?",
    "Вам нужны 400000 рублей?",
    "Ваш доход 400000 рублей?",
    "Вы зарабатываете 400000 рублей?",
    "Ежемесячный платёж 400000 рублей?",
    "Квартира стоит 400000 рублей?",
])
def test_a_pending_debt_confirmation_does_not_bind_other_money_contexts(text):
    from dialogue_frame import request_fields
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert "debts.total" not in request_fields(text, frame)


@pytest.mark.parametrize("text,path", [
    ("Назовите сумму вашего дохода в рублях.", "employment.income"),
    ("Какова сумма ежемесячного платежа в рублях?", "debts.monthly_payment"),
])
def test_direct_other_money_collection_is_removed_from_the_debt_task(text, path):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    result = constrain_collection(text, frame)
    assert result.text == "" and path in result.removed_fields


def test_a_contact_decline_repair_shares_the_two_call_budget_with_intake_recovery(monkeypatch):
    import llm_agent
    import policy_voice
    from config import AppConfig
    replies = iter(["Без номера заявку дальше не передать, это обязательное условие.",
                    "Можно продолжить здесь и обсудить ваши вопросы."])
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        return next(replies)
    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame("debts.total", "collecting_debt_context", require_question=True,
                          refused_fields=frozenset({"client.phone"}))
    result = policy_voice.render({}, AppConfig(openrouter_api_key="unused"), expected=frame.expected,
        status=frame.status, fallback="Какова общая сумма долга?", frame=frame, fallback_question=frame.expected)
    assert result.text == "Можно продолжить здесь и обсудить ваши вопросы.\n\nКакова общая сумма долга?"
    assert result.asked_field == "debts.total"
    assert result.reason == "model_advisory_revised_typed_question" and len(calls) == 2


@pytest.mark.parametrize("last,valid", [
    ("Как к вам обращаться?", True),
    ("Система просит назвать ваше имя.", False),
])
def test_recovery_preserves_its_last_question_and_validates_the_complete_composed_body(monkeypatch, last, valid):
    import llm_agent
    import policy_voice
    import prompts
    from config import AppConfig
    replies = iter(["Сумма понятна.\n\nМожно продолжить.", "Сумма понятна.\n\nПродолжим.\n\nУточним одно сведение.\n\n" + last])
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        return next(replies)
    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame("client.name", "collecting_contacts", require_question=True)
    result = policy_voice.render({}, AppConfig(openrouter_api_key="unused"), expected=frame.expected,
        status=frame.status, fallback="Как к вам обращаться?", frame=frame, fallback_question=frame.expected)
    assert len(calls) == 2 and len(prompts.split_bubbles(result.text)) <= 3
    assert "Как к вам обращаться?" in result.text and result.asked_field == "client.name"
    assert result.source == "model"
    if valid:
        assert result.reason == "model_advisory_revised"
        assert "Уточним одно сведение." in result.text
    else:
        # The valid explanation remains in the complete revision. Its forbidden
        # tail is replaced by a typed question before the final critic verdict.
        assert result.reason == "model_advisory_revised_typed_question"
        assert "Сумма понятна." in result.text
    assert "Система" not in result.text
