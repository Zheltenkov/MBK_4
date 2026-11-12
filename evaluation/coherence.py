"""Оценка связности разговора целиком, а не отдельной реплики.

`v4_humanity_score` смотрит на пузырь изолированно и потому не видит целый класс
дефектов: реплика бывает безупречной сама по себе и неуместной в контексте. Так
ход из лога `emotional_debt_kazan` получил 100 при том, что на «Сейчас не
работаю» бот ответил «Отлично, Ирина, теперь усилим заявку активами».

Здесь проверяется именно контекст: настроение разговора, реакция на вопрос
клиента, повторы и навязчивое обращение по имени. Все эвристики детерминированы
и объясняют себя текстом — отчёт должен быть читаем заказчиком, а не только
разработчиком.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CoherenceTurn:
    """Ход разговора: реплика клиента и то, что бот ответил на неё."""

    user: str | None
    bubbles: list[str]


# Сигналы беды в речи клиента. Намеренно не включают нейтральные «долг» и
# «задолженность»: в анкете это обычные фактические ответы («задолженность
# около 650 тысяч»), и от них настроение разговора не меняется.
BAD_NEWS_MARKERS = (
    "не справляюсь",
    "не справляемся",
    "не работаю",
    "нет работы",
    "без работы",
    "уволил",
    "сократил",
    "потерял работу",
    "нечем платить",
    "не плачу",
    "просрочк",
    "коллектор",
    "приставы",
    "арест",
    "банкрот",
    "болею",
    "болезн",
    "инвалид",
    "умер",
    "похорон",
    "развод",
    "тяжело",
    "паник",
    "страшно",
    "отчаян",
    "выхода нет",
    "запутал",
)

# Оценочные зачины. Уместны в спокойном разговоре и читаются как издёвка после
# плохой новости, поэтому проверяются не сами по себе, а вместе с настроением.
EVALUATIVE_OPENERS = (
    "отлично",
    "супер",
    "прекрасно",
    "замечательно",
    "здорово",
    "чудесно",
    "великолепно",
    "шикарно",
    "класс",
)

# Зачин — это первые слова реплики. «Отлично» в середине объяснения не создаёт
# того же эффекта, поэтому окно ограничено.
OPENER_WINDOW = 40

# Короткие совпадения ловит атомарный scorer через V4_CANNED_PHRASES. Здесь
# интересны содержательные предложения, повторённые дословно.
MIN_REPEAT_LENGTH = 20

# Обращение по имени: «Ирина, в каком городе…».
_NAME_ADDRESS = re.compile(r"^([А-ЯЁ][а-яё]{2,})\s*,")

_TONE_PENALTY = 25
_IGNORED_QUESTION_PENALTY = 15
_NAME_PENALTY = 10
_REPEAT_PENALTY = 15


def dialog_coherence_score(
    turns: Sequence[CoherenceTurn],
) -> tuple[int, list[str]]:
    """Возвращает 0..100 и объяснения по всему разговору.

    Пустой разговор не является нарушением: harness вызывает функцию и для
    сценариев, оборвавшихся до первого содержательного хода.
    """
    if not turns:
        return 100, []

    reasons: list[str] = []
    penalty = 0

    penalty += _score_tone(turns, reasons)
    penalty += _score_ignored_questions(turns, reasons)
    penalty += _score_name_overuse(turns, reasons)
    penalty += _score_repeated_lines(turns, reasons)

    return max(0, 100 - penalty), reasons


def _score_tone(turns: Sequence[CoherenceTurn], reasons: list[str]) -> int:
    """Штрафует оценочный зачин после того, как клиент рассказал о беде.

    Настроение накопительное и не сбрасывается нейтральным ответом: клиент,
    сказавший «не справляюсь с долгами», а следом назвавший имя, всё ещё не ждёт
    услышать «супер». Попарная проверка такой случай пропускает.
    """
    penalty = 0
    bad_news_seen = False
    for index, turn in enumerate(turns, start=1):
        if bad_news_seen:
            opener = _opener_text(turn.bubbles)
            for word in EVALUATIVE_OPENERS:
                if re.search(rf"(?<![а-яё]){word}", opener):
                    penalty += _TONE_PENALTY
                    reasons.append(
                        f"ход {index}: оценочный зачин «{word}» после плохой "
                        "новости клиента"
                    )
                    break
        if _has_bad_news(turn.user):
            bad_news_seen = True
    return penalty


def _score_ignored_questions(
    turns: Sequence[CoherenceTurn],
    reasons: list[str],
) -> int:
    """Штрафует ход, где клиент спросил, а бот сразу задал свой вопрос.

    Заказчик описал это как «потупее прошлого в плане коммуникации»: анкета
    продолжается, вопрос клиента остаётся без ответа.
    """
    penalty = 0
    for index, turn in enumerate(turns, start=1):
        if not turn.user or "?" not in turn.user:
            continue
        first = next((bubble.strip() for bubble in turn.bubbles if bubble.strip()), "")
        if first and _is_only_questions(first):
            penalty += _IGNORED_QUESTION_PENALTY
            reasons.append(
                f"ход {index}: вопрос клиента остался без ответа — "
                "бот сразу задал свой"
            )
    return penalty


def _is_only_questions(bubble: str) -> bool:
    """True, если в пузыре нет ни одного утверждения перед вопросом.

    Одного «заканчивается на ?» мало: реплика «Извините, вы правы — квартира
    есть. Уточню локацию: она в Москве?» и отвечает, и спрашивает. Именно так
    проверка ложно сработала на ходу 9 прогона 20260729_122003.
    """
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", bubble)]
    return all(
        sentence.endswith("?")
        for sentence in sentences
        if sentence
    )


def _score_name_overuse(
    turns: Sequence[CoherenceTurn],
    reasons: list[str],
) -> int:
    """Штрафует обращение по имени три хода подряд.

    Один-два раза — это внимание к человеку, каждый ход — рассылка. Проверка
    работает без знания самого имени: важен повтор одного и того же обращения.
    """
    addresses = [_name_address(turn.bubbles) for turn in turns]
    for index, (first, second, third) in enumerate(
        zip(addresses, addresses[1:], addresses[2:]), start=3
    ):
        if first and first == second == third:
            reasons.append(
                f"ход {index}: обращение по имени «{first}» три реплики подряд"
            )
            return _NAME_PENALTY
    return 0


def _score_repeated_lines(
    turns: Sequence[CoherenceTurn],
    reasons: list[str],
) -> int:
    """Штрафует дословно повторённые содержательные предложения бота.

    Исключение — повторённый вопрос, перед которым бот объяснил, почему
    спрашивает снова. Клиент ответил не о том, бот сказал «сумму я уже
    зафиксировал, сейчас спрашиваю именно про возраст» и повторил вопрос: это
    ровно то поведение, ради которого писалось правило про повторные вопросы,
    а не заевшая пластинка. Голый повтор без единого слова признания штраф
    по-прежнему получает.
    """
    counts: Counter[str] = Counter()
    originals: dict[str, str] = {}
    for turn in turns:
        explained = _acknowledges_the_repeat(turn)
        for bubble in turn.bubbles:
            key = _normalize(bubble)
            if len(key) < MIN_REPEAT_LENGTH:
                continue
            if explained and bubble.strip().endswith("?"):
                continue
            counts[key] += 1
            originals.setdefault(key, bubble.strip())

    penalty = 0
    for key, count in counts.items():
        if count > 1:
            penalty += _REPEAT_PENALTY
            reasons.append(
                f"повтор реплики {count} раза: «{originals[key]}»"
            )
    return penalty


# Признание того, что вопрос уже задавался или ответ уже получен. Список
# держится узким: он снимает штраф, поэтому широкие слова вроде «понятно» сюда
# не годятся — иначе оправданием станет любая вежливая реплика.
_REPEAT_EXPLANATION = (
    "уже зафиксировал",
    "уже знаю",
    "уже спрашивал",
    "уже говорил",
    "уже сказал",
    "сейчас спрашиваю",
    "меня интересует именно",
    "мне нужно именно",
    "нужен именно",
    "повтор",
)


def _acknowledges_the_repeat(turn: CoherenceTurn) -> bool:
    """Есть ли в ходе объяснение, почему бот спрашивает снова."""
    return any(
        marker in bubble.casefold()
        for bubble in turn.bubbles
        for marker in _REPEAT_EXPLANATION
    )


def _has_bad_news(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.casefold()
    return any(marker in lowered for marker in BAD_NEWS_MARKERS)


def _opener_text(bubbles: list[str]) -> str:
    first = next((bubble.strip() for bubble in bubbles if bubble.strip()), "")
    return first[:OPENER_WINDOW].casefold()


def _name_address(bubbles: list[str]) -> str:
    first = next((bubble.strip() for bubble in bubbles if bubble.strip()), "")
    match = _NAME_ADDRESS.match(first)
    return match.group(1) if match else ""


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", " ", text.casefold()).strip()
