from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from copy import deepcopy
from typing import Any, Dict, List, Literal

from names_ru import FIRST_NAMES, capitalize_name, first_name_from_fio


LIST_FACT_ROOTS = {"properties", "vehicles"}
_DISPLAY_NAME_RE = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$", re.UNICODE)

_RECEIPT_FIELD_TOPICS = {
    "client.region_class": r"(?:город|регион|город\s+и\s+регион|место\s+жительства)",
    "client.name": r"имя",
    "client.phone": r"(?:номер(?:\s+телефона)?|телефон|контакт)",
    "debts.total": r"(?:сумм[ау](?:\s+долга)?|долг|задолженность)",
}
_RECEIPT_STATE = (
    r"(?:вижу|увидел[аи]?|учла|уч[её]л|учт[её]н[аоы]?|записал[аи]?|записан[аоы]?|"
    r"зафиксировал[аи]?|зафиксирован[аоы]?|отметил[аи]?|отмечен[аоы]?|принял[аи]?|получил[аи]?|получен[аоы]?|"
    r"извест(?:ен|н[аоы])|яс(?:ен|н[аоы])|есть)"
)

# The frame is condition -> communication request -> optional answer/presence.
# Topic nouns and adjectives are unrestricted. No client-facing sentence is stored.
_AVAILABILITY_CALL = re.compile(r"\b(?:пишите|напишите|спрашивайте|обращайтесь|задавайте)\b", re.I)
_INFORMATION_EVENT = re.compile(
    r"\b(?:есть|будет|будут|появ\w*|возник\w*|остан\w*)\s+"
    r"(?:(?:[а-яё]+(?:ые|ие|ый|ий|ая|яя|ое|ее|о|е)|какие(?:-то|-нибудь)?|ещ[её])\s+){0,10}"
    r"вопрос\w*\b", re.I,
)
# Closed speech intents share their infinitives and explicit first-person forms.
# They never infer availability from arbitrary future-tense business actions.
_COMMUNICATION_INTENTS = {
    "ответить": ("отвечу", "ответим"),
    "объяснить": ("объясню", "объясним"),
    "пояснить": ("поясню", "поясним"),
    "обсудить": ("обсужу", "обсудим"),
    "поговорить": ("поговорю", "поговорим"),
    "разобраться": ("разберусь", "разберёмся", "разберемся"),
}
_COMMUNICATION_INFINITIVES = "|".join(_COMMUNICATION_INTENTS)
_COMMUNICATION_FUTURES = "|".join(form for forms in _COMMUNICATION_INTENTS.values() for form in forms)
_CONDITIONAL_INFORMATION_ACTIONS = "|".join(
    infinitive for infinitive in _COMMUNICATION_INTENTS if infinitive in {"обсудить", "поговорить"}
) + "|уточнить|спросить|продолжить"
_DISCUSSION_INTENT_FORMS = frozenset(form for infinitive in ("обсудить", "поговорить", "разобраться")
                                   for form in (infinitive, *_COMMUNICATION_INTENTS[infinitive]))
_DISCUSSION_INFINITIVES = "|".join(
    intent for intent in _COMMUNICATION_INTENTS if intent in {"обсудить", "поговорить", "разобраться"}
) + "|разобрать"
_OPTIONAL_DISCUSSION_CONDITION = re.compile(r"\b(?:если\s+(?:вы\s+)?хотите|при\s+желании)\b", re.I)
_OPTIONAL_DISCUSSION_ACTION = re.compile(
    rf"\b(?:(?:я|мы)\s+)?(?:могу|можем)\s+(?:{_DISCUSSION_INFINITIVES})\b", re.I,
)
# Only generic discussion objects and chat/manner padding are safe to remove.
# Unknown objects or another predicate keep the whole sentence for model repair.
_GENERIC_DISCUSSION_PADDING = re.compile(
    r"(?:\s|[,;.!?—–:-]|\b(?:детал[ьи]|ситуаци[а-я]*|вариант[а-я]*|вопрос[а-я]*|"
    r"ваш[а-я]*|вам|вами|с|в|во|на|к|ко|о|об|из|это(?:го)?|что|ближе|"
    r"здесь|тут|чат[а-я]*|прямо|вместе|подробнее|дальше|ещ[её])\b)*", re.I,
)
_STRICT_GENERIC_INTENT_FORMS = _DISCUSSION_INTENT_FORMS | {"объясним", "поясним"}
_INFORMATION_DISCUSSION = re.compile(
    r"\b(?:хотите|захотите|захочется|пожелаете|решите|надумаете)\b[^.!?;:]*"
    rf"\b(?:{_CONDITIONAL_INFORMATION_ACTIONS})\b", re.I,
)
_INDEFINITE_NEED = re.compile(r"\bчто(?:-|\s+)(?:то|нибудь)\b[^.!?;:]*\b(?:понадоб\w*|потреб\w*)\b", re.I)
_FINITE_PREDICATE = re.compile(
    r"\b[а-яё]+(?:ет|ёт|ют|ут|ит|ат|ят|ется|ётся|ются|утся|ится|атся|ятся|ете|ите|итесь|ешь|ешься)\b", re.I,
)
_CONCRETE_AVAILABILITY_DETAIL = re.compile(
    r"\b(?:номер\w*|телефон\w*|контакт\w*|имя|фамили\w*|адрес\w*|город\w*|регион\w*|"
    r"сумм\w*|остат\w*|дат[ауые]|плат[её]ж\w*|паспорт\w*|возраст\w*|доход\w*|зарплат\w*)\b", re.I,
)
_GENERIC_ANSWER = re.compile(
    r"^(?:(?:я|мы)\s+)?(?:(?:могу|можем|постараюсь|постараемся|готов[аы]?)\s+"
    rf"(?P<infinitive>{_COMMUNICATION_INFINITIVES})|(?P<future>{_COMMUNICATION_FUTURES}))\b", re.I,
)
_GENERIC_PRESENCE = re.compile(r"(?:(?:я|мы)\s+)?(?:здесь|тут|на\s+связи|в\s+чате)", re.I)
_GENERIC_HELP_READINESS = re.compile(r"(?:(?:я|мы)\s+)?(?:готов[аы]?|могу|можем)\s+помочь", re.I)
_SPEECH_BOUNDARY = re.compile(r"\n[ \t]*\n|(?<=[.!…])\s+")
_ACT_SEPARATOR = re.compile(r"[,;.!—–]|(?<=\s)-(?=\s)|\s+и\s+", re.I)


