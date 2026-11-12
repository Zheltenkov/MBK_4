"""Let the existing conversation model speak while policy retains every business decision."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import re
import time
from typing import Any, Literal

import llm_agent
import prompts
import policy_factual_audit
import policy_voice_critic
from config import AppConfig
from dialogue_frame import (
    DialogueFrame, constrain_collection, debt_amount_reply_issue, discussion_followup_issue, request_fields,
)
from pii_masking import contains_private_placeholder, mask_payload_for_llm
from policy_content import CONSULTATION_PURPOSE, ContentIssue, content_issues
from utils import guard_bubbles, omit_empty_field_receipt_bubbles

VERSION = "policy-voice-v33-task-relevance"
# Runtime semantics are independent of the human-readable version label.
STYLE_DIAGNOSTICS_ADVISORY = True
QuestionField = Literal["debts.total", "client.region_class", "client.name", "client.phone", "intent"]
StyleIssue = Literal["unrequested_receipt"]


@dataclass(frozen=True)
class ReplyDiagnostic:
    """Code-owned findings and their effective verdict remain distinct from the model assessment."""

    code_content_issues: tuple[ContentIssue, ...]
    effective_verdict: policy_voice_critic.SemanticVerdict | None
    code_style_issues: tuple[StyleIssue, ...] = ()


@dataclass(frozen=True)
class SpokenPolicyReply:
    text: str
    asked_field: QuestionField | None
    source: Literal["model", "fallback"]
    reason: str
    unbound_financial_question: bool = False
    critic_model: str | None = None
    critic_calls: int = 0
    critic_assessments: tuple[policy_voice_critic.CriticAssessment, ...] = ()
    code_user_acts: tuple[str, ...] = ()
    diagnostic_assessments: tuple[ReplyDiagnostic, ...] = ()
    factual_assessments: tuple[policy_factual_audit.FactualAssessment, ...] = ()


def field_receipt_style_issues(text: str, frame: DialogueFrame, latest: str) -> tuple[StyleIssue, ...]:
    """Suggest a style revision for a sole clerical receipt, without rejecting safe speech."""
    if (frame.mode != "debt" or frame.status != "collected_held" or frame.offer_present
            or frame.allowed_field is not None or frame.question_goal is not None
            or frame.confirming or frame.closing or frame.contact_declined or not text.strip()):
        return ()
    # Reuse the existing whole-field-value proof and receipt role. A free short
    # reaction, requested confirmation, affect or substantive body stays intact.
    remainder = omit_empty_field_receipt_bubbles(
        text, received_fields=frame.received_fields, user_message=latest,
        known_region="client.region_class" in frame.known_fields,
        proven_plain_fields=frame.plain_reply_fields,
    )
    return ("unrequested_receipt",) if not remainder.strip() else ()


def normalize_brief_field_reply(text: str, frame: DialogueFrame, latest: str) -> str:
    """Keep an original model acknowledgment before its single semantic assessment."""
    if not frame.brief_field_reply:
        return text
    return omit_empty_field_receipt_bubbles(
        text, received_fields=frame.received_fields, user_message=latest,
        known_region="client.region_class" in frame.known_fields,
        proven_plain_fields=frame.plain_reply_fields, retain_acknowledgement=True,
    )


def accepts_brief_style_without_repair(
    text: str, frame: DialogueFrame, verdict: policy_voice_critic.SemanticVerdict | None,
) -> bool:
    """Apply after hard and semantic validation to bound optional style work."""
    return bool(frame.brief_field_reply and verdict is not None and verdict.accept
                and not policy_factual_audit.requires_audit(frame, text))


def is_code_checked_brief_reply(text: str, frame: DialogueFrame) -> bool:
    """Certify only an acknowledgment after a code-proven neutral region reply."""
    # The field proof comes from raw input in core, before privacy masking.
    # This closed speech act cannot assert facts, ask questions or authorize work.
    return bool(frame.brief_field_reply and frame.question_goal is None
                and re.fullmatch(r"(?:да|ага|угу|хорошо|ладно|понял[аи]?|понятно|ясно|понимаю)"
                                 r"(?:[.!…]|\.\.\.)?", text.strip(), re.I)
                and not policy_factual_audit.requires_audit(frame, text))


_DELIVERY_CLAIM = re.compile(
    r"(?:заявк\w*\s+(?:уже\s+)?(?:отправлен|передан|доставлен)|"
    r"(?:отправил[аи]?|передал[аи]?|отправлю|передам|передаю|отправляю)\s+(?:ваш\w*\s+)?(?:заявк|данн|контакт|ситуац|номер)|"
    r"(?:могу\s+(?:помочь\s+)?|могла\s+|смогу\s+|помогу\s+)(?:передать|отправить)\s+(?:ваш\w*\s+)?(?:заявк|данн|контакт|ситуац|номер)|"
    r"\b(?:вам|с\s+вами)\s+(?:обязательно\s+)?(?:позвонят|перезвонят|свяжутся)|"
    r"\b(?:звонк\w*|звонить)[^.!?]{0,55}\bне\s+буд(?:ет|ут)\b|"
    r"(?:спишем|спишут|одобрим|точно\s+одобр|гарантирован)|"
    # Passive outcome claims must not match the noun "списание" in an explanation.
    r"долг\w*.{0,25}(?:будут\s+)?списан(?:а|о|ы)?\b|"
    r"квартир\w*.{0,25}(?:не\s+заберут|сохранится)|"
    r"(?:вам\s+подойд[её]т|вам\s+положено)\s+(?:банкрот|списан))", re.IGNORECASE,
)
_CONSENT_CLAIM = re.compile(
    r"(?:(?:согласие|разрешение)\s+(?:на\s+передачу(?:\s+данных)?\s+)?"
    r"(?:уже\s+)?(?:получен|принят|принял[аи]?|сохран|запис)|"
    r"спасибо\s+за\s+(?:ваше\s+)?(?:согласие|разрешение)|"
    r"(?:вы\s+)?(?:согласились|разрешили\s+передач)|"
    r"(?<!\bне )\b(?:получил[аи]?|принял[аи]?|сохранил[аи]?|записал[аи]?)\s+"
    r"(?:ваш[ео]\s+)?(?:согласие|разрешение)|"
    r"(?:оформил[аи]?|подтвердил[аи]?)\s+(?:ваше\s+)?согласие)", re.IGNORECASE,
)
_GENERAL_DISCHARGE_POSSIBILITY = re.compile(
    # A general procedure heading can introduce the same bounded possibility.
    # The remaining clause must still match in full, without personal promises.
    r"\s*(?:списание\s+возможно\s+через\s+(?:судебное\s+)?банкротство\s*:\s*)?"
    r"(?:(?:при\s+соблюдении\s+условий\s+закона|по\s+правилам\s+закона)\s+)?"
    r"кредитн(?:ый|ые)\s+долг(?:и)?\s+(?:может|могут)\s+быть\s+списан(?:ы)?"
    r"(?:\s+полностью)?(?:\s+(?:при\s+соблюдении\s+условий\s+закона|по\s+правилам\s+закона))?"
    r"[\s,.!?]*", re.IGNORECASE,
)
def question_field(path: str | None) -> QuestionField | None:
    allowed: dict[str, QuestionField] = {
        "debts.total": "debts.total", "client.region_class": "client.region_class",
        "client.name": "client.name", "client.phone": "client.phone", "intent": "intent",
    }
    return allowed.get(path or "")


# This detector observes intake requests for bookkeeping. An unrecognised wording
# is still a valid conversational answer and must not become a canned fallback.
_REQUEST = re.compile(r"^\s*(?:а\s+)?(?:скажите|подскажите|напишите|укажите|назовите|сообщите|"
                      r"расскажите|уточните|сколько|како[йяе]|где|как|хотите)\b", re.IGNORECASE)
_FINANCIAL_TERM = re.compile(r"(?:кредит|займ|сумм|денег|рубл)", re.IGNORECASE)
_NEGATED_OUTCOME = re.compile(
    r"\b(?:не\s+(?:могу|можем)\s+(?:вам\s+)?(?:обещать|гарантировать)|"
    r"не\s+(?:обещаю|обещаем|гарантирую|гарантируем)|нельзя\s+(?:обещать|гарантировать))"
    r"\s*,?\s*(?:что\s+)?(?:все\s+)?(?:ваши\s+)?(?:долги\s+)?(?:полностью\s+)?$", re.IGNORECASE,
)
_BUTTON_CLAIM = re.compile(
    r"(?:нажмите|нажать|выберите|кликните).{0,50}(?:кнопк|согласи)|"
    r"кнопк\w*.{0,30}(?:останется|находится|появится)", re.IGNORECASE,
)
_REFUSAL_CLAIM = re.compile(
    r"отказ\s+(?:уже\s+)?(?:сохран[её]н|записан|принят)|передач[ау]\s+(?:останов(?:ила|лена)|отмен(?:яем|яю|или|ена))", re.IGNORECASE,
)
_FUTURE_REFUSAL_CLAIM = re.compile(
    r"(?:передавать|отправлять).{0,20}не\s+буд[уе]|"
    r"не\s+буд[уе].{0,20}(?:передавать|отправлять)", re.IGNORECASE,
)
_OFFER_STATUSES = frozenset({"awaiting_consent", "queued", "declined", "signal_pending", "outcome_unknown",
                             "pending", "stale", "forbidden", "unavailable", "rejected"})


def _requests(text: str) -> set[QuestionField]:
    """Compatibility observer sharing the same collection semantics as the guard."""
    frame = DialogueFrame(None, "")
    fields = {path for sentence in re.findall(r"[^.!?\n]+[.!?]?", text)
              for path in request_fields(sentence, frame)}
    return {typed for path in fields if (typed := question_field(path)) is not None}



_DEBT_APPLICATION = re.compile(r"\b(?:анкет\w*|заявк\w*|оформлен\w*)\b", re.I)
_DEBT_START = re.compile(r"\b(?:оформ\w*|заполн\w*|подать|подайте|подад\w*|перей\w*|прой\w*|начать|начн\w*)\b", re.I)
_DEBT_START_REQUEST = re.compile(r"\b(?:хотите|готовы|давайте|предлагаю|можете|заполните|оформите|подайте|перейдите|пройдите)\b", re.I)
_DEBT_INFORMATION = re.compile(r"\b(?:поня\w*|ясн\w*|узна\w*|определ\w*|подойд\w*|доступ\w*|объясн\w*|поясн\w*)\b", re.I)
_DEBT_NECESSITY = re.compile(r"\b(?:нужно|надо|необходимо|требуется|прид[её]тся|только\s+после|лишь\s+после)\b", re.I)
_DEBT_LINK = re.compile(r"\b(?:ссылк\w*|страниц\w*)\b", re.I)
_DEBT_LINK_OUTCOME = re.compile(r"\b(?:станет|будет|появит\w*|увид\w*|получ\w*|(?:присл|пришл|высл|вышл)\w*|отправ\w*|(?:показ|покаж)\w*|посмотр\w*|(?:перейт|перейд)\w*|откр[ыо]\w*|готова)\b", re.I)
_DEBT_DENIED_ACTION = re.compile(
    r"\bне\s+(?:(?:нужно|надо|необходимо|требуется|следует|могу|можем|буду|будем)\s+)?$|"
    r"\bне\s+(?:могу|можем)\s+(?:вам\s+)?(?:обещать|гарантировать)\b[^.!?;]*$|"
    r"\bне\s+(?:обещаю|обещаем|гарантирую|гарантируем|знаю|знаем)\b[^.!?;]*$", re.I,
)


def unavailable_debt_continuation_issue(text: str, frame: DialogueFrame) -> Literal["unavailable_debt_continuation"] | None:
    """A completed held intake grants no new form, result page or application gate."""
    if frame.mode != "debt" or frame.status != "collected_held" or frame.offer_present:
        return None
    # Quoted examples explain an operation without asserting that it exists here.
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"\n]*"', "", text)
    for sentence in re.findall(r"[^.!?;\n]+[.!?;]?", unquoted):
        # Separate a denial from a later proposed action. The application's lack
        # of obligation or availability cannot authorize another clause's CTA.
        for clause in re.split(r"\b(?:но|однако|зато)\b|;|,\s*(?=давайте|готовы|хотите)", sentence, flags=re.I):
            application = _DEBT_APPLICATION.search(clause)
            link = _DEBT_LINK.search(clause)
            if application is None and link is None:
                continue
            if not _DEBT_START_REQUEST.search(clause) and re.search(r"\b(?:если|когда)\b", clause, re.I) and re.search(
                r"\b(?:другом|другой|внешн\w*|когда-нибудь|подключат|подключен\w*)\b", clause, re.I,
            ):
                continue
            if link is not None:
                outcome = _DEBT_LINK_OUTCOME.search(clause)
                if outcome is not None and not _DEBT_DENIED_ACTION.search(clause[:outcome.start()]):
                    return "unavailable_debt_continuation"
            if application is None:
                continue
            start = _DEBT_START.search(clause)
            # A client's explicit submission to an outside institution describes
            # that institution's procedure. It grants no operation to this chat.
            external_submission = bool(
                start is not None and re.fullmatch(r"подать|подайте|подач\w*", start.group(), re.I)
                and re.search(r"\b(?:в|через)\s+(?:(?:свой|ваш|собственный)\s+)?(?:банк\w*|МФЦ)\b|"
                              r"\bкредитору\b", clause[start.end():], re.I)
                and not re.search(r"\b(?:наш\w*|здесь)|\b(?:этом|нашем)\s+чате|"
                                  r"\bчерез\s+(?:меня|нас)\b", clause, re.I)
            )
            if external_submission:
                continue
            request = _DEBT_START_REQUEST.search(clause)
            if start is not None and request is not None and not _DEBT_DENIED_ACTION.search(clause[:start.start()]):
                return "unavailable_debt_continuation"
            # An application's requirements do not gate general explanations or
            # establish that a future application will diagnose personal suitability.
            information = _DEBT_INFORMATION.search(clause)
            necessity = _DEBT_NECESSITY.search(clause)
            current_chat_step = bool(start is not None and re.search(
                r"\b(?:наш\w*|здесь)|\b(?:этом|нашем)\s+чате|\bчерез\s+(?:меня|нас)\b", clause, re.I,
            ) and not _DEBT_DENIED_ACTION.search(clause[:start.start()]))
            if (information is not None or current_chat_step) and necessity is not None and not _DEBT_DENIED_ACTION.search(clause[:necessity.start()]):
                return "unavailable_debt_continuation"
            if information is not None and re.search(r"\bбез\s+(?:новой\s+)?(?:заявк\w*|анкет\w*)", clause, re.I) and re.search(
                r"\bне\s+(?:могу|можем|смогу|сможем|получится|возможно)\b", clause, re.I,
            ):
                return "unavailable_debt_continuation"
    return None

def unrequested_cancellation_issue(text: str, frame: DialogueFrame) -> Literal["unrequested_cancellation_topic"] | None:
    # The whole incoming reference has no cancellation/deletion task. This
    # private code proof constrains relevance without granting consent or sends.
    if (frame.mode == "debt" and frame.known_phone_reference and "client.phone" in frame.known_fields
            and any(re.search(r"\b(?:отмен\w*|аннулир\w*|удал\w*)\b", sentence, re.I)
                    and re.search(r"\b(?:заявк\w*|номер|телефон\w*|данн\w*)\b", sentence, re.I)
                    for sentence in re.findall(r"[^.!?]+", text))):
        return "unrequested_cancellation_topic"
    return None


def validate_reply(text: str, expected: QuestionField | None, *, frame: DialogueFrame | None = None) -> bool:
    if not text.strip() or contains_private_placeholder(text):
        return False
    frame = frame or DialogueFrame(expected, "")
    if unrequested_cancellation_issue(text, frame):
        return False
    # The code-owned farewell decision closes collection and offer pressure.
    # A substantive user question is classified separately and keeps this false.
    if frame.closing and ("?" in text or _BUTTON_CLAIM.search(text)):
        return False
    if unavailable_debt_continuation_issue(text, frame) is not None:
        return False
    if debt_amount_reply_issue(text, frame) is not None:
        return False
    if discussion_followup_issue(text, frame) is not None:
        return False
    if content_issues(text, mode=frame.mode, question_goal=frame.trusted_question_goal):
        return False
    if _unsupported_recipient_contact_outcome(text, frame):
        return False
    # Existing production speech guards remain the common boundary for both flows.
    bubbles = prompts.split_bubbles(text)
    if guard_bubbles(bubbles) != bubbles:
        return False
    for sentence in re.findall(r"[^.!?\n]+[.!?]?", text):
        if _CONSENT_CLAIM.search(sentence):
            return False
        for clause in re.split(r"\b(?:но|однако|зато)\b|;", sentence, flags=re.IGNORECASE):
            for claim in _DELIVERY_CLAIM.finditer(clause):
                # A denial qualifies its own outcome, never an unrelated later assertion.
                prefix = clause[:claim.start()]
                denied_current_action = bool(re.search(r"\bне\s*$", prefix) and
                                             re.match(r"(?:передаю|отправляю)", claim.group(), re.IGNORECASE))
                # A real pending offer can explain its button-gated future action.
                # This exception never licenses a statement that anything was sent.
                conditional_offer_action = bool(
                    frame is not None and frame.offer_present and frame.offer_status == "awaiting_consent"
                    and not frame.conversation_declined
                    and re.match(r"(?:могу|могла|смогу|помогу)\b", claim.group(), re.I)
                    and re.search(r"\bчтобы\s+(?:я\s+)?$", prefix, re.I)
                    and re.search(r"(?<!\bне )\b(?:подтверд\w*|нажм\w*|выбер\w*)[^.!?]*кнопк", prefix, re.I)
                )
                # A direct denial of a guarantee is a qualification. It never
                # qualifies another outcome claim in this or a later clause.
                denied_guarantee = bool(re.match(r"гарантирован", claim.group(), re.I)
                                        and re.search(r"\bне\s*$", prefix, re.I))
                # Only this source-bounded general credit-debt possibility is
                # exempted. Added approvals, fees or eligibility rules remain
                # rejected even when they retain the modal verb.
                general_discharge_possibility = bool(
                    frame.mode == "debt" and _GENERAL_DISCHARGE_POSSIBILITY.fullmatch(clause)
                )
                if not (denied_current_action or conditional_offer_action or denied_guarantee
                        or general_discharge_possibility or _NEGATED_OUTCOME.search(prefix)):
                    return False
    return not constrain_collection(text, frame).removed_fields


_EXCLUSIVE_RECIPIENT = re.compile(
    r"\b(?:данн\w*|сведени\w*|заявк\w*)\b[^.!?;]*?"
    r"\b(?:переда\w*|уйд\w*|отправ\w*|получ\w*|увид\w*)\b[^.!?;]*?\b(?:только|исключительно)\b|"
    r"\b(?:только|исключительно)\b[^.!?;]{0,60}\b(?:компани\w*|получател\w*)\b"
    r"[^.!?;]{0,30}\b(?:получ\w*|увид\w*)\b[^.!?;]{0,30}\b(?:данн\w*|сведени\w*)\b", re.I,
)
_NO_OTHER_RECIPIENT = re.compile(
    r"\bдругих\s+получател\w*(?:\s+(?:сейчас|здесь|теперь))*\s+нет\b|\bнет\s+других\s+получател\w*", re.I,
)
_RECIPIENT_CONTACT_ABSENCE = re.compile(
    r"\b(?:они|она|компани\w*|специалист\w*)\s+(?:просто\s+)?не\s+"
    r"(?:увид\w*|узна\w*)\b[^.!?;]{0,65}\b(?:номер|контакт\w*|как\s+с\s+вами\s+связаться)\b", re.I,
)
_RECIPIENT_CALLBACK = re.compile(
    r"\b(?:(?:именно\s+)?(?:она|они)\s+)?свяж(?:ется|утся)\s+с\s+вами\b", re.I,
)
_RECIPIENT_NONASSERTION = re.compile(
    r"\b(?:не\s+(?:утверждаю|подтверждаю|гарантирую)|нельзя\s+(?:утверждать|гарантировать)|не\s+означает)"
    r"(?:\s*,?\s*что)?\s*$|\bесли\s+(?:бы\s+)?$", re.I,
)
_ONLY_ACTION_CONDITION = re.compile(
    r"(?:только|исключительно)\s+(?:после|если|когда|при\b|с\s+вашего|по\s+вашему|в\s+(?:случае|рамках))\b", re.I,
)


def _unsupported_recipient_exclusivity(text: str, frame: DialogueFrame) -> bool:
    """One declared offer recipient cannot establish a global recipient guarantee."""
    if frame.mode != "onloader" and not frame.offer_present:
        return False
    def unquote_name_or_omit_claim(match: re.Match[str]) -> str:
        quoted = match.group()[1:-1]
        return "" if _EXCLUSIVE_RECIPIENT.search(quoted) or _NO_OTHER_RECIPIENT.search(quoted) else quoted

    # Quoted claims are not assertions. Quoted company names still participate
    # in the surrounding exclusive-recipient assertion.
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', unquote_name_or_omit_claim, text)
    for clause in re.findall(r"[^.!?;\n]+[.!?;]?", unquoted):
        if clause.rstrip().endswith("?"):
            continue
        for pattern in (_EXCLUSIVE_RECIPIENT, _NO_OTHER_RECIPIENT):
            for match in pattern.finditer(clause):
                prefix = clause[:match.start()]
                if _RECIPIENT_NONASSERTION.search(prefix) or _NEGATED_OUTCOME.search(prefix):
                    continue
                if pattern is _EXCLUSIVE_RECIPIENT:
                    if re.search(r"\bне\s+(?:переда\w*|уйд\w*|отправ\w*|получ\w*|увид\w*|только)\b", match.group(), re.I):
                        continue
                    only = re.search(r"\b(?:только|исключительно)\b", match.group(), re.I)
                    if only is not None and _ONLY_ACTION_CONDITION.match(clause[match.start() + only.start():]):
                        continue
                return True
    return False


def _unsupported_recipient_contact_outcome(text: str, frame: DialogueFrame) -> bool:
    """An offer confirms its purpose, without proving calls or other contact sources."""
    if frame.mode != "onloader" and not frame.offer_present:
        return False
    # Quoted assertions and questions are conversational material, not promises.
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', "", text)
    for clause in re.findall(r"[^.!?;\n]+[.!?;]?", unquoted):
        if clause.rstrip().endswith("?"):
            continue
        for pattern in (_RECIPIENT_CALLBACK, _RECIPIENT_CONTACT_ABSENCE):
            for claim in pattern.finditer(clause):
                prefix = clause[:claim.start()]
                if not (_RECIPIENT_NONASSERTION.search(prefix) or _NEGATED_OUTCOME.search(prefix)):
                    return True
    return False


def _unsupported_action(text: str, frame: DialogueFrame) -> bool:
    offer_status = frame.offer_status
    return _unsupported_recipient_exclusivity(text, frame) or (not frame.offer_present and bool(_BUTTON_CLAIM.search(text))) or (
        offer_status in _OFFER_STATUSES and offer_status != "declined" and bool(_REFUSAL_CLAIM.search(text))) or (
        offer_status in _OFFER_STATUSES and offer_status not in {"awaiting_consent", "declined"}
        and bool(_FUTURE_REFUSAL_CLAIM.search(text)))


def _safe_explanation(text: str, frame: DialogueFrame) -> str:
    """Keep validated explanation sentences when a recovery also invents an action."""
    paragraphs: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        sentences = [sentence.strip() for sentence in re.findall(r"[^.!?\n]+[.!?]?", paragraph)]
        safe = [sentence for sentence in sentences
                if validate_reply(sentence, frame.allowed_field, frame=frame)
                and not _unsupported_action(sentence, frame)]
        if safe:
            paragraphs.append(" ".join(safe))
    return "\n\n".join(paragraphs)


def _model_reply(text: str, frame: DialogueFrame, reason: str) -> SpokenPolicyReply | None:
    """Validate the exact capped body and observe its actual question from scratch."""
    paragraphs = [part.strip() for part in text.replace("\r\n", "\n").split("\n\n") if part.strip()]
    # Keep the full explanation and final question within the three-bubble cap.
    if len(paragraphs) > 3:
        paragraphs = paragraphs[:2] + [" ".join(paragraphs[2:])]
    body = "\n\n".join(paragraphs)
    expected = frame.allowed_field
    if not validate_reply(body, expected, frame=frame) or _unsupported_action(body, frame):
        return None
    collection = constrain_collection(body, frame)
    asked = collection.asked_field
    unbound = expected == "debts.total" and asked is None and any(
        ("?" in sentence or _REQUEST.search(sentence)) and _FINANCIAL_TERM.search(sentence)
        for sentence in re.findall(r"[^.!?\n]+[.!?]?", body)
    )
    return SpokenPolicyReply(body, asked, "model", reason, bool(unbound))


def _required_question(reply: SpokenPolicyReply, frame: DialogueFrame,
                       fallback: str, fallback_question: QuestionField | None) -> SpokenPolicyReply:
    """Use the typed question only when the two-call budget has left the step incomplete."""
    expected = frame.allowed_field
    if not frame.require_question or expected is None or reply.asked_field is not None or reply.unbound_financial_question:
        return reply
    if fallback_question != expected:
        return reply
    question = constrain_collection(fallback, frame).text.strip()
    if not question or not validate_reply(question, expected, frame=frame) or _unsupported_action(question, frame):
        return reply
    paragraphs = reply.text.split("\n\n")
    if len(paragraphs) >= 3:
        paragraphs[-1] += " " + " ".join(question.split())
    else:
        paragraphs.append(question)
    combined = _model_reply("\n\n".join(paragraphs), frame, reply.reason)
    if combined is None:
        return reply
    # This field belongs to a newly rendered, trusted plan question.
    return replace(combined, asked_field=expected, reason=reply.reason + "_typed_question")


def _fallback_reply(frame: DialogueFrame, fallback: str, fallback_question: QuestionField | None,
                    reason: str) -> SpokenPolicyReply:
    purpose = prompts.verified_policy_purpose(frame, concise=True)
    # A failed interpretation may still use facts supplied by the current
    # validated card. A model hint selects no new authority or business action.
    card_recovery = (purpose is None and not frame.closing and frame.mode == "onloader" and frame.offer_present
                     and bool((frame.offer_recipient_name or "").strip()) and not frame.conversation_paused
                     and (not frame.contact_declined or frame.question_goal == "offer_information_purpose"))
    if card_recovery:
        purpose = CONSULTATION_PURPOSE
    if purpose is not None:
        # A degraded reply can still identify the backend-validated current
        # recipient. Conversation history provides no authority for this label.
        if ((card_recovery or frame.trusted_question_goal in {"offer_information_purpose", "offer_recipient"}) and frame.offer_present
                and frame.offer_recipient_name):
            purpose = f"Получатель этого предложения — «{frame.offer_recipient_name}». " + purpose
        fallback, fallback_question = purpose, None
    elif frame.closing:
        # A failed farewell generation must not reopen the current offer.
        fallback, fallback_question = "Всего доброго.", None
    elif (frame.mode == "debt" and frame.confirming and frame.allowed_field == "debts.total"
          and not frame.conversation_declined and "client.phone" not in frame.refused_fields):
        # An unavailable model must not restart an unsuccessful precision loop.
        # Keep the completion requirement explicit without inventing the client's amount.
        fallback = ("Для завершения анкеты понадобится точная сумма долга. "
                    "Если сейчас её нет под рукой, можем пока обсудить ваши вопросы здесь.")
        fallback_question = None
    elif frame.contact_declined:
        fallback = "Хорошо, можем продолжить разговор здесь."
    collection = constrain_collection(fallback, frame)
    body = collection.text.strip() or "Можем обсудить ваши вопросы здесь."
    checked = _model_reply(body, frame, reason)
    if checked is None:
        return SpokenPolicyReply("Можем обсудить ваши вопросы здесь.", None, "fallback", reason)
    asked = checked.asked_field
    if (fallback_question == frame.allowed_field and fallback_question is not None
            and body == fallback.strip()):
        asked = fallback_question
    return replace(checked, source="fallback", asked_field=asked)


def _revision_payload(payload: dict[str, Any], draft: str, frame: DialogueFrame,
                      first: SpokenPolicyReply | None,
                      draft_redactor: Callable[[str], str] | None,
                      semantic_flags: tuple[str, ...] = ()) -> dict[str, Any]:
    # turn_instruction is not traversed by the existing payload masker. Redact the
    # draft as a message before embedding it there, with the original context policy.
    revision = mask_payload_for_llm({**payload, "current_context_readonly": True}, True)
    # Re-establish the rendering role from code after masking, including G2.
    revision.pop("policy_reply_role", None)
    if frame.brief_field_reply:
        revision["policy_reply_role"] = "brief_field_repair"
    if issue := unavailable_debt_continuation_issue(draft, frame):
        semantic_flags = tuple(dict.fromkeys((*semantic_flags, issue)))
    if issue := unrequested_cancellation_issue(draft, frame):
        semantic_flags = tuple(dict.fromkeys((*semantic_flags, issue)))
    if followup_issue := discussion_followup_issue(draft, frame):
        semantic_flags = tuple(dict.fromkeys((*semantic_flags, followup_issue)))
    # Only a locally observed whole receipt from a validated first model reply
    # can become editing material. Hard and mixed diagnoses keep regeneration.
    if (frame.brief_field_reply and first is not None and first.source == "model" and first.text == draft
            and semantic_flags and set(semantic_flags) <= {"unrequested_receipt", "repeated_reply"}
            and field_receipt_style_issues(draft, frame, str(payload.get("latest_user_message") or ""))):
        safe_draft = (draft_redactor(draft) if draft_redactor is not None else str(mask_payload_for_llm(
            {**payload, "latest_user_message": draft, "current_context_readonly": True}, True,
        ).get("latest_user_message") or ""))
        revision["turn_instruction"] = prompts.policy_brief_style_edit_instruction(safe_draft)
        return revision
    if semantic_flags or not draft.strip():
        # Rejected or wholly omitted drafts provide no useful editing source.
        # Regenerate from the current context, without adding a third call.
        revision["turn_instruction"] = prompts.policy_regeneration_instruction(frame, semantic_flags)
        return revision
    safe_draft = (draft_redactor(draft) if draft_redactor is not None else str(mask_payload_for_llm(
        {**payload, "latest_user_message": draft, "current_context_readonly": True}, True,
    ).get("latest_user_message") or ""))
    # Preserve the closed diagnosis while removing the rejected question as an
    # editing anchor. Allowed-field pressure and factual-only edits keep it.
    forbidden_collection = any(
        not frame.allows(path) for path in constrain_collection(safe_draft, frame).removed_fields
    )
    unsupported_contact = _unsupported_recipient_contact_outcome(safe_draft, frame)
    revision["turn_instruction"] = prompts.policy_revision_instruction(
        frame, safe_draft,
        needs_repair=first is None,
        missing_question=bool(frame.require_question and frame.allowed_field is not None
                              and (first is None or first.asked_field is None)),
        semantic_flags=semantic_flags,
        include_draft=not (first is None and (forbidden_collection or unsupported_contact)),
        unsupported_recipient_contact_outcome=unsupported_contact,
    )
    return revision


def _collection_only(text: str, frame: DialogueFrame) -> bool:
    sentences = [sentence for sentence in re.findall(r"[^.!?\n]+[.!?]?", text) if sentence.strip()]
    return bool(sentences) and all(request_fields(sentence, frame) for sentence in sentences)


def render(payload: dict[str, Any], config: AppConfig, *, expected: QuestionField | None,
           status: str, fallback: str, usage_collector: Callable[..., None] | None = None,
           frame: DialogueFrame | None = None,
           fallback_question: QuestionField | None = None,
           draft_redactor: Callable[[str], str] | None = None,
           critic_usage_collector: Callable[..., None] | None = None,
           factual_usage_collector: Callable[..., None] | None = None,
           finalize_reply: Callable[[str], str] | None = None) -> SpokenPolicyReply:
    """Hard validation blocks output. Bounded semantic diagnostics remain advisory."""
    frame = frame or DialogueFrame(expected, status, offer_present=status in _OFFER_STATUSES,
                                   offer_status=status if status in _OFFER_STATUSES else None)
    model_payload = dict(payload)
    # An inherited payload hint cannot select the reduced prompt on another task.
    model_payload.pop("policy_reply_role", None)
    if frame.brief_field_reply:
        model_payload["policy_reply_role"] = "brief_field"
    model_payload["turn_instruction"] = prompts.policy_turn_instruction(frame)
    deadline = time.monotonic() + min(config.request_timeout, 40)
    assessments: list[policy_voice_critic.CriticAssessment] = []
    diagnoses: list[ReplyDiagnostic] = []
    factual_assessments: list[policy_factual_audit.FactualAssessment] = []

    def phase_config(seconds: int, tokens: int) -> AppConfig | None:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            return None
        return replace(config, enable_pii_masking=True, max_tokens=min(config.max_tokens, tokens),
                       request_timeout=min(config.request_timeout, seconds, remaining))

    def record(reply: SpokenPolicyReply) -> SpokenPolicyReply:
        return replace(reply, critic_model=(config.extractor_model or "").strip() or None,
                       critic_calls=sum(item.attempted for item in assessments),
                       critic_assessments=tuple(assessments), diagnostic_assessments=tuple(diagnoses),
                       factual_assessments=tuple(factual_assessments))

    def fallback_reply(reason: str) -> SpokenPolicyReply:
        return record(_fallback_reply(frame, fallback, fallback_question, reason))

    def prepare(text: str, reason: str, *, repair: bool = False) -> tuple[str, SpokenPolicyReply | None]:
        # Only the fully composed body is diagnosed. A caller can apply its
        # existing name cleanup here, rather than mutating an accepted reply later.
        try:
            if finalize_reply is not None:
                text = finalize_reply(text)
            text = normalize_brief_field_reply(text, frame, str(payload.get("latest_user_message") or ""))
        except Exception:
            return "", None
        if repair:
            # These failures concern the whole action. Removing its qualifier
            # can change the request or erase the verified recipient.
            if (unavailable_debt_continuation_issue(text, frame)
                    or discussion_followup_issue(text, frame)
                    or _unsupported_recipient_contact_outcome(text, frame)
                    or debt_amount_reply_issue(text, frame) == "approximate_amount_request"):
                return text, None
            text = _safe_explanation(constrain_collection(text, frame).text, frame)
        candidate = _model_reply(text, frame, reason)
        if candidate is not None:
            if repair:
                candidate = _required_question(candidate, frame, fallback, fallback_question)
            text, reason = candidate.text, candidate.reason
        candidate = _model_reply(text, frame, reason)
        return (candidate.text if candidate is not None else text), candidate

    def assess(text: str) -> policy_voice_critic.CriticAssessment:
        critic_config = phase_config(6, 300)
        result = (policy_voice_critic.assess(
            payload, text, frame, critic_config, usage_collector=critic_usage_collector,
            draft_redactor=draft_redactor,
        ) if critic_config is not None else policy_voice_critic.CriticAssessment(
            None, config.extractor_model, "deadline_exhausted", False,
            llm_agent.ModelFailure("deadline", "deadline", "DeadlineExceeded"),
        ))
        # A transport may return after its nominal timeout. Late approval does
        # not become successful semantic validation for this orchestration step.
        if time.monotonic() >= deadline:
            result = replace(result, verdict=None, reason="deadline_exhausted",
                             failure=llm_agent.ModelFailure("deadline", "deadline", "DeadlineExceeded"))
        assessments.append(result)
        # The raw model assessment retains its identity. This local style cue
        # cannot become a content rejection or a business-state decision.
        diagnoses.append(ReplyDiagnostic((), result.verdict, field_receipt_style_issues(
            text, frame, str(payload.get("latest_user_message") or ""),
        )))
        return result

    def complete(candidate: SpokenPolicyReply | None) -> bool:
        return candidate is not None and not (
            frame.require_question and frame.allowed_field is not None and candidate.asked_field is None
        )


    def audit(candidate: SpokenPolicyReply) -> str | None:
        # Only applicable, hard-valid model speech needs this independent
        # comparison. A skipped audit is not recorded as model approval.
        if (not config.policy_factual_audit_enabled
                or not policy_factual_audit.requires_audit(frame, candidate.text)):
            return None
        audit_config = phase_config(policy_factual_audit.MAX_PHASE_SECONDS, policy_factual_audit.MAX_REPLY_TOKENS)
        if audit_config is None:
            result = policy_factual_audit.FactualAssessment(
                None, config.extractor_model, "deadline_exhausted", False,
            )
        else:
            try:
                result = policy_factual_audit.assess(
                    payload, candidate.text, frame, audit_config,
                    usage_collector=factual_usage_collector if factual_usage_collector is not None else critic_usage_collector,
                    draft_redactor=draft_redactor,
                )
            except Exception as exc:
                # An adapter failure cannot approve a factual assertion or
                # make an unaudited candidate eligible for first-safe reuse.
                result = policy_factual_audit.FactualAssessment(
                    None, config.extractor_model, "model_unavailable", True,
                    failure=llm_agent.model_failure(exc),
                )
            if time.monotonic() >= deadline:
                result = replace(result, verdict=None, reason="deadline_exhausted", rows=(),
                                 failure=llm_agent.ModelFailure("deadline", "deadline", "DeadlineExceeded"))
        factual_assessments.append(result)
        if result.verdict == "supported":
            return None
        return "factual_audit_unsupported" if result.verdict == "unsupported" else "factual_audit_unavailable"

    def diagnosed(candidate: SpokenPolicyReply, assessment: policy_voice_critic.CriticAssessment,
                  *, revised: bool) -> SpokenPolicyReply:
        if assessment.verdict is None:
            reason = "model_advisory_unavailable"
        elif not assessment.verdict.accept:
            reason = "model_advisory_disputed"
        else:
            reason = "model_advisory_revised" if revised else "model_advisory_accepted"
        if candidate.reason.endswith("_typed_question"):
            reason += "_typed_question"
        return record(replace(candidate, reason=reason))

    def repair_candidate(first_body: str, first_reply: SpokenPolicyReply | None,
                         flags: tuple[str, ...], *, hard_repair: bool) -> tuple[SpokenPolicyReply | None, str]:
        repair_config = phase_config(14, 650)
        if repair_config is None:
            return None, "voice_deadline_exhausted"
        try:
            revised = llm_agent.generate_reply(
                _revision_payload(model_payload, first_body, frame, first_reply, draft_redactor, flags),
                repair_config, usage_collector=usage_collector, max_attempts=1,
            ).strip()
        except Exception:
            return None, "hard_repair_unavailable"
        # Optional semantic improvement must not salvage an unsafe candidate
        # into a fragment and thereby replace an already hard-valid answer.
        _, candidate = prepare(revised, "model_advisory_revised", repair=hard_repair)
        question_from_user = bool(frame.question_goal is not None or "?" in str(payload.get("latest_user_message") or ""))
        loses_answer = (first_reply is not None and question_from_user and not frame.require_question
                        and candidate is not None and _collection_only(candidate.text, frame))
        if candidate is None or not complete(candidate) or loses_answer:
            return None, "hard_repair_invalid"
        if factual_issue := audit(candidate):
            return None, factual_issue
        return candidate, ""

    voice_config = phase_config(14, 650)
    if voice_config is None:
        return fallback_reply("voice_deadline_exhausted")
    try:
        draft = llm_agent.generate_reply(model_payload, voice_config, usage_collector=usage_collector,
                                        max_attempts=1).strip()
    except Exception:
        return fallback_reply("model_unavailable")
    draft, first = prepare(draft, "model_advisory_accepted")
    if not complete(first) or first is None:
        # Repair known hard-contract failures before any optional diagnosis.
        repaired, error = repair_candidate(draft, first, (), hard_repair=True)
        if repaired is None:
            return fallback_reply(error)
        return diagnosed(repaired, assess(repaired.text), revised=True)

    first_assessment = assess(first.text)
    if factual_issue := audit(first):
        # Factual uncertainty cannot be treated as optional style feedback.
        # G1 has no first-safe privilege unless its applicable audit passed.
        repaired, error = repair_candidate(first.text, first, (factual_issue,), hard_repair=False)
        if repaired is None:
            return fallback_reply(error)
        return diagnosed(repaired, assess(repaired.text), revised=True)
    first_style = diagnoses[-1].code_style_issues
    # One accepted critique suffices for a hard-valid, nonlegal field response.
    # Legal content and disputed/uncertain diagnostics retain their repair path.
    if accepts_brief_style_without_repair(first.text, frame, first_assessment.verdict):
        return diagnosed(first, first_assessment, revised=False)
    if not first_style and (first_assessment.verdict is None or first_assessment.verdict.accept):
        return diagnosed(first, first_assessment, revised=False)
    flags = tuple(dict.fromkeys((*first_style, *(first_assessment.verdict.flags if first_assessment.verdict else ()))))
    repaired, error = repair_candidate(first.text, first, flags, hard_repair=False)
    if repaired is None:
        # A semantic suggestion cannot veto an already hard-valid answer.
        if first_style:
            return record(replace(first, reason="model_style_first_preserved"))
        suffix = "repair_failed" if error == "hard_repair_invalid" else "repair_unavailable"
        return record(replace(first, reason="model_advisory_disputed_" + suffix))
    revised_assessment = assess(repaired.text)
    if diagnoses[-1].code_style_issues:
        return record(replace(first, reason="model_style_first_preserved"))
    return diagnosed(repaired, revised_assessment, revised=True)
