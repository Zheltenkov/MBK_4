"""Grounded conversational acts and bounded claims, independent of model transport."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, TypeVar, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from dialogue_frame import DialogueFrame
from debt_policy import phone_contact_declined, transfer_contact_declined
from onloader.policy import _explicit_transfer_refusal

ClaimPredicate = Literal[
    "phone_required_to_save_chat", "phone_required_to_chat", "consultation_purpose", "engineering_transfer_purpose",
    "recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient", "transfer_completed",
    "consent_recorded", "refusal_recorded",
    "conversation_refusal_acknowledged",
]
CLAIM_PREDICATES: tuple[ClaimPredicate, ...] = get_args(ClaimPredicate)
REQUEST_SLOT_NAMES = ("phone_request", "transfer_consent_request")
ScanCategory = Literal["contact_purpose", "contact_capability", "action_status", "user_choice"]
COVERAGE_CATEGORIES = frozenset({"contact_purpose", "contact_capability", "action_status", "user_choice"})
EnvelopeKind = Literal["plain", "json_fence", "unlabeled_fence", "invalid"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UserAct(_Strict):
    kind: Literal["decline_transfer", "decline_phone"]
    evidence: str = Field(min_length=1, max_length=400)
    subject: Literal["client", "other", "unknown"]
    scope: Literal["current_offer", "contact_collection"]
    mode: Literal["asserted", "question", "conditional", "quoted", "reported"]


InformationNeedKind = Literal["transfer_purpose", "debt_explanation"]
INFORMATION_NEED_KINDS: tuple[InformationNeedKind, ...] = get_args(InformationNeedKind)


class InformationNeed(_Strict):
    kind: InformationNeedKind
    evidence: str = Field(min_length=1, max_length=400)


class UserAnalysis(_Strict):
    acts: tuple[UserAct, ...] = Field(max_length=4)
    information_need: InformationNeed | None = None


class ClaimEvidence(_Strict):
    value: StrictBool
    evidence: str = Field(min_length=1, max_length=600)
    modality: Literal["asserted", "conditional", "question", "quoted", "not_established"]
    scope: Literal["current_offer", "chat", "global"]


class Claim(ClaimEvidence):
    predicate: ClaimPredicate


class Coverage(_Strict):
    category: ScanCategory
    completed: StrictBool


class RequestEvidence(_Strict):
    mode: Literal["requested", "mentioned", "quoted"]
    evidence: str = Field(min_length=1, max_length=600)


class SpeechAct(RequestEvidence):
    kind: Literal["request_phone", "request_transfer_consent"]


ClaimEntries = Annotated[tuple[ClaimEvidence, ...], Field(min_length=1, max_length=3)]
RequestEntries = Annotated[tuple[RequestEvidence, ...], Field(min_length=1, max_length=3)]


class ClaimSlots(_Strict):
    phone_required_to_save_chat: ClaimEntries | None
    phone_required_to_chat: ClaimEntries | None
    consultation_purpose: ClaimEntries | None
    engineering_transfer_purpose: ClaimEntries | None
    recipient_callback: ClaimEntries | None
    recipient_no_other_contact_source: ClaimEntries | None
    exclusive_recipient: ClaimEntries | None
    transfer_completed: ClaimEntries | None
    consent_recorded: ClaimEntries | None
    refusal_recorded: ClaimEntries | None
    conversation_refusal_acknowledged: ClaimEntries | None


class RequestSlots(_Strict):
    phone_request: RequestEntries | None
    transfer_consent_request: RequestEntries | None


class ClaimAnalysis(_Strict):
    predicates: ClaimSlots
    requests: RequestSlots

    @property
    def claims(self) -> tuple[Claim, ...]:
        result: list[Claim] = []
        for predicate in CLAIM_PREDICATES:
            entries: tuple[ClaimEvidence, ...] | None = getattr(self.predicates, predicate)
            for item in entries or ():
                result.append(Claim(predicate=predicate, value=item.value, evidence=item.evidence,
                                    modality=item.modality, scope=item.scope))
        return tuple(result)

    @property
    def speech_acts(self) -> tuple[SpeechAct, ...]:
        result: list[SpeechAct] = []
        for slot, kind in (("phone_request", "request_phone"), ("transfer_consent_request", "request_transfer_consent")):
            entries: tuple[RequestEvidence, ...] | None = getattr(self.requests, slot)
            for item in entries or ():
                result.append(SpeechAct(kind=cast(Literal["request_phone", "request_transfer_consent"], kind),
                                        mode=item.mode, evidence=item.evidence))
        return tuple(result)

    @property
    def coverage(self) -> tuple[Coverage, ...]:
        # This records structural presence, never a model's claim of semantic completeness.
        return tuple(Coverage(category=cast(ScanCategory, category), completed=True)
                     for category in sorted(COVERAGE_CATEGORIES))


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _frame_digest(frame: DialogueFrame) -> str:
    public = {key: sorted(value) if isinstance(value, frozenset) else value for key, value in asdict(frame).items()}
    return _digest(json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


@dataclass(frozen=True)
class TurnAuthority:
    frame: DialogueFrame
    context_digest: str
    latest_digest: str
    draft_digest: str

    @classmethod
    def from_frame(cls, frame: DialogueFrame, latest: str, draft: str) -> TurnAuthority:
        return cls(frame, _frame_digest(frame), _digest(latest), _digest(draft))


NeedSchemaErrorCode = Literal[
    "literal_error", "missing", "string_type", "string_too_long", "string_too_short",
    "extra_forbidden", "model_type", "json_invalid", "invalid_value", "duplicate_field", "other",
]
NeedSchemaField = Literal["kind", "evidence", "root", "unknown"]
_NEED_SCHEMA_ERROR_CODES: dict[str, NeedSchemaErrorCode] = {
    code: code for code in get_args(NeedSchemaErrorCode)
}


@dataclass(frozen=True)
class NeedSchemaIssue:
    """Closed optional-channel diagnostics omit model values, raw names and errors."""

    code: NeedSchemaErrorCode
    field: NeedSchemaField
    count: int


def _need_schema_issue_summary(error: ValidationError) -> tuple[NeedSchemaIssue, ...]:
    counts: Counter[tuple[NeedSchemaErrorCode, NeedSchemaField]] = Counter()
    for detail in error.errors(include_url=False, include_context=False, include_input=False)[:32]:
        location = detail["loc"]
        field: NeedSchemaField = "root" if not location else "unknown"
        if location == ("kind",):
            field = "kind"
        elif location == ("evidence",):
            field = "evidence"
        counts[(_NEED_SCHEMA_ERROR_CODES.get(detail["type"], "other"), field)] += 1
    return tuple(NeedSchemaIssue(code, field, min(count, 16))
                 for (code, field), count in sorted(counts.items())[:8])


@dataclass(frozen=True)
class SemanticParse:
    user: UserAnalysis | None
    claims: ClaimAnalysis | None
    user_error: str | None
    claims_error: str | None
    latest_digest: str
    draft_digest: str
    need_error: str | None = None
    need_schema_issues: tuple[NeedSchemaIssue, ...] = ()
    # Full latest-clause scope is checked before discarding the original text.
    debt_transfer_refusal_evidence: frozenset[str] = frozenset()
    blocked_transfer_capability_evidence: frozenset[str] = frozenset()


class _Object(dict[str, object]):
    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__(pairs)
        self.duplicates = frozenset(key for key, count in Counter(key for key, _ in pairs).items() if count > 1)


_NON_JSON = object()
T = TypeVar("T", bound=BaseModel)


def _invalid(value: object) -> bool:
    if value is _NON_JSON:
        return True
    if isinstance(value, _Object):
        return bool(value.duplicates) or any(_invalid(item) for item in value.values())
    return isinstance(value, list) and any(_invalid(item) for item in value)


def _json_envelope(raw: str) -> tuple[str, EnvelopeKind] | None:
    # Bound the original bytes before removing whitespace or any outer fence.
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
            return None
    except UnicodeError:
        return None
    body = raw.strip()
    kind: EnvelopeKind = "plain"
    if body.startswith("```"):
        lines = body.replace("\r\n", "\n").split("\n")
        if len(lines) < 3 or lines[0].strip().casefold() not in {"```", "```json"} or lines[-1].strip() != "```":
            return None
        if any(line.lstrip().startswith("```") for line in lines[1:-1]):
            return None
        kind = "unlabeled_fence" if lines[0].strip() == "```" else "json_fence"
        body = "\n".join(lines[1:-1]).strip()
    return (body, kind) if body.startswith("{") and body.endswith("}") else None


def json_envelope_kind(raw: str) -> EnvelopeKind:
    """Describe only the outer format. This does not certify JSON or domain validity."""
    envelope = _json_envelope(raw)
    return envelope[1] if envelope is not None else "invalid"


def _decode(raw: str) -> _Object | None:
    try:
        envelope = _json_envelope(raw)
        if envelope is None:
            return None
        value = json.loads(envelope[0], object_pairs_hook=_Object, parse_constant=lambda _: _NON_JSON)
        return value if isinstance(value, _Object) and set(value) <= {"user", "claims", "style"} else None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None


def _channel(root: _Object | None, key: str, model: type[T], *,
             need_schema_issues: list[NeedSchemaIssue] | None = None) -> tuple[T | None, str | None]:
    try:
        if root is None or key not in root or key in root.duplicates or _invalid(root[key]):
            if need_schema_issues is not None:
                need_schema_issues.append(NeedSchemaIssue("invalid_value", "root", 1))
            return None, "invalid_schema"
        # JSON mode accepts immutable tuples from arrays while preserving strict leaf types.
        return model.model_validate_json(json.dumps(root[key], allow_nan=False)), None
    except ValidationError as error:
        if need_schema_issues is not None:
            need_schema_issues.extend(_need_schema_issue_summary(error))
        return None, "invalid_schema"
    except (ValueError, TypeError, RecursionError):
        if need_schema_issues is not None:
            need_schema_issues.append(NeedSchemaIssue("other", "root", 1))
        return None, "invalid_schema"


def parse_style_channel(raw: str) -> dict[str, object]:
    root = _decode(raw)
    value = root.get("style") if root is not None else None
    if root is None or "style" in root.duplicates or not isinstance(value, _Object) or _invalid(value):
        raise ValueError("invalid_style_channel")
    return dict(value)


def parse_semantics(raw: str, *, latest_user_message: str, draft: str) -> SemanticParse:
    root = _decode(raw)
    user, user_error, need_error, need_schema_issues = _user_channel(root)
    claims, claims_error = _channel(root, "claims", ClaimAnalysis)
    if user is not None:
        evidence = [act.evidence for act in user.acts]
        if any(not span.strip() or latest_user_message.count(span) != 1 for span in evidence):
            user, user_error = None, "invalid_evidence"
        elif user.information_need is not None:
            span = user.information_need.evidence
            if not span.strip() or latest_user_message.count(span) != 1:
                user, need_error = user.model_copy(update={"information_need": None}), "invalid_evidence"
    if claims is not None and any(not item.evidence.strip() or draft.count(item.evidence) != 1
                                  for item in (*claims.claims, *claims.speech_acts)):
        claims, claims_error = None, "invalid_evidence"
    own_debt_refusals = frozenset(
        act.evidence for act in user.acts
        if act.kind == "decline_transfer" and _debt_transfer_evidence_is_own(latest_user_message, act.evidence)
    ) if user is not None else frozenset()
    blocked_transfer_capabilities = frozenset(
        claim.evidence for claim in claims.claims
        if claim.predicate == "transfer_completed" and claim.value is False
        and claim.modality == "asserted" and claim.scope == "current_offer"
        and _blocked_transfer_capability_is_bound(draft, claim.evidence)
    ) if claims is not None else frozenset()
    return SemanticParse(user, claims, user_error, claims_error, _digest(latest_user_message), _digest(draft),
                         need_error, need_schema_issues, own_debt_refusals, blocked_transfer_capabilities)


class _UserActs(_Strict):
    acts: tuple[UserAct, ...] = Field(max_length=4)


def _user_channel(root: _Object | None) -> tuple[UserAnalysis | None, str | None, str | None, tuple[NeedSchemaIssue, ...]]:
    if root is None or "user" in root.duplicates or not isinstance(root.get("user"), _Object):
        return None, "invalid_schema", None, ()
    value = root["user"]
    assert isinstance(value, _Object)
    if set(value) - {"acts", "information_need"} or "acts" in value.duplicates:
        return None, "invalid_schema", None, ()
    acts_root = _Object([("user", _Object([("acts", value.get("acts"))]))])
    acts, error = _channel(acts_root, "user", _UserActs)
    need_root = _Object([("need", value.get("information_need"))])
    need_issues: list[NeedSchemaIssue] = []
    need, need_error = (_channel(need_root, "need", InformationNeed, need_schema_issues=need_issues)
                        if value.get("information_need") is not None else (None, None))
    if "information_need" in value.duplicates:
        need, need_error = None, "invalid_schema"
        need_issues = [NeedSchemaIssue("duplicate_field", "root", 1)]
    # A malformed hint remains a local abstention. It neither discards valid user
    # acts nor becomes an authoritative task, consent, contact or business action.
    return (UserAnalysis(acts=acts.acts, information_need=need) if acts is not None else None), error, need_error, tuple(need_issues)


@dataclass(frozen=True)
class SemanticEvaluation:
    effective_frame: DialogueFrame
    applied_user_acts: tuple[UserAct, ...]
    user_valid: bool
    claims_valid: bool
    issues: tuple[str, ...]
    safe: bool
    information_need: InformationNeed | None
    need_error: str | None = None
    # Outgoing validation never grants authority to an unavailable incoming channel.
    outgoing_safe: bool = False
    user_issues: tuple[str, ...] = ()
    claims_issues: tuple[str, ...] = ()


_DECLINING_CUE = re.compile(
    r"\b(?:не|нет|без|отказ\w*|откаж\w*|отклон\w*|отмен\w*|отзыв\w*|воздерж\w*|"
    r"обойд\w*|исключ\w*|прекрат\w*|перестан\w*|стоп|хватит)\b", re.I,
)
_CHOICE_REFERENCE = {
    "decline_transfer": re.compile(r"\b(?:переда\w*|отправ\w*|направ\w*|подтвержд\w*|"
                                   r"согласи\w*|разреш\w*|партн[её]р\w*)\b", re.I),
    "decline_phone": re.compile(r"\b(?:телефон\w*|номер\w*|контакт\w*)\b", re.I),
}
_DENIED_DECLINE = re.compile(
    r"\bне\s+(?:(?:буду|будем|стал[аи]?|собираюсь|хочу)\s+)?"
    r"(?:против|возраж\w*|отказ\w*|откаж\w*|отклон\w*|отмен\w*|отзыв\w*|воздерж\w*)\b", re.I,
)


def _debt_transfer_evidence_is_own(latest: str, evidence: str) -> bool:
    """Do not let a model trim a hypothesis, quote or another person's decision.

    Only the new no-offer debt admission uses this conservative scope proof.
    Existing offer-bound choices retain their established authority contract.
    """
    start = latest.find(evidence)
    end = start + len(evidence)
    if start < 0:
        return False
    # Quotes may span several punctuation-delimited sentences. A model cannot
    # make their inner imperative authoritative by omitting the opening quote.
    quoted = tuple(re.finditer(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'", latest, re.S))
    if any(match.start() < end and match.end() > start for match in quoted):
        return False
    unquoted = re.sub(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'", "", latest, flags=re.S)
    if any(mark in unquoted for mark in ('"', "'", "«", "»", "“", "”")):
        return False
    context = " ".join(
        match.group() for match in re.finditer(r"[^.!?;]+[.!?;]?", latest)
        if match.start() < end and match.end() > start
    )
    if re.search(
        r"[:?«»“”\"']|\b(?:если|допустим|предположим|бы|ли|раньше|вчера|"
        r"сказал\w*|говорил\w*|спросил\w*|написал\w*|"
        r"мама|папа|муж|жена|друг|подруга|он|она|они|сын|дочь|клиент)\b|"
        r"\bне\s+(?:знаю|уверен\w*|помню|понимаю)\b", context, re.I,
    ):
        return False
    # Bind the negative predicate to the own-data operation, not just any "не"
    # elsewhere in a sentence ending with affirmative permission.
    own_data = r"(?:мои|моих|свои|своих)\s+(?:данн\w*|сведен\w*|контакт\w*|заявк\w*)\b"
    transfer = r"(?:передавать|передавайте|передавай|передать|передайте|отправлять|отправляйте|отправляй|отправить|отправьте)"
    transfer_noun = r"(?:передач\w*|отправ\w*)"
    # A fronted own-data operation can precede its first-person prohibition.
    # Bind the whole containing clause; a model-cropped quote or a later negation
    # cannot establish a decision. This admission never grants consent or delivery.
    fronted_operation = r"(?:передавать|передать|отправлять|отправить)"
    recipient = r"(?:консультанту|специалисту|партн[её]ру|третьим\s+лицам)"
    if re.fullmatch(
        rf"\s*(?:(?:пока|сейчас)\s+)?{fronted_operation}\s+{own_data}"
        rf"(?:\s+{recipient})?\s+(?:я\s+)?не\s+разрешаю[.!]?\s*", context, re.I,
    ):
        return True
    return re.match(
        r"^\s*(?:(?:нет|да|ага|хорошо|понял[аи]?|спасибо|но)\s*[,!]?\s+)*"
        r"(?:я\s+)?(?:(?:пока|сейчас|больше|теперь|впредь)\s+)?"
        rf"(?:не\s+разрешаю\s+(?:{transfer}|{transfer_noun})\s+{own_data}|"
        rf"не\s+(?:хочу|буду|готов[а]?)\s+{transfer}\s+{own_data}|"
        rf"не\s+(?:соглашаюсь|даю\s+согласия)\s+на\s+{transfer_noun}\s+{own_data}|"
        rf"(?:не|прошу\s+не)\s+{transfer}\s+{own_data}|"
        rf"(?:без|давайте\s+без|предпочитаю\s+без)\s+{transfer_noun}\s+{own_data}|"
        rf"предпочитаю\s+не\s+{transfer}\s+{own_data}|"
        rf"{own_data}\s+(?:никуда\s+)?не\s+{transfer}\b)", context, re.I,
    ) is not None


def _refusal_is_bound(act: UserAct, frame: DialogueFrame) -> bool:
    """A model label alone cannot attach an acknowledgement to an active offer.

    Explicit operation refusals work anywhere in the chat. Elliptical refusals
    additionally require the actual preceding code-owned collection question.
    These are admission conditions, not a general proof of language understanding.
    """
    target = "transfer" if act.kind == "decline_transfer" else "phone"
    other_kind = "decline_phone" if act.kind == "decline_transfer" else "decline_transfer"
    for clause in re.split(r"[.!?;]|\b(?:но|однако|зато)\b", act.evidence, flags=re.I):
        # A negated objection does not establish refusal. A separate later
        # clause can still decline its own explicitly named operation.
        scoped = _DENIED_DECLINE.sub(" ", clause)
        if not _DECLINING_CUE.search(scoped):
            continue
        if _CHOICE_REFERENCE[act.kind].search(scoped):
            return True
        if not _CHOICE_REFERENCE[other_kind].search(scoped) and frame.reply_target == target:
            return True
    return False


_DIRECT_TRANSFER_DECLINE = re.compile(
    r"(?:я\s+)?(?:"
    r"не\s+разрешаю\s+(?:передачу|отправку)(?:\s+(?:моих\s+)?(?:данных|сведений|контактов|заявки))?|"
    r"не\s+(?:передавать|передавайте|отправлять|отправляйте)"
    r"(?:\s+(?:(?:мои|свои|эти)\s+)?(?:данные|сведения|контакты|заявку))?|"
    r"не\s+(?:хочу|буду|готов[а]?)\s+подтверждать(?:\s+(?:передачу|отправку))?|"
    r"(?:(?:давайте|тогда|лучше)\s+)?без\s+(?:передачи|отправки)"
    r"(?:\s+(?:(?:моих|своих|этих)\s+)?(?:данных|сведений|контактов|заявки))?)", re.I,
)
_SHORT_DECLINE = re.compile(r"(?:нет(?:\s*,?\s*спасибо)?|не\s+(?:надо|нужно|хочу|буду|готов[а]?))", re.I)


def explicit_conversational_declines(latest: str, frame: DialogueFrame) -> tuple[str, ...]:
    """Retain narrow own refusals independently of model availability.

    Broader interpretations still use the model channel. Quoted, reported,
    hypothetical and question clauses cannot supply this code-owned decision.
    No affirmative permission or delivery action is produced here.
    """
    unquoted = re.sub(r"""«[^»]*»|“[^”]*”|"[^"]*"|'[^']*'""", "[quoted]", latest)
    if any(mark in unquoted for mark in ('"', "'", "«", "»", "“", "”")):
        return ()
    found: list[str] = []
    if frame.mode == "debt" and transfer_contact_declined(latest):
        found.append("decline_transfer")
    for sentence in re.findall(r"[^.!?;]+[.!?;]?", unquoted):
        clauses = re.split(r",\s*(?=(?:а\s+)?(?:можно|зачем|почему|как|что|чем|есть)\b)", sentence, flags=re.I)
        for original in clauses:
            if re.search(r"[:?]|\[quoted\]|\b(?:если|допустим|предположим|бы)\b", original, re.I):
                continue
            # Preserve the same whole-context own No even if C times out. It is
            # a negative conversational choice, never a formal consent receipt.
            if (frame.mode == "debt" and "intent" in frame.known_fields
                    and _debt_transfer_evidence_is_own(latest, original.strip())):
                found.append("decline_transfer")
            clause = original.strip().rstrip(".!;… ")
            own = re.sub(r"^(?:(?:нет|да|ага|спасибо|понял[аи]?)\s*[,!]\s*)+", "", clause, flags=re.I)
            if frame.mode == "debt" and (
                (frame.reply_target == "phone" and _SHORT_DECLINE.fullmatch(clause))
                or (re.match(
                    r"^(?:я\s+)?(?:не\b|нет\b|без\b|телефон\w*|номер\w*|контакт\w*|"
                    r"предпочитаю\b|лучше\b|давайте\b)", own, re.I,
                ) and phone_contact_declined(own, expected=frame.reply_target == "phone"))
            ):
                found.append("decline_phone")
            if frame.offer_present and frame.offer_status == "awaiting_consent" and (
                _explicit_transfer_refusal(own, independent_clauses=False)
                or _DIRECT_TRANSFER_DECLINE.fullmatch(own)
                or (frame.reply_target == "transfer" and _SHORT_DECLINE.fullmatch(clause))
            ):
                found.append("decline_transfer")
    return tuple(dict.fromkeys(found))