def optional_discussion_offer_kind(text: str) -> Literal["generic", "mixed"] | None:
    """Share the speech-act role, retaining substantive or uncertain objects for repair."""
    actions = [action for action in _OPTIONAL_DISCUSSION_ACTION.finditer(text)
               if not re.search(r"\bне\s+$", text[:action.start()], re.I)]
    if not _OPTIONAL_DISCUSSION_CONDITION.search(text) or not actions:
        return None
    padding = _OPTIONAL_DISCUSSION_ACTION.sub("", _OPTIONAL_DISCUSSION_CONDITION.sub("", text))
    return "generic" if _GENERIC_DISCUSSION_PADDING.fullmatch(padding) else "mixed"


def _availability_information_condition(text: str) -> bool:
    body = re.sub(r"^(?:если|когда)\s+", "", text, count=1, flags=re.I)
    if body == text or re.search(r"[,;.!?:—–]", body):
        return False
    # Each coordinated condition needs its own information role. A generic
    # question in one clause cannot hide a substantive event in the next one.
    discussion_scope = False
    for clause in re.split(r"\s+(?:и|или)\s+", body, flags=re.I):
        event = (_INFORMATION_EVENT.search(clause) or _INFORMATION_DISCUSSION.search(clause)
                 or _INDEFINITE_NEED.search(clause))
        if event is None and discussion_scope:
            # Coordinated infinitives can share the preceding desire predicate.
            event = re.match(r"(?:что(?:-|\s+)(?:то|нибудь)\s+)?"
                             rf"(?:{_CONDITIONAL_INFORMATION_ACTIONS})\b", clause, re.I)
        if event is None:
            return False
        discussion_scope = discussion_scope or _INFORMATION_DISCUSSION.search(clause) is not None
        # Outside the role-bound predicate, noun/topic padding is permitted.
        # An inflected topic immediately after a preposition is not a verb
        # merely because its ending coincides with a finite Russian verb.
        prefix = clause[:event.start()].strip()
        if prefix and not re.match(r"^(?:по|о|об|обо|про|в|во)\s+", prefix, re.I):
            return False
        padding = prefix + " " + clause[event.end():]
        if re.search(r"\b(?:не|ни|котор\w*|что|как|чтобы|поскольку)\b", padding, re.I):
            return False
        for predicate in _FINITE_PREDICATE.finditer(padding):
            prefix = padding[:predicate.start()]
            if not re.search(r"\b(?:о|об|обо|про|по|в|во|на|с|со|для|к|ко)\s+$", prefix, re.I):
                return False
    return True


def _availability_answer_or_presence(text: str) -> bool:
    for clause in _ACT_SEPARATOR.split(text.strip(" ,;.!—–:-")):
        clause = clause.strip()
        if not clause or _GENERIC_PRESENCE.fullmatch(clause) or _GENERIC_HELP_READINESS.fullmatch(clause):
            continue
        answer = _GENERIC_ANSWER.match(clause)
        if answer is None:
            return False
        padding = clause[answer.end():]
        intent = (answer.group("infinitive") or answer.group("future")).casefold()
        # New communication forms prove only the speech intent itself or its chat
        # location. Objects, external participants and other unknown padding stay.
        collective_manner = intent in _DISCUSSION_INTENT_FORMS and padding.strip().casefold() == "вместе"
        if (intent in _STRICT_GENERIC_INTENT_FORMS and padding.strip()
                and not _GENERIC_PRESENCE.fullmatch(padding.strip()) and not collective_manner):
            return False
        # Manner/topic padding may vary, but a new proposition or support claim
        # is not an availability suffix.
        if re.search(r"\b(?:что|чтобы|как|почему|если|когда|кто|где|я|мы)\b", padding, re.I) or _FINITE_PREDICATE.search(padding):
            return False
    return True


