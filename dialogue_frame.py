"""Closed conversational task state and collection requests, independent of wording style."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from routing import get_matrix
from routing.normalize import MONEY_APPROXIMATION, text_matches_location_list
from utils import _plain_received_field_answer, optional_discussion_offer_kind


QuestionField = Literal["debts.total", "client.region_class", "client.name", "client.phone", "intent"]
ResponseGoal = Literal["debt_amount_purpose", "offer_information_purpose", "offer_recipient", "debt_explanation"]
DebtAmountReplyIssue = Literal[
    "unbound_amount_confirmation", "unconfirmed_amount_assertion", "suggested_amount_copy", "approximate_amount_request",
]
_AMOUNT_IMPERATIVE = r"(?:напиши(?:те)?|назови(?:те)?|укажи(?:те)?|сообщи(?:те)?|уточни(?:те)?)"


@dataclass(frozen=True)
class DialogueFrame:
    expected: QuestionField | None
    status: str
    known_fields: frozenset[str] = frozenset()
    refused_fields: frozenset[str] = frozenset()
    mode: Literal["debt", "onloader"] = "debt"
    offer_present: bool = False
    offer_status: str | None = None
    conversation_declined: bool = False
    confirming: bool = False
    require_question: bool = False
    question_goal: ResponseGoal | None = None
    received_fields: frozenset[QuestionField] = frozenset()
    offer_recipient_name: str | None = None
    conversation_paused: bool = False
    question_goal_origin: Literal["code", "model_hint"] = "code"
    reply_target: Literal["transfer", "phone"] | None = None
    closing: bool = False
    defer_discussion_followup: bool = False
    plain_reply_fields: frozenset[QuestionField] = frozenset()
    transfer_blocked: bool = False  # Internal capability proof; not part of model context.
    known_phone_reference: bool = False  # Whole-turn code proof; never permission to transfer.

    @property
    def contact_declined(self) -> bool:
        """Conversation can continue after either contact collection or transfer is declined."""
        return "client.phone" in self.refused_fields or self.conversation_declined or self.conversation_paused

    @property
    def trusted_question_goal(self) -> ResponseGoal | None:
        """Only code-owned goals can change validation or collection permissions."""
        return self.question_goal if self.question_goal_origin == "code" else None

    @property
    def allowed_field(self) -> QuestionField | None:
        if self.closing:
            return None
        if (self.conversation_declined or self.conversation_paused) and self.expected in {"client.name", "client.phone"}:
            return None
        # Explaining the reason for debt collection temporarily defers intake.
        # Keep expected intact so the existing plan can resume on the next turn.
        if self.trusted_question_goal in {"debt_amount_purpose", "debt_explanation"}:
            return None
        return self.expected if self.expected not in self.known_fields | self.refused_fields else None

    @property
    def brief_field_reply(self) -> bool:
        """A proven neutral region update gets one conversational response role."""
        return bool(self.mode == "debt" and self.status == "collected_held" and not self.offer_present
                    and self.allowed_field is None and self.trusted_question_goal is None
                    and not self.confirming and not self.closing and not self.contact_declined
                    and "client.region_class" in self.known_fields
                    and "client.region_class" in self.plain_reply_fields)

    def allows(self, path: str) -> bool:
        return path == self.allowed_field


def _discussion_followup_spans(
    text: str, frame: DialogueFrame, *, removable_only: bool = False,
) -> list[tuple[int, int]]:
    """Observe optional questions and discussion offers, independent of punctuation."""
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"\n]*"', lambda match: " " * len(match.group()), text)
    spans: list[tuple[int, int]] = []
    for sentence in re.finditer(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?", unquoted):
        body = sentence.group()
        reported = _REPHRASING.search(body) or re.match(
            r"\s*(?:в\s+примере|(?:вы|я|мы|он|она|они)\s+(?:спрашива\w*|спрос\w*|сказа\w*))\b",
            body, re.I,
        )
        if reported or request_fields(body, frame):
            continue
        optional_offer = optional_discussion_offer_kind(body)
        if not body.rstrip().endswith("?") and not optional_offer:
            continue
        if removable_only and optional_offer == "mixed":
            continue
        # Never remove quoted material that happens to share a sentence with an act.
        if removable_only and re.search(r'[«»“”"]', text[sentence.start():sentence.end()]):
            continue
        spans.append(sentence.span())
    return spans


def should_defer_discussion_followup(frame: DialogueFrame, *, previous_reply: str, user_message: str) -> bool:
    """A proven field-value reply leaves a previous optional question unanswered."""
    if (frame.mode != "debt" or frame.status != "collected_held" or frame.offer_present
            or frame.expected is not None or frame.closing or frame.question_goal is not None):
        return False
    return bool(_discussion_followup_spans(previous_reply, frame)) and any(
        _plain_received_field_answer(user_message, field) for field in frame.known_fields
    )


def discussion_followup_issue(text: str, frame: DialogueFrame) -> Literal["repeated_discussion_followup"] | None:
    """The field reply does not authorize another attempt at the optional discussion question."""
    if frame.defer_discussion_followup and _discussion_followup_spans(text, frame):
        return "repeated_discussion_followup"
    return None


def omit_deferred_discussion_followup(text: str, frame: DialogueFrame) -> str:
    """Keep the model's useful body before analysis, never turn a question-only draft into silence."""
    if not frame.defer_discussion_followup:
        return text
    cleaned = text
    for start, end in reversed(_discussion_followup_spans(text, frame, removable_only=True)):
        cleaned = cleaned[:start] + cleaned[end:]
    cleaned = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", cleaned).strip()
    return cleaned if any(character.isalnum() for character in cleaned) else text


