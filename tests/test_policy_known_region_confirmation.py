"""Known geography cannot become another confirmation task through named places."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import core
import dialogue_frame
from dialogue_frame import DialogueFrame, constrain_collection, request_fields
import llm_agent
import policy_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


RECORDED_CONFIRMATION = "Казань — это Татарстан, верно? Уточняю регион для точности."


@pytest.mark.parametrize("confirmation", [
    "Казань — это Татарстан, верно?", "Самара — это Самарская область, правильно?",
    "Москва, верно?", "Казань, так?", "Подтвердите, Казань — это Татарстан.",
])
def test_catalogued_place_confirmations_are_observed_as_region_collection(confirmation):
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}))
    assert request_fields(confirmation, frame) == {"client.region_class"}
    assert constrain_collection(confirmation, frame).removed_fields == ("client.region_class",)
    assert policy_voice._model_reply(confirmation, frame, "synthetic") is None


def test_the_confirmation_object_comes_from_the_existing_catalogue_instead_of_a_city_whitelist(monkeypatch):
    matrix = SimpleNamespace(regions={"synthetic": ["Область Примерная\tГород Примерный"]})
    monkeypatch.setattr(dialogue_frame, "get_matrix", lambda: matrix, raising=False)
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}))
    assert request_fields("Город Примерный — это Область Примерная, верно?", frame) == {"client.region_class"}


@pytest.mark.parametrize("reply", [
    "Казань находится в Татарстане.",
    "Где находится Казань?",
    "Казань — это Татарстан?",
    "Вы спрашиваете, Казань находится в Татарстане, верно?",
    "Переезд в Казань может тревожить. Можем спокойно обсудить вашу ситуацию.",
    "Вы уточнили, что теперь живёте в Казани.",
    "Фраза «Казань — это Татарстан, верно?» повторяет вопрос.",
    "Всё верно?",
    "Правильно понимаю условия?",
])
def test_geographic_explanations_faq_emotion_correction_quotes_and_other_confirmations_stay_free(reply):
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}))
    assert request_fields(reply, frame) == set()
    assert constrain_collection(reply, frame).text == reply
    assert policy_voice._model_reply(reply, frame, "synthetic") is not None


def test_a_real_unresolved_location_can_still_be_clarified():
    frame = DialogueFrame("client.region_class", "collecting_debt_context", confirming=True)
    text = "Казань — это Татарстан, верно?"
    assert request_fields(text, frame) == {"client.region_class"}
    assert constrain_collection(text, frame).asked_field == "client.region_class"
    assert policy_voice._model_reply(text, frame, "synthetic") is not None


def test_an_ordinary_bubble_does_not_scan_the_geographic_catalogue(monkeypatch):
    def forbidden():
        pytest.fail("Ordinary speech without a confirmation cue must not scan the geography catalogue")

    monkeypatch.setattr(dialogue_frame, "get_matrix", forbidden, raising=False)
    assert request_fields("Можем обсудить вашу ситуацию здесь.", DialogueFrame(None, "collected_held")) == set()


def test_a_resolved_city_in_the_same_chat_is_not_asked_for_again(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Known-region controls must not call a provider or submit a lead")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    safe = "Можем обсудить варианты решения долгового вопроса."
    calls, analyses = install(monkeypatch, [RECORDED_CONFIRMATION, safe], [semantic_raw()] * 2)
    state = debt_state(debts={"total": 400000}, client={"phone": "+70000000001", "name": "Ирина"})
    history = deepcopy(state["chat_history"])
    user = "Поняла. Я в Казани."
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    assert prepared["facts"]["client"]["region_class"] == "rf_other"
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert text == safe and len(calls) == 2 and len(analyses) == 1
    # The known-region question is code-rejected before C. G2 still gets C.
    assert analyses[0][0] == safe
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["chat_history"][:len(history)] == history and not committed.get("delivery")
