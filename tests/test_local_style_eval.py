"""Offline harness checks do not invoke a model or a commercial integration."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json

import pytest
from policy_voice import SpokenPolicyReply

from config import AppConfig
from evaluation import local_style_eval as harness


def test_stage11_review_stays_pending_despite_automatic_contract_pass():
    contract = harness.manual_review_contract()
    assert contract["version"] == "stage11-status-and-intro-v1"
    assert contract["status"] == "pending"
    assert set(contract["criteria"].values()) == {"pending"}
    assert contract["required_reviewers"] == ["executor", "root", "independent_critic"]
    assert contract["automatic_contract_pass_does_not_accept_these_criteria"] is True
    assert contract["customer_style_acceptance"] == "pending"
    # A caller annotating one artifact must not silently accept a later run.
    contract["criteria"]["formal_intro_overpromise"] = "accepted"
    assert harness.manual_review_contract()["criteria"]["formal_intro_overpromise"] == "pending"


def test_source_snapshot_pins_matrix_yaml_content_alongside_python(tmp_path, monkeypatch):
    paths = harness.source_hashes()
    assert "routing/matrix.yaml" in paths
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"unchanged synthetic source")
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    before = harness.source_hashes()
    (tmp_path / "routing/matrix.yaml").write_bytes(b"script: []\n")
    after = harness.source_hashes()
    changed = [name for name in before if before[name] != after[name]]
    assert changed == ["routing/matrix.yaml"]


def test_stage11_keeps_personas_and_examples_except_approved_identity_and_bootstrap_answers():
    corpus = json.dumps([asdict(case) for case in harness.cases() + harness.targeted_cases()],
                        ensure_ascii=False, sort_keys=True)
    assert hashlib.sha256(corpus.encode()).hexdigest() == "5fb03ae1330b395889bb5e2ef2edf82c66430c41aeb240fa412f261eeefd6344"
    old_identity_answer = "Работаю с этим каждый день, вопросы задаю одни и те же — немудрено спутать."
    new_identity_answer = "Я AI-помощник МБК. Помогаю разобраться с вашим вопросом."
    examples = str(harness.prompts.STYLE_EXAMPLES)
    assert examples.count(new_identity_answer) == 1 and old_identity_answer not in examples
    restored_examples = examples.replace(new_identity_answer, old_identity_answer)
    old_first_answer = "Давайте разберёмся. Прежде чем продолжим, подскажите, пожалуйста, как к вам обращаться?"
    new_first_answer = "Подскажите, как к вам обращаться?"
    new_first_line = f"{harness.prompts.ASSISTANT_NAME}: {new_first_answer}"
    old_first_line = f"{harness.prompts.ASSISTANT_NAME}: {old_first_answer}"
    assert restored_examples.count(new_first_line) == 1 and old_first_line not in restored_examples
    restored_examples = restored_examples.replace(new_first_line, old_first_line)
    assert hashlib.sha256(restored_examples.encode()).hexdigest() == "c3afafe70f33a6c1fa4845f44095661eaaf245e5aa15296423604b7ff39253ba"


@pytest.mark.parametrize(("message", "reply", "diagnostic_count"), [
    ("Казань, Татарстан.", "Город и регион увидела, спасибо.\n\nПо долгам у меня пока всё.", 1),
    ("Казань, Татарстан.", "Эти данные у меня уже есть.\n\nПередача заявки ещё не подключена.", 0),
])
def test_actual_v5_isolated_status_receipts_need_new_manual_review(message, reply, diagnostic_count):
    # The old finite grammar sees only one of these two semantically dry receipts.
    assert harness.neutral_receipt_prefix_count([reply], message) == diagnostic_count
    assert harness.natural_tone_contracts(message, [reply], refused=False) == []
    assert harness.TONE_RUBRIC_VERSION == "natural-tone-v1"
    assert harness.manual_review_contract()["criteria"]["unrequested_fact_status_receipts"] == "pending"


@pytest.mark.parametrize(("message", "reply", "refused"), [
    ("Боюсь, уже не справляюсь.", "Понимаю, это тяжело. Давайте разберём, что можно сделать.", False),
    ("Вы учли сумму?", "Поняла, 400 тысяч.", False),
    ("Номер оставлять не хочу.", "Хорошо, продолжим без номера.", True),
    ("Почему уточняете сумму?", "Сумма помогает понять, какие варианты имеет смысл обсуждать.", False),
])
def test_stage11_positive_emotion_confirmation_refusal_and_explanation_remain_valid(message, reply, refused):
    assert harness.natural_tone_contracts(message, [reply], refused=refused) == []


def test_actual_v2_personalized_declarative_phone_request_triggers_controlled_refusal():
    reply = ("Чтобы подготовить анкету, мне нужен ваш номер телефона.\n\n"
             "Продолжить разговор и задать вопросы можно и без него — контакт нужен только для оформления.")
    assert "client.phone" in harness.requested_fields(reply)
    case = harness.targeted_cases()[1]
    detour = harness.choose_detour(case, harness._initial(case), reply, set(), None, 4)
    assert detour is not None and detour.id == "phone_no"
    assert harness.phone_refused("Не хочу оставлять. Можно без банкротства?", reply)


@pytest.mark.parametrize("reply", [
    "Ваш номер уже известен, повторять его не нужно.",
    "Мне не нужен ваш номер телефона.",
    "Телефон не нужен, продолжим здесь.",
    "Номер нужен для оформления заявки, а вопросы можно обсудить здесь.",
])
def test_known_unneeded_or_generic_contact_purpose_is_not_personalized_data_request(reply):
    assert "client.phone" not in harness.requested_fields(reply)


def test_actual_v2_application_constraint_with_available_chat_is_not_generic_phone_coercion():
    reply = "Без номера анкету оформить не получится, но общаться здесь мы можем спокойно."
    assert harness.natural_tone_contracts("Что можно сделать?", [reply], refused=False) == []


@pytest.mark.parametrize("reply", [
    "Без номера не получится продолжить чат. Для оформления заявки номер нужен. Вопросы можно здесь.",
    "Номер обязателен, чтобы продолжать чат, для оформления заявки. Вопросы можно здесь.",
    "Номер обязателен для оформления заявки, чтобы продолжить чат. Вопросы можно здесь.",
])
def test_application_mention_never_excuses_a_separate_chat_prerequisite(reply):
    findings = harness.natural_tone_contracts("Что можно сделать?", [reply], refused=False)
    assert any(row["code"] == "phone_pressure_before_refusal" for row in findings)


def test_actual_v2_conditional_onloader_question_is_not_a_recorded_personal_refusal():
    case = harness.cases()[4]
    assert harness.terminal_reason(case, {}, "А если не хочу передавать?") is None
    assert harness.terminal_reason(case, {}, "Я не хочу передавать. Можно общаться здесь?") == "verbal_refusal_conversation_only"
    assert harness.terminal_reason(case, {}, "Нет, не разрешаю передачу. Никаких кнопок нажимать не хочу.") == "verbal_refusal_conversation_only"
    assert harness.terminal_reason(case, {}, "Не передавать.") == "verbal_refusal_conversation_only"


def test_v2_measurement_fix_is_versioned_without_changing_personas_or_tone_thresholds():
    assert harness.MEASUREMENT_CONTRACT_VERSION == "stage11-request-and-refusal-scope-v2"
    assert harness.TONE_RUBRIC_VERSION == "natural-tone-v1"
    assert (harness.RECEIPT_STREAK_LIMIT, harness.RECEIPT_PREFIX_LIMIT) == (3, 2)


def test_actual_v3_served_phone_act_drives_client_without_changing_independent_quality_oracle():
    case = harness.targeted_cases()[1]
    text = ("Чтобы сохранить ответы в анкете, нужен номер телефона. "
            "Продолжить можно и без номера, если пока не готовы оставить.")
    assert "client.phone" not in harness.requested_fields(text)
    detour = harness.choose_detour(case, harness._initial(case), text, set(), None, 4,
                                   served_question_field="client.phone")
    assert detour is not None and detour.id == "phone_no"
    evidence = harness.control_trigger_evidence(detour, {"bot": [text], "voice_question_field": "client.phone"})
    assert evidence["source"] == "served_question_act"
    assert evidence["visible_reply"] == text
    assert evidence["manual_confirmation"] == "pending"
    assert evidence["automatic_coverage_certified"] is False


def test_producer_phone_metadata_without_a_visible_request_never_certifies_coverage():
    case = harness.targeted_cases()[1]
    text = "Номер уже сохранён, повторять его не нужно."
    detour = harness.choose_detour(case, harness._initial(case), text, set(), None, 4,
                                   served_question_field="client.phone")
    assert detour is not None
    evidence = harness.control_trigger_evidence(detour, {"bot": [text], "voice_question_field": "client.phone"})
    assert evidence["independent_text_request_observed"] is False
    assert evidence["manual_confirmation"] == "pending"
    assert evidence["automatic_coverage_certified"] is False
    assert harness.choose_detour(case, harness._initial(case), text, set(), None, 4) is None


def test_independently_observed_control_stimulus_keeps_its_separate_proof():
    case = harness.targeted_cases()[1]
    text = "На какой номер телефона оформить заявку?"
    detour = harness.choose_detour(case, harness._initial(case), text, set(), None, 4)
    evidence = harness.control_trigger_evidence(detour, {"bot": [text], "voice_question_field": "client.phone"})
    assert evidence["source"] == "independent_text_observer"
    assert evidence["automatic_coverage_certified"] is True
    assert evidence["manual_confirmation"] == "not_required"


def test_metadata_only_client_stimulus_stays_uncertified_in_complete_case(monkeypatch):
    from copy import deepcopy

    case = harness.targeted_cases()[1]
    monkeypatch.setattr(harness.core, "stream_opening", lambda *args, **kwargs: iter(["Здравствуйте."]))
    monkeypatch.setattr(harness, "_actor", lambda *args: "Продолжим.")
    replies = iter(["Номер уже сохранён, повторять его не нужно.",
                    "Можно продолжить общение здесь.",
                    "Реструктуризация меняет условия и график платежей."])

    def execute(state, message, config, usage):
        prepared = deepcopy(state)
        prepared["dialog_phase"] = "contact_declined_held"
        text = next(replies)
        voice = {"source": "model", "reason": "model_critic_accepted", "question_field": "client.phone",
                 "critic_assessments": [{"model": config.extractor_model, "attempted": True,
                                         "reason": "accepted", "verdict": {"accept": True, "flags": []}}]}
        return harness.TurnExecution(prepared, prepared, [text], voice, 0.0)

    monkeypatch.setattr(harness, "execute_core_turn", execute)
    result = harness.run_case(case, settings(), max_turns=3)
    assert result["completion_met"] is True and result["missing_controlled_detours"] == []
    assert result["dialog"][2]["contract_id"] == "phone_no"
    assert result["dialog"][2]["control_trigger_evidence"]["visible_reply"] == "Номер уже сохранён, повторять его не нужно."
    assert result["control_coverage_automatically_certified"] is False
    assert result["control_coverage_manual_review_required"] is True
    assert result["execution_status"] == "completed_pending_control_validation"


def settings() -> AppConfig:
    return AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=True,
                     debt_policy_enabled=True, entry_routing_enabled=False,
                     extractor_model="deepseek/deepseek-v4-pro")


@pytest.fixture(autouse=True)
def offline_semantic_critic(monkeypatch):
    """Harness unit tests never call a paid model, including the new diagnostic role."""
    monkeypatch.setattr(harness.llm_agent, "generate_policy_critique",
                        lambda *args, **kwargs: '{"accept":true,"flags":[]}', raising=False)


def test_case_matrix_contains_two_standard_two_debt_and_one_known_onloader():
    cases = harness.cases()
    assert [case.kind for case in cases] == ["normal", "normal", "debt", "debt", "onloader"]
    assert cases[2].known_contact and cases[4].known_contact
    assert "phone" not in harness._initial(cases[4])["facts"].get("client", {})


def test_local_initial_debt_policy_is_latched_without_contact_loss():
    state = harness._initial(harness.cases()[2])
    assert state["questionnaire_policy"]["mode"] == "debt"
    assert state["facts"]["client"]["name"] == "Ирина"
    assert state["facts"]["client"]["phone"]


def test_delivery_is_forbidden_even_if_an_unexpected_caller_tries_it():
    original = harness.core.delivery.submit
    with harness.service_boundaries(settings()) as counts:
        with pytest.raises(RuntimeError, match="forbidden"):
            harness.core.delivery.submit({}, None)
    assert counts["delivery_attempts"] == 1
    assert harness.core.delivery.submit is original


def test_local_geography_resolves_only_grounded_bare_oblique_city_against_catalogue():
    facts = {"client": {"region_class": "rf_other"}, "assets": {"realty": {"city": "Казани"}}}
    result = harness.local_geography(facts, "Да, квартира в Казани.", harness.core.get_matrix())
    assert result["assets"]["realty"]["region_class"] == "mbk_list"
    assert result["assets"]["realty"]["city"] == "Казани"
    assert "region_class" not in facts["assets"]["realty"]
    assert "region" not in result["assets"]["realty"]
    assert harness.SERVICE_BOUNDARY_VERSION == "local-geography-stored-city-parity-v2"


@pytest.mark.parametrize("city", [None, "Неизвестныйгород", "Да, именно там."])
def test_local_geography_does_not_guess_a_property_city_from_confirmation_or_client(city):
    facts = {"client": {"city": "Казань", "region_class": "rf_other"}, "assets": {"realty": {}}}
    if city is not None:
        facts["assets"]["realty"]["city"] = city
    result = harness.local_geography(facts, "Да, именно там.", harness.core.get_matrix(),
                                     asked_paths=["assets.realty.region_class"])
    assert result["assets"]["realty"].get("region_class") is None


def test_local_geography_keeps_conflicting_stored_city_and_region_unknown():
    facts = {"assets": {"realty": {"city": "Казани", "region": "Москва"}}}
    result = harness.local_geography(facts, "Да, именно там.", harness.core.get_matrix(),
                                     asked_paths=["assets.realty.region_class"])
    assert result["assets"]["realty"].get("region_class") is None


def test_transport_allowlist_and_budget_fail_before_unknown_network(monkeypatch):
    calls = []
    monkeypatch.setattr(harness.llm_agent, "_urlopen", lambda *args: calls.append(args))
    from urllib.request import Request
    with harness.service_boundaries(settings(), request_limit=1) as counts:
        with pytest.raises(RuntimeError, match="Non-model"):
            harness.llm_agent._urlopen(settings(), Request("https://crm.example.invalid/private"))
        harness.llm_agent._urlopen(settings(), Request("https://openrouter.ai/api/v1/chat/completions"))
        with pytest.raises(RuntimeError, match="budget"):
            harness.llm_agent._urlopen(settings(), Request("https://openrouter.ai/api/v1/chat/completions"))
    assert counts["model_http_requests"] == 1
    assert len(calls) == 1


def test_real_core_entrypoint_has_delivery_disabled_and_turn_limit(monkeypatch):
    calls = []
    actual_prepare = harness.core.prepare_turn

    def prepare(*args, **kwargs):
        calls.append(kwargs["submit_delivery"])
        return actual_prepare(*args, **kwargs)

    monkeypatch.setattr(harness, "_client_reply", lambda *args: "У меня долгов 400 тысяч рублей")
    monkeypatch.setattr(harness.core, "prepare_turn", prepare)
    monkeypatch.setattr(harness.llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs:
                        json.dumps({"text": "", "question": "В каком городе вы живёте?", "question_field": "client.region_class"}))
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[2], settings(), max_turns=2)
    assert calls == [False, False]
    assert result["user_turns"] == 2
    assert result["delivery_present"] is False
    assert result["final_policy"] == "debt"
    assert "synthetic-opaque-contact-handle" not in json.dumps(result)


def test_onloader_typed_yes_remains_without_consent(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "да")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs:
                        json.dumps({"text": "Решение можно выбрать кнопкой под предложением.", "question": None, "question_field": None}))
    with harness.service_boundaries(settings()) as counts:
        result = harness.run_case(harness.cases()[4], settings(), max_turns=2)
    assert result["onloader_decision"] is None
    assert counts["delivery_attempts"] == 0
    assert result["user_turns"] == 2


@pytest.mark.parametrize("limit", [0, 23])
def test_invalid_turn_budget_is_rejected_before_any_model(limit):
    with pytest.raises(ValueError):
        harness.run_case(harness.cases()[0], settings(), max_turns=limit)


def test_model_failure_is_reported_without_private_error_body(monkeypatch):
    monkeypatch.setattr(harness.core, "stream_opening", lambda *args, **kwargs: iter(["Здравствуйте"]))

    def fail(*args):
        raise RuntimeError("PRIVATE_PROVIDER_BODY")

    monkeypatch.setattr(harness, "_client_reply", fail)
    result = harness.run_case(harness.cases()[0], settings(), max_turns=1)
    assert result["error_class"] == "RuntimeError"
    assert "PRIVATE_PROVIDER_BODY" not in json.dumps(result)


def test_failed_opening_still_returns_a_bounded_report_without_fake_style_scores(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_OPENING")
    monkeypatch.setattr(harness.core, "stream_opening", fail)
    result = harness.run_case(harness.cases()[0], settings(), max_turns=1)
    assert result["error_class"] == "RuntimeError"
    assert result["mean_humanity"] is None and result["coherence_score"] is None
    assert "PRIVATE_PROVIDER_OPENING" not in json.dumps(result)


def test_precommit_voice_source_survives_commit_and_is_not_reported_as_pure_model_success(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Почему нужен номер?")

    def speech(prepared, *args, **kwargs):
        prepared["_policy_spoken"] = SpokenPolicyReply("Номер повторять не нужно.", None, "fallback", "invalid_speech")
        return iter(["Номер повторять не нужно."])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    checkpoints = []
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=1, checkpoint=checkpoints.append)
    assert result["voice_fallback_count"] == 1
    assert result["voice_model_count"] == 0
    assert result["execution_status"] == "incomplete"
    assert result["dialog"][1]["voice_reason"] == "invalid_speech"
    assert checkpoints[0]["dialog"][1]["voice_source"] == "fallback"


def test_stop_request_prevents_any_new_client_model_call(monkeypatch):
    called = []
    monkeypatch.setattr(harness, "_client_reply", lambda *args: called.append(True))
    result = harness.run_case(harness.cases()[4], settings(), max_turns=8, stop_requested=lambda: True)
    assert result["execution_status"] == "stopped"
    assert result["user_turns"] == 0
    assert called == []


def test_candidate_observer_records_successful_text_without_replacing_the_model_response(monkeypatch):
    candidate = "Это только пояснение к предложению."
    received = []
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "А что это значит?")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: candidate)

    def speech(prepared, *args, **kwargs):
        actual = harness.llm_agent.generate_reply({}, settings())
        received.append(actual)
        prepared["_policy_spoken"] = SpokenPolicyReply(actual, None, "model", "answer_only")
        return iter([actual])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=1)
    assert received == [candidate]
    assert result["dialog"][1]["voice_candidates"] == [candidate]
    assert result["dialog"][1]["bot"] == [candidate]


def test_no_non_https_endpoint_can_enter_live_boundary():
    with pytest.raises(ValueError, match="HTTPS"):
        with harness.service_boundaries(replace(settings(), base_url="http://example.invalid")):
            pass


def test_original_personas_are_preserved_and_targeted_detours_are_separate():
    originals = harness.cases()
    targeted = harness.targeted_cases()
    assert len(originals) == 5 and len(targeted) == 3
    assert all(not case.targeted and not case.detours for case in originals)
    assert all(case.targeted and case.detours for case in targeted)
    assert originals[2].persona == targeted[0].persona


@pytest.mark.parametrize("text,field", [
    ("Какая общая сумма долга?", "debts.total"),
    ("Укажите номер телефона для связи.", "client.phone"),
    ("Есть ли активные просрочки?", "debts.overdue_duration"),
    ("На какую сумму кредита вы рассчитываете?", "request.amount"),
])
def test_independent_collection_oracle_detects_questions_and_imperatives(text, field):
    assert field in harness.requested_fields(text)


def test_contact_reassurance_is_not_a_contact_request():
    assert "client.phone" not in harness.requested_fields("Номер повторно вводить не нужно. Чем ещё помочь?")
    assert "client.phone" not in harness.requested_fields("Зачем спрашивать телефон? Он уже есть.")


@pytest.mark.parametrize("reply", [
    "Номер уже есть. Хотите, объясню, зачем нужен телефон?",
    "Вы спрашиваете, зачем уточнять сумму долга?",
    "Ваше согласие не получено, поэтому данные не передаю.",
    "Ваши данные не переданы компании.",
])
def test_meta_questions_and_negated_action_receipts_are_positive_controls(reply):
    findings = harness.turn_contracts(harness.cases()[4], {}, "Объясните", [reply],
                                      known={"client.phone", "debts.total"}, refused=False)
    assert findings == []


def test_explicit_correction_to_an_uncertain_range_reopens_the_reference_amount():
    state = {"facts": {"debts": {"total": 400000}}, "questionnaire_policy": {
        "amount_observation": {"kind": "range", "lower": 300000, "upper": 500000}}}
    known = {"debts.total", "client.phone"}
    harness.update_known_ledger(known, state, harness.cases()[2], "Я ошиблась, там от 300 до 500 тысяч")
    assert "debts.total" not in known and "client.phone" in known


def test_score100_cannot_hide_repeated_known_fact():
    text = "Какая у вас общая сумма долга?"
    assert harness.v4_humanity_score([text])[0] == 100
    findings = harness.turn_contracts(harness.cases()[2], {"plan": {"fields": []}}, "400 тысяч", [text],
                                      known={"debts.total"}, refused=False)
    assert "known_fact_requested_again" in {item["code"] for item in findings}


def test_score100_cannot_hide_unplanned_overdue_collection():
    text = "Есть ли сейчас просрочки?"
    assert harness.v4_humanity_score([text])[0] == 100
    findings = harness.turn_contracts(harness.cases()[2], {"plan": {"fields": ["client.region_class"]}}, "400 тысяч", [text],
                                      known=set(), refused=False)
    assert "unplanned_collection_field" in {item["code"] for item in findings}


def test_score100_cannot_hide_phone_pressure_after_refusal():
    text = "Оставьте номер телефона, чтобы с вами связались."
    assert harness.v4_humanity_score([text])[0] == 100
    findings = harness.turn_contracts(harness.cases()[3], {"plan": {"fields": ["client.phone"]}}, "Номер не дам", [text],
                                      known=set(), refused=True)
    assert "phone_pressure_after_refusal" in {item["code"] for item in findings}


def test_pressure_without_a_question_mark_is_still_a_failure_after_no():
    text = "Жаль, без номера помочь не получится."
    findings = harness.turn_contracts(harness.cases()[3], {}, "Не оставлю", [text], known=set(), refused=True)
    assert "phone_pressure_after_refusal" in {item["code"] for item in findings}


def test_contact_pronoun_after_a_single_phone_request_keeps_its_entity_scope():
    previous = "Оставьте, пожалуйста, номер телефона для связи."
    reply = "Без номера не получится — это единственное, чего сейчас не хватает для завершения. Напишите его, пожалуйста, в любом формате."
    findings = harness.turn_contracts(harness.cases()[3], {"plan": {"fields": ["client.phone"]}},
                                      "Номер оставлять не хочу. Можно без него?", [reply], known=set(), refused=True,
                                      previous_reply=previous)
    assert "phone_pressure_after_refusal" in {item["code"] for item in findings}


def test_narrow_callback_explanation_does_not_forbid_continuing_without_a_phone():
    reply = "Без номера не получится позвонить. Но на вопросы я могу ответить здесь."
    findings = harness.turn_contracts(harness.cases()[3], {}, "Можно без телефона?", [reply],
                                      known=set(), refused=True, previous_reply="На какой номер можно с вами связаться?")
    assert findings == []


def test_pronoun_is_not_assumed_to_be_a_phone_after_a_different_entity_question():
    findings = harness.turn_contracts(harness.cases()[0], {}, "Ответ", ["Напишите его, пожалуйста."],
                                      known=set(), refused=True, previous_reply="В каком городе вы живёте?")
    assert findings == []


def test_consent_prompt_after_verbal_no_is_not_a_meaningful_response():
    text = "Разрешаете передать сведения текущей заявки для консультации?"
    assert harness.v4_humanity_score([text])[0] == 100
    findings = harness.turn_contracts(harness.cases()[4], {}, "Нет", [text], known=set(), refused=False,
                                      transfer_refused=True)
    assert "consent_pressure_after_verbal_refusal" in {item["code"] for item in findings}


@pytest.mark.parametrize("text,code", [
    ("Передаю ваши данные компании.", "unsupported_delivery_claim"),
    ("Заявка отправлена партнёру.", "unsupported_delivery_claim"),
    ("Согласие сохранено.", "unsupported_decision_recording_claim"),
    ("Больше никто доступа не получит.", "unsupported_privacy_or_call_promise"),
])
def test_score100_cannot_hide_business_claims(text, code):
    assert harness.v4_humanity_score([text])[0] == 100
    findings = harness.turn_contracts(harness.cases()[4], {}, "Да", [text], known=set(), refused=False)
    assert code in {item["code"] for item in findings}


def test_no_send_statement_is_a_positive_control_for_action_oracle():
    findings = harness.turn_contracts(harness.cases()[4], {}, "Нет", ["Хорошо, я не передаю ваши данные."],
                                      known=set(), refused=False)
    assert findings == []


def test_controlled_faq_requires_both_parts_of_an_answer():
    contract = harness.targeted_cases()[0].detours[1]
    missing = harness.turn_contracts(harness.cases()[2], {}, contract.message, ["Подскажите вашу сумму долга?"],
                                     known=set(), refused=False, detour=contract)
    assert "controlled_question_unanswered" in {item["code"] for item in missing}
    answered = harness.turn_contracts(harness.cases()[2], {}, contract.message,
                                      ["Реструктуризация меняет график платежей. При банкротстве рассматривается освобождение от долгов."],
                                      known=set(), refused=False, detour=contract)
    assert answered == []


def test_repeating_the_previous_prompt_does_not_pass_keyword_answer_groups():
    previous = "Компания рассматривает обращение для консультации. Разрешаете передачу?"
    contract = harness.targeted_cases()[2].detours[2]
    findings = harness.turn_contracts(harness.cases()[4], {}, contract.message, [previous],
                                      known=set(), refused=False, detour=contract, previous_reply=previous)
    assert "controlled_detour_repeated_previous_prompt" in {item["code"] for item in findings}


def test_eight_turns_without_terminal_are_not_full_completion(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Объясните ещё раз")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: "Это предложение для консультации.")
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=8)
    assert result["user_turns"] == 8
    assert result["completion_met"] is False and result["execution_status"] == "incomplete"
    assert result["contract_passed"] is False


def test_conversation_refusal_is_followed_by_exactly_two_labelled_tail_turns(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Нет, не разрешаю передачу")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: "Хорошо, ничего не передаю.")
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=22)
    assert result["completion_met"] is True and result["post_terminal_turns"] == 2
    assert result["user_turns"] == 3
    assert [row["message_source"] for row in result["dialog"][1:]] == ["stochastic_actor", "postterminal_tail", "postterminal_tail"]
    assert result["onloader_decision"] is None


@pytest.mark.parametrize("reply,known,requested", [
    ("Ирина, а в каком городе и регионе находится эта квартира?", {"client.region_class"}, "assets.realty.region_class"),
    ("Машина сейчас в кредите или залоге, или чистая?", {"assets.has_vehicle"}, "assets.vehicle.pledged"),
])
def test_different_property_and_vehicle_attributes_are_not_repeats(reply, known, requested):
    fields = harness.requested_fields(reply)
    assert requested in fields and not fields.intersection(known)
    assert harness.turn_contracts(harness.cases()[0], {}, "Ответ", [reply], known=known, refused=False) == []


def test_question_entities_do_not_bleed_between_clauses():
    fields = harness.requested_fields("В каком городе вы живёте? Есть ли квартира в собственности?")
    assert "client.region_class" in fields
    assert "assets.realty.region_class" not in fields


def test_confirmed_entity_subfields_are_tracked_for_future_repeat_checks():
    state = {"facts": {"assets": {"realty": {"region_class": "rf_other"}, "vehicle": {"pledged": False}}}}
    known = harness._known_fields(state, harness.cases()[0])
    assert {"assets.realty.region_class", "assets.vehicle.pledged"}.issubset(known)


def test_local_geo_stub_uses_extracted_property_city_without_an_extra_region_question():
    facts = {"client": {"region_class": "rf_other"}, "assets": {"has_realty": True,
             "realty": {"kind": "flat", "city": "Казань"}}}
    result = harness.local_geography(facts, "Да, квартира в Казани.", harness.core.get_matrix(),
                                     asked_paths=["assets.has_realty"])
    expected = harness.classify_region_confident("Казань", harness.core.get_matrix(), include_realty_list=True)
    assert result["assets"]["realty"]["region_class"] == expected
    assert result["client"]["region_class"] == "rf_other"
    assert "region_class" not in facts["assets"]["realty"]


def test_local_geo_stub_reuses_the_existing_declensional_city_normalizer():
    result = harness.local_geography({}, "В Казани.", harness.core.get_matrix(),
                                     asked_paths=["assets.realty.region_class"])
    expected = harness.classify_region_confident("Казань", harness.core.get_matrix(), include_realty_list=True)
    assert result["assets"]["realty"]["region_class"] == expected
    assert "client" not in result


def test_unknown_local_geography_remains_unknown_instead_of_defaulting_to_a_region():
    result = harness.local_geography({}, "В Несуществующеграде.", harness.core.get_matrix(),
                                     asked_paths=["client.region_class"])
    assert result == {}


def test_main_lifecycle_guard_is_checked_before_any_model_and_records_the_silent_user(monkeypatch):
    state = harness._initial(harness.cases()[4])
    state["chat_history"] = [{"role": "user", "content": "Спасибо"}, {"role": "assistant", "content": "Всего доброго"}]
    before_count = state["message_count"]

    def forbidden(*args, **kwargs):
        raise AssertionError("A silent lifecycle turn must not reach preparation or a model")

    monkeypatch.setattr(harness.core, "prepare_turn", forbidden)
    monkeypatch.setattr(harness.llm_agent, "generate_reply", forbidden)
    result = harness.execute_core_turn(state, "Спасибо", settings(), harness.SessionUsage("synthetic"))
    assert result.bubbles == []
    assert result.state["chat_history"] == state["chat_history"] + [{"role": "user", "content": "Спасибо"}]
    assert result.state["message_count"] == before_count
    assert result.voice["source"] == "lifecycle_silence"


def test_user_departure_is_distinct_from_completed_intake_and_missing_detours_stay_missing(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Спасибо")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: "Всего доброго.")
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.targeted_cases()[0], settings(), max_turns=22)
    assert result["terminal_reason"] == "user_departure"
    assert result["completion_met"] is True and result["goal_completion_met"] is False
    assert "why_debt" in result["missing_controlled_detours"]
    assert result["contract_passed"] is False


def test_property_presence_does_not_confirm_ownership_form_or_owners():
    reply = ("Ирина, квартира в частной собственности или муниципальная? "
             "И кто собственник — только вы, вы с супругом или есть другие владельцы?")
    fields = harness.requested_fields(reply)
    assert fields == {"assets.realty.ownership_form", "assets.realty.owners"}
    known = harness._known_fields({"facts": {"assets": {"has_realty": True}}}, harness.cases()[0])
    assert harness.turn_contracts(harness.cases()[0], {}, "Да, квартира в Казани.", [reply],
                                  known=known, refused=False) == []


@pytest.mark.parametrize("reply,field", [
    ("Квартира в частной собственности или муниципальная?", "assets.realty.ownership_form"),
    ("Кто собственник — вы или другие владельцы?", "assets.realty.owners"),
    ("Есть ли квартира в собственности?", "assets.has_realty"),
])
def test_confirmed_property_attribute_repetition_still_fails(reply, field):
    state = {"facts": {"assets": {"has_realty": True, "realty": {
        "ownership_form": "private", "owners": "self"}}}}
    known = harness._known_fields(state, harness.cases()[0])
    findings = harness.turn_contracts(harness.cases()[0], state, "Ответ", [reply],
                                      known=known, refused=False)
    assert any(item["code"] == "known_fact_requested_again" and item["field"] == field for item in findings)


def test_incomplete_held_is_conversation_terminal_without_completed_goal(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Не помню")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: "Уточните сумму долга?")
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[2], settings(), max_turns=22)
    assert result["terminal_reason"] == "incomplete_held"
    assert result["completion_met"] is True
    assert result["goal_completion_met"] is False
    assert result["contract_passed"] is False


@pytest.mark.parametrize("message", ["Не хочу оставлять.", "Не оставляю номер."])
def test_declined_phone_wording_is_recognized_in_an_actual_phone_turn(message):
    assert harness.phone_refused(message, "Оставьте, пожалуйста, номер телефона для связи.")


@pytest.mark.parametrize("reply", [
    "Без номера заявку дальше не передать, это обязательное условие. Но настаивать не буду.",
    "Без номера не продолжить работу по вашему вопросу.",
    "Номер телефона — обязательное условие.",
])
def test_after_refusal_impossibility_or_obligation_claim_is_pressure(reply):
    findings = harness.turn_contracts(harness.cases()[3], {}, "Не оставляю номер.", [reply],
                                      known=set(), refused=True)
    assert any(item["code"] == "phone_pressure_after_refusal" for item in findings)


@pytest.mark.parametrize("reply", [
    "Номер нужен специалисту, чтобы перезвонить. Но можем продолжить обсуждение здесь без него.",
    "Без номера специалист не сможет позвонить. На вопросы по долгам отвечу здесь.",
])
def test_truthful_phone_purpose_with_available_chat_is_not_pressure(reply):
    findings = harness.turn_contracts(harness.cases()[3], {}, "Можно без него?", [reply],
                                      known=set(), refused=True)
    assert findings == []


def test_actual_continue_here_answer_meets_phone_no_detour_without_keyword_overlap():
    detour = harness.targeted_cases()[1].detours[0]
    findings = harness.turn_contracts(harness.cases()[3], {}, detour.message,
        ["Конечно, продолжим прямо здесь. Что именно вас сейчас беспокоит по долгам?"],
        known=set(), refused=True, detour=detour)
    assert findings == []


def test_conditional_chat_continuation_does_not_pass_phone_refusal_contract():
    detour = harness.targeted_cases()[1].detours[0]
    findings = harness.turn_contracts(harness.cases()[3], {}, detour.message,
        ["Продолжим здесь, если оставите номер телефона."], known=set(), refused=True, detour=detour)
    assert {"phone_pressure_after_refusal", "controlled_question_unanswered"}.issubset(
        {finding["code"] for finding in findings})


@pytest.mark.parametrize("reply,code", [
    ("Без передачи контактов консультация невозможна, но это ваше право.", "consent_pressure_after_verbal_refusal"),
    ("Хорошо, не настаиваю. Передачу отменяем.", "unsupported_cancellation_claim"),
])
def test_actual_onloader_refusal_cannot_imply_impossible_chat_or_cancelled_operation(reply, code):
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Нет, не передавать.", [reply], known=set(), refused=False, transfer_refused=True)
    assert code in {finding["code"] for finding in findings}


def test_offer_refusal_keeps_available_chat_without_recording_business_decision():
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Не передавать.", ["Не буду передавать. На вопросы могу ответить здесь."],
        known=set(), refused=False, transfer_refused=True)
    assert findings == []
    assert harness.terminal_reason(harness.cases()[4], {}, "Не передавать.") == "verbal_refusal_conversation_only"


def test_observed_refusal_is_not_a_substitute_for_actual_bound_application_preference():
    case = harness.cases()[4]
    state = harness._initial(case)
    findings = harness.refusal_state_contracts(case, state, observed_refusal=True)
    assert [item["code"] for item in findings] == ["contact_refusal_not_latched"]
    assert harness._turn("Не передавать.", ["Хорошо"], 0, state)["application_offer_declined"] is False


def test_actual_same_presentation_refusal_proves_latch_but_other_presentation_does_not():
    case = harness.cases()[4]
    state = harness._initial(case)
    state["onloader_dialogue"] = {"presentation_id": state["onloader"]["presentation_id"], "declined": True}
    assert harness.refusal_state_contracts(case, state, observed_refusal=True) == []
    assert harness._turn("Нет", ["Хорошо"], 0, state)["application_offer_declined"] is True
    state["onloader_dialogue"]["presentation_id"] = "different-synthetic-presentation"
    assert harness.refusal_state_contracts(case, state, observed_refusal=True)


def test_actual_verbal_yes_cannot_be_acknowledged_as_accepted_permission():
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Да.", ["Спасибо, разрешение приняла."], known=set(), refused=False)
    assert "unsupported_decision_recording_claim" in {finding["code"] for finding in findings}


def test_explicit_nonacceptance_of_permission_is_a_positive_control():
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Да.", ["Разрешение не приняла. Текстовая реплика не сохраняет решение."],
        known=set(), refused=False)
    assert findings == []


def test_actual_residence_alternative_is_a_known_region_repeat():
    reply = "Вы живёте в самой Москве или в области? С регионом разобрались, спасибо."
    assert "client.region_class" in harness.requested_fields(reply)
    findings = harness.turn_contracts(harness.cases()[3], {"plan": {"fields": ["client.name"]}},
        "Именно в Москве.", [reply], known={"client.region_class"}, refused=False)
    assert {"known_fact_requested_again", "unplanned_collection_field"}.issubset(
        {finding["code"] for finding in findings})
    assert "client.region_class" not in harness.requested_fields("Вы живёте один или с семьёй?")


def test_thanking_for_consent_and_denial_of_future_calls_without_authority_fail():
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Да.", ["Спасибо за согласие. Никаких реальных звонков или обязательств сейчас не будет."],
        known=set(), refused=False)
    assert {"unsupported_decision_recording_claim", "unsupported_privacy_or_call_promise"}.issubset(
        {finding["code"] for finding in findings})


def test_generic_purpose_explanation_does_not_claim_received_consent():
    findings = harness.turn_contracts(harness.cases()[4], {"onloader": {"decision": None}},
        "Зачем это?", ["Телефон нужен для связи со специалистом. Согласие пока не получено."],
        known=set(), refused=False)
    assert findings == []


@pytest.mark.parametrize("reason", ["model_question_recovered", "model_revised_typed_question",
    "review_unavailable_degraded_typed_question", "review_rejected_degraded_typed_question"])
def test_recorded_name_recovery_requires_a_name_question_in_final_served_bubbles(reason):
    voice = {"reason": reason, "question_field": "client.name"}
    missing = harness.recovered_name_question_contracts(
        ["Москва и область — разные регионы.", "Вы живёте в самой Москве или в области?", "С регионом разобрались, спасибо."], voice)
    assert [finding["code"] for finding in missing] == ["planned_question_missing_from_reply"]
    assert harness.recovered_name_question_contracts(["Спасибо.", "Как к вам обращаться?"], voice) == []


@pytest.mark.parametrize("form,owners,uncertain,expected", [
    ("private", "client_only", None, True),
    ("private", "client_and_spouse", None, True),
    ("municipal", "client_only", None, False),
    ("private", "others", None, False),
    ("private", "client_only", "assets.realty.owners", False),
    ("private", "client_only", "assets.realty.ownership_form", False),
    ("private", "client_only", "assets.has_realty", False),
])
def test_reference_presence_requires_self_owners_and_compatible_ownership_form(form, owners, uncertain, expected):
    state = {"facts": {"assets": {"realty": {"ownership_form": form, "owners": owners}}},
             "fact_statuses": {uncertain: {"status": "unknown"}} if uncertain else {}}
    known = harness._known_fields(state, harness.cases()[0])
    assert ("assets.has_realty" in known) is expected
    assert "has_realty" not in state["facts"]["assets"]


def test_reference_does_not_replace_explicit_false_or_infer_unknown_vehicle():
    state = {"facts": {"assets": {"has_realty": False, "realty": {
        "ownership_form": "private", "owners": "client_only"}}}}
    known = harness._known_fields(state, harness.cases()[0])
    assert "assets.has_realty" in known
    assert state["facts"]["assets"]["has_realty"] is False
    assert "assets.has_vehicle" not in known


def test_property_restriction_uses_the_actual_matrix_path_for_repeat_detection():
    reply = "Квартира сейчас в залоге или без обременений?"
    assert harness.requested_fields(reply) == {"assets.realty.restriction"}
    state = {"facts": {"assets": {"realty": {"restriction": "none"}}}}
    findings = harness.turn_contracts(harness.cases()[0], state, "Без обременений.", [reply],
        known=harness._known_fields(state, harness.cases()[0]), refused=False)
    assert any(finding["code"] == "known_fact_requested_again" and
               finding["field"] == "assets.realty.restriction" for finding in findings)


@pytest.mark.parametrize("path,status", [
    ("assets.has_realty", "conflict"),
    ("assets.realty.ownership_form", {"status": "conflict"}),
    ("assets.realty.owners", {}),
    ("assets.realty.owners", None),
    ("assets.has_realty", False),
])
def test_new_presence_inference_rejects_conflicted_or_malformed_status(path, status):
    state = {"facts": {"assets": {"realty": {"ownership_form": "private", "owners": "client_only"}}},
             "fact_statuses": {path: status}}
    assert "assets.has_realty" not in harness._known_fields(state, harness.cases()[0])


def test_new_presence_inference_accepts_only_explicit_known_or_confirmed_status():
    state = {"facts": {"assets": {"realty": {"ownership_form": "private", "owners": "client_only"}}},
             "fact_statuses": {"assets.has_realty": "known", "assets.realty.ownership_form": {"status": "confirmed"},
                               "assets.realty.owners": "confirmed"}}
    assert "assets.has_realty" in harness._known_fields(state, harness.cases()[0])


def test_confirmed_self_owners_imply_presence_without_inventing_missing_form():
    state = {"facts": {"assets": {"realty": {"owners": "client_and_spouse"}}}}
    assert "assets.has_realty" in harness._known_fields(state, harness.cases()[0])
    assert "ownership_form" not in state["facts"]["assets"]["realty"]
    state["fact_statuses"] = {"assets.realty.ownership_form": {"status": "unknown"}}
    assert "assets.has_realty" not in harness._known_fields(state, harness.cases()[0])


@pytest.mark.parametrize("reply", [
    "Чтобы я могла продолжить работу по вашей ситуации, оставьте, пожалуйста, номер телефона для связи.",
    "Уточните номер телефона для обязательной проверки — и вернусь с результатом здесь.",
    "Без номера решение получить не выйдет. Напишите его, пожалуйста.",
    "Номер телефона — обязательное условие продолжения.",
])
def test_pre_refusal_phone_pressure_fails_without_waiting_for_a_no(reply):
    findings = harness.natural_tone_contracts("Сергей.", [reply], refused=False)
    assert "phone_pressure_before_refusal" in {finding["code"] for finding in findings}


@pytest.mark.parametrize("reply", [
    "Если хотите обратный звонок, можете оставить номер телефона.",
    "Номер нужен специалисту, чтобы перезвонить. Здесь можно продолжить без него.",
    "Для обратного звонка номер обязателен. А вопросы можем обсудить здесь.",
    "Номер для продолжения не обязателен, можем общаться здесь.",
    "Можете оставить номер, если вам удобно. Какой вопрос хотите обсудить?",
    "Номер уже есть в анкете, повторно вводить его не нужно.",
])
def test_natural_optional_or_narrow_callback_phone_explanation_is_not_coercion(reply):
    assert harness.natural_tone_contracts("Зачем номер?", [reply], refused=False) == []


@pytest.mark.parametrize("reply,expected", [
    ("Поняла.", 1),
    ("Приняла, 400 тысяч. В каком городе вы живёте?", 1),
    ("Спасибо, имя записала. Оставьте номер, если хотите обратный звонок.", 1),
    ("Правильно поняла, речь про 280 или 300 тысяч?", 0),
    ("Поняла, номер не оставляем. Можем продолжить здесь.", 0),
    ("Понимаю, что из-за долга сейчас тяжело. Что больше всего беспокоит?", 0),
    ("Сумма влияет на возможные варианты. Где вы живёте?", 0),
])
def test_receipt_detection_preserves_contextual_acknowledgement(reply, expected):
    assert harness.neutral_receipt_prefix_count([reply]) == expected


def test_a_single_poniala_is_not_a_word_ban():
    assert harness.natural_tone_contracts("400 тысяч.", ["Поняла. В каком городе вы живёте?"], refused=False) == []


def test_two_empty_acknowledgements_in_one_reply_are_redundant():
    findings = harness.natural_tone_contracts("Сергей.",
        ["Имя приняла, спасибо. Спасибо, имя записала. Какой у вас вопрос?"], refused=False)
    assert "duplicated_perfunctory_ack" in {finding["code"] for finding in findings}


def test_three_consecutive_receipt_led_turns_fail_even_with_different_words():
    prior = [{"user": "400 тысяч", "bot": ["Приняла, 400 тысяч. Где живёте?"]},
             {"user": "Москва", "bot": ["Спасибо, регион я вижу. Как к вам обращаться?"]}]
    findings = harness.natural_tone_contracts("Сергей.", ["Спасибо, имя записала. Что ещё обсудим?"],
        refused=False, prior_dialog=prior)
    assert any(finding["code"] == "repeated_perfunctory_ack" and
               finding["consecutive_receipt_turns"] == 3 for finding in findings)


def test_substantive_answer_breaks_the_receipt_streak():
    prior = [{"user": "400 тысяч", "bot": ["Приняла, 400 тысяч."]},
             {"user": "Зачем сумма?", "bot": ["От суммы зависит, какие варианты стоит обсуждать."]}]
    assert harness.natural_tone_contracts("Москва.", ["Поняла. Как к вам обращаться?"],
        refused=False, prior_dialog=prior) == []


def test_closing_courtesy_is_not_an_intake_receipt():
    prior = [{"user": "400 тысяч", "bot": ["Приняла."]},
             {"user": "Москва", "bot": ["Спасибо, регион я вижу."]}]
    assert harness.natural_tone_contracts("Спасибо.", ["Хорошо."], refused=False, prior_dialog=prior) == []


def test_receipt_grammar_covers_amount_echo_and_supplied_city_without_calling_explanation_empty():
    assert harness.neutral_receipt_prefix_count(["280 тысяч — поняла, фиксирую эту сумму."], "280 тысяч") == 1
    assert harness.neutral_receipt_prefix_count(["Регион я уже вижу — Москва и область."], "Москва") == 1
    assert harness.neutral_receipt_prefix_count(["Поняла, Казань находится в Татарстане."], "Казань, Татарстан") == 0
    assert harness.neutral_receipt_prefix_count(["Поняла, Москва относится к доступному региону."], "Москва") == 0


def test_explicit_confirmation_question_makes_the_acknowledgement_meaningful_and_resets_streak():
    prior = [{"user": "400 тысяч", "bot": ["Приняла, 400 тысяч."]},
             {"user": "Москва", "bot": ["Спасибо, регион я вижу."]}]
    assert harness.natural_tone_contracts("Вы учли сумму?", ["Поняла, 400 тысяч."],
        refused=False, prior_dialog=prior) == []
    prior.append({"user": "Вы учли сумму?", "bot": ["Поняла, 400 тысяч."]})
    assert harness.natural_tone_contracts("Сергей.", ["Спасибо, имя записала."],
        refused=False, prior_dialog=prior) == []


def test_confirmation_question_does_not_justify_two_receipts_in_the_same_reply():
    findings = harness.natural_tone_contracts("Вы учли сумму?", ["Поняла, 400 тысяч. Сумму записала."],
        refused=False)
    assert "duplicated_perfunctory_ack" in {finding["code"] for finding in findings}


def test_application_only_required_contact_with_explicit_chat_choice_is_truthful():
    for reply in ["Для оформления заявки номер обязателен. А вопросы можно обсудить здесь.",
                  "Номер обязателен для оформления заявки, вопросы можно здесь."]:
        assert harness.natural_tone_contracts("Зачем телефон?", [reply], refused=False) == []


@pytest.mark.parametrize("reply", [
    "Номер обязателен, чтобы продолжать чат при оформлении заявки. А вопросы можно обсудить здесь.",
    "Без номера не получится продолжить. При оформлении заявки вопросы можно обсудить здесь.",
])
def test_nearby_application_word_does_not_excuse_general_chat_coercion(reply):
    findings = harness.natural_tone_contracts("Сергей.", [reply], refused=False)
    assert "phone_pressure_before_refusal" in {finding["code"] for finding in findings}


def test_polite_phone_anaphora_fires_controlled_refusal_after_actual_optional_request():
    case = harness.targeted_cases()[1]
    reply = ("Чтобы подготовить анкету, нужен номер телефона. Можете оставить его здесь? "
             "Продолжить общение можно и без этого.")
    assert "client.phone" in harness.requested_fields(reply)
    selected = harness.choose_detour(case, {}, reply, set(), None, 4)
    assert selected is not None and selected.id == "phone_no"
    assert harness.natural_tone_contracts("Сергей.", [reply], refused=False) == []


def test_unrelated_anaphora_does_not_invent_a_phone_request():
    assert "client.phone" not in harness.requested_fields("Документ нужен для анкеты. Можете оставить его здесь?")


def test_cli_exposes_only_safe_paid_usage_before_a_failed_disk_checkpoint(monkeypatch, tmp_path, capsys):
    import errno
    import sys
    from pathlib import Path

    result = {"id": "normal_anxious", "error_class": None, "contract_passed": True,
              "usage": {"total_calls": 2, "total_cost_usd": 0.002, "session_id": "PRIVATE_SESSION_SENTINEL",
                        "raw_provider_body": "PRIVATE_BODY_SENTINEL",
                        "by_role": {"conversation": {"calls": 1, "cost_usd": 0.001,
                                                     "raw": "PRIVATE_ROLE_SENTINEL"},
                                    "client_actor": {"calls": 1, "cost_usd": 0.001}}}}
    monkeypatch.setattr(sys, "argv", ["local_style_eval", "--out", str(tmp_path / "run.json"),
                                      "--ids", "normal_anxious", "--max-turns", "12"])
    monkeypatch.setattr(harness, "load_config", settings)
    monkeypatch.setattr(harness, "run_case", lambda *args, **kwargs: result)
    actual_write = Path.write_text

    def failed_checkpoint(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return actual_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failed_checkpoint)
    with pytest.raises(OSError) as caught:
        harness.main()
    assert caught.value.errno == errno.ENOSPC
    output = capsys.readouterr().out
    accounting = json.loads(output)
    assert accounting["event"] == "case_model_accounting" and accounting["checkpoint_status"] == "pending"
    assert accounting["calls"] == 2 and accounting["estimated_cost_usd"] == 0.002
    assert accounting["by_role"]["client_actor"]["calls"] == 1
    assert "PRIVATE_" not in output


def test_two_pass_observer_keeps_both_raw_candidates_and_final_revision_metadata(monkeypatch):
    candidates = ["Первый черновик пояснения.", "Общаться здесь можно без передачи сведений."]
    responses = iter(candidates)
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Что это значит?")
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: next(responses))

    def speech(prepared, *args, **kwargs):
        harness.llm_agent.generate_reply({}, settings())
        revised = harness.llm_agent.generate_reply({}, settings())
        prepared["_policy_spoken"] = SpokenPolicyReply(revised, None, "model", "model_revised")
        return iter([revised])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=1)
    assert result["dialog"][1]["voice_candidates"] == candidates
    assert result["dialog"][1]["bot"] == [candidates[1]]
    assert result["voice_reviewed_count"] == 1 and result["voice_review_degraded_count"] == 0
    assert result["full_voice_review_completed"] is True and result["voice_quality_eligible"] is True


@pytest.mark.parametrize("reject_first", [False, True])
def test_distinct_critic_trace_is_not_speech_and_all_role_usage_is_counted(monkeypatch, reject_first):
    config = settings()
    speech_texts = ["Первый черновик пояснения.", "Общаться здесь можно без передачи сведений."]
    generated = []
    assessed = []
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Что это значит?")

    def generate(payload, cfg, *, usage_collector=None, **kwargs):
        text = speech_texts[len(generated)]
        generated.append(text)
        usage_collector(cfg.model, 100, 10, 0)
        return text

    def assess(payload, draft, frame, cfg, *, usage_collector=None, **kwargs):
        reject = reject_first and not assessed
        verdict = harness.policy_voice_critic.SemanticVerdict(
            accept=not reject, flags=["unrequested_receipt"] if reject else [])
        usage_collector(cfg.extractor_model, 50, 5, 0)
        result = harness.policy_voice_critic.CriticAssessment(
            verdict, cfg.extractor_model, "semantic_rejection" if reject else "accepted", True)
        assessed.append(result)
        return result

    def speech(prepared, message, cfg, *, usage_collector=None, critic_usage_collector=None, **kwargs):
        for _ in range(2 if reject_first else 1):
            text = harness.llm_agent.generate_reply({}, cfg, usage_collector=usage_collector)
            harness.policy_voice_critic.assess({}, text, None, cfg,
                                               usage_collector=critic_usage_collector)
        prepared["_policy_spoken"] = SpokenPolicyReply(
            text, None, "model", "model_critic_revised" if reject_first else "model_critic_accepted",
            critic_model=cfg.extractor_model, critic_calls=len(assessed), critic_assessments=tuple(assessed))
        return iter([text])

    monkeypatch.setattr(harness.llm_agent, "generate_reply", generate)
    monkeypatch.setattr(harness.policy_voice_critic, "assess", assess)
    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(config):
        result = harness.run_case(harness.cases()[4], config, max_turns=1)
    turn = result["dialog"][1]
    count = 2 if reject_first else 1
    assert turn["voice_candidates"] == speech_texts[:count]
    assert [item["verdict"]["accept"] for item in turn["semantic_critic_assessments"]] == ([False, True] if reject_first else [True])
    assert result["semantic_critic_attempts"] == count
    assert result["semantic_critic_accepted"] == 1
    assert result["semantic_critic_rejected"] == int(reject_first)
    assert result["usage"]["by_role"]["conversation"]["calls"] == count
    assert result["usage"]["by_role"]["policy_voice_critic"]["calls"] == count
    assert result["usage"]["total_calls"] == count * 2
    assert result["full_voice_review_completed"] is True


def test_critic_success_label_without_observed_verdict_cannot_accept_quality(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Объясните, пожалуйста.")

    def speech(prepared, *args, **kwargs):
        text = "Общение здесь доступно без передачи сведений."
        prepared["_policy_spoken"] = SpokenPolicyReply(text, None, "model", "model_critic_accepted")
        return iter([text])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=1)
    assert result["dialog"][1]["semantic_critic_assessments"] == []
    assert result["full_voice_review_completed"] is False
    assert result["voice_quality_eligible"] is False


@pytest.mark.parametrize("reason,accepted", [
    ("model_advisory_accepted", True), ("model_advisory_revised", True),
    ("model_advisory_disputed", False), ("model_advisory_unavailable", None),
    ("model_advisory_disputed_repair_failed", False),
    ("model_advisory_disputed_repair_unavailable", False),
])
def test_advisory_verdicts_never_certify_style_even_when_hard_contracts_pass(monkeypatch, reason, accepted):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Нет, не разрешаю передачу.")
    text = "Можем просто продолжить общение здесь."
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: text)

    def assess(*args, **kwargs):
        verdict = (harness.policy_voice_critic.SemanticVerdict(
            accept=accepted, flags=[] if accepted else ["wrong_current_turn"])
            if accepted is not None else None)
        return harness.policy_voice_critic.CriticAssessment(verdict, settings().extractor_model,
            "model_unavailable" if accepted is None else "accepted" if accepted else "semantic_rejection", True)

    def speech(prepared, message, config, **kwargs):
        # Include the new hard-repair topology with two speakers but one critic.
        if reason == "model_advisory_revised":
            harness.llm_agent.generate_reply({}, config)
        candidate = harness.llm_agent.generate_reply({}, config)
        harness.policy_voice_critic.assess({}, candidate, None, config)
        prepared["_policy_spoken"] = SpokenPolicyReply(candidate, None, "model", reason)
        return iter([candidate])

    monkeypatch.setattr(harness.policy_voice_critic, "assess", assess)
    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=3)
    assert result["contract_passed"] is True and result["hard_contract_acceptance_eligible"] is True
    assert result["voice_hard_contracts_eligible"] is True
    assert result["full_voice_review_completed"] is None and result["voice_quality_eligible"] is None
    assert result["voice_reviewed_count"] == 0
    assert result["voice_eligibility_scope"] == "hard_contracts_only_manual_style_pending"
    assert result["execution_status"] == "completed_advisory_manual_style_pending"
    assert result["dialog"][1]["semantic_critic_assessments"][0]["verdict"] == (
        {"accept": accepted, "flags": [] if accepted else ["wrong_current_turn"]} if accepted is not None else None)


def test_advisory_success_label_without_observed_speaker_cannot_certify_origin(monkeypatch):
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Нет, не разрешаю передачу.")

    def speech(prepared, *args, **kwargs):
        text = "Можем продолжить общение здесь."
        prepared["_policy_spoken"] = SpokenPolicyReply(text, None, "model", "model_advisory_accepted")
        return iter([text])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    with harness.service_boundaries(settings()):
        result = harness.run_case(harness.cases()[4], settings(), max_turns=3)
    assert result["dialog"][1]["voice_candidates"] == []
    assert result["full_voice_review_completed"] is None
    assert result["voice_hard_contracts_eligible"] is False
    assert result["hard_contract_acceptance_eligible"] is False


def test_advisory_cli_success_only_accepts_hard_contracts_and_keeps_style_pending(monkeypatch, tmp_path):
    import sys

    report_path = tmp_path / "advisory.json"
    monkeypatch.setattr(sys, "argv", ["local_style_eval", "--out", str(report_path),
                                      "--ids", "onloader_known_contact", "--max-turns", "12"])
    monkeypatch.setattr(harness, "load_config", settings)
    result = {"id": "onloader_known_contact", "error_class": None, "contract_passed": True,
              "voice_quality_eligible": None, "voice_hard_contracts_eligible": True,
              "hard_contract_acceptance_eligible": True,
              "usage": {"total_calls": 0, "total_cost_usd": 0, "by_role": {}}}
    monkeypatch.setattr(harness, "run_case", lambda *args, **kwargs: result)
    assert harness.main() == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["hard_contract_acceptance_eligible"] is True
    assert report["quality_acceptance_eligible"] is False
    assert report["quality_acceptance_eligibility_scope"] == "manual_style_pending"
    assert report["manual_review_contract"]["status"] == "pending"


@pytest.mark.parametrize("reason", ["model_advisory_unavailable", "model_advisory_unavailable_typed_question"])
def test_retained_valid_draft_after_advisory_unavailability_never_claims_style_acceptance(monkeypatch, reason):
    typed = reason.endswith("_typed_question")
    case = harness.cases()[3] if typed else harness.cases()[4]
    if typed:
        initial = harness._initial(case)
        initial["facts"] = {"debts": {"total": 280000}, "client": {"region_class": "rf_other"}}
        monkeypatch.setattr(harness, "_initial", lambda selected: initial)
    monkeypatch.setattr(harness, "_client_reply", lambda *args: "Казань" if typed else "Нет, не разрешаю передачу.")
    monkeypatch.setattr(harness.llm_agent, "extract_facts", lambda *args, **kwargs: {})
    draft = "Сумма и регион известны." if typed else "Общаться здесь можно без передачи сведений."
    responses = iter([draft, draft])

    def generate(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(harness.llm_agent, "generate_reply", generate)
    def critique(*args, **kwargs):
        raise RuntimeError("PRIVATE_REVIEW_PROVIDER_BODY")

    monkeypatch.setattr(harness.llm_agent, "generate_policy_critique", critique)
    with harness.service_boundaries(settings()) as transport:
        result = harness.run_case(case, settings(), max_turns=1 if typed else 3)
    assert transport["model_http_requests"] == 0
    assert result["dialog"][1]["voice_reason"] == reason
    if not typed:
        assert result["completion_met"] is True
        assert result["execution_status"] == "completed_advisory_manual_style_pending"
    assert result["voice_model_count"] >= 1 and result["voice_advisory_unavailable_count"] >= 1
    assert result["full_voice_review_completed"] is None and result["voice_quality_eligible"] is None
    assert result["voice_eligibility_scope"] == "hard_contracts_only_manual_style_pending"
    assert result["voice_hard_contracts_eligible"] is not typed
    assert (result["voice_mixed_question_count"] > 0) is typed
    assert "PRIVATE_REVIEW_PROVIDER_BODY" not in json.dumps(result)


def test_stopped_before_first_case_is_not_quality_eligible_without_model_calls(monkeypatch, tmp_path):
    import sys
    report_path = tmp_path / "run.json"
    report_path.with_suffix(".stop").touch()
    monkeypatch.setattr(sys, "argv", ["local_style_eval", "--out", str(report_path),
                                      "--ids", "normal_anxious", "--max-turns", "12"])
    monkeypatch.setattr(harness, "load_config", settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("No model case is allowed after stop")

    monkeypatch.setattr(harness, "run_case", forbidden)
    assert harness.main() == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["cases"] == [] and report["quality_acceptance_eligible"] is False
    assert report["transport_requests"]["model_http_requests"] == 0


def test_stage12_source_pins_cover_transport_and_receipt_implementation():
    assert {"transport.py", "utils.py", "policy_content.py", "routing/matrix.yaml"} <= harness.source_hashes().keys()


def test_model_open_ledger_returns_the_original_response_without_reading_or_counting_usage(monkeypatch):
    from urllib.request import Request

    class Response:
        status = 200

        def read(self):
            raise AssertionError("Opening diagnostics must not read the response body")

    response = Response()
    monkeypatch.setattr(harness.llm_agent, "_urlopen", lambda *args: response)
    with harness.service_boundaries(settings()) as counts:
        actual = harness.llm_agent._urlopen(settings(), Request("https://openrouter.ai/api/v1/chat/completions"))
    assert actual is response
    assert counts["model_http_requests"] == 1 and counts["model_transport_failures"] == 0
    assert counts["attempt_count_scope"] == "logical_pool_requests_physical_retries_unknown"
    row = counts["model_open_attempts"][0]
    assert row["ordinal"] == 1 and row["outcome"] == "opened" and row["phase"] == "open"
    assert row["http_status"] == 200 and row["failure"] is None and row["elapsed_ms"] >= 0
    assert "usage" not in row


@pytest.mark.parametrize("http_status", [None, 429, 503])
def test_model_open_failure_ledger_keeps_typed_cause_but_no_private_exception_text(monkeypatch, http_status):
    from urllib.error import HTTPError
    from urllib.request import Request

    failure = (HTTPError("https://PRIVATE_ENDPOINT_SENTINEL", http_status, "PRIVATE_BODY_SENTINEL", {}, None)
               if http_status else TimeoutError("PRIVATE_BODY_SENTINEL"))

    def fail(*args):
        raise failure

    monkeypatch.setattr(harness.llm_agent, "_urlopen", fail)
    with harness.service_boundaries(settings()) as counts:
        with pytest.raises(type(failure)) as captured:
            harness.llm_agent._urlopen(settings(), Request("https://openrouter.ai/api/v1/chat/completions"))
    assert captured.value is failure
    assert counts["model_http_requests"] == counts["model_transport_failures"] == 1
    row = counts["model_open_attempts"][0]
    assert row["outcome"] == "failed" and row["elapsed_ms"] >= 0
    assert row["failure"]["kind"] == ("http" if http_status else "timeout")
    assert row["failure"]["http_status"] == http_status and row["failure"]["phase"] == "open"
    assert row["failure"]["retryable"] is True
    assert "PRIVATE_" not in json.dumps(counts)


def test_opened_body_failure_does_not_become_a_completed_model_usage_record(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise TimeoutError("PRIVATE_BODY_READ_SENTINEL")

    monkeypatch.setattr(harness.llm_agent, "_urlopen", lambda *args: Response())
    with harness.service_boundaries(settings()) as counts:
        with pytest.raises(Exception):
            harness.llm_agent._post(settings(), {"model": settings().model}, max_attempts=1)
    assert counts["model_http_requests"] == 1 and counts["model_transport_failures"] == 0
    row = counts["model_open_attempts"][0]
    assert row["outcome"] == "opened" and row["http_status"] == 200
    assert "PRIVATE_" not in json.dumps(counts)


def test_failure_trace_copies_only_the_explicit_private_safe_runtime_schema():
    failure = harness.llm_agent.ModelFailure(kind="read", phase="read", error_type="IncompleteRead",
                                             http_status=None, retryable=True)
    assert harness._failure_trace(failure) == {
        "kind": "read", "phase": "read", "error_type": "IncompleteRead", "http_status": None, "retryable": True,
    }
    assert harness._failure_trace(None) is None


def test_assessor_duration_and_failure_survive_the_dialogue_trace_separately_from_speech(monkeypatch):
    config = settings()
    failure = harness.llm_agent.ModelFailure(kind="http", phase="open", error_type="HTTPError",
                                             http_status=503, retryable=True)
    assessment = harness.policy_voice_critic.CriticAssessment(
        None, config.extractor_model, "model_unavailable", True, failure=failure, elapsed_ms=217)
    monkeypatch.setattr(harness.llm_agent, "extract_facts", lambda *args, **kwargs: {})
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: "Можно обсуждать здесь.")
    monkeypatch.setattr(harness.policy_voice_critic, "assess", lambda *args, **kwargs: assessment)

    def speech(prepared, message, cfg, **kwargs):
        text = harness.llm_agent.generate_reply({}, cfg)
        harness.policy_voice_critic.assess({}, text, None, cfg)
        prepared["_policy_spoken"] = SpokenPolicyReply(text, None, "model", "model_advisory_unavailable")
        return iter([text])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    executed = harness.execute_core_turn(harness._initial(harness.cases()[4]), "Что это значит?", config,
                                         harness.SessionUsage("synthetic-failure-trace"))
    row = harness._turn("Что это значит?", executed.bubbles, executed.latency, executed.state,
                        voice=executed.voice)
    trace = row["semantic_critic_assessments"][0]
    assert trace.get("failure") == harness._failure_trace(failure)
    assert trace.get("elapsed_ms") == 217
    assert row["voice_candidates"] == ["Можно обсуждать здесь."]
    assert row["voice_reason"] == "model_advisory_unavailable"


def test_closed_schema_issue_counts_survive_the_observer_without_becoming_voice_candidates(monkeypatch):
    config = settings()
    issue = harness.policy_voice_critic.CriticSchemaIssue("literal_error", "flags_item", 2)
    assessment = harness.policy_voice_critic.CriticAssessment(
        None, config.extractor_model, "invalid_schema", True, schema_issues=(issue,))
    text = "Можно обсуждать вопросы здесь."
    monkeypatch.setattr(harness.policy_voice_critic, "assess", lambda *args, **kwargs: assessment)
    monkeypatch.setattr(harness.llm_agent, "generate_reply", lambda *args, **kwargs: text)

    def speech(prepared, message, cfg, **kwargs):
        response = harness.llm_agent.generate_reply({}, cfg)
        harness.policy_voice_critic.assess({}, response, None, cfg)
        prepared["_policy_spoken"] = SpokenPolicyReply(response, None, "model", "model_advisory_unavailable")
        return iter([response])

    monkeypatch.setattr(harness.core, "stream_reply", speech)
    result = harness.execute_core_turn(harness._initial(harness.cases()[4]), "Зачем данные?", config,
                                      harness.SessionUsage("synthetic-schema-observer"))
    row = harness._turn("Зачем данные?", result.bubbles, result.latency, result.state, voice=result.voice)
    assert row["semantic_critic_assessments"][0].get("schema_issues") == [
        {"code": "literal_error", "field": "flags_item", "count": 2}]
    assert row["voice_candidates"] == [text]
    assert harness.MODEL_DIAGNOSTIC_CONTRACT_VERSION == "private-safe-model-failure-v2-schema-codes"