def is_plain_debt_knowledge_request(user_message: str) -> bool:
    """Recognize a whole general question without application facts or choices."""
    concept = r"(?:реструктуризац[а-яё]*|списани[а-яё]*|банкротств[а-яё]*)(?:\s+(?:кредита|долга))?"
    comparison = (
        rf"(?:чем\s+(?:{concept}\s+отличается\s+от\s+{concept}|отличается\s+{concept}\s+от\s+{concept})|"
        rf"(?:в\s+ч[её]м\s+)?(?:разница|разницу|отличие)\s+между\s+{concept}\s+и\s+{concept})"
    )
    polite = r"(?:(?:объясни(?:те)?|поясни(?:те)?|подскажи(?:те)?)[,\s]+)?"
    return bool(len(user_message) <= 260 and re.fullmatch(
        rf"\s*{polite}(?:что\s+такое\s+{concept}|{comparison})[.!?\s]*", user_message, re.I,
    ))


def omit_explanatory_preference_followup(text: str, frame: DialogueFrame, *, user_message: str) -> str:
    """Keep a complete knowledge answer without adding an unsolicited preference task."""
    if (frame.mode != "debt" or frame.status != "collected_held" or frame.expected is not None
            or frame.trusted_question_goal != "debt_explanation" or frame.offer_present
            or frame.contact_declined or frame.confirming or frame.closing):
        return text
    # Personal next steps, additional instructions and unknown clauses stay free.
    if not is_plain_debt_knowledge_request(user_message):
        return text
    body, separator, tail = text.rpartition("\n\n")
    if not separator or "?" in body or not any(character.isalnum() for character in body):
        return text
    # All words belong to a generic preference question. A factual assertion,
    # quote, meaningful clarification or unknown object cannot match this act.
    subject = (r"(?:что(?:\s+именно)?(?:\s+из\s+этого)?|"
               r"какой(?:\s+из\s+этих)?\s+(?:вариант|способ|путь))")
    recipient = r"(?:\s+(?:вам|вас|сейчас|больше))*"
    preference = r"(?:ближе|интересует|волнует|предпочитаете)"
    suffix = r"(?:\s+(?:вам|вас|сейчас|больше|к\s+вашей\s+ситуации))*"
    if re.fullmatch(rf"\s*{subject}{recipient}\s+{preference}{suffix}\?\s*", tail, re.I):
        return body.rstrip()
    return text