def _blocked_transfer_capability_is_bound(draft: str, evidence: str) -> bool:
    """A current-chat inability statement is not a denial of a past receipt."""
    pattern = (
        r"\s*Передачу\s+(?:заявки|данных)\s+из\s+(?:этого|текущего)\s+чата\s+"
        r"(?:сейчас|пока)\s+(?:оформить|выполнить|осуществить)\s+не\s+могу[.!]?\s*"
    )
    if re.fullmatch(pattern, evidence, re.I) is None:
        return False
    start = draft.find(evidence)
    end = start + len(evidence)
    if start < 0:
        return False
    # Scope proof uses the full draft, including multi-sentence quotations.
    quoted_pattern = r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'"
    quoted = tuple(re.finditer(quoted_pattern, draft, re.S))
    if any(match.start() < end and match.end() > start for match in quoted):
        return False
    unquoted = re.sub(quoted_pattern, "", draft, flags=re.S)
    if any(mark in unquoted for mark in ('"', "'", "«", "»", "“", "”")):
        return False
    context = " ".join(
        match.group() for match in re.finditer(r"[^.!?;]+[.!?;]?", draft)
        if match.start() < end and match.end() > start
    )
    return re.fullmatch(pattern, context, re.I) is not None


def _claim_issue(claim: Claim, frame: DialogueFrame, *,
                 blocked_transfer_evidence: frozenset[str] = frozenset()) -> str | None:
    if claim.modality in {"question", "quoted"}:
        return None
    predicate = claim.predicate
    if claim.modality == "not_established":
        unknown_delivery = (predicate == "transfer_completed" and claim.scope == "current_offer"
                            and frame.offer_present and frame.offer_status in {"signal_pending", "outcome_unknown"})
        if predicate in {"recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"} or unknown_delivery:
            return None
        return "unsupported_uncertainty_" + predicate
    if predicate in {"phone_required_to_save_chat", "phone_required_to_chat", "engineering_transfer_purpose"}:
        return predicate if claim.value else None
    if predicate in {"recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"}:
        return "unverified_" + predicate
    if predicate == "consultation_purpose":
        return None if claim.value and claim.scope == "current_offer" and frame.offer_present else "unverified_purpose"
    if claim.modality != "asserted":
        return None
    if predicate == "conversation_refusal_acknowledged":
        expected_refusal = (frame.conversation_declined if claim.scope == "current_offer" else
                            ("client.phone" in frame.refused_fields or frame.conversation_declined)
                            if claim.scope == "chat" else None)
        return None if expected_refusal is not None and claim.value is expected_refusal else "unsupported_refusal_acknowledgment"
    # Receipt status is code-owned. A conversational refusal never becomes a formal receipt.
    if (predicate == "transfer_completed" and claim.value is False and claim.scope == "current_offer"
            and frame.transfer_blocked and frame.mode == "debt" and "intent" in frame.known_fields
            and not frame.offer_present and claim.evidence in blocked_transfer_evidence):
        # Only the bound present capability denial is supported here. Other
        # negative receipts, formal decisions and affirmative claims are unchanged.
        return None
    expected: bool | None = None
    if claim.scope == "current_offer" and frame.offer_present:
        if predicate == "transfer_completed" and frame.offer_status in {"awaiting_consent", "declined", "rejected"}:
            expected = False
        if predicate == "consent_recorded" and frame.offer_status in {"awaiting_consent", "queued", "declined"}:
            expected = frame.offer_status == "queued"
        if predicate == "refusal_recorded" and frame.offer_status in {"awaiting_consent", "queued", "declined"}:
            expected = frame.offer_status == "declined"
    return None if expected is not None and claim.value is expected else "unverified_" + predicate


