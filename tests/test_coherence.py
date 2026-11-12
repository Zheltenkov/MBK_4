"""Проверки диалоговой связности — то, что не видно в отдельной реплике.

Атомарный `v4_humanity_score` оценивает пузырь сам по себе и поэтому ставит 100
ходу, который отвечает «Отлично» на «Сейчас не работаю». Здесь проверяется
контракт другого уровня: реплика должна быть уместна относительно того, что
клиент сказал раньше по ходу разговора.
"""
from __future__ import annotations

from evaluation.coherence import CoherenceTurn, dialog_coherence_score


def test_positive_opener_after_bad_news_is_penalised():
    """Реальная регрессия из логов: emotional_debt_kazan, ход 9, humanity 100."""
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "Здравствуйте. Я уже не справляюсь с долгами.",
                ["Понимаю, разберёмся.", "Как к вам обращаться?"],
            ),
            CoherenceTurn(
                "Сейчас не работаю.",
                [
                    "Отлично, Ирина, теперь усилим заявку активами — "
                    "это повысит шансы.",
                    "Есть ли у вас недвижимость в собственности?",
                ],
            ),
        ]
    )

    assert score < 100
    assert any("отлично" in reason.lower() for reason in reasons)


def test_bad_news_mood_persists_through_neutral_answers():
    """Клиент сказал о беде на первом ходу, а «супер» прилетает на третьем.

    Попарная проверка это пропускает: «Ирина.» — нейтральный ответ. Настроение
    разговора живёт дольше одной пары реплик, поэтому оценка накопительная.
    """
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "Здравствуйте. Я уже не справляюсь с долгами.",
                ["Понимаю, разберёмся.", "Как к вам обращаться?"],
            ),
            CoherenceTurn(
                "Ирина.",
                ["Ирина, супер. Сейчас за пару минут соберу нужные данные."],
            ),
        ]
    )

    assert score < 100
    assert any("супер" in reason.lower() for reason in reasons)


def test_calm_dialog_keeps_warm_openers():
    """Оценка не запрещает тёплый тон — только неуместный."""
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "Здравствуйте, нужны деньги на ремонт.",
                ["Давайте разберёмся.", "Как к вам обращаться?"],
            ),
            CoherenceTurn(
                "Василий.",
                ["Василий, супер. Сейчас за пару минут соберу нужные данные."],
            ),
            CoherenceTurn("Пятьсот тысяч.", ["Сколько вам полных лет?"]),
        ]
    )

    assert score == 100
    assert reasons == []


def test_unanswered_client_question_is_penalised():
    """«Потупее в плане коммуникации» — бот пропускает вопрос и гнёт анкету."""
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "А зачем вам мой возраст?",
                ["В каком городе и регионе вы живёте?"],
            ),
        ]
    )

    assert score < 100
    assert any("вопрос клиента" in reason for reason in reasons)


def test_answered_client_question_is_not_penalised():
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "А зачем вам всё это?",
                [
                    "Хочу понять вашу ситуацию точно, а не гадать.",
                    "В каком городе вы живёте?",
                ],
            ),
        ]
    )

    assert score == 100
    assert reasons == []


def test_answer_and_question_in_one_bubble_is_not_penalised():
    """Ложное срабатывание из прогона 20260729_122003, ход 9.

    Пузырь может одновременно ответить и спросить — по признаку «заканчивается
    на ?» такая реплика выглядела проигнорированным вопросом.
    """
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "Квартира есть, я уже всё сказал. Вы читаете мои сообщения?",
                [
                    "Извините, вы правы — квартира есть. Уточню только "
                    "локацию: она тоже в Москве или в другом регионе?",
                ],
            ),
        ]
    )

    assert score == 100
    assert reasons == []


def test_bubble_of_only_questions_is_still_penalised():
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn(
                "А зачем вам это?",
                ["В каком городе живёте? И в каком регионе?"],
            ),
        ]
    )

    assert score < 100
    assert any("вопрос клиента" in reason for reason in reasons)


def test_addressing_by_name_three_turns_in_a_row_is_penalised():
    """Обращение по имени в каждой реплике читается как рассылка, а не разговор."""
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn("Ирина.", ["Ирина, какая сумма вам нужна?"]),
            CoherenceTurn("300 тысяч.", ["Ирина, сколько вам полных лет?"]),
            CoherenceTurn("41 год.", ["Ирина, в каком городе вы живёте?"]),
        ]
    )

    assert score < 100
    assert any("имени" in reason for reason in reasons)


def test_two_turns_with_the_name_are_allowed():
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn("Ирина.", ["Ирина, какая сумма вам нужна?"]),
            CoherenceTurn("300 тысяч.", ["Ирина, сколько вам полных лет?"]),
            CoherenceTurn("41 год.", ["В каком городе вы живёте?"]),
        ]
    )

    assert score == 100
    assert reasons == []


def test_repeated_bot_line_is_penalised():
    """Один и тот же довесок на каждом шаге — заевшая пластинка."""
    line = "Это заметно расширяет набор доступных решений."
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn("Да.", ["Есть ли у вас недвижимость?", line]),
            CoherenceTurn("Нет.", ["Есть ли у вас автомобиль?", line]),
        ]
    )

    assert score < 100
    assert any("повтор" in reason for reason in reasons)


def test_empty_dialog_is_not_scored_as_failure():
    score, reasons = dialog_coherence_score([])

    assert score == 100
    assert reasons == []


def test_repeat_after_acknowledging_is_not_a_broken_record():
    """Клиент ответил не о том, бот признал это и переспросил.

    Прогон 31.07.2026: «Сумму я уже зафиксировал, сейчас спрашиваю именно про
    возраст» + «Сколько вам полных лет?» — ровно то поведение, ради которого
    писалось правило про повторные вопросы. Метрика штрафовала за дословный
    повтор самого вопроса, не отличая объяснение от заевшей пластинки.
    """
    question = "Сколько вам полных лет?"
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn("Антон.", [question]),
            CoherenceTurn(
                "400 тысяч.",
                ["Сумму я уже зафиксировал, сейчас спрашиваю именно про возраст.",
                 question],
            ),
        ]
    )

    assert score == 100, reasons


def test_bare_repeat_without_explanation_is_still_penalised():
    """Обратная сторона: повтор без единого слова признания — та самая
    пластинка, на которую жаловался заказчик."""
    question = "Сколько вам полных лет?"
    score, reasons = dialog_coherence_score(
        [
            CoherenceTurn("Антон.", [question]),
            CoherenceTurn("400 тысяч.", [question]),
        ]
    )

    assert score < 100
    assert any("повтор" in reason for reason in reasons)
