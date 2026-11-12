"""Whole empty receipt acts are omitted only after proven value-only replies."""
import pytest

from utils import omit_empty_field_receipt_bubbles


@pytest.mark.parametrize("receipt", [
    "Спасибо, регион я вижу.", "Спасибо это я уже вижу.", "Эти данные у меня уже есть.",
    "Город и регион увидела, спасибо.", "Регион учтён.", "Я записала регион.",
])
def test_actual_empty_field_receipts_leave_the_useful_bubble_exactly_unchanged(receipt):
    useful = "Если хотите, можем обсудить вашу ситуацию подробнее."
    assert omit_empty_field_receipt_bubbles(receipt + "\n\n" + useful,
        received_fields=frozenset({"client.region_class"}), user_message="Казань, Татарстан.") == useful


def test_a_reply_containing_only_an_empty_field_receipt_returns_empty_for_the_existing_repair():
    assert omit_empty_field_receipt_bubbles("Город и регион увидела, спасибо.",
        received_fields=frozenset({"client.region_class"}), user_message="Казань, Татарстан.") == ""


@pytest.mark.parametrize("message", [
    "Мой регион уже учли?", "Казань, подтвердите пожалуйста.", "Казань, пожалуйста запишите.",
    "Нет, не хочу оставлять номер.", "Казань, мне страшно.", "Казань. Я переживаю.",
    "Казань, не уверена.", "Казань, если не ошибаюсь.", "Казань, спасибо.",
    "Неизвестное место.", "Казань!", '"Казань"',
])
def test_unproven_plain_input_keeps_the_whole_output(message):
    text = "Спасибо, регион я вижу.\n\nМожем обсудить вопросы здесь."
    assert omit_empty_field_receipt_bubbles(text, received_fields=frozenset({"client.region_class"}),
                                           user_message=message) == text


@pytest.mark.parametrize("text", [
    "Спасибо.", "Поняла.", "Понимаю, как вам тяжело.\n\nМожем обсудить ситуацию здесь.",
    "Регион я вижу. От региона зависит порядок обращения.",
    "Вы спрашиваете, учтён ли регион?", "Контакт нужен компании для консультации.",
    "Спасибо, регион я вижу. Можете задать следующий вопрос.",
    "Ваше имя записала.\n\nМожем продолжить.",
])
def test_unmatched_or_meaningful_bubbles_are_never_rewritten(text):
    assert omit_empty_field_receipt_bubbles(text, received_fields=frozenset({"client.region_class"}),
                                           user_message="Казань, Татарстан.") == text


@pytest.mark.parametrize("fields", [frozenset(), frozenset({"untrusted"}),
    frozenset({"client.name", "client.region_class"})])
def test_only_one_current_supported_received_field_can_activate_the_filter(fields):
    text = "Эти данные у меня уже есть."
    assert omit_empty_field_receipt_bubbles(text, received_fields=fields, user_message="Казань.") == text


@pytest.mark.parametrize("field,message,receipt", [
    ("client.name", "Сергей.", "Имя записала."),
    ("client.phone", "+7 (999) 123-45-67", "Номер вижу."),
    ("debts.total", "Ровно 400 000 рублей.", "Сумму долга учла."),
])
def test_other_supported_plain_field_values_use_the_same_receipt_act_contract(field, message, receipt):
    assert omit_empty_field_receipt_bubbles(receipt, received_fields=frozenset({field}), user_message=message) == ""


def test_a_plain_restatement_of_a_known_region_does_not_require_a_new_receipt():
    useful = "Если хотите, можем обсудить ваши вопросы здесь."
    assert omit_empty_field_receipt_bubbles("Да, Татарстан я отметила.\n\n" + useful,
        received_fields=frozenset(), known_region=True, user_message="Да, Татарстан.") == useful


@pytest.mark.parametrize("message", [
    "Да, Татарстан. Всё верно поняли?", "Да, Татарстан. Мне страшно.", "Неизвестное место.",
])
def test_known_region_presence_does_not_make_a_question_emotion_or_unknown_place_plain(message):
    text = "Да, Татарстан я отметила.\n\nМожем обсудить ваши вопросы здесь."
    assert omit_empty_field_receipt_bubbles(text, received_fields=frozenset(),
        known_region=True, user_message=message) == text


def test_a_restatement_without_any_confirmed_region_does_not_activate_the_filter():
    text = "Да, Татарстан я отметила.\n\nМожем обсудить ваши вопросы здесь."
    assert omit_empty_field_receipt_bubbles(text,
        received_fields=frozenset(), user_message="Да, Татарстан.") == text


def test_the_existing_chat_omits_the_known_region_receipt_without_inventing_a_received_fact(monkeypatch):
    from copy import deepcopy

    import core
    from tests.test_policy_voice import debt_state, model_reply, settings

    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": 400000})
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": []})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def no_delivery(*args, **kwargs):
        pytest.fail("A conversational receipt cannot send an application")

    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    useful = "Если хотите, можем обсудить ваши вопросы здесь."
    model_reply(monkeypatch, "Да, Татарстан я отметила.\n\n" + useful)
    prepared = core.prepare_turn(state, "Да, Татарстан.", settings(), submit_delivery=False)
    assert prepared["_policy_received_fields"] == []
    before = deepcopy(prepared["facts"])
    speech = "".join(core.stream_reply(prepared, "Да, Татарстан.", settings()))
    committed = core.commit_turn(prepared, "Да, Татарстан.", speech)["current_state"]
    assert speech == useful
    assert committed["facts"] == before
    assert not committed.get("delivery")


@pytest.mark.parametrize("message,receipt", [
    ("Поняла. Я в Казани.", "Казань приняла."),
    ("Да, Татарстан.", "Татарстан, Казань — записала."),
])
def test_recorded_whole_region_receipt_acts_leave_the_useful_continuation(message, receipt):
    useful = "Если хотите, можем обсудить ваши вопросы здесь."
    assert omit_empty_field_receipt_bubbles(receipt + "\n\n" + useful,
        received_fields=frozenset({"client.region_class"}), user_message=message) == useful


@pytest.mark.parametrize("message", [
    "Поняла. Я в Казани, мне страшно.", "Поняла. Я в Казани. Что дальше?",
    "Я в Казани?", "Я не в Казани.", "Я в неизвестном городе.",
])
def test_a_first_person_location_with_emotion_question_negation_or_unknown_place_is_not_plain(message):
    text = "Казань приняла.\n\nЕсли хотите, можем обсудить ваши вопросы здесь."
    assert omit_empty_field_receipt_bubbles(text,
        received_fields=frozenset({"client.region_class"}), user_message=message) == text
