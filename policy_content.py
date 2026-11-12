"""Curated factual bounds for policy explanations, without transport or eligibility logic."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


ContentIssue = Literal["unconditional_discharge", "engineering_transfer_purpose", "overbroad_harm_exception",
                       "court_only_discharge", "bankruptcy_only_discharge", "bankruptcy_income_shortcut",
                       "categorical_partial_discharge", "phone_required_to_save_chat"]


@dataclass(frozen=True)
class VerifiedSource:
    url: str
    published_on: str
    checked_on: str
    scope: str


# These official publications support only these basic statutory limits.
# They are not a consolidated-law freshness guarantee or an individual legal assessment.
SOURCES = (
    VerifiedSource(
        "https://www.nalog.gov.ru/rn77/service/complaint_decision/16170689/",
        "2025-04-10", "2026-11-07", "Article 213.28(3),(5), current payments survive discharge",
    ),
    VerifiedSource(
        "https://epp.genproc.gov.ru/ru/proc_61/activity/legal-education/explain/e5877832/",
        "2025-06-17", "2026-11-07", "Article 213.28(5) surviving claims and 223.6 extrajudicial discharge exists",
    ),
    VerifiedSource(
        "https://sfr.gov.ru/order/law_accidents/federal_laws/~11281",
        "2025-01-22", "2026-11-07",
        "Page updated 2026-10-29, official law copy edition 2025-12-29 effective 2026-03-01; section 5 existence only",
    ),
    VerifiedSource(
        "https://vsrf.ru/files/29015/", "2020-06-11", "2026-11-08",
        "Supreme Court Plenum 6 paragraphs 30-34, Article 415 creditor forgiveness exists; no individual promise",
    ),
    VerifiedSource(
        "https://www.cbr.ru/explan/359-fz/", "2024-03-04", "2026-11-08",
        "FAQ 71, restructuring changes a consumer-credit agreement; not a definition of judicial restructuring",
    ),
    VerifiedSource(
        "https://www.vsrf.ru/files/15010/", "2015-10-13", "2026-11-08",
        "Plenum 45 paragraph 11, statutory grounds for a debtor petition; no individual eligibility assessment",
    ),
)

RESTRUCTURING_EXPLANATION = (
    "При реструктуризации потребительского кредита меняют условия его договора, "
    "например срок и график платежей. Само изменение условий не означает списания долга."
)
BANKRUPTCY_EXPLANATION = (
    "Закон предусматривает судебное и внесудебное банкротство. "
    "При судебном банкротстве возможно освобождение от исполнения требований кредиторов по правилам закона. "
    "Оно не означает автоматического прекращения любых обязательств. Например, сохраняются алименты, "
    "требования о возмещении вреда жизни или здоровью и текущие платежи. "
    "Статья 415 ГК РФ предусматривает также прощение долга кредитором при соблюдении закона, "
    "поэтому освобождение от долга не сводится к банкротству. Согласие кредитора на конкретное прощение "
    "здесь не подтверждено. "
    "Для судебного банкротства оцениваются условия закона. Само наличие или отсутствие дохода "
    "не определяет доступность процедуры. При соблюдении условий закона кредитный долг может быть "
    "списан полностью. Сохраняющиеся виды обязательств рассматриваются отдельно. "
    "Доступность конкретного порядка и результат для конкретного человека здесь не определены."
)
# The model retains the full fact context above. A degraded spoken answer uses
# this shorter projection, with conditional outcomes and qualified exceptions.
DEBT_EXPLANATION_FALLBACK = (
    "При реструктуризации меняют срок или график потребительского кредита. Сам долг остаётся.\n\n"
    "При банкротстве могут освободить от долгов. Есть исключения. Например, алименты и возмещение вреда жизни "
    "или здоровью сохраняются. Бывает и прощение долга кредитором, но оно зависит от его согласия. "
    "Что подойдёт вам, по этим данным пока неясно."
)
CONSULTATION_PURPOSE = (
    "Контакт предназначен компании для связи с клиентом и консультации по его обращению. "
    "Общаться здесь можно и без передачи данных."
    " Здесь можно задавать вопросы по кредитам и долгам."
)
CONTACT_CAPABILITY_LIMITS = (
    "Карточка подтверждает назначение и указанного получателя. Фактический звонок компании "
    "и наличие у неё других источников контакта здесь не подтверждены."
)

# Match the observed categorical claim, not the word 'bankruptcy' or a style choice.
_UNCONDITIONAL = re.compile(
    r"\bполн\w*\s+(?:прекращени\w*|освобождени\w*)\s+(?:от\s+)?"
    r"(?:(?:всех|любых)\s+)?(?:обязательств\w*|долг\w*)\b", re.I,
)
_NEGATED_ASSERTION = re.compile(
    r"\bне\s+(?:означа\w*|гарантиру\w*|обеща\w*|явля\w*|представля\w*)\s*[^.!?;:]{0,35}$"
    r"|\bне\s+$", re.I,
)
_DENIED_RESULT = re.compile(r"^\s+не\s+(?:гарантиру\w*|предусмотр\w*)\b", re.I)
_DENIED_ENGINEERING_PURPOSE = re.compile(
    r"\bне\s+(?:(?:нужн\w*|предназначен\w*)\s+)?(?:для|чтобы)\b", re.I,
)
_ENGINEERING_PURPOSE = re.compile(
    r"\b(?:передач\w*|контакт\w*|данн\w*|сведени\w*)[^.!?;:]{0,65}"
    r"\b(?:для\s+проверк\w*\s+связи|чтобы\s+проверить\s+связь)\b", re.I,
)
_HARM_REQUIREMENT = re.compile(r"\bвозмещени\w*\s+(?:моральн\w*\s+)?вред\w*\b", re.I)
_SURVIVING_REQUIREMENT = re.compile(
    r"\b(?:сохраняются|сохраняется|остаются|оста[её]тся)\b|\bне\s+(?:списыва\w*|прекраща\w*)\b", re.I,
)
_PAYMENT_OBLIGATION = re.compile(
    r"\b(?:прид[её]тся|нужно|надо|необходимо|обязан[аоы]?|должен|должна|должны|должно)\s+"
    r"(?:(?!(?:не|ли)\b)[\w-]+\s+){0,4}"
    r"(?:платить|выплачивать|оплачивать|погашать|возмещать)\b", re.I,
)
_NEGATED_PAYMENT_OBLIGATION = re.compile(r"\bне\s+(?:буд(?:у|ешь|ет|ем|ете|ут)\s+)?$", re.I)
_DENIED_PAYMENT_ASSERTION = re.compile(
    r"\b(?:не\s+(?:утвержд\w*|говор\w*|счита\w*)|"
    r"(?:нельзя|неверно|ошибочно)\s+(?:говорить|утверждать|считать))\b", re.I,
)
_DISCHARGE_EXCEPTION_LIST = re.compile(
    r"\b(?:освобождени\w*\s+от\s+(?:долг\w*|обязательств\w*|требован\w*)|"
    r"списани\w*\s+(?:долг\w*|задолжен\w*))[^.!?]{0,75}?"
    r"\b(?P<marker>кроме(?!\s+того\b)|за\s+исключением)\b", re.I,
)
_QUALIFIED_HARM = re.compile(r"^\s*[,—–-]?\s*(?:причин[её]н\w*\s+)?(?:жизни|здоровью)\b", re.I)
_DENIED_UNIVERSAL_HARM = re.compile(
    r"\bне\s+(?:всякое|любое|каждое)\s+$|"
    r"\bнельзя\s+(?:сказать|утверждать)\s*,?\s*(?:что\s+)?(?:любое|всякое|каждое)\s+$", re.I,
)
_COURT_ONLY = re.compile(
    r"\b(?:списани\w*|банкротств\w*|освобождени\w*)[^.!?]{0,55}"
    r"(?<!не )\b(?:только|исключительно)\s+(?:через\s+суд|в\s+судебном\s+порядке)\b", re.I,
)
_BANKRUPTCY_ONLY = re.compile(
    r"\b(?:списани\w*|освобождени\w*)[^.!?]{0,55}(?<!не )\b(?:только|исключительно)\s+"
    r"(?:(?:через|в)\s+)?(?:процедур\w*\s+)?банкротств\w*\b", re.I,
)
_BANKRUPTCY_INCOME_DEFINITION = re.compile(
    r"\bбанкротств\w*\s*(?:[—–-]\s*)?(?:это\s+|(?:возможно|доступно)\s+только\s+)?"
    r"(?P<predicate>(?:только\s+)?(?:когда|если)\s+(?:(?:совсем|вообще|уже)\s+)?"
    r"(?:платить\s+(?:совсем\s+)?нечем|нечем\s+платить|нет\s+(?:никакого\s+)?доход\w*))", re.I,
)
_PARTIAL_ONLY_DISCHARGE = re.compile(
    r"\b(?:списани\w*|освобождени\w*)\b[^.!?;\n]{0,120}?"
    r"(?P<predicate>не\s*полн(?:ое|ым)\b|(?:только|всегда|лишь)\s+частичн\w*\b)", re.I,
)
_DENIED_DEFINITION = re.compile(
    r"\b(?:(?:нельзя|неверно|ошибочно)\s+(?:говорить|утверждать|считать)|"
    r"не\s+(?:утверждаю|говорю|считаю))\s*,?\s*(?:что\s*)?$|"
    r"\b(?:неверно|ошибочно)\s*:\s*$", re.I,
)


def _categorical_bankruptcy_claim(pattern: re.Pattern[str], text: str) -> bool:
    """Keep quoted, conditional and qualified explanations outside a blanket definition."""
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"\n]*"', "", text)
    # A following invitation is a separate speech act, not a question about the claim.
    unquoted = re.sub(r",\s*(?=(?:хотите|готовы|давайте)\b)", "\n", unquoted, flags=re.I)
    for sentence in re.findall(r"[^.!?;\n]+[.!?;]?", unquoted):
        if "?" in sentence or re.match(r"\s*(?:если|допустим|предположим)\b", sentence, re.I):
            continue
        for claim in pattern.finditer(sentence):
            prefix = sentence[:claim.start()]
            predicate_prefix = sentence[:claim.start("predicate")]
            if (_DENIED_DEFINITION.search(prefix) or _NEGATED_ASSERTION.search(prefix)
                    or _DENIED_DEFINITION.search(predicate_prefix)
                    or re.search(r"\bне\s*$", predicate_prefix, re.I)
                    or re.search(r"\bне\s+(?:бывает|является|означает|сводится)\s*$", predicate_prefix, re.I)):
                continue
            # Partial relief can describe a possible case without defining every discharge.
            if (pattern is _PARTIAL_ONLY_DISCHARGE and claim.group("predicate").lower().startswith("не")
                    and re.search(r"\b(?:может\s+быть|иногда|в\s+отдельных\s+случаях|бывает)\b",
                                  predicate_prefix, re.I)):
                continue
            return True
    return False


def _bankruptcy_only_discharge(text: str) -> bool:
    """One statutory counterexample rejects an exclusive general definition, not bankruptcy-scoped explanations."""
    for clause in re.split(r"[.!?;\n]", text):
        if re.match(r"\s*(?:при|в)\s+(?:(?:судебн\w*|внесудебн\w*|процедур\w*)\s+)?банкротств\w*\b",
                    clause, re.I):
            continue
        for claim in _BANKRUPTCY_ONLY.finditer(clause):
            if re.search(r"\b(?:нельзя|неверно|ошибочно)\s+(?:говорить|утверждать|считать)?"
                         r"\s*,?\s*(?:что\s+)?$", clause[:claim.start()], re.I):
                continue
            return True
    return False


def _court_only_discharge(text: str) -> bool:
    """Distinguish a universal court-only claim from an explicitly judicial scope or denial."""
    for clause in re.split(r"[.!?;\n]", text):
        if re.match(r"\s*(?:при|в)\s+судебн\w*\s+(?:банкротств\w*|процедур\w*|порядке)\b", clause, re.I):
            continue
        for claim in _COURT_ONLY.finditer(clause):
            if re.search(r"\b(?:нельзя|неверно|ошибочно)\s+(?:говорить|утверждать|считать)?"
                         r"\s*,?\s*(?:что\s+)?$", clause[:claim.start()], re.I):
                continue
            return True
    return False


def _asserted_payment_obligation(clause: str) -> bool:
    """An asserted duty to keep paying is the same survival claim in ordinary speech."""
    if _DENIED_PAYMENT_ASSERTION.search(clause):
        return False
    return any(not _NEGATED_PAYMENT_OBLIGATION.search(clause[:match.start()])
               for match in _PAYMENT_OBLIGATION.finditer(clause))


def _overbroad_harm_exception(text: str) -> bool:
    """Only the asserted universal survival of an unqualified harm claim is rejected."""
    unquoted = re.sub(r"""«[^»]*»|"[^"]*"|'[^']*'""", " ", text)
    # A denial or a payment duty in one contrasted clause cannot qualify another.
    unquoted = re.sub(r"\b(?:но|однако|зато|а(?!\s+также\b))\b", "\n", unquoted, flags=re.I)
    for clause in re.findall(r"[^.!?;\n]+[.!?]?", unquoted):
        if "?" in clause or re.search(r"\b(?:если|допустим|предположим)\b", clause, re.I):
            continue
        surviving = _SURVIVING_REQUIREMENT.search(clause)
        payment_obligation = _asserted_payment_obligation(clause)
        exception = _DISCHARGE_EXCEPTION_LIST.search(clause)
        if exception is not None and (
            "?" in clause or re.match(r"\s*(?:(?:а|но)\s+)?(?:если|допустим|предположим)\b", clause, re.I)
            or _NEGATED_ASSERTION.search(clause[:exception.start()])
            or re.search(r"\b(?:нельзя|неверно|ошибочно)\s+(?:говорить|утверждать|считать)\b[^.!?]{0,25}$",
                         clause[:exception.start()], re.I)
            or re.search(r"\bне\s*$", clause[:exception.start("marker")], re.I)
        ):
            exception = None
        if surviving is None and exception is None and not payment_obligation:
            continue
        if re.search(r"\bне\s+(?:всегда|обязательно)\s+(?:сохраня\w*|оста\w*)\b", clause, re.I):
            continue
        for harm in _HARM_REQUIREMENT.finditer(clause):
            # Without a survival predicate, only a member of the asserted exception list qualifies.
            if surviving is None and not payment_obligation and (exception is None or harm.start() < exception.end()):
                continue
            if _DENIED_UNIVERSAL_HARM.search(clause[:harm.start()]):
                continue
            if "моральн" in harm.group().casefold() or _QUALIFIED_HARM.match(clause[harm.end():]):
                continue
            return True
    return False


