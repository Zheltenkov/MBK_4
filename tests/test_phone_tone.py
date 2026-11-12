"""Phone-purpose prompt contracts, preserving the original persona and examples."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from dialogue_frame import DialogueFrame, constrain_collection, contact_blocks_chat
import core
import llm_agent
import prompts


def prompt_assignment(name: str) -> ast.expr:
    tree = ast.parse(Path(prompts.__file__).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return deepcopy(node.value)
    raise AssertionError("Prompt assignment missing")


class NormalizeApprovedPromptChanges(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str):
            # Restore only the historical capability block for the bounded AST
            # comparison. The current truthful rule is checked independently below.
            old_capabilities = (
                "\n1a. Не обещай того, чего не делаешь. Ты не видишь заявок клиента, не проверяешь\n"
                "   их статус и не соединяешь с менеджером — всё это делается после того, как ты\n"
                "   передашь лид. Если человек спрашивает про свою заявку — честно скажи, что её\n"
                "   отсюда не видно, и предложи то, что действительно можешь: продолжить подбор\n"
                "   или оставить номер для связи."
            )
            node.value = re.sub(r"\n1a\..*?(?=\n1b\.)", old_capabilities, node.value, flags=re.DOTALL)
            node.value = re.sub(r"\n5a\..*?(?=\n6\.)", "\n5a. [phone instruction]\n", node.value, flags=re.DOTALL)
            node.value = re.sub(r"\n- Инициатива твоя\..*?(?=\n- Имя знаешь)", "\n- [data purpose instruction]",
                                node.value, flags=re.DOTALL)
            # Restore the sampled original identity rule solely for the bounded
            # AST comparison. Separate assertions require the new honest rule.
            old_identity = (
                "\n6a. «Ты бот?», «это ИИ?», «с роботом говорю?» — не втягивайся в этот разговор. Отшутись\n"
                "   или уведи в сторону одной короткой фразой и сразу вернись к вопросу, на котором\n"
                "   остановились. НЕ клянись, что ты человек, и не оправдывайся: развёрнутое «я живой\n"
                "   человек, а не программа» звучит подозрительнее самого вопроса и уводит разговор\n"
                "   ещё дальше. Одна лёгкая фраза — и дальше по делу."
            )
            node.value = re.sub(r"\n6a\..*?(?=\n7\.)", old_identity, node.value, flags=re.DOTALL)
            node.value = node.value.replace(
                "Я AI-помощник МБК. Помогаю разобраться с вашим вопросом.",
                "Работаю с этим каждый день, вопросы задаю одни и те же — немудрено спутать.",
            )
            node.value = node.value.replace(
                "{assistant}: Подскажите, как к вам обращаться?\n\nКлиент: Василий",
                "{assistant}: Давайте разберёмся. Прежде чем продолжим, подскажите, пожалуйста, как к вам обращаться?\n\nКлиент: Василий",
            )
        return node


@pytest.mark.parametrize("name,expected", [
    ("CONVERSATION_SYSTEM_PROMPT", "07f9a9b5d73a3eb3e097b83dce0b4fd7a93e2c797d3db8bf5dbc9742cd219f3d"),
    ("STYLE_EXAMPLES", "72c0ba65d007e8ce9dbf73f6e29ed6db8b7362a76e8d37bda1aced872b0511b0"),
])
def test_only_the_approved_phone_identity_and_capability_changes_affect_the_original_voice(name, expected):
    value = NormalizeApprovedPromptChanges().visit(prompt_assignment(name))
    assert hashlib.sha256(ast.dump(value, include_attributes=False).encode()).hexdigest() == expected


def test_the_brief_identity_is_derived_from_the_frozen_original_persona_and_gender_rule():
    original_identity, boundary, _ = prompts.CONVERSATION_SYSTEM_PROMPT.partition("\n\nЖЁСТКИЕ ПРАВИЛА")
    assert boundary and prompts.CONVERSATION_IDENTITY == original_identity
    assert prompts._SELF_GENDER_RULE in original_identity
    assert prompts.policy_brief_reaction_prompt().startswith(original_identity + "\n\n")


def test_the_capability_rule_uses_current_context_without_promising_post_transfer_features():
    rule = prompts.CONVERSATION_SYSTEM_PROMPT.split("1a.", 1)[1].split("\n1b.", 1)[0]
    assert "Используй явно переданные сведения о текущем" in rule
    assert "обращении, карточке и состоянии операции" in rule
    assert "сама по себе не подтверждает проверку статуса" in rule
    assert "соединение с менеджером" in rule and "будущий звонок" in rule
    assert "Ты не видишь заявок клиента" not in rule
    assert "всё это делается после того" not in rule


def test_the_base_phone_rule_separates_verified_contact_actions_from_conversation():
    rule = prompts.CONVERSATION_SYSTEM_PROMPT.split("5a.", 1)[1].split("\n6.", 1)[0]
    assert "оформления заявки" in rule
    assert "подтверждена текущей инструкцией" in rule
    assert "Продолжение разговора" in rule
    assert "без номера заявку дальше не передать" not in rule


def test_a_direct_identity_question_gets_a_short_honest_answer_in_the_base_rule():
    rule = prompts.CONVERSATION_SYSTEM_PROMPT.split("6a.", 1)[1].split("\n7.", 1)[0]
    assert "честно" in rule and "AI-помощник МБК" in rule
    assert "Не выдавай себя за человека" in rule
    assert "Отшутись" not in rule and "уведи в сторону" not in rule


def test_the_identity_example_demonstrates_the_same_honesty_without_changing_its_next_question():
    example = prompts.STYLE_EXAMPLES.split("Клиент: А ты вообще бот или человек?", 1)[1]
    assert example.splitlines()[1] == f"{prompts.ASSISTANT_NAME}: Я AI-помощник МБК. Помогаю разобраться с вашим вопросом."
    assert "Так что с просрочками, сейчас есть?" in example
    assert "Работаю с этим каждый день" not in example


def test_the_initial_example_starts_with_the_actual_question_without_a_formal_preamble():
    example = prompts.STYLE_EXAMPLES.split("Клиент: Здравствуйте, нужны деньги\n", 1)[1].split("\n\n", 1)[0]
    assert example == f"{prompts.ASSISTANT_NAME}: Подскажите, как к вам обращаться?"


def test_the_ordinary_contact_instruction_keeps_phone_purpose_and_choice_explicit():
    instruction = prompts.turn_instruction(SimpleNamespace(
        action="contacts", fields=[SimpleNamespace(path="client.phone")], matched=None,
    ))
    assert "оформления заявки" in instruction
    assert "Продолжение разговора" in instruction
    assert prompts.PHONE_OBJECTION_RULE in instruction
    assert prompts.CONVERSATION_VOICE in instruction
    assert "без номера решение получить нельзя" not in instruction


def test_a_question_about_phone_purpose_is_answered_without_a_repeated_obligatory_request():
    rule = prompts.PHONE_OBJECTION_RULE
    assert "зачем" in rule and "назначение" in rule
    assert "беседа" in rule.casefold()
    assert "не дублируй просьбу" in rule
    assert "Причину (без номера" not in rule


def test_the_held_debt_phone_step_invites_contact_for_the_questionnaire_before_any_refusal():
    frame = DialogueFrame("client.phone", "collecting_contacts", require_question=True)
    instruction = prompts.policy_turn_instruction(frame)
    assert "Контакт нужен для анкеты" in instruction
    assert "Продолжение разговора" in instruction
    assert "назначение" in instruction
    assert "Ограничения возможностей — внутренний фон" in instruction
    assert "Не заявляй о получателе и будущей отправке" in instruction
    assert "Не обещай связаться, передать специалисту или вернуть результат" in instruction
    assert "Ответы анкеты сохраняются и без телефона" in instruction
    assert "Не представляй сохранение или фиксацию ответов причиной просьбы" in instruction
    # The model still controls natural wording and asks only the current field.
    assert "Выбирай форму ответа по смыслу разговора" in instruction
    assert "конкретным вопросом" in instruction


def test_a_known_or_declined_contact_does_not_receive_a_new_phone_invitation():
    for frame in [
        DialogueFrame(None, "collected_held", known_fields=frozenset({"client.phone"})),
        DialogueFrame(None, "contact_declined_held", refused_fields=frozenset({"client.phone"})),
        DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                      offer_status="awaiting_consent", known_fields=frozenset({"client.phone"})),
    ]:
        assert "Контакт нужен для анкеты" not in prompts.policy_turn_instruction(frame)


def test_refusal_instruction_does_not_present_the_chat_as_unavailable_without_phone():
    instruction = prompts.REFUSED_CONTACT_INSTRUCTION
    assert "прими отказ" in instruction.casefold()
    assert "общение" in instruction.casefold()
    assert "наше условие" not in instruction
    assert "Следующий пузырь сам скажет" not in instruction


def test_the_actual_conversation_message_builder_uses_the_scoped_phone_instruction():
    instruction = prompts.policy_turn_instruction(DialogueFrame("client.phone", "collecting_contacts"))
    messages = llm_agent._conversation_messages({
        "facts": {}, "short_history": [], "latest_user_message": "А зачем номер?",
        "turn_instruction": instruction,
    })
    assert messages[0]["content"] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES
    assert messages[2]["content"] == instruction
    assert messages[-1]["content"] == "А зачем номер?"


def test_legacy_and_policy_share_voice_guidance_without_importing_legacy_faq_actions():
    instruction = prompts.policy_turn_instruction(DialogueFrame(None, "collected_held"))
    assert prompts.CONVERSATION_VOICE in instruction
    assert prompts.CONVERSATION_VOICE in prompts.CONVERSATION_FREEDOM
    assert "человеческий смысл" in prompts.CONVERSATION_VOICE
    assert "коротко подвести итог" in prompts.CONVERSATION_VOICE
    assert "потом всё равно задай" not in prompts.CONVERSATION_VOICE
    assert "передавать заявку специалисту" not in prompts.CONVERSATION_VOICE
    assert "потом всё равно задай" not in instruction


def test_an_ordinary_fact_moves_the_task_forward_without_requiring_a_receipt():
    assert "Обычный фактический ответ" in prompts.CONVERSATION_VOICE
    assert "сразу продолжай текущую задачу" in prompts.CONVERSATION_VOICE
    assert "человек сам просит подтвердить понимание" in prompts.CONVERSATION_VOICE


def test_an_uncertain_amount_is_confirmed_by_the_client_instead_of_announced_as_exact():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    instruction = prompts.policy_turn_instruction(frame)
    assert "Уточни у человека" in instruction
    assert "Приблизительный ответ сохраняет неопределённость" in instruction
    assert "Можно естественно подтвердить названную сумму" not in instruction


def test_the_latest_region_answer_has_its_own_function_without_personal_values():
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total", "client.region_class"}),
                          received_fields=frozenset({"client.region_class"}))
    instruction = prompts.policy_turn_instruction(frame)
    latest = next(part for part in instruction.split("\n\n") if part.startswith("Функция текущего хода"))
    assert "город и регион" in latest
    assert "общая сумма" not in latest
    assert "текущую задачу" in latest
    assert "Служебный список не пересказывай" in latest


def test_a_current_question_keeps_priority_when_the_same_turn_also_provides_a_fact():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_amount_purpose",
                          received_fields=frozenset({"client.region_class"}))
    instruction = prompts.policy_turn_instruction(frame)
    assert instruction.index("Сейчас человек спрашивает, зачем нужна сумма долга") < instruction.index("Функция текущего хода")


def test_a_turn_without_received_fields_does_not_inherit_a_previous_answer_function():
    assert "Функция текущего хода" not in prompts.policy_turn_instruction(DialogueFrame(None, "collected_held"))


def test_missing_phone_closing_keeps_the_chat_available_without_another_phone_cta():
    text = " ".join(prompts.survey_completed_message(thanks_url=None, has_phone=False)).casefold()
    assert "вопросы" in text and "здесь" in text
    assert "номер" not in text and "телефон" not in text
    assert "передумаете" not in text
    url = "https://example.invalid/result"
    assert url in " ".join(prompts.survey_completed_message(thanks_url=url, has_phone=True))


@pytest.mark.parametrize("text", [
    "Чтобы продолжить разговор, телефон не нужен.",
    "Без номера можем обсудить ваш вопрос здесь.",
])
def test_positive_chat_availability_survives_policy_and_legacy_contact_filters(text):
    frame = DialogueFrame("client.phone", "collecting_contacts")
    assert contact_blocks_chat(text, phone_context=True) is False
    assert constrain_collection(text, frame).text == text
    assert core._safe_contact_reaction([text]) == text


def test_a_negative_contact_requirement_does_not_excuse_a_separate_coercive_clause():
    frame = DialogueFrame("client.phone", "collecting_contacts")
    fact = "Номер будет указан в заявке."
    for pressure in [
        "Чтобы продолжить разговор, телефон не нужен, но без номера я не смогу помочь.",
        "Чтобы продолжить разговор, оставьте номер, хотя телефон не нужен.",
    ]:
        assert contact_blocks_chat(pressure, phone_context=True) is True
        constrained = constrain_collection(fact + " " + pressure, frame)
        assert constrained.text == fact
        assert pressure not in constrained.text


@pytest.mark.parametrize("text,blocked", [
    ("Без номера продолжить работу по вашему вопросу не получится.", True),
    ("Без него ответить здесь не смогу.", True),
    ("Без телефона помочь не сможем.", True),
    ("Без номера продолжить оформление анкеты не получится, но вопросы можно обсудить здесь.", False),
    ("Без телефона продолжить разговор можно, а оформить заявку не получится.", False),
    ("Без номера отвечать на вопросы не запрещено.", False),
])
def test_phone_barrier_grammar_supports_both_predicate_orders_without_blocking_application_scope(text, blocked):
    assert contact_blocks_chat(text, phone_context=True) is blocked


def test_first_script_step_has_no_phantom_backend_transition_instruction():
    transition = core.get_matrix().script[0].transition
    assert transition is None
    plan = core.Plan(action="ask", step=0, transition=transition)
    assert prompts._transition_block(plan) == ""


def test_policy_fact_metadata_is_backstage_context_instead_of_a_receipt_to_recite():
    frame = DialogueFrame("client.name", "collecting_contacts",
                          known_fields=frozenset({"debts.total", "client.region_class"}),
                          received_fields=frozenset({"client.region_class"}), require_question=True)
    instruction = prompts.policy_turn_instruction(frame)
    assert "Уже подтверждено —" not in instruction
    assert "В последнем сообщении получен ответ" not in instruction
    assert "Сам выбери реакцию и объяснение" not in instruction
    assert "внутренние ограничения" in instruction
    assert "не требуют отчёта о получении" in instruction
    assert "конкретным вопросом" in instruction
    assert "человек сам просит подтвердить понимание" in instruction


def test_completed_debt_step_has_a_useful_conversation_goal_in_both_speech_passes():
    frame = DialogueFrame(None, "collected_held", received_fields=frozenset({"client.region_class"}))
    instruction = prompts.policy_turn_instruction(frame)
    revision = prompts.policy_revision_instruction(frame, "Синтетический черновик.",
                                                     needs_repair=False, missing_question=False)
    goal = "Текущая цель — содержательно продолжить обсуждение ситуации с долгами"
    assert goal in instruction and goal in revision
    assert goal in prompts.policy_regeneration_instruction(frame, ("wrong_current_turn",))
    assert "не требует нового вопроса анкеты" in instruction
    assert "сохраняя за человеком выбор" in instruction
    assert "Вопрос, эмоция или просьба подтвердить понимание" in instruction


@pytest.mark.parametrize("frame", [
    DialogueFrame("debts.total", "collecting_debt_context", require_question=True),
    DialogueFrame("client.phone", "collecting_contacts", require_question=True),
    DialogueFrame("client.name", "collected_held", require_question=True),
    DialogueFrame(None, "contact_declined_held"),
    DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True, offer_status="awaiting_consent"),
    DialogueFrame(None, "collected_held", mode="onloader"),
])
def test_completed_debt_conversation_goal_does_not_replace_another_task(frame):
    assert "Текущая цель — содержательно продолжить обсуждение ситуации с долгами" not in prompts.policy_turn_instruction(frame)


def test_a_current_faq_precedes_the_completed_step_goal():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_amount_purpose")
    instruction = prompts.policy_turn_instruction(frame)
    assert "Сейчас человек спрашивает, зачем нужна сумма долга" in instruction
    # The specific FAQ now owns this turn outright, without a competing
    # generic invitation to restart a completed discussion.
    assert "Текущая цель — содержательно продолжить обсуждение ситуации с долгами" not in instruction


@pytest.mark.parametrize("text", [
    "Понимаю, как тяжело жить с постоянными звонками. Можем обсудить ситуацию здесь.",
    "Да, регион учтён. Дополнительные сведения для этой анкеты сейчас не нужны.",
])
def test_completion_goal_preserves_meaningful_empathy_and_requested_confirmation(monkeypatch, text):
    import policy_voice
    from config import AppConfig

    monkeypatch.setattr(llm_agent, "generate_reply", lambda *args, **kwargs: text)
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}))
    result = policy_voice.render({"latest_user_message": "Меня беспокоят звонки. Мой регион учли?"},
                                  AppConfig(openrouter_api_key="synthetic-unused"), expected=None,
                                  status=frame.status, frame=frame, fallback="Можем продолжить здесь.")
    assert result.text == text and result.asked_field is None
    assert result.reason == "model_advisory_accepted"
