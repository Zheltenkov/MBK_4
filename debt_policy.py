"""Deterministic local debt questionnaire, never CRM/legal eligibility or a sender.

Policy presence is a durable delivery hold. Legacy routes/classification are not
called by this module. Raw browser goals/URLs/model intent cannot activate it.
"""
from __future__ import annotations

import math
import hmac
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from customer_context import AuthorizedApplication
from routing.engine import get_fact
from routing.normalize import MONEY_APPROXIMATION, parse_amount
from routing.planner import Plan, PlannedField
from routing.schema import Matrix

POLICY_KEY = "questionnaire_policy"
VERSION = 1
MAX_SAFE_AMOUNT = 2**53 - 1
DebtBand = Literal["unknown", "below_190k", "190k_to_299999", "300k_plus", "spans_boundary"]
EntryEvidence = Literal["none", "explicit", "ambiguous", "reject"]
_Reason = Literal["explicit_request", "trusted_context", "ambiguous_request", "confirmed_intent",
                  "declined_intent", "cancelled_request", "restored_invalid"]


class DebtObservation(BaseModel):
    """Exact/range/unknown evidence stays separate from canonical scalar facts."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["exact", "range", "unknown"]
    amount: int | None = None
    lower: int | None = None
    upper: int | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> DebtObservation:
        values = (self.amount, self.lower, self.upper)
        if any(value is not None and (type(value) is not int or not 0 <= value <= MAX_SAFE_AMOUNT) for value in values):
            raise ValueError("invalid debt amount")
        if self.kind == "exact" and (self.amount is None or self.lower is not None or self.upper is not None):
            raise ValueError("exact debt needs one amount")
        if self.kind == "range" and (self.amount is not None or (self.lower is None and self.upper is None)
            or (self.lower is not None and self.upper is not None and self.lower > self.upper)):
            raise ValueError("invalid debt interval")
        if self.kind == "unknown" and any(value is not None for value in values):
            raise ValueError("unknown debt cannot carry a number")
        return self


class DebtPolicyState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = VERSION
    mode: Literal["debt", "clarify", "cancelled"] = "debt"
    reason: _Reason = "explicit_request"
    ever_debt: Literal[True] = True
    amount_observation: DebtObservation | None = None
    # A contact choice is distinct from the debt goal and never implies consent.
    phone_collection: Literal["open", "declined"] = "open"
    # A negative chat preference survives restart without authorizing any sender.
    transfer_declined: bool = False

    def as_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def with_observation(self, observation: DebtObservation) -> DebtPolicyState:
        return self.model_copy(update={"amount_observation": observation})


_TRUSTED_SEAL = object()


@dataclass(frozen=True, repr=False)
class TrustedDebtSignal:
    _seal: object = field(repr=False)
    _binding_digest: str = field(repr=False)


def trusted_signal_from_authorized(authorized: AuthorizedApplication, now: float) -> TrustedDebtSignal | None:
    """Only a live server-authorized current request can supply this entry hint."""
    if not isinstance(authorized, AuthorizedApplication):
        return None
    authorized.assert_authorized(now)
    goal = authorized.to_anketa().get("goal")
    if goal in {"debt_relief", "bankruptcy", "restructuring"}:
        return TrustedDebtSignal(_TRUSTED_SEAL, authorized.binding_digest)
    return None


def debt_delivery_held(state: Mapping[str, object]) -> bool:
    # Presence, including unknown version/malformed/restored/cancelled, always fails closed.
    return POLICY_KEY in state


def parse_policy(state: Mapping[str, object]) -> DebtPolicyState | None:
    if POLICY_KEY not in state:
        return None
    try:
        raw = state[POLICY_KEY]
        if not isinstance(raw, Mapping) or not {"version", "mode", "reason", "ever_debt"} <= raw.keys() \
                or type(raw.get("version")) is not int or raw.get("version") != VERSION \
                or raw.get("ever_debt") is not True:
            raise ValueError("malformed persisted debt policy")
        return DebtPolicyState.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        return DebtPolicyState(mode="clarify", reason="restored_invalid")


def _text(message: str) -> str:
    return " ".join(message.casefold().replace("ё", "е").split())


_DEBT_WORD = re.compile(r"банкрот|реструктур|списа\w*\s+(?:(?:мои|свои)\s+)?(?:долг|задолжен|кредит|займ)|\b(?:долг|задолжен)\w*\b")
_DEBT_REQUEST = re.compile(
    r"(?:\b(?:я\s+)?(?:хочу|готов(?:а)?|планирую|собираюсь|решил(?:а)?)\b.{0,28}"
    r"(?:банкрот|списать\s+(?:(?:мои|свои)\s+)?(?:долг|задолжен|кредит|займ)|реструктур))"
    r"|(?:\b(?:мне\s+)?нуж(?:на|но|ен)\b.{0,30}(?:банкрот|реструктур|спис(?:ать|ани\w*)\s+(?:долг|задолжен)))"
    r"|(?:\b(?:помогите|помоги|помочь)\b.{0,24}(?:списать\s+(?:(?:мои|свои)\s+)?долг|с\s+(?:моим\s+)?банкрот))"
    r"|(?:\bкак\s+(?:мне\s+)?списать\s+мои\s+долг)"
    r"|(?:\b(?:проконсультируйте|проконсультируй)\s+меня\b.{0,24}(?:банкрот|реструктур))"
)
_OWN_DEBT_HELP_REQUEST = re.compile(
    r"\b(?:я\s+)?хочу\s+(?:уменьшить|снизить)\s+платежи\s+по\s+"
    r"(?:моим|своим|текущим|существующим|тем)\s+(?:долг\w*|задолжен\w*)\b"
    r"|\bмне\s+нуж(?:на|но|ен)\s+(?:не\s+новый\s+кредит\s*,\s*а\s+)?"
    r"помощь\s+с\s+платежами\s+по\s+(?:моим|своим|текущим|существующим)\s+"
    r"(?:долг\w*|задолжен\w*)\b"
)
_CASH_REQUEST = re.compile(r"\b(?:хочу\s+(?:(?:взять|получить|оформить)\s+)?|нужен\s+|нужны\s+|"
                           r"нужно\s+получить\s+|возьму\s+|взять\s+)(?:новый\s+)?"
                           r"(?:кредит|займ|заем|денег|деньги|ипотек)")
_DEBT_NEGATED = re.compile(r"(?:\bне\s+(?:хочу|нуж\w*|готов\w*|планирую|буду)\b.{0,25}(?:банкрот|спис|реструктур))"
                           r"|(?:(?:банкрот\w*|реструктур\w*)\s+(?:мне\s+)?не\s+(?:нуж\w*|хочу))"
                           r"|(?:без\s+банкрот)")
_CASH_NEGATED = re.compile(r"\b(?:не\s+(?:хочу|нуж\w*|буду)|без)\b.{0,18}(?:кредит|за[её]м|займ)")
_THIRD_PARTY = re.compile(r"\b(?:у\s+(?:моего|моей|друга|подруги|мамы|отца|жены|мужа)|"
                          r"(?:он|она|друг|подруга|мама|отец|жена|муж)\s+"
                          r"(?:хочет|сказал\w*|написал\w*|говорит|спросил\w*|просит)|ему|ей)\b")
_THIRD_TARGET = re.compile(r"(?:долг\w*|задолж\w*|банкрот\w*)\s+"
    r"(?:(?:моего|моей|своего|своей)\s+)?(?:отца|матери|мамы|папы|брата|сестры|друга|подруги|мужа|жены)\b")
_HYPOTHETICAL = re.compile(r"^(?:если|допустим|предположим)\b|\b(?:не\s+уверен\w*|хочу\s+ли|стоит\s+ли)\b")
_STOP = re.compile(r"^(?:стоп|отмена|отменить|закончим|завершить\s+консультацию|не\s+хочу\s+продолжать)[.! ]*$")
_PHONE_REFUSAL = re.compile(
    r"\b(?:не\s+(?:хочу|буду|готов[а]?)\s+(?:оставлять|давать|указывать|сообщать)\s+"
    r"(?:(?:свой|вам|мой)\s+)?(?:телефон|номер|контакт)|"
    r"(?:телефон|номер|контакт)\s+(?:свой\s+)?не\s+(?:дам|оставлю|укажу|сообщу|оставляю|даю|указываю|сообщаю)|"
    r"(?:телефон|номер|контакт)\s+(?:оставлять|давать|указывать|сообщать)\s+"
    r"(?:(?:пока|сейчас)\s+)?не\s+(?:хочу|буду|готов[а]?)|"
    r"не\s+(?:дам|оставлю|укажу|сообщу|оставляю|даю|указываю|сообщаю)\s+"
    r"(?:(?:свой|вам)\s+)?(?:телефон|номер|контакт)|"
    r"не\s+звоните|без\s+(?:телефона|номера))\b"
)
_ELLIPTICAL_PHONE_REFUSAL = re.compile(
    r"\s*(?:я\s+)?не\s+(?:хочу|буду|готов[а]?)\s+(?:оставлять|давать|указывать|сообщать)[.!]?\s*"
)
_PHONE_DECLINE_CHOICE = re.compile(
    r"\s*(?:нет(?:\s*,?\s*(?:спасибо|не\s+(?:хочу|буду|готов[а]?)))?|"
    r"не\s+(?:хочу|буду|готов[а]?))[.!]?\s*"
)
_PHONE_PREFERENCE_CLAUSE = re.compile(
    r"^\s*(?:я\s+)?(?:лучше|предпочитаю|давайте)\s+без\s+(?:телефона|номера)\s*[,;]"
)


def phone_contact_declined(message: str, *, expected: bool) -> bool:
    """An explicit contact refusal does not cancel a request about debts."""
    text = _text(message)
    # Quoted and conditional examples are not a contact decision.
    if any(mark in text for mark in ('"', "'", "«", "»", "“", "”")):
        return False
    # A later FAQ must not erase an explicit refusal in an earlier assertion.
    statements = re.findall(r"[^.!?]+[.!?]?", text)
    explicit = any(
        # An asserted preference before a follow-up question remains a decision.
        (not sentence.endswith("?") or _PHONE_PREFERENCE_CLAUSE.match(sentence) is not None)
        and not re.search(r"\b(?:если|допустим|предположим|ли)\b|не\s+уверен", sentence)
        and not _THIRD_PARTY.search(sentence)
        and (_PHONE_REFUSAL.search(sentence) or (expected and (
            _ELLIPTICAL_PHONE_REFUSAL.fullmatch(sentence) or _PHONE_DECLINE_CHOICE.fullmatch(sentence)
        )))
        for sentence in statements
    )
    return explicit


_TRANSFER_OBJECT = r"(?:(?:мои|свои|эти|моих|своих|этих)\s+)?(?:данные|данных|сведения|сведений|контакты|контактов|заявку)"
_DEBT_TRANSFER_DECLINE = re.compile(
    rf"(?:нет\s*,\s*)?(?:я\s+)?(?:(?:пока|сейчас)\s+)?(?:"
    rf"не\s+(?:передавайте|отправляйте|передавать|отправлять)\s+{_TRANSFER_OBJECT}|"
    rf"не\s+(?:хочу|буду|готов[а]?)\s+(?:(?:пока|сейчас)\s+)?(?:передавать|отправлять)\s+{_TRANSFER_OBJECT}|"
    rf"не\s+разрешаю\s+(?:передачу|отправку|передавать|отправлять)\s+{_TRANSFER_OBJECT}|"
    rf"без\s+(?:передачи|отправки)\s+{_TRANSFER_OBJECT}|"
    rf"{_TRANSFER_OBJECT}\s+(?:никуда\s+)?не\s+(?:передавайте|отправляйте)|"
    rf"(?:никуда\s+)?(?:передавать|отправлять)\s+{_TRANSFER_OBJECT}\s+не\s+(?:нужно|надо))"
    rf"(?:\s+(?:специалисту|менеджеру|консультанту))?", re.I,
)
_CHAT_ONLY_PREFERENCE = re.compile(
    r"(?:я\s+)?(?:(?:пока|сейчас)\s+)?(?:хочу|предпочитаю|давайте)\s+"
    r"(?:обсудить|обсуждать|поговорить|разобраться)"
    r"(?:\s+(?:вс[её]|это|здесь|тут|в\s+чате)){1,4}", re.I,
)


def _transfer_refusal_clauses(message: str) -> list[str]:
    # Keep each independent assertion separate. An unknown or quoted clause
    # never becomes a decision, while a later explicit own refusal still can.
    unquoted = re.sub(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'", "[quoted]", message)
    if any(mark in unquoted for mark in ('"', "'", "«", "»", "“", "”")):
        return []
    return [clause.strip().rstrip(".!;… ") for clause in re.findall(r"[^.!?;]+[.!?;]?", unquoted)
            if not re.search(r"[:?]|\[quoted\]|\b(?:если|допустим|предположим|бы)\b", clause, re.I)]


def transfer_contact_declined(message: str) -> bool:
    """An explicit own transfer refusal needs neither a live offer nor a model."""
    return any(_DEBT_TRANSFER_DECLINE.fullmatch(clause) for clause in _transfer_refusal_clauses(message))


def pure_debt_turn_act(message: str, *, active_debt: bool) -> Literal["entry", "decline_transfer"] | None:
    """Prove a whole no-fact speech act before skipping fact extraction."""
    text = _text(message)
    if re.fullmatch(r"разобраться с долгами\s*\.?", text):
        return "entry"
    clauses = _transfer_refusal_clauses(text)
    # Every original clause must be accounted for, including questions/quotes.
    original = [clause.strip().rstrip(".!;… ") for clause in re.findall(r"[^.!?;]+[.!?;]?", text)]
    if (active_debt and clauses and clauses == original and transfer_contact_declined(text)
            and all(_DEBT_TRANSFER_DECLINE.fullmatch(clause) or _CHAT_ONLY_PREFERENCE.fullmatch(clause)
                    for clause in clauses)):
        return "decline_transfer"
    return None


def detect_entry(message: str) -> EntryEvidence:
    text = _text(message)
    # The whole greeting choice is an explicit own request, not a quoted/questioned mention.
    if re.fullmatch(r"разобраться с долгами\s*\.?", text):
        return "explicit"
    if not text or not _DEBT_WORD.search(text):
        return "none"
    # Natural own-debt help is an asserted goal, not just a closed "yes".
    # Scope its new vocabulary to a whole declarative clause; the existing
    # quote/third-party/cash guards below still apply to the complete message.
    own_help = any(
        _OWN_DEBT_HELP_REQUEST.match(sentence.strip())
        and not sentence.endswith("?")
        and not re.search(r"\b(?:если|допустим|предположим|бы)\b|"
                          r"\bне\s+(?:хочу|нуж(?:на|но|ен))\b|"
                          r"\b(?:чуж\w*|клиент\w*|сосед\w*|знаком\w*|коллег\w*|"
                          r"организац\w*|компан\w*|фирм\w*)\b", sentence)
        for sentence in re.findall(r"[^.!?]+[.!?]?", text)
    )
    debt = bool(_DEBT_REQUEST.search(text)) or own_help
    cash = bool(_CASH_REQUEST.search(text)) and not bool(_CASH_NEGATED.search(text))
    negated = bool(_DEBT_NEGATED.search(text))
    # Quotes, indirect reports and questions about a term need an explicit self-request.
    if (any(mark in text for mark in ('"', "'", '«', '»', '“', '”')) or _THIRD_PARTY.search(text)
            or _THIRD_TARGET.search(text) or _HYPOTHETICAL.search(text)
            or re.search(r"\bчто\s+такое\b|\bпример\s+фразы\b|\bизбежать\s+банкрот", text)):
        return "ambiguous" if not cash else "none"
    if negated:
        clauses = re.split(r"[,;]|\bно\b", text)
        if any(_DEBT_REQUEST.search(clause) and not _DEBT_NEGATED.search(clause) for clause in clauses):
            return "ambiguous"
        return "none" if cash else "reject"
    if debt and cash:
        return "ambiguous"
    if debt:
        return "explicit"
    # A credit request mentioning a debt is still a credit request, not a product switch.
    if cash:
        return "none"
    return "ambiguous"


def select_policy(state: Mapping[str, object], user_message: str, *, enabled: bool = False,
                  trusted_signal: TrustedDebtSignal | None = None) -> DebtPolicyState | None:
    existing = parse_policy(state)
    text = _text(user_message)
    evidence = detect_entry(user_message)
    if existing is not None:
        if existing.mode == "cancelled":
            return existing
        if _STOP.fullmatch(text):
            return existing.model_copy(update={"mode": "cancelled", "reason": "cancelled_request"})
        if existing.mode == "debt" and _DEBT_NEGATED.search(text):
            # A question or a mention of an alternative is not an asserted
            # cancellation. Only the existing negative predicate establishes it.
            unquoted = re.sub(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'", "", text)
            debt_clauses: list[str] = []
            for sentence in re.findall(r"[^.!?]+[.!?]?", unquoted):
                sentence = sentence.strip()
                if _HYPOTHETICAL.search(sentence) or _THIRD_PARTY.search(sentence) or _THIRD_TARGET.search(sentence):
                    continue
                # Preserve a separate refusal before a later question, while
                # leaving the question itself free to use any word order.
                clauses = re.split(r"[,;]", sentence)[:-1] if sentence.endswith("?") else [sentence]
                for clause in clauses:
                    predicate = _DEBT_NEGATED.search(clause)
                    if predicate is not None and re.search(r"\bне\b", predicate.group()):
                        debt_clauses.append(clause)
            if not debt_clauses:
                return existing
            if evidence == "ambiguous":
                return existing.model_copy(update={"mode": "clarify", "reason": "ambiguous_request"})
            return existing.model_copy(update={"mode": "cancelled", "reason": "declined_intent"})
        if existing.mode == "clarify":
            # Declarative closing punctuation does not change these existing closed answers.
            # Keep questions, quotations and all other parsers on their original input.
            closed_answer = text.rstrip(".!… ")
            if evidence == "explicit" or closed_answer in {"да", "да, по моим долгам", "да хочу", "да, хочу"}:
                return existing.model_copy(update={"mode": "debt", "reason": "confirmed_intent"})
            if evidence == "reject" or closed_answer in {"нет", "нет спасибо", "нет, спасибо"}:
                return existing.model_copy(update={"mode": "cancelled", "reason": "declined_intent"})
        return existing  # Never silently resume standard credit/classification.
    if not enabled:
        return None
    context = state.get("customer_context")
    digest = context.get("binding_digest") if isinstance(context, Mapping) else None
    if (isinstance(trusted_signal, TrustedDebtSignal) and trusted_signal._seal is _TRUSTED_SEAL
            and isinstance(context, Mapping) and context.get("context_status") == "ready"
            and isinstance(digest, str) and hmac.compare_digest(digest, trusted_signal._binding_digest)):
        if _DEBT_NEGATED.search(text) or (_CASH_REQUEST.search(text) and not _CASH_NEGATED.search(text)):
            return DebtPolicyState(mode="clarify", reason="ambiguous_request")
        return DebtPolicyState(reason="trusted_context")
    if evidence == "explicit":
        return DebtPolicyState(reason="explicit_request")
    if evidence == "ambiguous":
        return DebtPolicyState(mode="clarify", reason="ambiguous_request")
    return None


_NUMBER = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d+)?"
_SCALAR_DEBT_ANSWER = re.compile(rf"(?:да(?:\s*,\s*|\s+))?(?:ровно\s+)?(?P<number>{_NUMBER})[.!]?")
_UNIT = r"(?:млрд|миллиард[а-я]*|млн|миллион[а-я]*|тыс(?:яч[а-я]*)?\.?|[кk]|руб(?:л[а-я]*)?\.?|₽)"
_MONEY = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})", re.IGNORECASE)
_RANGE = re.compile(rf"(?:от\s+)?(?P<left>{_NUMBER})\s*(?P<lu>{_UNIT})?\s*(?:-|–|—|до)\s*"
                    rf"(?P<right>{_NUMBER})\s*(?P<ru>{_UNIT})?", re.IGNORECASE)
_UNCERTAIN = re.compile(
    rf"не\s+(?:знаю|помню|уверен)|не\s+скажу|не\s+хочу\s+говорить|{MONEY_APPROXIMATION.pattern}", re.I,
)
_COMPARATIVE_AMOUNT = re.compile(rf"\b(?:ближе|скорее|скорей|пожалуй)\b\s*[,—-]?\s*(?:к\s*)?{_NUMBER}")
_ALTERNATIVE_AMOUNT = re.compile(rf"{_NUMBER}\s*(?:{_UNIT})?\s*,?\s*\b(?:или|либо)\b\s*(?:к\s+)?{_NUMBER}")
_DEBT_ANCHOR = re.compile(r"\bдолг\w*|\bзадолжен\w*")
_ZERO_DEBT = re.compile(r"^(?:у\s+меня\s+)?(?:долгов\s+нет|нет\s+долгов|общая\s+задолженность\s+(?:ноль|0))[.! ]*$")


def _amount(number: str, unit: str) -> int | None:
    try:
        normalized_unit = "к" if unit.casefold() == "k" else unit
        parsed = parse_amount(number + " " + normalized_unit)
        # Existing normalization rounds money; never round across190k/300k locally.
        multiplier = (1000000000 if normalized_unit.startswith(("млрд", "миллиард")) else
                      1000000 if normalized_unit.startswith(("млн", "миллион")) else
                      1000 if normalized_unit.startswith(("тыс", "к")) else 1)
        exact = Decimal(number.replace(" ", "").replace("\u00a0", "").replace(",", ".")) * multiplier
        if not exact.is_finite() or exact != exact.to_integral_value() or not 0 <= exact <= MAX_SAFE_AMOUNT:
            return None
        return parsed if parsed == int(exact) else None
    except (OverflowError, ValueError, InvalidOperation):
        return None


def detect_debt_observation(message: str, *, expected: bool) -> DebtObservation | None:
    """Ground only debt-anchored amounts or answers to the actual debt question.

    No arbitrary first-number extraction. Ranges and vague statements never become
    a scalar or a lower-bound substitute, even after repeated confirmations.
    """
    text = _text(message)
    if not text or _THIRD_PARTY.search(text) or _THIRD_TARGET.search(text) \
            or any(mark in text for mark in ('"', "'", '«', '»')):
        return None
    if not expected and not _DEBT_ANCHOR.search(text):
        return None
    if re.match(r"^(?:а\s+)?(?:если|допустим|предположим)\b", text):
        return None
    # A FAQ is not an updated amount, even when an old debt question is still pending.
    own_amount = re.search(r"(?:у\s+меня|мой\s+долг|моя\s+задолженность|долг\w*\s+(?:составля\w*\s+)?)\s*\d", text)
    # Referring to the speaker's example does not report the client's current debt.
    if re.search(r"\bвы\s+(?:назвали|предложили|предлагаете|сказали|спросили|спрашивали)\b|"
                 r"\bв\s+примере\b", text) and not re.search(r"\b(?:у\s+меня|мой\s+долг|моя\s+задолженность)\b", text):
        return None
    if ("?" in text or re.match(r"^(?:а\s+)?(?:зачем|почему|как|что|можно|нужно|сколько)\b", text)) and not own_amount:
        return None
    # This questionnaire asks rubles; a multiplier is not permission to convert currencies.
    if re.search(r"[$€]|\b(?:usd|eur)\b|доллар|евро", text, re.IGNORECASE):
        return DebtObservation(kind="unknown")
    if _ZERO_DEBT.fullmatch(text):
        return DebtObservation(kind="exact", amount=0)
    # "Нет действующих кредитов" says nothing about total debts/collections.
    if re.search(r"нет\s+(?:действующ\w*|активн\w*)?\s*кредит", text) and not _DEBT_ANCHOR.search(text):
        return None
    if _UNCERTAIN.search(text):
        return DebtObservation(kind="unknown")
    # Comparison qualifies this numeric answer, not an unrelated later FAQ.
    if _COMPARATIVE_AMOUNT.search(text) or _ALTERNATIVE_AMOUNT.search(text):
        return DebtObservation(kind="unknown")
    if re.search(r"зарплат|\bдоход\w*|зарабаты|\bтелефон\w*|\bвозраст\w*", text) or _CASH_REQUEST.search(text):
        # An unrelated contact/task statement does not retract an existing debt amount.
        return DebtObservation(kind="unknown") if _DEBT_ANCHOR.search(text) and re.search(r"\d", text) else None
    interval = _RANGE.search(text)
    if interval:
        lu, ru = interval.group("lu") or "", interval.group("ru") or ""
        if not lu and not ru:
            return DebtObservation(kind="unknown")
        lower, upper = _amount(interval.group("left"), lu or ru), _amount(interval.group("right"), ru or lu)
        if lower is None or upper is None or lower > upper:
            return DebtObservation(kind="unknown")
        return DebtObservation(kind="range", lower=lower, upper=upper)
    amounts = list(_MONEY.finditer(text))
    if len(amounts) != 1:
        scalar = _SCALAR_DEBT_ANSWER.fullmatch(text) if expected else None
        if scalar is not None:
            # The actual pending question states rubles. Small bare numbers remain unit-ambiguous.
            value = _amount(scalar.group("number"), "рублей")
            return DebtObservation(kind="exact", amount=value) if value is not None and value >= 1000 \
                else DebtObservation(kind="unknown")
        return None
    match = amounts[0]
    # Open-ended bounds are still intervals, not the named endpoint as an exact debt.
    prefix = text[:match.start()].strip()
    if prefix.endswith("-") or re.search(r"\bминус$", prefix):
        return DebtObservation(kind="unknown")
    value = _amount(match.group("number"), match.group("unit"))
    if value is None:
        return DebtObservation(kind="unknown")
    if re.search(r"(?:^|\s)(?:от|более|больше|свыше)$", prefix):
        return DebtObservation(kind="range", lower=value)
    if re.search(r"(?:^|\s)(?:до|менее|меньше)$", prefix):
        return DebtObservation(kind="range", upper=value)
    # Extra naked numbers (age, count, another amount) make the answer ambiguous.
    if re.search(r"\d", text[:match.start()] + text[match.end():]):
        return DebtObservation(kind="unknown")
    return DebtObservation(kind="exact", amount=value)


def _scalar_band(value: int | float) -> DebtBand:
    if value < 190000:
        return "below_190k"
    if value < 300000:
        return "190k_to_299999"
    return "300k_plus"


def debt_band(value: object) -> DebtBand:
    if isinstance(value, DebtObservation):
        if value.kind == "exact" and value.amount is not None:
            return _scalar_band(value.amount)
        if value.kind == "range":
            if value.lower is not None and value.upper is not None:
                a, b = _scalar_band(value.lower), _scalar_band(value.upper)
                return a if a == b else "spans_boundary"
            return "spans_boundary"
        return "unknown"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_SAFE_AMOUNT:
        return "unknown"
    if isinstance(value, float) and not math.isfinite(value):
        return "unknown"
    return _scalar_band(value)


def derive_debt_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    """No active-loan→zero/history shortcut and no guessed asset/contact/age facts."""
    return deepcopy(dict(facts))


def preserve_debt_total(before: Mapping[str, Any], after: Mapping[str, Any], *,
                        explicit_debt_update: bool = False) -> dict[str, Any]:
    """Root supplies True only for a grounded exact observation, never a model path."""
    result = deepcopy(dict(after))
    old = before.get("debts")
    old_debts = old if isinstance(old, Mapping) else {}
    current = result.get("debts")
    if not isinstance(current, dict):
        current = {}
        result["debts"] = current
    if not explicit_debt_update:
        if "total" in old_debts:
            current["total"] = deepcopy(old_debts["total"])
        else:
            current.pop("total", None)
    if current.get("has_active_credits") is False and current.get("credit_history") == "no_overdue" \
            and "credit_history" not in old_debts:
        current.pop("credit_history", None)
    return result


def _planned(matrix: Matrix, paths: list[str], *, confirmation: bool = False) -> Plan:
    allowed = {"debts.total", "client.region_class", "client.name", "client.phone"}
    fields = [PlannedField(path=path, spec=matrix.fields[path], needs_confirmation=confirmation) for path in paths]
    return Plan(action="contacts" if paths and paths[0] in {"client.name", "client.phone"} else "ask",
                fields=fields, statuses=[], matched=None,
                forbidden=[PlannedField(path=path, spec=spec) for path, spec in matrix.fields.items()
                           if path not in allowed or path not in paths])


def _requires_confirmation(statuses: Mapping[str, object] | None, path: str) -> bool:
    if statuses is None:
        return False
    if not isinstance(statuses, Mapping):
        return True
    if path not in statuses:
        return False
    raw = statuses[path]
    if isinstance(raw, Mapping):
        raw = raw.get("status")
    # Malformed/unrecognized metadata is uncertainty, never a reason to accept a value.
    return not isinstance(raw, str) or raw not in {"known", "confirmed"}


def _attempt_count(attempts: Mapping[str, int] | None, path: str) -> int:
    raw = attempts.get(path, 0) if isinstance(attempts, Mapping) else 0
    return raw if type(raw) is int and raw >= 0 else 0


def _incomplete() -> Plan:
    return Plan(action="no_fit", statuses=[], matched=None, transition="debt_incomplete")


def plan_debt(matrix: Matrix, facts: dict[str, Any], policy: DebtPolicyState, *,
              fact_statuses: Mapping[str, object] | None = None, attempts: Mapping[str, int] | None = None,
              contact_known: bool = False) -> Plan:
    if policy.mode == "cancelled":
        return Plan(action="no_fit", statuses=[], matched=None)
    if policy.mode == "clarify":
        return _planned(matrix, [])
    observation = policy.amount_observation
    needs_confirmation = _requires_confirmation(fact_statuses, "debts.total")
    if observation is not None and observation.kind != "exact":
        needs_confirmation = True
    amount = get_fact(facts, "debts.total")
    if needs_confirmation or debt_band(amount) == "unknown":
        if _attempt_count(attempts, "debts.total") >= 3:
            return _incomplete()
        return _planned(matrix, ["debts.total"], confirmation=needs_confirmation)
    if debt_band(amount) == "below_190k":
        return Plan(action="no_fit", statuses=[], matched=None)
    region_confirmation = _requires_confirmation(fact_statuses, "client.region_class")
    if region_confirmation or get_fact(facts, "client.region_class") not in ("moscow_mo", "rf_other"):
        if _attempt_count(attempts, "client.region_class") >= 3:
            return _incomplete()
        return _planned(matrix, ["client.region_class"], confirmation=region_confirmation)
    if policy.phone_collection == "declined" or policy.transfer_declined:
        # Keep answering in this chat without restarting the contact questionnaire.
        return Plan(action="no_fit", statuses=[], matched=None, transition="debt_contact_declined")
    for path, max_attempts in (("client.name", 2), ("client.phone", 3)):
        if path == "client.phone" and contact_known:
            continue
        if get_fact(facts, path) in (None, "") and _attempt_count(attempts, path) < max_attempts:
            return _planned(matrix, [path])
    missing = get_fact(facts, "client.name") in (None, "") or (
        not contact_known and get_fact(facts, "client.phone") in (None, ""))
    return Plan(action="no_fit", statuses=[], matched=None,
                transition="debt_incomplete" if missing else "debt_collected")


def policy_phase(policy: DebtPolicyState, plan: Plan, facts: Mapping[str, Any]) -> str:
    if policy.mode != "debt":
        return "clarifying_intent" if policy.mode == "clarify" else "cancelled_held"
    if plan.fields:
        return "collecting_contacts" if plan.action == "contacts" else "collecting_debt_context"
    if plan.transition == "debt_incomplete":
        return "incomplete_held"
    if plan.transition == "debt_contact_declined":
        return "contact_declined_held"
    return "below_local_floor_held" if debt_band(get_fact(dict(facts), "debts.total")) == "below_190k" else "collected_held"


def render_debt_reply(policy: DebtPolicyState, plan: Plan, facts: Mapping[str, Any]) -> str:
    """Offline opening and fallback speech. Policy owns the question and result."""
    # Scope the statement to this feature, since the same chat may have earlier applications.
    delivery_note = "Передача заявки по долгам из этого чата пока не подключена."
    if policy.mode == "clarify":
        return "Вы хотите обсудить списание или реструктуризацию именно ваших долгов?"
    if policy.mode == "cancelled":
        return "Хорошо, остановимся. " + delivery_note
    if policy.phone_collection == "declined" and not plan.fields:
        return "Хорошо, номер оставлять не нужно. Если есть вопросы, можем обсудить их здесь."
    if plan.fields:
        path = plan.fields[0].path
        if path == "debts.total":
            return ("Уточните, пожалуйста, общую сумму долга одним числом в рублях, без диапазона."
                    if plan.fields[0].needs_confirmation else
                    "Какая у вас общая сумма долга? Укажите, пожалуйста, сумму в рублях или тысячах рублей.")
        return {
            "client.region_class": ("Уточните, пожалуйста, ваш город и регион."
                                    if plan.fields[0].needs_confirmation else "В каком городе и регионе вы живёте?"),
            "client.name": "Как к вам обращаться?",
            "client.phone": "На какой номер можно с вами связаться?",
        }.get(path, "Уточним сведения о вашем обращении.")
    if policy_phase(policy, plan, facts) == "below_local_floor_held":
        return ("Здесь принимают обращения с долгом от 190 тысяч рублей. "
                "Возможность банкротства нужно обсудить со специалистом отдельно. "
                + delivery_note)
    if policy_phase(policy, plan, facts) == "incomplete_held":
        return "Пока не хватает данных, чтобы продолжить. " + delivery_note
    return ("Данных для начала достаточно. Дальше нужен разбор со специалистом. "
            + delivery_note)
