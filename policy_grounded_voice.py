"""Bounded policy speech with independently grounded user acts and outgoing claims."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import time
import re
import logging
from typing import Any

from config import AppConfig
from dialogue_frame import (
    DialogueFrame, constrain_collection, debt_amount_reply_issue, discussion_followup_issue,
    is_plain_debt_knowledge_request,
)
import debt_policy
import llm_agent
import policy_factual_audit
import policy_semantic_analysis
import policy_semantics
import policy_voice
import policy_voice_critic
import prompts
from policy_content import content_issues
from routing import Plan


VERSION = "policy-grounded-voice-v16-simple-debt-conversation"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticChannelDiagnostic:
    user_valid: bool
    outgoing_safe: bool
    strict_safe: bool
    user_issues: tuple[str, ...]
    claims_issues: tuple[str, ...]


@dataclass(frozen=True)
class GroundedReply:
    spoken: policy_voice.SpokenPolicyReply
    applied_user_acts: tuple[str, ...]
    paused: bool
    effective_frame: DialogueFrame
    catalog_recovery_sources: tuple[policy_factual_audit.SourceFact, ...] = ()
    channel_diagnostics: tuple[SemanticChannelDiagnostic, ...] = ()


def _catalog_faq_recovery(latest: str, frame: DialogueFrame) -> tuple[policy_factual_audit.SourceFact, ...]:
    """Select exact public facts only for a bounded, code-recognized debt FAQ.

    This is degraded output after failed validation, never a generation shortcut
    or evidence about the client's eligibility, permission or application facts.
    """
    if frame.mode != "debt" or frame.offer_present or frame.closing:
        return ()
    # The new pre-intent path is scoped to the application's actual clarify
    # question. Other legacy frames keep their existing verified fallback.
    if "intent" not in frame.known_fields and frame.expected != "intent":
        return ()
    if any(mark in latest for mark in ('"', "'", "«", "»", "“", "”")):
        return ()
    parts = [match.group().strip() for match in re.finditer(r"[^.!?;]+[.!?;]?", latest)]
    questions = [part for part in parts if part.endswith("?")]
    if len(questions) != 1:
        return ()
    remaining = " ".join(part for part in parts if part != questions[0])
    # A simultaneous proven No may accompany the FAQ, but arbitrary extra
    # instructions or reported speech do not select this recovery response.
    if remaining and debt_policy.pure_debt_turn_act(remaining, active_debt=True) != "decline_transfer":
        return ()
    question = re.sub(r"^а\s+", "", questions[0].casefold()).strip()
    question = re.sub(r"^(?:подскажите|объясните|поясните)[,\s]+", "", question)
    income_question = re.fullmatch(
        r"(?:если\s+я\s+(?:официально\s+)?работаю(?:\s+официально)?\s*,?\s*"
        r"(?:мне\s+)?(?:вообще\s+)?(?:может\s+)?(?:подойти|быть\s+доступно)\s+банкротство|"
        r"(?:можно|доступно)\s+ли\s+(?:мне\s+)?банкротство\s*,?\s*если\s+я\s+(?:официально\s+)?работаю|"
        r"влияет\s+ли\s+(?:наличие|отсутствие)\s+дохода\s+на\s+доступность\s+банкротства)\s*\?", question,
    )
    if income_question:
        # A personal availability question still requires confirmed own intent;
        # pure definitions below neither establish that intent nor change it.
        if "intent" not in frame.known_fields:
            return ()
        required = (
            "Для судебного банкротства оцениваются условия закона.",
            "Само наличие или отсутствие дохода не определяет доступность процедуры.",
            "Доступность конкретного порядка и результат для конкретного человека здесь не определены.",
        )
    elif is_plain_debt_knowledge_request(re.sub(r"\bдолгов\b", "долга", question)) and "реструктуризац" in question:
        required = (
            "При реструктуризации потребительского кредита меняют условия его договора, например срок и график платежей.",
            "Само изменение условий не означает списания долга.",
        )
        if "списан" in question or "банкротств" in question:
            required += (
                "При судебном банкротстве возможно освобождение от исполнения требований кредиторов по правилам закона.",
                "Оно не означает автоматического прекращения любых обязательств.",
                "Доступность конкретного порядка и результат для конкретного человека здесь не определены.",
            )
    else:
        return ()
    # Matching against the current shared catalog prevents a stale hardcoded
    # answer surviving removal or revision of its underlying verified facts.
    catalog = policy_factual_audit._source_catalog(prompts.policy_verified_context(frame))
    by_text = {fact.text: fact for fact in catalog}
    return tuple(by_text[text] for text in required) if all(text in by_text for text in required) else ()


def _known_debt_amount_recovery(latest: str, frame: DialogueFrame, facts: object,
                                reason: str) -> policy_voice.SpokenPolicyReply | None:
    """Read back one confirmed fact after failure, without interpreting a decision."""
    if (frame.mode != "debt" or frame.offer_present or frame.closing or frame.confirming
            or frame.expected == "debts.total" or not {"intent", "debts.total"} <= frame.known_fields
            or not isinstance(facts, dict)):
        return None
    # Only a whole question about our current record qualifies. Numbers, quotes,
    # instructions, new amounts and third-party balances cannot select this path.
    question = latest.casefold().replace("ё", "е").strip()
    if not re.fullmatch(
            r"(?:а\s+)?(?:какую\s+сумму\s+(?:моего\s+)?долга\s+вы\s+(?:сейчас\s+)?учитываете|"
            r"какая\s+сумма\s+(?:моего\s+)?долга\s+у\s+вас\s+(?:сейчас\s+)?(?:указана|записана)|"
            r"сколько\s+долга\s+у\s+вас\s+(?:сейчас\s+)?записано)\s*\?", question):
        return None
    debts = facts.get("debts")
    amount = debts.get("total") if isinstance(debts, dict) else None
    if (debt_policy.debt_band(amount) == "unknown" or not isinstance(amount, (int, float))
            or isinstance(amount, bool) or (isinstance(amount, float) and not amount.is_integer())):
        return None
    # This is a state projection, never unchecked G text or an F opinion.
    body = f"Сейчас учитываю долг {int(amount):,} рублей.".replace(",", " ")
    checked = policy_voice._model_reply(body, frame, f"code_known_debt_amount:{reason}")
    if checked is None or checked.text != body or checked.asked_field is not None:
        return None
    return replace(checked, source="fallback")


def _practical_knowledge_recovery(latest: str, frame: DialogueFrame,
                                 reason: str) -> policy_voice.SpokenPolicyReply | None:
    """Project a missing practical source, not a typical list or unchecked G."""
    if (frame.mode != "debt" or frame.offer_present or frame.closing
            or "intent" not in frame.known_fields):
        return None
    question = " ".join(latest.casefold().replace("ё", "е").split())
    combined = re.fullmatch(
        r"(?:а\s+)?что\s+специалист\s+будет\s+делать(?:\s+дальше)?\s+и\s+что\s+мне\s+подготовить\s*\?",
        question,
    )
    documents = re.fullmatch(
        r"(?:а\s+)?какие\s+документы\s+(?:мне\s+)?(?:нужны|понадобятся)"
        r"(?:\s+для\s+(?:консультации|банкротства))?\s*\?", question,
    )
    # Only the whole practical question qualifies: no reported speech, new
    # facts, instructions, mixed decisions or legal procedure advice.
    if combined is None and documents is None:
        return None
    try:
        public = prompts.policy_verified_context(frame)
        # A future supplied list/service fact must not be contradicted by a
        # stale "unknown" answer. Reuse the same necessary coverage seam as F.
        if any(not isinstance(text, str) or policy_factual_audit._document_claims(text)
               or policy_factual_audit._actor_claims(text) for text in public.values()):
            return None
    except Exception:
        return None
    body = ("Пока не могу точно сказать, как будет работать специалист и какие документы понадобятся — "
            "этих данных у меня нет." if combined else
            "Подтверждённого списка документов у меня сейчас нет, поэтому называть его наугад не буду.")
    checked = policy_voice._model_reply(body, frame, f"code_practical_context_unavailable:{reason}")
    if checked is None or checked.text != body or checked.asked_field is not None:
        return None
    return replace(checked, source="fallback")


def _caller_owned_debt_recovery(fallback: str, frame: DialogueFrame, facts: object,
                               reason: str) -> policy_voice.SpokenPolicyReply | None:
    """Retain only canonical policy-owned speech, never arbitrary caller text.

    A degraded model interpretation may stay paused while the application asks
    its own intent question or states its unavailable transfer capability.
    Neither response interprets the latest message or grants a new action.
    """
    if (frame.mode != "debt" or frame.offer_present or frame.closing
            or frame.conversation_declined or "client.phone" in frame.refused_fields):
        return None
    if frame.expected == "intent" and "intent" not in frame.known_fields:
        canonical = debt_policy.render_debt_reply(
            debt_policy.DebtPolicyState(mode="clarify"), Plan(action="ask"), {},
        )
        source = "code_intent_clarification"
    elif (frame.status == "collected_held" and frame.expected is None and not frame.confirming
          and {"intent", "debts.total", "client.region_class", "client.name", "client.phone"} <= frame.known_fields
          and isinstance(facts, dict) and debt_policy.debt_band((facts.get("debts") or {}).get("total"))
          in {"190k_to_299999", "300k_plus"}):
        canonical = debt_policy.render_debt_reply(
            debt_policy.DebtPolicyState(), Plan(action="no_fit", transition="debt_collected"), facts,
        )
        source = "code_debt_hold"
    else:
        return None
    if fallback.strip() != canonical:
        return None
    body = canonical
    if source == "code_debt_hold" and frame.known_phone_reference and frame.transfer_blocked:
        # Project the known contact and blocked action for this exact turn,
        # after proving the caller's current canonical hold. No consent receipt.
        body = ("Номер уже есть, повторять его не нужно. "
                "Передача заявки по долгам из этого чата пока не подключена.")
        source = "code_known_contact_hold"
    checked = policy_voice._model_reply(body, frame, f"{source}:{reason}")
    if checked is None or checked.text != body:
        return None
    return replace(checked, source="fallback")


def render(payload: dict[str, Any], config: AppConfig, *, frame: DialogueFrame,
           fallback: str, usage_collector: Callable[..., None] | None = None,
           critic_usage_collector: Callable[..., None] | None = None,
           factual_usage_collector: Callable[..., None] | None = None,
           draft_redactor: Callable[[str], str] | None = None,
           finalize_reply: Callable[[str], str] | None = None) -> GroundedReply:
    """Analyse each final draft before disclosure, retaining at most two G+C pairs."""
    deadline = time.monotonic() + min(config.request_timeout, 40)
    current = frame
    assessments: list[policy_voice_critic.CriticAssessment] = []
    diagnoses: list[policy_voice.ReplyDiagnostic] = []
    factual_assessments: list[policy_factual_audit.FactualAssessment] = []
    channel_diagnostics: list[SemanticChannelDiagnostic] = []
    recovery_sources: tuple[policy_factual_audit.SourceFact, ...] = ()
    # Unambiguous own refusals are a deterministic state transition. A provider
    # outage cannot erase them. Broader language remains a grounded model candidate.
    code_acts = policy_semantics.explicit_conversational_declines(
        str(payload.get("latest_user_message") or ""), frame,
    )
    acts = list(code_acts)
    if "decline_transfer" in code_acts:
        current = replace(current, conversation_declined=True)
    if "decline_phone" in code_acts:
        current = replace(current, refused_fields=current.refused_fields | {"client.phone"})
    paused = frame.conversation_paused and not code_acts
    current = replace(current, conversation_paused=paused)
    first_safe: policy_voice.SpokenPolicyReply | None = None
    first_frame: DialogueFrame | None = None
    have_user_interpretation = bool(code_acts)
    repair_flags: tuple[str, ...] = ()
    skipped_code_analysis = False
    previous_reply = next((str(item.get("content") or "") for item in reversed(payload.get("short_history") or [])
                           if isinstance(item, dict) and item.get("role") == "assistant"), "")
    latest = str(payload.get("latest_user_message") or "")
    # These whole inputs have no unknown fact or affirmative decision to infer.
    proven_debt_act = (debt_policy.pure_debt_turn_act(latest, active_debt="intent" in frame.known_fields)
                       if frame.mode == "debt" else None)

    def code_repair_flags(text: str) -> tuple[str, ...]:
        flags: list[str] = list(content_issues(text, mode=current.mode, question_goal=current.trusted_question_goal))
        for issue in (debt_amount_reply_issue(text, current),
                      policy_voice.unavailable_debt_continuation_issue(text, current),
                      policy_voice.unrequested_cancellation_issue(text, current),
                      discussion_followup_issue(text, current)):
            if issue:
                flags.append(issue)
        if constrain_collection(text, current).removed_fields:
            flags.append("unallowed_collection")
        return tuple(dict.fromkeys(flags))

    def remaining_config(seconds: int, tokens: int) -> AppConfig | None:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            return None
        return replace(config, enable_pii_masking=True, request_timeout=min(seconds, remaining),
                       max_tokens=min(config.max_tokens, tokens))

    def finish(spoken: policy_voice.SpokenPolicyReply) -> GroundedReply:
        nonlocal paused, current
        if code_acts:
            paused = False
            current = replace(current, conversation_paused=False)
        recorded = replace(spoken, critic_model=config.effective_policy_semantic_model,
                           critic_calls=sum(item.attempted for item in assessments),
                           critic_assessments=tuple(assessments), code_user_acts=code_acts,
                           diagnostic_assessments=tuple(diagnoses),
                           factual_assessments=tuple(factual_assessments))
        return GroundedReply(recorded, tuple(acts), paused, current, recovery_sources, tuple(channel_diagnostics))

    def trusted_fallback(reason: str) -> GroundedReply:
        nonlocal recovery_sources
        # A stored fact remains answerable during an interpretation outage;
        # the existing pause and negative authorities are deliberately retained.
        amount_reply = _known_debt_amount_recovery(latest, current, payload.get("facts"), reason)
        if amount_reply is not None:
            return finish(amount_reply)
        practical_reply = _practical_knowledge_recovery(latest, current, reason)
        if practical_reply is not None:
            return finish(practical_reply)
        recovery_sources = _catalog_faq_recovery(latest, current)
        if recovery_sources:
            # No unverified G text or fabricated C/F verdict enters this body.
            body = " ".join(fact.text for fact in recovery_sources)
            checked = policy_voice._model_reply(body, current, f"catalog_faq:{reason}")
            if checked is not None and checked.text == body and checked.asked_field is None:
                logger.info("policy_catalog_faq_recovery", extra={
                    "reason": reason, "source_ids": [fact.id for fact in recovery_sources],
                })
                return finish(replace(checked, source="fallback"))
            recovery_sources = ()
        owned = _caller_owned_debt_recovery(fallback, current, payload.get("facts"), reason)
        if owned is not None:
            return finish(owned)
        # A paused interpretation never licenses an acknowledgement of refusal.
        quiet = "Можем продолжить разговор здесь."
        hinted_goal = current.question_goal_origin == "model_hint"
        quiet_fallback = paused or current.contact_declined or hinted_goal
        body = quiet if quiet_fallback else fallback
        # A hint may select an explanation made exclusively from verified card
        # facts. Its provenance remains unchanged, so it grants no collection or action.
        fallback_frame = current
        fallback_question = None if quiet_fallback else fallback_frame.allowed_field
        result = policy_voice._fallback_reply(fallback_frame, body, fallback_question, reason)
        return finish(result)

    for attempt in range(2):
        generation_config = remaining_config(14, 650)
        if generation_config is None:
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            paused = paused or not assessments
            current = replace(current, conversation_paused=paused)
            break
        model_payload = dict(payload)
        if current.brief_field_reply:
            model_payload["policy_reply_role"] = "brief_field" if attempt == 0 else "brief_field_repair"
        elif proven_debt_act is not None:
            model_payload["policy_reply_role"] = "debt_act"
        else:
            model_payload.pop("policy_reply_role", None)
        model_payload["turn_instruction"] = (
            prompts.policy_turn_instruction(current) if attempt == 0 else
            prompts.policy_regeneration_instruction(current, repair_flags)
        )
        if current.conversation_paused:
            model_payload["turn_instruction"] += (
                "\nВыбор передачи пока не установлен. Продолжай разговор без просьб о контакте, "
                "согласии и без утверждения, что отказ получен или сохранён."
            )
        try:
            if (attempt and current.brief_field_reply and first_safe is not None and first_frame == current):
                # Reuse the code-owned style gate and redaction as part of the
                # optional attempt, preserving first-safe if preparation fails.
                model_payload = policy_voice._revision_payload(
                    model_payload, first_safe.text, current, first_safe, draft_redactor, repair_flags,
                )
            draft = llm_agent.generate_reply(model_payload, generation_config,
                                            usage_collector=usage_collector, max_attempts=1).strip()
            if finalize_reply is not None:
                draft = finalize_reply(draft)
            draft = policy_voice.normalize_brief_field_reply(draft, current, latest)
        except Exception:
            # An optional style attempt has no new authority to establish.
            # Keep G1 on provider failure without diagnosing an empty G2.
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            draft = ""
        if (proven_debt_act == "entry" and current.status == "collected_held"
                and current.allowed_field is None and not current.offer_present and not current.confirming
                and not any(mark in draft for mark in ('"', "'", "«", "»", "“", "”"))):
            # Remove only complete forbidden collection acts. A remaining model
            # reaction still needs all hard, semantic and applicable factual checks.
            collection = constrain_collection(draft, current)
            retained = policy_voice._model_reply(collection.text, current, "model_semantic_checked")
            if collection.removed_fields and retained is not None:
                draft = retained.text
                logger.info("policy_entry_collection_omitted", extra={"attempt": attempt + 1})
        # Normalize before analysis. No appending, salvage or cleanup follows approval.
        candidate = policy_voice._model_reply(draft, current, "model_semantic_checked")
        if (candidate is None and attempt and current.mode == "debt"
                and channel_diagnostics and channel_diagnostics[0].user_valid):
            # C1 already bound the same incoming message. A code-invalid G2
            # cannot be served; re-auditing it adds no outgoing permission.
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            logger.info("policy_invalid_repair_analysis_skipped", extra={"attempt": attempt + 1})
            return trusted_fallback("code_invalid_repair")
        if candidate is not None:
            draft = candidate.text
            if (current == frame and not assessments and not code_acts
                    and policy_voice.is_code_checked_brief_reply(draft, current)):
                # No model assessment is fabricated for code-certified neutral speech.
                logger.info("policy_brief_code_approval", extra={"critic_skipped": True})
                return finish(replace(candidate, reason="model_code_checked_brief"))
        proven_contact_reference = (current == frame and current.mode == "debt" and current.known_phone_reference
                                    and {"intent", "client.phone"} <= current.known_fields)
        if candidate is None and (skipped_code_analysis or (
                not attempt and not assessments and not current.offer_present and current.question_goal is None
                and (proven_debt_act is not None or proven_contact_reference or (
                    current == frame and current.brief_field_reply and not code_acts and not paused
                    and not policy_factual_audit.requires_audit(current, draft))))):
            # Code proves the incoming act and has already rejected the output.
            # Discarded output cannot gain authority from a semantic assessment.
            repair_flags = code_repair_flags(draft) or ("hard_contract",)
            event = ("policy_debt_code_rejection" if proven_debt_act is not None or proven_contact_reference
                     else "policy_brief_code_rejection")
            logger.info(event, extra={
                "code_repair_flags": repair_flags, "attempt": attempt + 1, "analysis_skipped": True,
                "known_contact_proof": proven_contact_reference,
            })
            if skipped_code_analysis:
                return trusted_fallback("known_contact_code_repair_invalid" if proven_contact_reference
                                        else "brief_code_repair_invalid")
            skipped_code_analysis = True
            continue
        # R1 under the eight-second cap had more validator outages on the same22;
        # provider load also changed. Early F rejection still avoids extra calls.
        analysis_config = remaining_config(10, 1000)
        if analysis_config is None:
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            paused = True
            current = replace(current, conversation_paused=True)
            break
        analysis_config = replace(analysis_config, max_tokens=1000)
        analysis = policy_semantic_analysis.analyze(
            payload, draft, current, analysis_config, usage_collector=critic_usage_collector,
            draft_redactor=draft_redactor,
        )
        assessments.append(analysis.assessment)
        # Preserve the raw model judgment for evaluation. Code contradictions
        # contribute to the effective diagnostic without rewriting that judgment.
        style_issues = policy_voice.field_receipt_style_issues(draft, current, latest)
        diagnoses.append(policy_voice.ReplyDiagnostic(
            analysis.code_content_issues, analysis.effective_verdict, style_issues,
        ))
        if (analysis.assessment.failure is not None
                and analysis.assessment.failure.kind in {"http", "timeout", "connection", "read", "deadline"}):
            # Rewording an answer cannot repair an unavailable validator.
            # Keep any earlier verified answer and every code-owned refusal.
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            paused = frame.conversation_paused or not (have_user_interpretation or proven_debt_act is not None)
            current = replace(current, conversation_paused=paused)
            return trusted_fallback("semantic_provider_unavailable")
        if analysis.authority is None:
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            paused = True
            current = replace(current, conversation_paused=True)
            return trusted_fallback("semantic_context_unavailable")
        decision = policy_semantics.evaluate_semantics(analysis.authority, analysis.semantics)
        channel_diagnostics.append(SemanticChannelDiagnostic(
            decision.user_valid, decision.outgoing_safe, decision.safe, decision.user_issues, decision.claims_issues,
        ))
        # User acts remain applicable when a separate outgoing-claim channel fails.
        for act in decision.applied_user_acts:
            if act.kind not in acts:
                acts.append(act.kind)
        # Both analyses concern the same latest message. A valid C2 can recover
        # the uncertainty introduced by C1, while a persisted incoming pause stays.
        # Conversely, a failed optional C2 cannot erase the valid C1 interpretation.
        paused = frame.conversation_paused or (not decision.user_valid and not have_user_interpretation)
        have_user_interpretation = have_user_interpretation or decision.user_valid
        if acts:
            paused = False
        current = replace(decision.effective_frame, conversation_paused=paused)
        if current.mode == "debt" and not decision.user_valid:
            # Rewording G cannot repair an invalid interpretation of the same
            # incoming message. Retain code-owned No and recover conservatively.
            return trusted_fallback("debt_user_interpretation_unavailable")
        if time.monotonic() >= deadline:
            if first_safe is not None and first_frame == current:
                return finish(replace(first_safe, reason="model_semantic_first_preserved"))
            return trusted_fallback("semantic_deadline_exhausted")
        checked = policy_voice._model_reply(draft, current, "model_semantic_checked")
        complete = checked is not None and not (
            current.require_question and current.allowed_field is not None and checked.asked_field is None
        )
        # A newly effective frame can change normalization. That new body needs
        # its own analysis, even when the difference concerns paragraph layout.
        factual_pass = (complete and checked is not None and checked.text == draft
                        and decision.safe and decision.user_valid)
        factual_flag = None
        if factual_pass and config.policy_factual_audit_enabled and policy_factual_audit.requires_audit(current, draft):
            # The small factual task has no user acts or application authority.
            # Only a supported result can qualify this draft for preservation.
            audit_config = remaining_config(policy_factual_audit.MAX_PHASE_SECONDS, policy_factual_audit.MAX_REPLY_TOKENS)
            if audit_config is None:
                audit = policy_factual_audit.FactualAssessment(
                    None, config.extractor_model, "deadline_exhausted", False,
                )
            else:
                try:
                    audit = policy_factual_audit.assess(
                        payload, draft, current, audit_config, usage_collector=factual_usage_collector,
                        draft_redactor=draft_redactor,
                    )
                except Exception as exc:
                    audit = policy_factual_audit.FactualAssessment(
                        None, config.extractor_model, "model_unavailable", True,
                        failure=llm_agent.model_failure(exc),
                    )
                if time.monotonic() >= deadline:
                    audit = replace(audit, verdict=None, reason="deadline_exhausted", rows=(),
                                    failure=llm_agent.ModelFailure("deadline", "deadline", "DeadlineExceeded"))
            factual_assessments.append(audit)
            if audit.verdict != "supported":
                if (audit.failure is not None and audit.failure.kind in {
                        "http", "timeout", "connection", "read", "deadline"}):
                    # Rephrasing G cannot restore an unavailable F provider.
                    # Preserve an already verified answer or use catalog-only recovery.
                    if first_safe is not None and first_frame == current:
                        return finish(replace(first_safe, reason="model_semantic_first_preserved"))
                    return trusted_fallback("factual_provider_unavailable")
                factual_pass = False
                factual_flag = ("factual_audit_unsupported" if audit.verdict == "unsupported"
                                else "factual_audit_unavailable")
        if factual_pass and checked is not None and current.mode == "debt":
            # Hard checks and required factual grounding have passed. Advisory
            # style, receipts or repetition do not spend another debt G+C pair.
            return finish(replace(checked, reason="model_debt_checked"))
        verdict = analysis.effective_verdict
        # Repetition is a bounded style repair, not authority for an action or
        # a reason to replace a verified first answer with an unverified draft.
        repeated = (not current.closing and bool(previous_reply.strip())
                    and " ".join(draft.split()) == " ".join(previous_reply.split())
                    and "?" not in latest and not re.search(r"повтор\w*|ещ[её]\s+раз|снова", latest, re.I))
        if factual_pass and checked is not None:
            if attempt:
                # Optional style repair cannot replace the first safe reply
                # with another clerical receipt or demand a third generation.
                if style_issues and first_safe is not None and first_frame == current:
                    return finish(replace(first_safe, reason="model_semantic_first_preserved"))
                return finish(checked)
            # The first C has already verified user acts and this exact visible
            # body. Pure style/repetition adds no further action authority.
            if (not decision.issues and verdict is not None and verdict.accept
                    and policy_voice.accepts_brief_style_without_repair(draft, current, analysis.assessment.verdict)):
                return finish(checked)
            if not style_issues and not repeated and (verdict is None or verdict.accept):
                return finish(checked)
            # A style suggestion can spend the remaining generation without vetoing G1.
            first_safe, first_frame = checked, current
        # Feed the actual code-owned rejection back to G2. A semantically clean
        # draft can still fail content, precision or the permitted collection step.
        hard_flags = code_repair_flags(draft)
        repair_flags = tuple(dict.fromkeys((*decision.issues, *hard_flags, *style_issues,
                                           *((factual_flag,) if factual_flag is not None else ())))) or (
            ("repeated_reply",) if repeated else
            tuple(verdict.flags) if verdict is not None and not verdict.accept else ("hard_contract",)
        )
    if first_safe is not None and first_frame == current:
        return finish(replace(first_safe, reason="model_semantic_first_preserved"))
    return trusted_fallback("semantic_unverified_fallback")