def _is_empty_availability_invitation(text: str) -> bool:
    text = " ".join(text.split()).strip(" .!")
    if (not text or len(text) > 320 or re.search(r"\d|https?://|www\.|[@?'\"«»“”„‟‹›]", text, re.I)
            or _CONCRETE_AVAILABILITY_DETAIL.search(text)
            or any(unicodedata.category(character).startswith("S") for character in text)):
        return False
    text = re.sub(r"^пожалуйста[!,.]\s*", "", text, count=1, flags=re.I)
    calls = list(_AVAILABILITY_CALL.finditer(text))
    if len(calls) != 1:
        return False
    call = calls[0]
    before = text[:call.start()].strip(" ,;.!—–:-")
    after = text[call.end():].strip(" ,;.!—–:-")
    if before:
        return _availability_information_condition(before) and _availability_answer_or_presence(after)
    # The same act can put its imperative first. Try only punctuation boundaries
    # between the information condition and an optional generic answer clause.
    boundaries = [len(after), *[match.start() for match in _ACT_SEPARATOR.finditer(after)]]
    return any(_availability_information_condition(after[:boundary].strip(" ,;.!—–:-"))
               and _availability_answer_or_presence(after[boundary:].strip(" ,;.!—–:-")) for boundary in boundaries)


def _availability_suffix(text: str) -> tuple[str, str] | None:
    """Locate a whole empty final act, retaining its preceding speech verbatim."""
    # Quoted material and URL punctuation do not establish a safe speech boundary.
    # Preserve uncertain cases, including an opening quote without its closing pair.
    if re.search(r"[\"'«»“”„‟‹›]|https?://|www\.", text, re.I):
        return None
    boundaries = [(0, 0), *((match.start(), match.end()) for match in _SPEECH_BOUNDARY.finditer(text))]
    for prefix_end, suffix_start in boundaries:
        prefix = text[:prefix_end]
        # A short abbreviated token may contain a period without ending a sentence.
        if prefix and re.search(r"\b[А-Яа-яЁёA-Za-z]{1,3}\.$", prefix):
            continue
        suffix = text[suffix_start:]
        if _is_empty_availability_invitation(suffix) or optional_discussion_offer_kind(suffix) == "generic":
            return prefix, suffix
    return None


def omit_repeated_availability_tail(text: str, *, recent_replies: Sequence[str], user_message: str) -> str:
    """Omit a repeated empty final act at a proven paragraph or sentence boundary."""
    if re.search(r"повтор\w*|ещ[её]\s+раз|снова|пиш\w*|напис\w*|связ\w*|"
                 r"обращ\w*|обща\w*|чат\w*|задат\w*|спросить", user_message, re.I):
        return text
    found = _availability_suffix(text)
    if found is None or not found[0].strip():
        return text
    prefix, _ = found
    # Several invitation acts alone remain an answer. Peel only complete empty
    # suffixes to prove that a substantive prefix survives this local omission.
    remaining = prefix
    while remaining.strip():
        prior = _availability_suffix(remaining)
        if prior is None:
            break
        remaining = prior[0]
    if not remaining.strip():
        return text
    repeated = any(_availability_suffix(reply) is not None for reply in recent_replies)
    return prefix if repeated else text


def _catalogue_location_names() -> set[str]:
    """Reuse the configured place names without guessing or resolving an address."""
    from routing.loader import get_matrix
    from routing.normalize import region_key

    return {region_key(name) for entries in get_matrix().regions.values() for entry in entries
            for name in entry.split("\t") if name.strip()}


def _plain_received_field_answer(message: str, field: str) -> bool:
    """Prove a whole value-only answer, rather than infer neutrality from no '?'."""
    value = message.strip().removesuffix(".").strip()
    if not value or len(value) > 140:
        return False
    if field == "client.name":
        return value.casefold().replace("ё", "е") in FIRST_NAMES
    if field == "client.phone":
        digits = re.sub(r"\D", "", value)
        return bool(re.fullmatch(r"[+()\d\s-]+", value) and (
            len(digits) == 10 or len(digits) == 11 and digits[0] in "78"
        ))
    if field == "debts.total":
        return re.fullmatch(
            r"(?:да\s*,?\s+)?(?:ровно\s+)?\d[\d\s]*(?:[,.]\d+)?\s*"
            r"(?:руб(?:лей|ля|ль)?|₽|тыс(?:яч[аи]?)?\.?|млн|миллион(?:а|ов)?)?", value, re.I,
        ) is not None
    if field != "client.region_class":
        return False
    # A short affirmative prefix still leaves an explicit whole place value.
    value = re.sub(r"^да\s*,?\s+", "", value, flags=re.I)
    statement = re.fullmatch(
        r"(?:понял[аи]?[.!]\s*)?я\s+(?:(?:сейчас|теперь)\s+)?"
        r"(?:(?:живу|проживаю)\s+)?(?:в|во)\s+([А-Яа-яЁёA-Za-z\s-]+)", value, re.I,
    )
    if statement is not None:
        from geo.enrichment import _in_city_queries
        from routing.normalize import locality_key, region_key

        # The complete neutral statement must identify a catalogue place.
        # A following question, emotion, negative or conflicting place cannot match.
        place = statement.group(1)
        variants = {region_key(place)}
        variants.update(region_key(name) for name, fixed in _in_city_queries("в " + place)
                        if locality_key(fixed) == locality_key(name))
        return len(variants & _catalogue_location_names()) == 1
    if not re.fullmatch(r"[А-Яа-яЁёA-Za-z\s,.-]+", value):
        return False
    # Use the existing local catalog only for exact whole components. A match
    # somewhere inside a sentence would hide emotion or a requested confirmation.
    from routing.normalize import region_key

    names = _catalogue_location_names()
    components = [region_key(part) for part in value.split(",")]
    return all(components) and all(part in names for part in components)