def _asserted(pattern: re.Pattern[str], text: str) -> bool:
    """A preceding denial applies only within its own clause, never a later disclaimer."""
    for clause in re.split(r"[.!?;:\n]", text):
        for match in pattern.finditer(clause):
            if (_NEGATED_ASSERTION.search(clause[:match.start()])
                    or _DENIED_RESULT.search(clause[match.end():])):
                continue
            # Denial of this technical purpose does not deny an unrelated later claim.
            if pattern is _ENGINEERING_PURPOSE and _DENIED_ENGINEERING_PURPOSE.search(match.group()):
                continue
            return True
    return False


def _phone_storage_dependency(text: str) -> bool:
    """Bind a contact requirement/request to its own storage-purpose clause."""
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"\n]*"', "", text)
    for sentence in re.split(r"[.!?;]|\b(?:но|однако|зато)\b", unquoted, flags=re.I):
        if _DENIED_PAYMENT_ASSERTION.search(sentence):
            continue
        contact = re.search(r"\b(?:телефон\w*|контакт\w*|номер)\b", sentence, re.I)
        if contact is None:
            continue
        storage_purpose = re.search(
            r"\b(?:чтобы\s+сохранить|для\s+сохранения)\b[^.!?;]{0,80}"
            r"\b(?:ответ\w*|анкет\w*|разговор\w*|переписк\w*|сведени\w*|данн\w*)\b", sentence, re.I,
        )
        if storage_purpose:
            request = re.search(r"\b(?:оставьте|напишите|сообщите|укажите)\b", sentence, re.I)
            required = re.search(r"\b(?:нужен|нужны|необходим\w*|требуется)\b", sentence, re.I)
            denied_requirement = bool(required and re.search(r"\bне\s*$", sentence[:required.start()], re.I))
            # An explicit denial of the storage dependency may be followed by
            # a separate, legitimate contact request for communication.
            if (denied_requirement and request and re.search(r"\bдля\s+связи\b", sentence[request.end():], re.I)
                    and not re.search(r"сохран", sentence[request.end():], re.I)):
                request = None
            if ((request and not re.search(r"\bне\s*$", sentence[:request.start()], re.I))
                    or (required and not denied_requirement)):
                return True
        # A stated inability without the contact is the same dependency; an
        # acknowledgement that something is already saved never matches it.
        if (re.search(r"\bбез\s+(?:номера(?:\s+телефона)?|телефона)\b", sentence, re.I)
                and re.search(r"\bне\s+(?:смогу|могу|получится)\s+сохранить\s+(?:ваши\s+)?"
                              r"(?:ответ\w*|анкет\w*|разговор\w*|переписк\w*)\b", sentence, re.I)):
            return True
    return False