def evaluate_semantics(authority: TurnAuthority, semantics: SemanticParse) -> SemanticEvaluation:
    frame = authority.frame
    bound = (authority.context_digest == _frame_digest(frame) and authority.latest_digest == semantics.latest_digest
             and authority.draft_digest == semantics.draft_digest)
    user_valid, claims_valid = bound and semantics.user is not None, bound and semantics.claims is not None
    user_issues: list[str] = []
    claims_issues: list[str] = []
    applied: list[UserAct] = []
    need = semantics.user.information_need if user_valid and semantics.user is not None else None
    need_error = semantics.need_error
    if not user_valid:
        user_issues.append("user_channel_unavailable" if bound else "context_mismatch")
        frame = replace(frame, conversation_paused=True)
    elif semantics.user is not None:
        unbound_refusal = False
        for act in dict.fromkeys(semantics.user.acts):
            if act.subject != "client" or act.mode != "asserted":
                continue
            if not _refusal_is_bound(act, frame):
                unbound_refusal = True
                continue
            if act.kind == "decline_transfer" and act.scope == "current_offer":
                awaiting_offer = frame.offer_present and frame.offer_status == "awaiting_consent"
                # Confirmed debt allows refusing future transfer before any offer;
                # clarify and onloader retain their existing offer-bound contract.
                active_debt = frame.mode == "debt" and "intent" in frame.known_fields and not frame.offer_present
                if awaiting_offer or (active_debt and act.evidence in semantics.debt_transfer_refusal_evidence):
                    frame = replace(frame, conversation_declined=True)
                    applied.append(act)
                elif active_debt:
                    unbound_refusal = True
            elif act.kind == "decline_phone" and act.scope == "contact_collection" and frame.mode == "debt":
                frame = replace(frame, refused_fields=frame.refused_fields | {"client.phone"})
                applied.append(act)
        if applied:
            frame = replace(frame, conversation_paused=False)
        elif unbound_refusal:
            # Ambiguity pauses persuasion without recording a decision. A valid
            # second interpretation can recover this same-turn uncertainty.
            user_valid = False
            user_issues.append("user_refusal_unbound")
            frame = replace(frame, conversation_paused=True)
    if not user_valid and need is not None:
        # A newly supplied hint from the rejected channel must not alter the
        # outgoing task. Preserve any already code-owned goal on the frame.
        need = None
        need_error = need_error or "user_channel_unavailable"
    if need is not None:
        hint_goal: Literal["offer_information_purpose", "debt_explanation"] | None = None
        if need.kind == "transfer_purpose" and frame.offer_present:
            hint_goal = "offer_information_purpose"
        elif need.kind == "debt_explanation" and frame.mode == "debt":
            hint_goal = "debt_explanation"
        else:
            need_error = "information_need_scope_mismatch"
            need = None
        if hint_goal is not None and frame.trusted_question_goal is None:
            frame = replace(frame, question_goal=hint_goal, question_goal_origin="model_hint")
    claims = semantics.claims
    if not bound:
        # Digest failure invalidates both channels, never just user authority.
        claims_issues.append("context_mismatch")
    if not claims_valid or claims is None:
        claims_issues.append("claims_channel_unavailable")
    else:
        categories = [scan.category for scan in claims.coverage if scan.completed]
        if len(categories) != 4 or set(categories) != COVERAGE_CATEGORIES or len(claims.coverage) != 4:
            claims_valid = False
            claims_issues.append("claims_coverage_incomplete")
        # Validate against the effective paused/refused frame, not the original
        # collection opportunity. Incoming uncertainty cannot authorize requests.
        claims_issues.extend(issue for claim in claims.claims if (issue := _claim_issue(
            claim, frame, blocked_transfer_evidence=semantics.blocked_transfer_capability_evidence,
        )) is not None)
        for speech in claims.speech_acts:
            if speech.mode == "requested":
                if speech.kind == "request_phone" and (frame.contact_declined or "client.phone" in frame.known_fields):
                    claims_issues.append("unallowed_phone_request")
                if speech.kind == "request_transfer_consent" and (
                    frame.contact_declined or not frame.offer_present or frame.offer_status != "awaiting_consent"
                ):
                    claims_issues.append("unallowed_consent_request")
        if authority.frame.trusted_question_goal == "offer_information_purpose" and not any(
            claim.predicate == "consultation_purpose" and claim.value and claim.modality in {"asserted", "conditional"}
            and claim.scope == "current_offer" for claim in claims.claims
        ):
            claims_issues.append("consultation_purpose_missing")
    unique_user_issues = tuple(dict.fromkeys(user_issues))
    unique_claims_issues = tuple(dict.fromkeys(claims_issues))
    unique_issues = tuple(dict.fromkeys((*unique_user_issues, *unique_claims_issues)))
    return SemanticEvaluation(frame, tuple(applied), user_valid, claims_valid, unique_issues,
                              user_valid and claims_valid and not unique_issues, need, need_error,
                              outgoing_safe=bound and claims_valid and not unique_claims_issues,
                              user_issues=unique_user_issues, claims_issues=unique_claims_issues)