def omit_empty_field_receipt_bubbles(text: str, *, received_fields: frozenset[str], user_message: str,
                                    known_region: bool = False,
                                    proven_plain_fields: frozenset[str] = frozenset(),
                                    retain_acknowledgement: bool = False) -> str:
    """Omit only a complete empty receipt bubble after a proven plain field value.

    Unmatched content is returned byte-for-byte. This deliberately preserves
    questions, refusal, affect and unknown input instead of guessing their act.
    """
    if not received_fields and known_region:
        # A value-only restatement can produce the same empty service act.
        # This local speech decision does not pretend that a new fact was accepted.
        field = "client.region_class"
    elif len(received_fields) == 1:
        field = next(iter(received_fields))
    else:
        return text
    topic = _RECEIPT_FIELD_TOPICS.get(field)
    # Only a matching code-owned field proof may survive input redaction.
    # The output must still match the same complete clerical receipt below.
    if topic is None or (field not in proven_plain_fields and not _plain_received_field_answer(user_message, field)):
        return text
    if field == "client.region_class":
        # Place-name echoes are the same receipt act as "the region is recorded".
        # Only catalogue names actually present in this candidate can fill its subject.
        echoes = sorted((name for name in _catalogue_location_names() if re.search(
            rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I)), key=len, reverse=True)
        if echoes:
            place = "(?:" + "|".join(re.escape(name) for name in echoes) + ")"
            topic = rf"(?:{topic}|{place}(?:\s*(?:,|\s+и)\s*{place})*)"
    data = rf"(?:{topic}|это|эти\s+(?:данные|сведения|ответы)|(?:ваши\s+)?(?:данные|сведения|ответы))"
    owner = r"(?:(?:я|мне|у\s+меня)\s+)?"
    adverb = r"(?:(?:уже|теперь)\s+)?"
    separator = r"(?:\s*[,—–-]\s*|\s+)"
    # A short comment about asking again adds no substance to a complete receipt.
    # Only this bounded question-management act may follow the receipt. Facts,
    # reassurance, questions and other actions keep the entire bubble unchanged.
    repetition = r"(?:(?:больше|снова|повторно|ещ[её]\s+раз)\s+)?"
    reask = r"(?:переспрашивать|уточнять|спрашивать)"
    reask_comment = (rf"(?:я\s+)?(?:{repetition}{reask}\s+(?:больше\s+)?не\s+"
                     rf"(?:буду|нужно|надо|требуется)|{repetition}не\s+"
                     rf"(?:буду|нужно|надо|требуется)\s+{reask})")
    # An acknowledgment plus the field itself still only echoes intake.
    # The acknowledgment alone remains a free conversational response.
    acknowledgement = r"(?:да|ага|угу|хорошо|ладно|понял[аи]?|понятно|ясно|понимаю)"
    field_echo = rf"(?P<echo_ack>{acknowledgement})[,\s—–-]+{topic}"
    # Gratitude for a factual update has a different role from gratitude for
    # trust, support or another meaningful act. Unknown objects stay intact.
    update_gratitude = (r"(?:спасибо|благодарю)(?:\s+вас)?\s+за\s+(?:(?:ваш[еу]|это)\s+)?"
                        r"(?:уточнение|ответ|информацию|сведения|данные)")
    # A whole recording act still only acknowledges intake without naming its
    # object. General reactions and recording with substantive objects stay free.
    recording_act = rf"{owner}{adverb}(?:записал[аи]?|зафиксировал[аи]?|отметил[аи]?)"
    receipt = re.compile(
        rf"\s*(?:(?P<prefix_ack>спасибо|да|хорошо)[,\s]+)?"
        rf"(?:{data}{separator}{owner}{adverb}(?P<after_ack>{_RECEIPT_STATE})|"
        rf"{owner}{adverb}(?P<before_ack>{_RECEIPT_STATE}){separator}{data}|{field_echo}|{update_gratitude}|{recording_act})"
        rf"(?:\s*[,!.…—–-]\s*{reask_comment})?(?:[,\s]+спасибо)?[.!…\s]*", re.I,
    )
    if retain_acknowledgement:
        # A single proven region receipt can retain the model's own speech act.
        # Passive properties and unmatched clauses remain complete sentences.
        match = receipt.fullmatch(text) if field == "client.region_class" and len(text) <= 180 else None
        if match is None:
            return text
        ack = match.group("prefix_ack") or match.group("echo_ack")
        if not ack:
            verb = match.group("before_ack") or match.group("after_ack") or ""
            if re.fullmatch(r"(?:вижу|учла|уч[её]л|[а-яё]*[аяиеё]л[аи]?)", verb, re.I):
                ack = verb
        if not ack:
            return text
        ending = re.search(r"[.!…]+\s*$", text)
        return ack[0].upper() + ack[1:] + (ending.group().strip() if ending else "")
    bubbles = text.split("\n\n")
    kept = [bubble for bubble in bubbles if len(bubble) > 180 or receipt.fullmatch(bubble) is None]
    return "\n\n".join(kept)