def debt_amount_reply_issue(text: str, frame: DialogueFrame) -> DebtAmountReplyIssue | None:
    """An uncertain amount requires the client's number, never agreement with the speaker's guess."""
    # A FAQ can defer collection without establishing an exact current debt.
    if not frame.confirming or frame.expected != "debts.total" or "debts.total" in frame.known_fields:
        return None
    # A decimal point belongs to the amount, so its qualification stays with it.
    for sentence in re.findall(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?", text):
        # Quoted examples and conditional explanations do not assert a current fact.
        unquoted = re.sub(r'«[^»]*»|"[^"\n]*"', "", sentence)
        # Unknown debt cannot license progress through intake. General chat has
        # a separate purpose and remains available without a completed amount.
        for clause in re.split(r"\b(?:но|однако|зато)\b|;", unquoted, flags=re.I):
            progress = re.search(r"\b(?:продолж\w*|заверш\w*|заполн\w*|двигаться\s+дальше)\b", clause, re.I)
            sufficiency = re.search(r"\b(?:достаточно|хватит|хватает)\b", clause, re.I)
            advancing = re.search(r"\b(?:можем|можно|давайте)\s+(?:уже\s+)?"
                                  r"(?:продолж\w*|заверш\w*|заполн\w*|двигаться\s+дальше)|\bпродолжим\b", clause, re.I)
            assertion = sufficiency or advancing
            chat_scope = re.search(
                r"\b(?:чат\w*|разговор\w*|бесед\w*|общени\w*|обсужден\w*|пояснен\w*|"
                r"общ\w*\s+консультаци\w*|отвеча\w*[^,.!?]{0,35}\bвопрос\w*)\b", clause, re.I,
            )
            exact_condition = re.search(r"\bесли\b[^,!?]{0,100}\bточн\w*\b[^,!?]*", clause, re.I)
            conditional_exact = exact_condition is not None and not re.search(
                r"\b(?:не|нет|неизвест\w*)\b", exact_condition.group(), re.I,
            )
            if (progress and assertion and "?" not in clause and not chat_scope and not conditional_exact
                    and not re.search(r"\bне\s*$", clause[:assertion.start()], re.I)):
                return "unconfirmed_amount_assertion"
        # An estimate requested as one point remains uncertain even without a
        # suggested literal. Elliptical qualifiers can form a second bubble of the same act.
        courteous = re.sub(r",?\s*\bпожалуйста\b\s*,?", " ", unquoted, flags=re.I)
        request_span = re.search(rf"\b{_AMOUNT_IMPERATIVE}\b[^,.!?]{{0,90}}", courteous, re.I)
        request_text = request_span.group() if request_span is not None else ""
        request_marker = MONEY_APPROXIMATION.search(request_text)
        estimate_request = bool(request_marker and not re.search(
            r"\bне\s*$", request_text[:request_marker.start()], re.I,
        ) and re.search(r"\b(?:сумм\w*|остат\w*|числ\w*)\b|\d", request_text, re.I))
        fragment = re.sub(r"^\s*(?:хотя\s+бы\s+)?", "", unquoted, count=1, flags=re.I)
        fragment_marker = MONEY_APPROXIMATION.match(fragment)
        estimate_fragment = bool(fragment_marker and re.match(
            r"\s*[,—-]?\s*(?:но\s+)?одним\s+числом\b(?!\s+(?:не|нельзя)\b)",
            fragment[fragment_marker.end():], re.I,
        ))
        if estimate_request or estimate_fragment:
            return "approximate_amount_request"
        # Asking to copy a proposed number creates false precision even when
        # qualified by "if it is closest". Check this act before the conditional exemption.
        if re.search(rf"\b{_AMOUNT_IMPERATIVE}\b\s*"
                     r"(?:,\s*)?(?:пожалуйста\s*,?\s*)?\d", unquoted, re.I):
            return "suggested_amount_copy"
        if not re.search(r"\d", unquoted) or re.search(r"\b(?:если|например)\b", unquoted, re.I):
            continue
        open_request = re.search(
            r"\b(?:сколько|какова|какая)\b|"
            r"\b(?:напиш\w*|назов\w*|укаж\w*|сообщ\w*|уточн\w*)[^.!?]{0,65}"
            r"\b(?:сумм\w*|остат\w*|числ\w*)\b", unquoted, re.I,
        )
        alternative = re.search(r"\b(?:больше|меньше|ближе)\b|"
                                r"\b(?:или|либо)\b[^.!?]{0,35}\b(?:примерн\w*|приблизительн\w*|неточн\w*)\b",
                                unquoted, re.I)
        if "?" in unquoted:
            if not open_request and not alternative:
                return "unbound_amount_confirmation"
            # An alternative clarifies uncertainty without asserting an exact value.
            continue
        # A complete numeric acknowledgement is an assertion of an exact value,
        # even without a bookkeeping verb. This applies only while the value is unknown.
        if re.fullmatch(r"\s*(?:(?:хорошо|да|верно)\s*[,—-]?\s*)?"
                        r"\d[\d\s\u00a0]*(?:[,.]\d+)?\s*(?:руб\w*|тыс\w*|млн|₽)?[.!]?\s*",
                        unquoted, re.I):
            return "unconfirmed_amount_assertion"
        # A recorded-value claim can precede or follow its number. It must not
        # turn an unconfirmed estimate into an accepted scalar in speech.
        qualified_estimate = any(not re.search(r"\bне\s*$", unquoted[:marker.start()], re.I)
                                 for marker in MONEY_APPROXIMATION.finditer(unquoted))
        if not open_request and not qualified_estimate:
            recording = re.finditer(
                r"\b(?:счита(?:ем|ю)|(?:за)?фиксир(?:ую|уем|овал[аи]?|ован[аоы]?)|"
                r"запис(?:ал[аи]?|ан[аоы]?)|запиш(?:у|ем)|подтвердил[аи]?)\b", unquoted, re.I,
            )
            for claim in recording:
                if not re.search(r"\bне\s+(?:(?:был[аио]?|будет)\s+)?$", unquoted[:claim.start()], re.I):
                    return "unconfirmed_amount_assertion"
    return None


@dataclass(frozen=True)
class CollectionResult:
    text: str
    asked_field: QuestionField | None
    removed_fields: tuple[str, ...]
    pressure_removed: bool = False


# These describe requests for data. Mere mentions and explanatory/rhetorical
# questions remain ordinary conversation and do not become intake actions.
_REQUEST_CUE = re.compile(
    r"\?|\b(?:подскажи(?:те)?|скажи(?:те)?|назови(?:те)?|назвать|укажи(?:те)?|указать|"
    r"напиши(?:те)?|написать|оставь(?:те)?|оставить|сообщи(?:те)?|сообщить|"
    r"расскажи(?:те)?|рассказать|уточни(?:те)?|уточнить|оставляй(?:те)?|указывай(?:те)?|"
    r"сообщай(?:те)?|уточняй(?:те)?|рассказывай(?:те)?|называй(?:те)?)\b", re.IGNORECASE,
)
_BRIEF_REQUEST_CUE = re.compile(
    r"\?|\b(?:подскажи(?:те)?|скажи(?:те)?|назови(?:те)?|укажи(?:те)?|напиши(?:те)?|"
    r"оставь(?:те)?|сообщи(?:те)?|расскажи(?:те)?|уточни(?:те)?)\b", re.IGNORECASE,
)
_INTENT_CONFIRMATION = re.compile(
    r"\b(?:вам\s+(?:важно|нужно)|вы\s+хотите)\b[^.!?]{0,65}"
    r"\b(?:разобра\w*|реш\w*|обсуд\w*)\b[^.!?]{0,65}(?:долг|банкрот|реструктур)", re.I,
)
_REPHRASING = re.compile(r"^\s*(?:вы\s+(?:спрашиваете|имеете\s+в\s+виду)|вопрос\s+в\s+том)", re.IGNORECASE)
_REQUIRED_PHONE = re.compile(r"(?<!\bне )\b(?:нужен|необходим)\s+[^.!?]*(?:телефон|номер|контакт)", re.I)
_EXPLAIN_OFFER = re.compile(r"^\s*(?:хотите\s*,?\s*(?:я\s+)?(?:объясню|расскажу|поясню)|"
                            r"могу\s+(?:вам\s+)?(?:объяснить|рассказать|пояснить))", re.I)
_DIRECT_COLLECTION = re.compile(r"\b(?:остав\w*|укаж\w*|напиш\w*|назов\w*|сообщ\w*)\b", re.I)
_OBJECT_FRONTED_REQUEST = (
    r"[^.!?]{0,45}\b(?:можете|сможете|могли\s+бы|готовы)\b[^.!?]{0,35}"
    r"\b(?:назвать|указать|написать|сообщить|уточнить|оставить)\b"
)


def _explanation_question(sentence: str) -> bool:
    return bool(_REPHRASING.search(sentence) or
                (_EXPLAIN_OFFER.search(sentence) and not _DIRECT_COLLECTION.search(sentence)))


def _known_contact_purpose(sentence: str, frame: DialogueFrame) -> bool:
    """A declarative explanation of an existing contact does not request it again."""
    return (frame.trusted_question_goal == "offer_information_purpose" and "client.phone" in frame.known_fields
            and "?" not in sentence and not _DIRECT_COLLECTION.search(sentence))
_PATTERNS = {
    "client.phone": r"(?:телефон|номер|контакт).{0,55}(?:нужен|необходим)|"
                    r"(?<!\bне )\b(?:нужен|необходим)\s+[^.!?]*(?:телефон|номер|контакт)|"
                    r"(?:какой|на\s+какой|ваш)[^.!?]*(?:телефон|номер)|"
                    r"(?:остав\w*|укаж\w*|указыв\w*|напиш\w*|сообщ\w*|назов\w*|назыв\w*)[^.!?]*(?:телефон|номер|контакт)",
    "client.name": r"как\s+(?:к\s+вам\b[^.!?]{0,30}обраща\w*|вас\b[^.!?]{0,30}(?:зовут|звать|назыв\w*))|"
                   r"(?:ваше|назов\w*|назыв\w*|укаж\w*|указыв\w*|напиш\w*|подскаж\w*)[^.!?]{0,35}\bимя\b",
    "client.region_class": r"(?:в\s+каком|какой|каком)[^.!?]{0,25}(?:город|регион)|"
                           r"(?:подскаж\w*|назов\w*|назыв\w*|укаж\w*|указыв\w*|сообщ\w*|рассказ\w*|уточн\w*)[^.!?]*(?:город|регион)|"
                           r"\bгде\b[^.!?]{0,40}(?:жив[её]те|проживаете|прописаны|зарегистрированы|находитесь)|"
                           r"\bвы\s+(?:жив[её]те|проживаете|прописаны|зарегистрированы)[^.!?]{0,30}\b(?:в|во)\b",
    "debts.total": r"(?:сколько[^.!?]{0,30}(?:осталось\s+(?:выплатить|погасить|вернуть)|долг|задолжен)|"
                   r"(?:какая|какую|какова)[^.!?]{0,35}сумм[^.!?]{0,35}(?:долг|задолжен)|"
                   r"(?:назов\w*|назыв\w*|назвать|уточн\w*|укаж\w*|указыв\w*|сообщ\w*|рассказ\w*|подскаж\w*|скажите|напиш\w*)[^.!?]*"
                   r"(?:сумм[^.!?]*(?:долг|задолжен)|остат\w*[^.!?]{0,35}(?:долг|задолжен|погасить)))",
    "debts.overdue_duration": r"(?:есть|имеются|имеется|бывают|допускали|долго|давно|месяц)[^.!?]*просроч|"
                              r"просроч[^.!?]*(?:есть|имеются|долго|давно|месяц)",
    "client.age": r"сколько\s+вам\s+(?:лет|полных)|ваш\s+возраст",
    "request.amount": r"(?:сумм[^.!?]*кредит[^.!?]*(?:нужн|рассчитыва|получить)|"
                      r"(?:какой|на\s+какой)[^.!?]*кредит[^.!?]*(?:хотите|получить|рассчитыва)|"
                      r"(?:сколько|какой)[^.!?]*(?:получить|хотите)[^.!?]*кредит|"
                      r"какая\s+сумма\s+вам\s+нужна|(?:нужен|нужно)\s+(?:вам\s+)?(?:кредит|за[её]м))",
    "employment.income": r"(?:какой|каков\w*|назов\w*|укаж\w*|напиш\w*|подскаж\w*)[^.!?]*(?:доход|зарплат)|"
                         r"сколько\s+вы\s+зарабатываете",
    "debts.monthly_payment": r"(?:сумм[^.!?]*плат[её]ж[^.!?]*месяц|ежемесячн\w*[^.!?]*плат[её]ж|"
                             r"плат[её]ж[^.!?]*ежемесячн)",
    "assets": r"есть\s+ли\s+у\s+вас[^.!?]*(?:недвижим|машин|автомобил|квартир)",
    "debts.composition": r"сумм[^.!?]*(?:включает|основной\s+долг|без\s+штраф)|"
                         r"(?:основной\s+долг|проценты\s+и\s+штрафы)[^.!?]*(?:известн|вместе|отдельно)",
}
# The requested object can precede its verb. Explanations such as "the exact
# amount can be found at the bank" remain outside these collection predicates.
for _path, _object in {
    "debts.total": r"\b(?:точную\s+|общую\s+)?(?:сумму\s+(?:долга|задолженности)|остаток\s+долга)\b",
    "client.phone": r"\b(?:номер(?:\s+телефона)?|телефон|контакт)\b",
    "client.name": r"\b(?:ваше\s+)?имя\b",
    "client.region_class": r"\b(?:ваш\s+)?(?:город|регион|место\s+жительства)\b",
}.items():
    _PATTERNS[_path] += "|" + _object + _OBJECT_FRONTED_REQUEST
_COLLECTION = {path: re.compile(pattern, re.IGNORECASE) for path, pattern in _PATTERNS.items()}
_CONTACT_PRESSURE = re.compile(r"без\s+(?:вашего\s+)?(?:передачи\s+)?(?:номера|телефона|контакта|контактов|данных)[^.!?]{0,80}"
                             r"(?:не\s+(?:смогу|сможем|могу|можем|получится|будет|передать|отправить|продолжить|завершить)|нельзя|невозможн\w*|обязательн)|"
                             r"(?:может|всё-таки|тогда)[^.!?]*(?:остав\w*|укаж\w*|напиш\w*)\s+(?:его|контакт)", re.IGNORECASE)
_CONTACT_NEED_PRESSURE = re.compile(r"(?:номер|телефон|контакт)[^.!?]*(?<!\bне )\b(?:нужен|необходим)", re.I)
_CONTACT_HELP_BARRIER = re.compile(
    r"без\s+(?:него|номера|телефона|контакта)[^.!?]{0,60}не\s+(?:смогу|могу|сможем|можем|получится)"
    r"[^.!?]{0,40}(?:продолж|помочь|ответить|работ)|"
    r"без\s+(?:него|номера|телефона|контакта)[^.!?,;]{0,60}(?:продолж\w*|помочь|ответить|работ\w*)"
    r"[^.!?,;]{0,40}не\s+(?:смогу|могу|сможем|можем|получится)", re.I,
)
_CONTACT_RETURN_CONDITION = re.compile(
    r"\bесли\s+(?:передума\w*|надума\w*)[^.!?]*(?:продолж\w*|помо\w*|ответ\w*)", re.I,
)
_CONTACT_PROGRESS_CONDITION = re.compile(
    r"\bчтобы\b[^.!?]{0,45}(?:продолж\w*|помочь|ответить)[^.!?]{0,85}(?:номер|телефон|контакт)", re.I,
)
_APPLICATION_CONTACT_SCOPE = re.compile(r"заявк|анкет|оформлен|обратн\w*\s+(?:связ|звон)", re.I)
_STORAGE_PURPOSE = re.compile(
    r"\b(?:чтобы|для)\s+(?:сохран\w*|запис\w*|(?:за)?фикс\w*)[^.!?;]{0,50}(?:ответ|данн|сведен|анкет)", re.I,
)
_CONTACT_NECESSITY = re.compile(
    r"\b(?:нуж\w*|необходим\w*|требуется|обязател\w*)[^.!?,;]{0,35}(?:номер|телефон|контакт)|"
    r"(?:номер|телефон|контакт)[^.!?,;]{0,35}\b(?:нуж\w*|необходим\w*|требуется|обязател\w*)", re.I,
)
_NECESSITY_DENIAL = re.compile(r"\bне\s+(?:будет\s+)?(?:нуж\w*|необходим\w*|требуется|обязател\w*)", re.I)


def contact_claims_storage_requirement(sentence: str) -> bool:
    """Contact prepares an application. Persisting chat answers does not require it."""
    clauses = re.split(r"\b(?:но|однако|зато)\b", sentence, flags=re.I)
    return any(_STORAGE_PURPOSE.search(clause) and _CONTACT_NECESSITY.search(clause)
               and not _NECESSITY_DENIAL.search(clause) for clause in clauses)


def contact_blocks_chat(sentence: str, *, phone_context: bool = False) -> bool:
    """An application may need contact, while the conversation remains available."""
    explicit_contact = bool(re.search(r"номер|телефон|контакт", sentence, re.I))
    condition = _CONTACT_PROGRESS_CONDITION.search(sentence)
    if condition is not None and not _DIRECT_COLLECTION.search(condition.group()) and re.match(
        r"\w*\s+(?:не\s+(?:нуж\w*|необходим\w*|требу\w*|обязател\w*)|необязател\w*)\b",
        sentence[condition.end():], re.I,
    ):
        # Denying the need for contact leaves any separate help barrier to its own check.
        condition = None
    barrier = _CONTACT_HELP_BARRIER.search(sentence) if explicit_contact or phone_context else None
    if condition is not None:
        # The continuation itself must name the application operation. A later
        # explanation of the number's purpose cannot qualify generic chat access.
        head = condition.group().split(",", 1)[0]
        if re.search(r"продолж\w*[^.!?,]{0,25}(?:чат|разговор|бесед|общени)", head, re.I) \
                or not _APPLICATION_CONTACT_SCOPE.search(head):
            return True
    if barrier is None:
        return False
    # A separate mention of an application cannot qualify a later denial of chat.
    scope = sentence[barrier.start():]
    chat_scope = re.search(r"продолж\w*[^.!?,]{0,25}(?:чат|разговор|бесед|общени)", scope, re.I)
    return bool(chat_scope or not _APPLICATION_CONTACT_SCOPE.search(scope))


_DEBT_AMOUNT_DECLARATION = re.compile(
    r"\s*(?:(?:вижу|у\s+(?:нас|вас)|вы\s+(?:назвали|указали))\s+)?"
    r"(?:(?:общая|общую|вся|полная|ваша|вашу)\s+)?"
    r"(?:сумм[ауы]\s+(?:долга|задолженности)|долг|задолженность)\s*[:—–-]?\s*"
    r"(?:(?:у\s+нас|у\s+вас|составляет|получается)\s+)?"
    r"\d[\d\s\u00a0]*(?:[,.]\d+)?\s*"
    r"(?:(?:тыс\w*|млн|миллион\w*)(?:\s+руб\w*)?|руб\w*|₽)?[.!]?\s*", re.I,
)
_DEBT_CONFIRMATION_QUESTION = re.compile(
    r"\s*(?:(?:это\s+)?(?:верно|правильно|так)|"
    r"(?:это|эта\s+(?:сумма|цифра)|сумма|цифра)\s+"
    r"(?:точн\w*|примерн\w*|приблизительн\w*)(?:\s+(?:сумма|цифра))?"
    r"(?:\s+или\s+(?:точн\w*|примерн\w*|приблизительн\w*)(?:\s+(?:сумма|цифра))?)?"
    r"|(?:это\s+)?(?:вся|общая|полная|суммарная)\s+(?:задолженность|сумма(?:\s+долга)?)"
    r"(?:\s+по\s+(?:всем\s+)?(?:кредитам|займам)(?:\s+и\s+(?:кредитам|займам))?)?"
    r"(?:\s+вместе)?"
    r")\s*\?\s*", re.I,
)
_CONFIRMATION_QUOTES = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'")


def _known_debt_confirmation(text: str, frame: DialogueFrame, previous_sentence: str) -> bool:
    """Bind a truth/precision/totality check to the immediately named known debt."""
    if frame.mode != "debt" or "debts.total" not in frame.known_fields or frame.expected == "debts.total":
        return False
    text = _CONFIRMATION_QUOTES.sub("[quoted]", text)
    if any(mark in text for mark in ('"', "'", "«", "»", "“", "”")):
        return False
    previous = previous_sentence
    for paragraph in text.split("\n\n"):
        for sentence in re.findall(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?", paragraph):
            # An anaphoric question inherits only its immediate declarative debt
            # quantity. A quote, condition or another subject cannot match it.
            if (_DEBT_CONFIRMATION_QUESTION.fullmatch(sentence)
                    and _DEBT_AMOUNT_DECLARATION.fullmatch(previous)):
                return True
            # The same two roles may share a sentence, joined by an em dash.
            for boundary in re.finditer(r"\s+[—–-]\s+", sentence):
                if (_DEBT_AMOUNT_DECLARATION.fullmatch(sentence[:boundary.start()])
                        and _DEBT_CONFIRMATION_QUESTION.fullmatch(sentence[boundary.end():])):
                    return True
            previous = sentence
        previous = ""
    return False


def request_fields(sentence: str, frame: DialogueFrame, *, previous_sentence: str = "",
                   allow_debt_confirmation: bool = True) -> set[str]:
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"\n]*"', "", sentence)
    debt_confirmation = allow_debt_confirmation and _known_debt_confirmation(sentence, frame, previous_sentence)
    # A proven plain update after intake has a reply-only role. Observe direct
    # requests without treating an infinitive in a report as a new question.
    brief_request = frame.brief_field_reply and bool(
        _BRIEF_REQUEST_CUE.search(unquoted) or re.search(_OBJECT_FRONTED_REQUEST, unquoted, re.I)
    )
    if _explanation_question(unquoted) and not brief_request and not debt_confirmation:
        return set()
    region_confirmation = False
    # Match an actual confirmation cue before consulting the existing pure
    # catalogue. A quoted fragment is not a new question about the client.
    if (not any(mark in unquoted for mark in ('«', '»', '"', '“', '”'))
            and re.search(r"\b(?:верно|правильно|так)\s*\?|\bподтверди(?:те)?\b", unquoted, re.I)):
        region_confirmation = any(text_matches_location_list(unquoted, entries)
                                  for entries in get_matrix().regions.values())
    if not _REQUEST_CUE.search(unquoted) and not _REQUIRED_PHONE.search(unquoted) and not region_confirmation:
        return set()
    fields = {path for path, pattern in _COLLECTION.items() if pattern.search(unquoted)}
    # A closed goal remains a data task even after the cursor has moved on.
    # The same allows() invariant handles both pending and repeated questions.
    if frame.mode == "debt" and _INTENT_CONFIRMATION.search(unquoted):
        fields.add("intent")
    if region_confirmation:
        fields.add("client.region_class")
    if debt_confirmation:
        fields.add("debts.total")
    # With a confirmed debt and no pending amount task, a bare scalar followed
    # by a confirmation cue is a repeated debt question. Threshold explanations
    # and other money topics do not have this whole confirmation shape.
    if (frame.mode == "debt" and "debts.total" in frame.known_fields
            and frame.expected != "debts.total" and re.fullmatch(
                r"\s*(?:(?:общая\s+)?(?:сумма|долг|задолженность)\s+)?"
                r"(?:(?:у\s+нас|у\s+вас|составляет|получается)\s+)?"
                r"\d[\d\s\u00a0]*(?:[,.]\d+)?\s*(?:руб\w*|тыс\w*|млн|₽)?"
                r"\s*[,—–-]?\s*(?:это\s+)?(?:верно|правильно|так)\s*\?\s*", unquoted, re.I)):
        fields.add("debts.total")
    if "debts.total" not in fields and re.search(r"\b(?:сколько|количество|число)\b[^.!?]*(?:кредит|займ)", unquoted, re.I):
        fields.add("debts.loan_count")
    # The actual pending confirmation supplies the topic. A currency quantity or
    # amount-choice question can therefore omit "debt", without depending on a verb.
    other_money = bool(fields.intersection({"request.amount", "employment.income"}) or re.search(
        r"(?:получить|нов\w*\s+(?:кредит|за[её]м)|вам\s+(?:нуж|требу)|рассчитыва|"
        r"доход|зарплат|зарабат|месяч|месяц|плат[её]ж|взнос|стоимост|стоит|цена)", unquoted, re.I,
    ))
    money_quantity = bool(re.search(r"\d[\d\s,.]*\s*(?:руб\w*|тыс\w*|млн|миллион\w*|₽)", unquoted, re.I))
    # A deictic amount/total reference plus an exclusive alternative reopens
    # totality, rather than asking a new explanatory question about debt types.
    if (frame.mode == "debt" and "debts.total" in frame.known_fields and frame.expected != "debts.total"
            and not other_money and re.search(r"\b(?:эти|эта|названн\w*|указанн\w*)\b", unquoted, re.I)
            and (money_quantity or re.search(r"\bсумм\w*\b", unquoted, re.I))
            and re.search(r"\b(?:только|лишь|исключительно)\b[^.!?]*\b(?:или|ещ[её])\b", unquoted, re.I)
            and re.search(r"кредит|за[её]м|займ|долг|задолжен", unquoted, re.I)):
        fields.add("debts.total")
    amount_choice = bool(re.search(r"\d", unquoted) and re.search(
        r"\b(?:ровно|больше|меньше|ближе|точн\w*|примерно)\b", unquoted, re.I,
    ))
    amount_request = bool(re.search(
        r"(?:назов\w*|назыв\w*|назвать|укаж\w*|указыв\w*|указать|напиш\w*|написать|сообщ\w*|рассказ\w*|уточн\w*)[^.!?]*(?:сумм|остат)",
        unquoted, re.I,
    ))
    if frame.expected == "debts.total" and not other_money and (
        amount_request or (frame.confirming and (money_quantity or amount_choice))
    ):
        fields.add("debts.total")
    if frame.expected == "debts.total" and "request.amount" not in fields and re.search(
        r"сколько\s+(?:всего\s+)?(?:получается|выходит|будет)(?:\s+в\s+сумме)?", unquoted, re.I,
    ):
        fields.add("debts.total")
    if (frame.expected == "client.phone" or "client.phone" in frame.refused_fields) and re.search(
        r"(?:остав\w*|укаж\w*|напиш\w*)\s+его\b", unquoted, re.IGNORECASE,
    ):
        fields.add("client.phone")
    if frame.expected == "intent" and re.search(r"хотите[^.!?]*(?:долг|банкрот|реструктур)", unquoted, re.I):
        fields.add("intent")
        fields.discard("debts.total")
    if _known_contact_purpose(unquoted, frame):
        fields.discard("client.phone")
    if brief_request and not fields:
        # Preserve known field diagnostics; this marker closes the remaining
        # question vocabulary without inventing a new business fact field.
        fields.add("unplanned_collection")
    return fields


def constrain_collection(text: str, frame: DialogueFrame) -> CollectionResult:
    """Remove inadmissible collection acts, preserving the model's useful explanation."""
    bubbles: list[str] = []
    removed: set[str] = set()
    asked: QuestionField | None = None
    changed = False
    pressure_removed = False
    for bubble in text.split("\n\n"):
        kept = []
        previous_sentence = ""
        quoted_spans = [quote.span() for quote in _CONFIRMATION_QUOTES.finditer(bubble)]
        unmatched_quote = any(mark in _CONFIRMATION_QUOTES.sub("", bubble)
                              for mark in ('"', "'", "«", "»", "“", "”"))
        for match in re.finditer(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?", bubble):
            sentence = match.group()
            # Quoted middle sentences may contain no visible quote character.
            # Original offsets prevent them from supplying this new binding.
            allow_confirmation = not unmatched_quote and not any(
                start < match.end() and end > match.start() for start, end in quoted_spans
            )
            fields = request_fields(sentence, frame, previous_sentence=previous_sentence,
                                    allow_debt_confirmation=allow_confirmation)
            previous_sentence = sentence if allow_confirmation else ""
            # A requested explanation of the existing company's contact purpose
            # does not reopen collection. Real requests and help barriers still do.
            purpose_only = _known_contact_purpose(sentence, frame)
            pressure = contact_claims_storage_requirement(sentence) or contact_blocks_chat(
                sentence, phone_context=frame.expected == "client.phone") or (
                frame.contact_declined and (
                _CONTACT_PRESSURE.search(sentence) or _CONTACT_RETURN_CONDITION.search(sentence)
                or (not purpose_only and _CONTACT_NEED_PRESSURE.search(sentence))
            ))
            if pressure and not _explanation_question(sentence):
                removed.add("client.phone")
                changed = pressure_removed = True
                continue
            forbidden = {path for path in fields if not frame.allows(path)}
            if forbidden:
                removed.update(forbidden)
                changed = True
                continue
            if frame.allowed_field in fields:
                asked = frame.allowed_field
            kept.append(sentence.strip())
        if kept:
            bubbles.append(" ".join(kept))
    return CollectionResult("\n\n".join(bubbles) if changed else text, asked, tuple(sorted(removed)), pressure_removed)


def last_collection_field(text: str, frame: DialogueFrame) -> QuestionField | None:
    """Observe the last actual question/request, never a stale questionnaire cursor."""
    sentences = re.findall(r"[^.!?\n]+[.!?]?", text)
    last_request = next((sentence for sentence in reversed(sentences)
                         if "?" in sentence or request_fields(sentence, frame)), "")
    return constrain_collection(last_request, frame).asked_field
