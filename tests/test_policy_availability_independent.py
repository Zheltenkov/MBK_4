"""Independent structural closing contrasts, without prescribing ordinary model wording."""
import pytest

from tests.test_policy_v22_independent import actual_turn
from tests.test_policy_voice import debt_state
from utils import omit_repeated_availability_tail


PRIOR = "Если есть другие вопросы — спрашивайте, я на связи."
USEFUL = "При реструктуризации меняют график платежей. Сам долг остаётся."
GENERIC_PARAPHRASES = [
    "Если по вашей ситуации возникнут дополнительные вопросы, напишите — постараюсь ответить по делу.",
    "Когда захотите подробнее обсудить доступные варианты, обращайтесь — я отвечу.",
    "Пишите, если останутся общие вопросы по теме — отвечу в чате.",
]


@pytest.mark.parametrize("tail", GENERIC_PARAPHRASES)
def test_generic_condition_and_reply_padding_does_not_create_a_new_speech_act(tail):
    assert omit_repeated_availability_tail(
        USEFUL + "\n\n" + tail, recent_replies=[PRIOR], user_message="Чем отличаются эти варианты?",
    ) == USEFUL


@pytest.mark.parametrize("tail", [
    "Если узнаете точный остаток долга, напишите его здесь.",
    "Если решите оставить номер телефона, напишите его здесь.",
    "Если уточните дату следующего платежа, напишите её здесь.",
    "Если захотите назвать своё имя, напишите его здесь.",
    "Если станет тревожно и захочется поговорить, пишите — разберёмся вместе.",
    "Если появятся вопросы по платежу в 15000 рублей, напишите — посмотрим его отдельно.",
    "Если появятся вопросы, напишите фразу «Я на связи».",
    "Если появятся вопросы, напишите через https://example.invalid/help.",
    "Если появятся вопросы, пишите — я понимаю, как вам сейчас страшно.",
    "Если банк не изменит график, напишите — обсудим следующий шаг.",
    "Если появятся вопросы и банк согласится списать долг, напишите — я отвечу.",
    "Если появятся вопросы и вам станет страшно, пишите — я отвечу.",
    "Если появятся вопросы, пишите — отвечу чтобы вы не переживали.",
])
def test_a_concrete_condition_field_emotion_or_instruction_is_not_an_empty_closing(tail):
    text = USEFUL + "\n\n" + tail
    assert omit_repeated_availability_tail(
        text, recent_replies=[PRIOR], user_message="Чем отличаются эти варианты?",
    ) == text


def test_a_sole_availability_answer_and_a_direct_question_about_it_remain_available():
    tail = GENERIC_PARAPHRASES[0]
    assert omit_repeated_availability_tail(
        tail, recent_replies=[PRIOR], user_message="Можно спросить позже?",
    ) == tail
    text = "Да, можно вернуться позже.\n\n" + tail
    assert omit_repeated_availability_tail(
        text, recent_replies=[PRIOR], user_message="Можно спросить позже?",
    ) == text


def test_two_actual_known_region_turns_keep_model_receipts_without_the_repeated_invitation(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": PRIOR}]
    first_receipt = "Казань отметила."
    answer, prepared, state, calls = actual_turn(
        monkeypatch, state, "Казань.", replies=[first_receipt + "\n\n" + GENERIC_PARAPHRASES[0]] * 2,
        updates=[{"path": "client.region_class", "value": "rf_other"}],
    )
    assert answer == first_receipt and prepared["_policy_spoken"].source == "model"
    assert prepared["_policy_spoken"].reason == "model_semantic_first_preserved"
    # A sole receipt now gets exactly one optional style revision. Both supplied
    # drafts have the same receipt, so the first safe answer stays available.
    assert len(calls) == 4
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 2
    assert len([call for call in calls if call["model"] == "synthetic-analysis"]) == 2
    assert all(item.code_style_issues == ("unrequested_receipt",)
               for item in prepared["_policy_spoken"].diagnostic_assessments)
    second_receipt = "Спасибо, регион ясен."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[second_receipt + "\n\n" + GENERIC_PARAPHRASES[1]] * 2,
    )
    assert answer == second_receipt and prepared["_policy_spoken"].source == "model"
    assert prepared["_policy_spoken"].reason == "model_semantic_first_preserved"
    assert len(calls) == 4
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 2
    assert len([call for call in calls if call["model"] == "synthetic-analysis"]) == 2
    assert all(item.code_style_issues == ("unrequested_receipt",)
               for item in prepared["_policy_spoken"].diagnostic_assessments)
    assert committed["facts"] == state["facts"]
    assert not committed.get("delivery")