def normalize_display_name(value: Any) -> str | None:
    """Возвращает только однозначное однословное имя для обращения.

    Позиция слова в ФИО не является надёжным признаком: формы и интеграции
    присылают как «Фамилия Имя Отчество», так и «Имя Фамилия Отчество».
    Поэтому многословное значение здесь намеренно не разбирается.
    """
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or not _DISPLAY_NAME_RE.fullmatch(normalized):
        return None
    return normalized


def client_display_name(facts: dict[str, Any] | None) -> str | None:
    """Имя, которое безопасно произнести клиенту.

    Явное ``display_name`` имеет приоритет. Однословное ``client.name``
    совместимо со старыми сессиями. Из ФИО имя берётся только по надёжным
    признакам — словарь имён, отчество, окончание фамилии
    (`names_ru.first_name_from_fio`); позицией слова оно не угадывается.

    Регистр приводится к имени: «сергей» → «Сергей». Бой 19.10.2026: клиент
    написал «сергей иванович» с маленькой буквы, и бот обратился к нему
    «Иванович».
    """
    if not isinstance(facts, dict):
        return None
    raw_client = facts.get("client")
    if not isinstance(raw_client, dict):
        return None
    single = normalize_display_name(raw_client.get("display_name")) or normalize_display_name(
        raw_client.get("name")
    )
    if single:
        return capitalize_name(single)
    return first_name_from_fio(raw_client.get("name"))


def _is_list_index(part: str) -> bool:
    return part.isdigit()


def _would_create_extra_list_item(path: str, facts: Dict[str, Any]) -> bool:
    parts = path.split(".")
    if len(parts) < 2 or parts[0] not in LIST_FACT_ROOTS or not _is_list_index(parts[1]):
        return False
    item_index = int(parts[1])
    if item_index == 0:
        return False
    existing_items = facts.get(parts[0])
    if not isinstance(existing_items, list):
        return True
    return item_index >= len(existing_items)


def set_by_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    current: Any = obj
    parts = path.split(".")
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(current, list) and _is_list_index(part):
            item_index = int(part)
            while len(current) <= item_index:
                current.append({} if not _is_list_index(next_part) else [])
            if current[item_index] is None:
                current[item_index] = {} if not _is_list_index(next_part) else []
            current = current[item_index]
            continue
        if not isinstance(current, dict):
            return
        if part not in current or current[part] is None:
            current[part] = [] if _is_list_index(next_part) else {}
        current = current[part]

    if isinstance(current, dict):
        current[parts[-1]] = value
    elif isinstance(current, list) and _is_list_index(parts[-1]):
        item_index = int(parts[-1])
        while len(current) <= item_index:
            current.append(None)
        current[item_index] = value


def apply_fact_updates(current_facts: Dict, fact_updates: List[Dict]) -> Dict:
    facts = deepcopy(current_facts)
    for update in fact_updates:
        path = update.get("path")
        if (
            path
            and update.get("value") is not None
            and update.get("conflict") is not True
            and not _would_create_extra_list_item(path, facts)
        ):
            set_by_path(facts, path, update["value"])
    return facts


_FORBIDDEN_GUARANTEES = (
    "точно одобр",
    "гарантируем одобр",
    "гарантированно одобр",
    "100% одобр",
    "гарантированно спишем",
    "спишем 100%",
    "мы выдадим кредит",
    "мы выдаем кредит",
    "мы выдаём кредит",
    "дадим вам деньги",
    "дадим вам денег",
)

# Обещания, которых бот не выполняет. Он не видит заявок в CRM и не связывает
# с менеджером: заявку создаёт лид-интейк, а звонит колл-центр заказчика.
# Боевая переписка 08.09.2026 — «Посмотрю, что у нас есть по вашей заявке» и
# «С менеджером свяжу»: обе фразы прозвучали и обе были неправдой.
_FORBIDDEN_PROMISES = (
    "посмотрю, что у нас есть по вашей заявке",
    "посмотрю вашу заявку",
    "посмотрю по вашей заявке",
    "проверю статус вашей заявки",
    "проверю вашу заявку",
    "статус вашей заявки",
    "с менеджером свяжу",
    "свяжу вас с менеджером",
    "соединю вас с менеджером",
    "соединю с менеджером",
    "передам менеджеру",
    "передам вашу заявку менеджеру",
)