def content_issues(text: str, *, mode: str, question_goal: str | None) -> tuple[ContentIssue, ...]:
    """Closed factual failures, scoped to the relevant conversational domain."""
    issues: list[ContentIssue] = []
    if _phone_storage_dependency(text):
        issues.append("phone_required_to_save_chat")
    if mode == "debt" and _asserted(_UNCONDITIONAL, text):
        issues.append("unconditional_discharge")
    if mode == "debt" and _overbroad_harm_exception(text):
        issues.append("overbroad_harm_exception")
    if mode == "debt" and _court_only_discharge(text):
        issues.append("court_only_discharge")
    if mode == "debt" and _bankruptcy_only_discharge(text):
        issues.append("bankruptcy_only_discharge")
    if mode == "debt" and _categorical_bankruptcy_claim(_BANKRUPTCY_INCOME_DEFINITION, text):
        issues.append("bankruptcy_income_shortcut")
    if mode == "debt" and _categorical_bankruptcy_claim(_PARTIAL_ONLY_DISCHARGE, text):
        issues.append("categorical_partial_discharge")
    if (mode == "onloader" and question_goal == "offer_information_purpose"
            and _asserted(_ENGINEERING_PURPOSE, text)):
        issues.append("engineering_transfer_purpose")
    return tuple(issues)


def corrective_facts(issues: tuple[ContentIssue, ...]) -> tuple[str, ...]:
    """Return evidence-backed guidance for repair, without prescribing the reply wording."""
    facts: list[str] = []
    if any(issue in issues for issue in ("unconditional_discharge", "court_only_discharge", "bankruptcy_only_discharge",
                                        "bankruptcy_income_shortcut", "categorical_partial_discharge")):
        facts.append(BANKRUPTCY_EXPLANATION)
    if "overbroad_harm_exception" in issues:
        facts.append("Для исключения из списания важен вид требования. "
                     "Примеры сохраняющихся требований — возмещение вреда жизни или здоровью "
                     "и морального вреда. Нельзя представлять любое возмещение вреда как такое исключение.")
    if "engineering_transfer_purpose" in issues:
        facts.append(CONSULTATION_PURPOSE)
    return tuple(facts)
