"""Finite factual contracts for the observed V7 explanations."""
import json
from copy import deepcopy

import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION, SOURCES, content_issues, corrective_facts
import prompts
import llm_agent
import policy_voice
import core
from config import AppConfig
from tests.test_policy_voice import debt_state


def test_the_existing_credit_chat_also_receives_verified_bankruptcy_facts_when_asked():
    messages = llm_agent._conversation_messages({"facts": {}, "latest_user_message": "Что такое банкротство?"})
    assert BANKRUPTCY_EXPLANATION in messages[1]["content"]
    assert messages[0]["content"] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES


def test_a_card_debit_question_does_not_introduce_bankruptcy_facts():
    messages = llm_agent._conversation_messages({"facts": {}, "latest_user_message": "Почему произошло списание с карты?"})
    assert BANKRUPTCY_EXPLANATION not in messages[1]["content"]


@pytest.mark.parametrize("text", [
    "Списание — это полное прекращение обязательств через суд, обычно когда платить объективно нечем.",
    "Банкротство означает полное освобождение от всех долгов.",
])
def test_categorical_discharge_definition_is_not_a_verified_fact(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("unconditional_discharge",)


@pytest.mark.parametrize("text", [
    "Банкротство не означает полного прекращения всех обязательств.",
    "Списание не гарантирует полного освобождения от всех долгов.",
    "При банкротстве возможно освобождение от требований кредиторов, но алименты сохраняются.",
    "Полное прекращение некоторых обязательств возможно, но другие требования сохраняются.",
    "Полное прекращение всех обязательств не гарантируется.",
])
def test_negation_and_qualified_explanations_remain_usable(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


@pytest.mark.parametrize("text", [
    "Данные уйдут компании — это тестовая передача для проверки связи.",
    "Передача нужна, чтобы проверить связь.",
])
def test_technical_check_is_not_the_business_purpose(text):
    assert content_issues(text, mode="onloader", question_goal="offer_information_purpose") == (
        "engineering_transfer_purpose",
    )


@pytest.mark.parametrize("text", [
    "Сведения нужны компании для связи с вами и консультации. Здесь используется тестовая передача.",
    "Это не передача для проверки связи. Цель — консультация компании по обращению.",
    "Проверка связи не является целью передачи.",
    "Передача не нужна для проверки связи. Цель — консультация по обращению.",
])
def test_local_disclosure_or_denial_does_not_change_consultation_purpose(text):
    assert not content_issues(text, mode="onloader", question_goal="offer_information_purpose")


def test_factual_guards_have_explicit_domain_scope():
    assert not content_issues("Полное прекращение обязательств.", mode="onloader", question_goal=None)
    assert not content_issues("Передача для проверки связи.", mode="debt", question_goal=None)


def test_same_curated_facts_reach_speaker_and_diagnostic_without_a_new_persona():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    instruction = prompts.policy_turn_instruction(frame)
    diagnostic = json.loads(prompts.policy_critic_messages(
        frame, draft="Объяснение", history=[], latest_user_message="Что такое списание?",
    )[-1]["content"])
    assert BANKRUPTCY_EXPLANATION in instruction
    assert diagnostic["task"]["verified_context"]["bankruptcy_basics"] == BANKRUPTCY_EXPLANATION
    assert "алименты" in BANKRUPTCY_EXPLANATION and "текущие платежи" in BANKRUPTCY_EXPLANATION
    assert all(source.url.startswith("https://") and source.checked_on in {"2026-11-07", "2026-11-08"}
               for source in SOURCES)


def test_repair_gets_closed_facts_without_a_prescribed_user_reply():
    assert corrective_facts(("unconditional_discharge",)) == (BANKRUPTCY_EXPLANATION,)
    facts = corrective_facts(("engineering_transfer_purpose",))
    assert len(facts) == 1 and "консультации" in facts[0]
    assert "проверки связи" not in facts[0]


@pytest.mark.parametrize("mode,goal,draft,issue,fact", [
    ("debt", "debt_explanation", "Списание — полное прекращение обязательств.",
     "unconditional_discharge", BANKRUPTCY_EXPLANATION),
    ("onloader", "offer_information_purpose", "Это передача для проверки связи.",
     "engineering_transfer_purpose", "Контакт предназначен компании для связи с клиентом и консультации"),
])
def test_hard_repair_includes_corrective_facts_before_the_untrusted_draft(mode, goal, draft, issue, fact):
    frame = DialogueFrame(None, "collected_held", mode=mode, question_goal=goal)
    instruction = prompts.policy_revision_instruction(frame, draft, needs_repair=True, missing_question=False)
    assert issue in instruction and fact in instruction
    assert instruction.index(fact) < instruction.index("Черновик для редактирования")
    assert instruction.endswith(json.dumps(draft, ensure_ascii=False))


def test_offer_purpose_separates_local_disclosure_from_contact_business_reason():
    frame = DialogueFrame(None, "onloader", mode="onloader", question_goal="offer_information_purpose")
    instruction = prompts.policy_turn_instruction(frame)
    assert "Локальный режим" in instruction
    assert "технической проверкой" in instruction
    assert "консультации" in prompts.verified_policy_purpose(frame)


@pytest.mark.parametrize("text", [
    "Алименты, возмещение вреда и текущие платежи остаются после банкротства.",
    "Требования о возмещении вреда не списываются.",
])
def test_a_generic_harm_claim_is_not_a_statutory_exception_of_every_kind(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("overbroad_harm_exception",)


@pytest.mark.parametrize("text", [
    "Требования о возмещении вреда жизни или здоровью сохраняются.",
    "Возмещение вреда, причинённого жизни или здоровью, сохраняется.",
    "Требования о возмещении морального вреда не списываются.",
    "Возмещение вреда не всегда сохраняется после банкротства, важен вид требования.",
    "Не всякое возмещение вреда сохраняется после банкротства.",
    "Нельзя сказать, что любое возмещение вреда остаётся.",
    "Возмещение вреда — жизни или здоровью — сохраняется.",
])
def test_qualified_or_explicitly_nonuniversal_harm_explanations_remain_valid(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


def test_the_render_entry_repairs_the_recorded_overbroad_harm_exception(monkeypatch):
    bad = "Алименты, возмещение вреда и текущие платежи остаются после банкротства."
    good = "Например, сохраняются алименты, возмещение вреда жизни или здоровью и текущие платежи."
    replies = iter([bad, good])
    calls = []

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(replies)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    result = policy_voice.render({}, AppConfig(openrouter_api_key="synthetic-unused"),
                                 expected=None, status=frame.status, frame=frame,
                                 fallback="Можно обсудить вопрос здесь.")
    assert result.text == good and len(calls) == 2
    assert "overbroad_harm_exception" in calls[1]["turn_instruction"]
    assert "морального вреда" in calls[1]["turn_instruction"]


@pytest.mark.parametrize("text", [
    "Списание возможно только через суд по закону о банкротстве.",
    "Банкротство возможно исключительно в судебном порядке.",
])
def test_the_recorded_court_only_universal_claim_is_not_a_verified_fact(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("court_only_discharge",)


@pytest.mark.parametrize("text", [
    "Списание возможно не только через суд: закон предусматривает и внесудебный порядок.",
    "Нельзя утверждать, что списание возможно только через суд.",
    "При судебном банкротстве освобождение от обязательств рассматривает суд.",
    "При судебном банкротстве списание возможно только через суд.",
])
def test_denial_or_explicit_judicial_scope_is_not_a_court_only_universal_claim(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


def test_the_curated_capsule_distinguishes_legal_existence_from_product_availability():
    assert "судебное и внесудебное банкротство" in BANKRUPTCY_EXPLANATION
    assert "конкретного порядка" in BANKRUPTCY_EXPLANATION
    assert "МФЦ" not in BANKRUPTCY_EXPLANATION


def test_the_render_entry_repairs_the_recorded_court_only_claim(monkeypatch):
    bad = "Списание возможно только через суд по закону о банкротстве."
    good = "Закон предусматривает судебное и внесудебное банкротство. Доступность конкретного порядка здесь не определена."
    replies = iter([bad, good])
    calls = []

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(replies)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    result = policy_voice.render({}, AppConfig(openrouter_api_key="synthetic-unused"),
                                 expected=None, status=frame.status, frame=frame,
                                 fallback="Можно обсудить вопрос здесь.")
    assert result.text == good and len(calls) == 2
    assert "court_only_discharge" in calls[1]["turn_instruction"]
    assert BANKRUPTCY_EXPLANATION in calls[1]["turn_instruction"]


@pytest.mark.parametrize("text", [
    "Списание возможно только через процедуру банкротства.",
    "Освобождение от долга возможно исключительно в процедуре банкротства.",
])
def test_the_general_definition_cannot_exclude_nonbankruptcy_debt_forgiveness(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("bankruptcy_only_discharge",)


@pytest.mark.parametrize("text", [
    "Списание возможно не только через процедуру банкротства.",
    "Нельзя утверждать, что списание возможно только через процедуру банкротства.",
    "При судебном банкротстве освобождение от требований происходит по правилам закона о банкротстве.",
    "В процедуре банкротства списание возможно только через процедуру банкротства.",
])
def test_nonuniversal_and_explicit_bankruptcy_scope_remain_valid(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


def test_the_statutory_counterexample_does_not_promise_a_creditor_will_forgive_the_debt():
    assert "415" in BANKRUPTCY_EXPLANATION and "прощение долга кредитором" in BANKRUPTCY_EXPLANATION
    assert "Согласие кредитора" in BANKRUPTCY_EXPLANATION


def test_the_render_entry_repairs_the_recorded_bankruptcy_only_definition(monkeypatch):
    bad = "Списание возможно только через процедуру банкротства."
    good = "Закон предусматривает и прощение долга кредитором. Его согласие на конкретное прощение здесь не подтверждено."
    replies = iter([bad, good])
    calls = []

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(replies)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    result = policy_voice.render({}, AppConfig(openrouter_api_key="synthetic-unused"),
                                 expected=None, status=frame.status, frame=frame,
                                 fallback="Можно обсудить вопрос здесь.")
    assert result.text == good and len(calls) == 2
    assert "bankruptcy_only_discharge" in calls[1]["turn_instruction"]
    assert "415" in calls[1]["turn_instruction"]


@pytest.mark.parametrize("text", [
    "Возможно освобождение от долгов, кроме алиментов и возмещения вреда.",
    "Списание долгов, за исключением алиментов и возмещения вреда.",
])
def test_discharge_exception_lists_still_require_the_kind_of_harm(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("overbroad_harm_exception",)


@pytest.mark.parametrize("text", [
    "Возможно освобождение от долгов, кроме алиментов и возмещения вреда жизни или здоровью.",
    "Возможно освобождение от долгов, за исключением возмещения морального вреда.",
    "Возможно освобождение от долгов, кроме того, можно обсудить возмещение вреда.",
    "Нельзя утверждать, что возможно освобождение от долгов, кроме алиментов и возмещения вреда.",
    "Если возможно освобождение от долгов, кроме алиментов и возмещения вреда, какие условия?",
    "Фраза «освобождение от долгов, кроме алиментов и возмещения вреда» неточна.",
])
def test_exception_list_detection_preserves_qualified_and_nonasserted_explanations(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_the_recorded_v10_exception_list_is_repaired_as_a_full_private_bounded_answer(monkeypatch):
    first = ("Реструктуризация — это изменение условий долга: например, продление срока или пересмотр графика платежей. "
             "Кредитор может на это пойти, но не обязан.\n\n"
             "Это не то же самое, что банкротство. Закон предусматривает судебное и внесудебное банкротство, "
             "где при соблюдении правил возможно освобождение от долгов, кроме алиментов и возмещения вреда. "
             "Также кредитор может простить долг по статье 415 ГК РФ, но это его право, а не гарантия.\n\n"
             "Что именно из этого вас сейчас интересует?")
    final = first.replace("кроме алиментов и возмещения вреда.",
                          "кроме алиментов и возмещения вреда жизни или здоровью.")
    candidates = iter([first, final])
    bodies = []

    def post(config, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        answer = json.dumps({"accept": True, "flags": []}) if body.get("response_format") else next(candidates)
        return {"choices": [{"message": {"content": answer}}]}

    monkeypatch.setattr(llm_agent, "_post", post)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Exception-list content controls cannot deliver a lead")

    monkeypatch.setattr(core.delivery, "submit", forbidden)
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": 400000})
    state["session_id"] = "synthetic-v10-content"
    before = deepcopy(state["facts"])
    config = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True, policy_voice_enabled=True,
                       extractor_model="synthetic-distinct-critic", fact_interpretation_mode="legacy")
    message = "Чем реструктуризация отличается от списания?"
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, config))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    # Keep the original hard-repair candidate, including its optional question.
    # Only that separate reopening tail is removed before the final critique.
    expected = final.removesuffix("\n\nЧто именно из этого вас сейчас интересует?")
    assert speech == expected
    assert [body["model"] for body in bodies] == [config.model, config.model, config.extractor_model]
    assert "overbroad_harm_exception" in json.dumps(bodies[1], ensure_ascii=False)
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    wire = json.dumps(bodies, ensure_ascii=False)
    assert "Ирина" not in wire and "+70000000401" not in wire
    assert committed["facts"] == before and committed["session_id"] == state["session_id"]
    assert committed["plan"]["fields"] == [] and committed.get("delivery") is None