_APPLICATION_STATUS = r"статус\s+вашей\s+заявки"
_VISIBILITY_POSITION = r"(?:(?:отсюда|здесь|сейчас|пока|в\s+(?:этом\s+)?чате)\s+){0,2}"
_NEGATIVE_VISIBILITY = r"(?:не\s+(?:вижу|виден|доступен)|недоступен)"
_APPLICATION_CHECK_OBJECT = r"(?:е[её]|его|(?:вашу\s+)?заявку|статус)"
_UNANSWERED_CONTACT = (
    r"почему\s+(?:(?:вам|с\s+вами|ещ[её]|пока|до\s+сих\s+пор)\s+){0,3}"
    r"не\s+(?:связались|позвонили|перезвонили|ответили)"
)
_DENIED_APPLICATION_CHECK = (
    rf"(?:\s+и\s+(?:я\s+)?не\s+могу\s+(?:{_APPLICATION_CHECK_OBJECT}\s+проверить"
    rf"|проверить\s+{_APPLICATION_CHECK_OBJECT}|проверить,\s+{_UNANSWERED_CONTACT}))?"
)
# The optional check is itself explicitly denied. Its closed object/complement
# cannot absorb another promise, a status assertion or a future callback.
_HONEST_STATUS_LIMITATION = re.compile(
    rf"(?:к\s+сожалению,\s*)?{_VISIBILITY_POSITION}"
    rf"(?:{_APPLICATION_STATUS}\s+{_VISIBILITY_POSITION}(?:(?:я|мне)\s+)?"
    rf"{_VISIBILITY_POSITION}{_NEGATIVE_VISIBILITY}"
    rf"|(?:я|мне)\s+{_VISIBILITY_POSITION}{_NEGATIVE_VISIBILITY}\s+"
    rf"{_VISIBILITY_POSITION}{_APPLICATION_STATUS}){_DENIED_APPLICATION_CHECK}[.!…]*", re.I,
)


def _has_unsupported_promise(text: str) -> bool:
    # Only a whole direct negative assertion can exempt the ambiguous status
    # marker. A quote, condition, question or second claim keeps every guard.
    honest_status = _HONEST_STATUS_LIMITATION.fullmatch(text.strip()) is not None
    return any(marker in text and not (marker == "статус вашей заявки" and honest_status)
               for marker in _FORBIDDEN_PROMISES)

# Прод 28.09.2026: бот обещал отменить заявку и удалить номер («заявку
# аннулировала, звонков не будет») — из чата не отменить и не удалить ничего.
# Регулярка, а не подстроки: глаголы в разных формах и с разным порядком слов.
_CANCEL_CLAIM_RE = re.compile(
    r"(?:\bотменю\b|аннулир|\bудалю\b|\bуберу\b|звонить не будем"
    r"|отменил[аи]?\s+(?:вашу\s+)?заявк|заявку\s+(?:вашу\s+)?отменил)"
)
# Тот же день: «звонков не будет», «это автоматический текст на странице» —
# при том что страница после заявки обещает звонок специалиста, а партнёры
# звонят. Отрицать звонок так же нечестно, как обещать его
# (`prompts.AFTER_LEAD_QUESTIONS`).
_CALL_DENIALS = (
    "звонков не будет",
    "звонка не будет",
    "не будет звонк",
    "звонков не планиру",
    "звонки не планиру",
    "звонить не будут",
    "звонить не будет",
    "никто не позвонит",
    "автоматический текст",
    "автоматическая формулировка",
    "стандартная формулировка",
)
# Бой 05.10.2026: на «Откуда вы взялись?» бот ответил «Вы сами оставили
# заявку на подбор». Чат открывается на странице у каждого посетителя, и
# заявки человек мог не оставлять вовсе.
_ORIGIN_CLAIMS = (
    "вы сами оставили заявку",
    "вы оставили заявку на подбор",
    "вы сами обратились",
)
_ORIGIN_FALLBACK = (
    "Я чат на сайте МБК — открываюсь у каждого, кто зашёл на страницу.",
)
# Бой 20.10.2026: «Это ложное срабатывание антивируса, сайт безопасный… отключите
# защиту на минуту». Ни безопасности сайта, ни причины предупреждения бот знать
# не может, а совет отключить защиту опасен в любом разговоре
# (`prompts.SAFETY_ADVICE_RULE`).
_UNSAFE_ADVICE_RE = re.compile(
    r"(?:отключ|выключ|деактивир|приостанов)\w*\s+(?:\w+\s+){0,2}"
    r"(?:защит|антивирус|брандмауэр|фаервол|блокировщик|касперск)"
    r"|ложн\w*\s+срабатыван|сайт\s+(?:абсолютно\s+|полностью\s+)?безопас"
    r"|ссылка\s+(?:абсолютно\s+|полностью\s+)?безопас"
)
_UNSAFE_ADVICE_FALLBACK = (
    "Защиту отключать не нужно. Если страница не открывается, попробуйте другой "
    "браузер или другое устройство.",
)
# После заявки бот страницу по ссылке не видит, а на ней — «заявка принята,
# менеджер свяжется». Бой 18–20.10.2026: «там подобранные варианты»,
# «выбирайте подходящее и оформляйте», «это единственное решение»
# (`prompts.AFTER_LEAD_QUESTIONS`).
_PAGE_CLAIMS = (
    "подобранные вариант",
    "подобранными вариант",
    "доступные вариант",
    "все вариант",
    "варианты по ссылке",
    "варианты уже",
    "варианты на странице",
    "решение по ссылке",
    "решение уже",
    "ваше решение",
    "единственное решение",
    "результат подбора",
    "выбирайте подходящ",
    "выберите подходящ",
    "заполните короткую форму",
    "заполните форму",
)
_CANCEL_FALLBACK = (
    "Отменить заявку или удалить номер отсюда я не могу — из чата у меня нет "
    "к ним доступа.",
)
_CALL_FALLBACK = (
    "Дальше всё, как написано на странице по ссылке: заявка передана, и если "
    "там сказано, что свяжется специалист, — так и есть.",
)

# Что сказать, если фильтр обещаний вырезал реплику целиком. Раньше здесь
# срабатывала запасная фраза гарантийного фильтра, и человек, спросивший про
# свою заявку, слышал «гарантировать одобрение или точную ставку не могу» —
# ревью 09.09.2026. Раньше вырезался в том числе честный ответ по правилу
# 1a промпта («статус вашей заявки я отсюда не вижу»): подстрока та же, что у
# ложного обещания. Целое прямое отрицание видимости теперь сохраняется.
# Для остальных вырезанных обещаний запасная фраза объясняет ограничение
# видимости и то, что бот может сделать на самом деле.
#
# Своих обещаний фраза не даёт: ни звонка, ни менеджера, ни срока. Каждая
# проходит все фильтры guard_bubbles, это закреплено тестом — иначе запасная
# фраза вырезала бы сама себя.
_PROMISE_FALLBACK = (
    "Саму заявку отсюда я не вижу — с ней работают уже после того, как данные "
    "переданы.",
    # Коротко и без «вашей ситуации»: на контактном шаге следом встаёт
    # просьба о телефоне «…подобрать под вашу ситуацию», и повтор режет слух.
    "Здесь могу продолжить подбор и передать ваши данные дальше.",
)
# После финала подбор уже закончен, и предлагать его заново значит начинать
# анкету по второму кругу. Остаётся то, что бот делает и после финала:
# отвечает на вопросы по разговору (POST_LINK_INSTRUCTION).
_PROMISE_FALLBACK_AFTER_FINISH = (
    _PROMISE_FALLBACK[0],
    "Если остались вопросы по нашему разговору — спрашивайте, отвечу.",
)

# Запасные фразы веток развилки на входе (`entry_routing.EXISTING`,
# `entry_routing.OFF_TOPIC`). Анкеты там нет, и общие фразы — «продолжить
# подбор», «ещё немного данных», «гарантировать одобрение не могу» — зовут
# туда, куда человек не шёл, а в ветке «уже оставлял заявку» ещё и обещают
# передачу, которой не будет: лид там не создаётся. Ключи — строки, а не
# импорт: utils ниже по слоям; тест сверяет их с константами.
# Ветка EXISTING не переиспользует _PROMISE_FALLBACK[0] («с ней работают…») потому
# что в этой ветке человек спрашивает о ЧУЖОЙ заявке: бот ничего о ней не знает
# и не может обещать, что «с ней работают». Фраза для ветки честно говорит:
# видимости нет ни статуса, ни решения.
_BRANCH_FALLBACK = {
    "existing": (
        "Саму заявку отсюда я не вижу — ни статуса, ни решения по ней.",
        "Если появится новый вопрос о деньгах или долгах — помогу прямо здесь.",
    ),
    "off_topic": (
        "С этим вопросом помочь не смогу — мы занимаемся кредитами и долгами.",
        "Если понадобится помощь с деньгами — пишите сюда.",
    ),
}

_MODEL_LINK_RE = re.compile(
    r"(?:https?://|www\.|\[\s*ссылка\s*\])",
    re.IGNORECASE,
)


# Внутренняя кухня. Промпт запрещает эти слова, но запрет исполняла только
# модель: 30.07.2026 в живом диалоге прошло «без номера телефона система не
# сформирует решение». Проверка `GLOBAL_FORBIDDEN` в эвале ловит такое уже после
# того, как клиент увидел, поэтому тот же список нужен и на рантайме.
_INTERNAL_TERMS = (
    "матриц",
    "маршрут",
    "приоритет",
    "json",
    # Слово общеупотребимое, но в этом разговоре у бота нет повода его
    # произносить: любая «система» здесь — это мы сами.
    "систем",
)


# Служебные маркеры маскировки ПД — существуют только для внешней модели.
# Если модель вернула их в реплике (обращение «[ФИО скрыто], какая сумма…»
# доехало до живого клиента, QA 28.08.2026), маркер вычищается, а фраза
# приводится в порядок. Список зеркалит плейсхолдеры pii_masking.py.
_MASK_PLACEHOLDER_RE = re.compile(
    r"\s*\[(?:ФИО скрыто|часть ФИО скрыта|остальная часть ФИО скрыта"
    r"|телефон|адрес скрыт|точный адрес скрыт)\]\s*,?\s*"
)


def _scrub_mask_placeholders(text: str) -> str:
    cleaned = _MASK_PLACEHOLDER_RE.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip().lstrip(",;:–- ").strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned or text


def guard_bubbles(
    bubbles: List[str],
    *,
    conversation_over: bool = False,
    scenario: str | None = None,
) -> List[str]:
    """Убирает гарантии, ссылки и упоминания внутренней кухни.

    Живые обороты («ставка ниже», «в районе», «шансы низкие») НЕ трогаем.
    Настоящая ссылка добавляется в ``core._delivery_followup`` уже после этого гарда,
    поэтому любой URL в ответе модели по определению не подтверждён.
    """
    bubbles = [_scrub_mask_placeholders(bubble) for bubble in bubbles]
    safe = []
    # Каким фильтром вырезано — от этого зависит запасная фраза: на вопрос про
    # заявку нельзя отвечать фразой про одобрение.
    cut_by: set[str] = set()
    for bubble in bubbles:
        lowered = bubble.lower()
        if any(marker in lowered for marker in _INTERNAL_TERMS):
            cut_by.add("internal")
            continue
        if any(marker in lowered for marker in _FORBIDDEN_GUARANTEES):
            cut_by.add("guarantee")
            continue
        if _has_unsupported_promise(lowered):
            cut_by.add("promise")
            continue
        if _CANCEL_CLAIM_RE.search(lowered):
            cut_by.add("cancel")
            continue
        if any(marker in lowered for marker in _CALL_DENIALS):
            cut_by.add("call")
            continue
        if any(marker in lowered for marker in _ORIGIN_CLAIMS):
            cut_by.add("origin")
            continue
        if _UNSAFE_ADVICE_RE.search(lowered):
            cut_by.add("unsafe")
            continue
        if conversation_over and any(marker in lowered for marker in _PAGE_CLAIMS):
            cut_by.add("page")
            continue
        if _MODEL_LINK_RE.search(bubble):
            cut_by.add("link")
            continue
        safe.append(bubble)

    if len(safe) == len(bubbles):
        return bubbles
    if safe:
        # A surviving invitation must not replace the answer about an existing
        # application. Restore only the known visibility limitation after a
        # promise cut, keeping cancellation/call/safety outcomes independent.
        if (scenario == "existing" and cut_by == {"promise"}
                and not any(_HONEST_STATUS_LIMITATION.fullmatch(bubble.strip())
                            or bubble == _BRANCH_FALLBACK["existing"][0] for bubble in safe)):
            return [_BRANCH_FALLBACK["existing"][0], *safe]
        return safe
    # Ложь про отмену заявки и про звонок опаснее общего случая: человек уходит
    # с неверным ожиданием. Запасная фраза говорит правду по сути вопроса — и в
    # анкете, и в ветках развилки.
    if "unsafe" in cut_by:
        return list(_UNSAFE_ADVICE_FALLBACK)
    if "cancel" in cut_by:
        return list(_CANCEL_FALLBACK)
    if "call" in cut_by or "page" in cut_by:
        return list(_CALL_FALLBACK)
    if "origin" in cut_by:
        return list(_ORIGIN_FALLBACK)
    # В ветке развилки вырезано всё — отвечаем фразой ветки, какой бы фильтр
    # ни сработал: общие запасные фразы написаны для анкеты.
    if scenario in _BRANCH_FALLBACK:
        return list(_BRANCH_FALLBACK[scenario])
    if cut_by == {"internal"}:
        # Вырезали всё — молчать нельзя, клиент останется без ответа. Нейтральная
        # фраза держит разговор; недостающий вопрос backend добавит сам.
        #
        # После финала та же фраза становится ложью: данных больше не нужно,
        # анкета закончена, и клиент уходит ждать вопросов, которых не будет.
        # Сюда попадает частое «а куда уходят мои данные» уже после отправки
        # лида: модель отвечает про базу и партнёра, фильтр внутренних терминов
        # вырезает ответ целиком, и остаётся обещание продолжить.
        if conversation_over:
            return ["Всё уже передано, от вас больше ничего не требуется."]
        return ["Давайте продолжим — мне нужно ещё немного данных по вашей ситуации."]
    if cut_by <= {"internal", "promise"}:
        # Гарантия или ссылка среди вырезанного перевешивают: обещание
        # одобрения опаснее, после него человек ждёт денег. Про заявку он
        # переспросит, и тогда ответ придёт свой.
        return list(
            _PROMISE_FALLBACK_AFTER_FINISH if conversation_over else _PROMISE_FALLBACK
        )
    return [
        "Гарантировать одобрение или точную ставку заранее не могу — это было бы нечестно.",
        "Но могу разобраться в ситуации и сказать, что реально имеет смысл.",
    ]
