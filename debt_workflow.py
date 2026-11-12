"""Apply the closed debt policy around the existing extraction pipeline."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from debt_policy import (
    POLICY_KEY, DebtObservation, DebtPolicyState, detect_debt_observation, derive_debt_facts,
    parse_policy, phone_contact_declined, plan_debt, select_policy,
)
from dialogue_frame import DialogueFrame, last_collection_field
from pii_masking import extract_local_pii_updates
from geo.enrichment import _in_city_queries
from routing import get_fact, get_matrix
from routing.normalize import classify_region_confident, locality_key
from utils import set_by_path


_SELF_LOCATION = re.compile(
    r"(?:^|[.!?;,]\s*)(?:я\s+)?(?:(?:сейчас|теперь)\s+)?"
    r"(?:живу|проживаю|прописан(?:а)?|зарегистрирован(?:а)?|переехал(?:а)?)\s+(?:в|во)\s+(.+)",
    re.IGNORECASE,
)
_LOCATION_OTHER_CONTEXT = re.compile(
    r"\b(?:не|нет|брат\w*|сестр\w*|друг\w*|мам\w*|отец|банк\w*|кредит\w*|"
    r"долг\w*|спис\w*|банкрот\w*|квартир\w*|недвижим\w*|работ\w*)\b", re.IGNORECASE,
)
_LOCATION_UNCERTAIN = re.compile(r"\b(?:не|нет|если|возможно|наверное|ли)\b|[?«»\"]", re.IGNORECASE)
_ELLIPTIC_SELF_LOCATION = re.compile(
    r"(?:^|[.!?;]\s*)я\s+(?:(?:сейчас|теперь)\s+)?(?:в|во)\s+(.+)$", re.IGNORECASE,
)
_KNOWN_PHONE_REFERENCE = re.compile(
    r"(?:(?:да\s*,?\s*)?(?:я\s+)?соглас(?:ен|на)\s+на\s+передачу"
    r"(?:\s+(?:моих\s+)?данных)?(?:\s+(?:специалисту|консультанту|партнеру))?[.!]\s*)?"
    r"(?:(?:телефон|номер)(?:\s+для\s+связи)?\s+(?:я\s+)?уже\s+"
    r"(?:оставил[а]?|оставлял[а]?|сообщил[а]?|указал[а]?)|"
    r"(?:я\s+)?уже\s+(?:оставил[а]?|оставлял[а]?|сообщил[а]?|указал[а]?)\s+"
    r"(?:свой\s+)?(?:телефон|номер))"
    r"(?:\s+(?:выше|ранее|вам))?[.!]?"
)


def known_phone_reference(state: dict[str, Any], message: str) -> bool:
    """Prove a whole reference adds no fact; never interpret transfer permission."""
    policy = parse_policy(state)
    if (policy is None or policy.mode != "debt" or state.get("onloader") is not None
            or not get_fact(state.get("facts") or {}, "client.phone")):
        return False
    text = " ".join(message.casefold().replace("ё", "е").split())
    return _KNOWN_PHONE_REFERENCE.fullmatch(text) is not None


def _catalogued_city(candidate: str) -> str | None:
    """Resolve a whole place name only against unique existing catalogue entries."""
    spoken = candidate.strip().rstrip(".!").strip()
    # Reuse the existing pure case-form generator, without calling its resolver.
    variants = {locality_key(spoken)}
    variants.update(locality_key(name) for name, fixed in _in_city_queries("в " + spoken)
                    if locality_key(fixed) == locality_key(name))
    matches: dict[str, str] = {}
    for entries in get_matrix().regions.values():
        for entry in entries:
            city = entry.split("\t", 1)[-1]
            key = locality_key(city)
            if key in variants:
                matches[key] = city
    return next(iter(matches.values())) if len(matches) == 1 else None


def _catalogued_region(candidate: str) -> tuple[str, str] | None:
    """Resolve a whole named subject, including common Russian case endings.

    The existing catalogue supplies names; morphology never invents a subject
    or a city. A region class is sufficient for this debt questionnaire.
    """
    text = re.sub(r"^(?:в|во)\s+", "", candidate.strip().casefold()).rstrip(". ")
    matrix = get_matrix()
    labels = {
        " ".join(re.sub(r"\([^)]*\)", "", entry.split("\t", 1)[0]).casefold().split())
        for entries in matrix.regions.values() for entry in entries if "\t" in entry
    }
    matches: dict[str, str] = {}
    for label in labels:
        variants = {label}
        if label.endswith("ая область"):
            stem = label[:-len("ая область")]
            variants.update({stem + "ой области", stem + "ую область"})
        elif label.endswith("ий край"):
            stem = label[:-len("ий край")]
            variants.update({stem + "ом крае", stem + "ого края"})
        if text not in variants:
            continue
        region = classify_region_confident(label, matrix)
        if region is not None:
            matches[label] = region
    if len(matches) != 1:
        return None
    label, region = next(iter(matches.items()))
    return region, label[:1].upper() + label[1:]


def _region_answer(message: str, *, asked: bool) -> tuple[str, str] | None:
    """Accept a local place answer or a first-person residence statement."""
    # A sentence inside a multi-sentence quote is not the client's residence.
    message = re.sub(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'", "[quoted]", message, flags=re.S)
    if any(mark in message for mark in ('"', "'", "«", "»", "“", "”")):
        return None
    compound_question = re.fullmatch(
        r"([^!?]+)[,;]\s*(?:а\s+)?(?:можно|как|что|почему|зачем|есть|получится|можете)\b[^!?]*\?\s*",
        message, re.IGNORECASE,
    )
    if compound_question is not None:
        # Only a whole catalogued city or an explicit residence statement can
        # supply the location. The following business question supplies none.
        assertion = compound_question.group(1).strip()
        residence = _SELF_LOCATION.fullmatch(assertion) or _ELLIPTIC_SELF_LOCATION.fullmatch(assertion)
        if not asked and residence is None:
            return None
        spoken = residence.group(1) if residence is not None else assertion
        city = _catalogued_city(spoken)
        region_only = _catalogued_region(spoken)
        if (city is None and region_only is None) or _LOCATION_UNCERTAIN.search(spoken):
            return None
        if region_only is not None:
            return region_only
        return _region_answer(city, asked=True) if city is not None else None
    separate_question = re.fullmatch(r"([^.!?]+[.!])\s*(.+\?)\s*", message, re.DOTALL)
    if separate_question is not None:
        assertion, question = separate_question.groups()
        statement = _ELLIPTIC_SELF_LOCATION.search(assertion) or _SELF_LOCATION.search(assertion)
        spoken = statement.group(1) if statement is not None else assertion
        # A separate FAQ does not retract a whole, catalogued place assertion.
        # A place correction or explicit uncertainty still requires clarification.
        if (_LOCATION_UNCERTAIN.search(assertion)
                or (_catalogued_city(spoken) is None and _catalogued_region(spoken) is None)
                or classify_region_confident(question, get_matrix()) is not None
                or re.search(r"\b(?:точнее|ошиб\w*|не\s+увер\w*)\b|\bя\b[^.!?]*\bне\b[^.!?]*жив",
                             question, re.I)):
            return None
        return _region_answer(assertion, asked=asked)
    answer = message
    if asked:
        secondary_region = re.fullmatch(r"([^,;?!]+)[,;]\s*(?:(?:но|а)\s+)?регион\s+не\s+(?:знаю|помню)[.!]?\s*",
                                       message, re.IGNORECASE)
        if secondary_region is not None:
            # Uncertainty about the administrative label does not retract the named city.
            answer = secondary_region.group(1).strip()
    statement = _SELF_LOCATION.search(answer)
    elliptical = _ELLIPTIC_SELF_LOCATION.search(answer)
    if elliptical is not None:
        # The whole elliptical clause must name one city. Do not truncate a
        # conflict or asset/third-person context into a seemingly certain place.
        spoken = elliptical.group(1)
        if _LOCATION_UNCERTAIN.search(spoken):
            return None
        candidate = _catalogued_city(spoken)
        if candidate is None:
            if region_only := _catalogued_region(spoken):
                return region_only
            return None
    elif statement is not None:
        residence_clause = re.split(r"[.!?]", statement.group(1), maxsplit=1)[0]
        if re.search(r"\b(?:или|либо|точнее)\b", residence_clause, re.I):
            return None
        candidate = re.split(r"[;,\n]|(?<![гдп])\.(?:\s|$)", statement.group(1), maxsplit=1)[0].strip()
    elif asked and not _LOCATION_OTHER_CONTEXT.search(answer):
        candidate = answer.strip()
    else:
        return None
    if asked:
        # A terminal courtesy aside does not make the named place hypothetical.
        candidate = re.sub(r"(?:,\s*|\s+)если\s+это\s+важно[.!]?\s*$", "", candidate, flags=re.IGNORECASE).strip()
    if _LOCATION_UNCERTAIN.search(candidate):
        return None
    if region_only := _catalogued_region(candidate):
        return region_only
    if canonical_city := _catalogued_city(candidate):
        candidate = canonical_city
    region = classify_region_confident(candidate, get_matrix())
    return (region, candidate) if region is not None else None


def select_for_turn(state: dict[str, Any], message: str, *, enabled: bool,
                    interpret_contact_refusal: bool = True) -> DebtPolicyState | None:
    """Selection precedes model work; once present, its hold survives failures/flag-off."""
    previous_policy = parse_policy(state)
    policy = select_policy(state, message, enabled=enabled)
    if policy is not None:
        if previous_policy is None:
            # Keep the session and facts, but start a budget for the newly chosen questionnaire.
            state["debt_script"] = {"attempts": {}}
            script = state.setdefault("script", {})
            script["asked"] = []
            script.pop("units_pending", None)
            state["pending_question"] = None
            if _unproven_legacy_zero(state):
                policy = policy.with_observation(DebtObservation(kind="unknown"))
                state.setdefault("fact_statuses", {})["debts.total"] = "needs_confirmation"
        # The semantic renderer owns code-grounded and model-grounded choices
        # together. Legacy callers retain this early deterministic interpretation.
        if interpret_contact_refusal and policy.mode == "debt" \
                and phone_contact_declined(message, expected=_phone_requested(state)):
            policy = policy.model_copy(update={"phone_collection": "declined"})
            # Refusing contact ends contact collection, not the debt conversation.
            script = state.setdefault("script", {})
            script["asked"] = [path for path in script.get("asked", []) if path not in {"client.name", "client.phone"}]
        state[POLICY_KEY] = policy.as_state()
        pending = (state.get("script") or {}).get("units_pending")
        if isinstance(pending, dict) and pending.get("path") != "debts.total":
            # A pending clarification about a new loan must not capture a debt answer.
            state["script"].pop("units_pending", None)
    return policy


def _phone_requested(state: dict[str, Any]) -> bool:
    # A retained plan cursor does not identify the visible question being
    # answered. Bare refusals need the latest actual request for a phone.
    frame = DialogueFrame("client.phone", "", mode="debt")
    for turn in reversed(state.get("chat_history") or []):
        if isinstance(turn, dict) and turn.get("role") == "assistant":
            return last_collection_field(str(turn.get("content") or ""), frame) == "client.phone"
    return False


def _unproven_legacy_zero(state: dict[str, Any]) -> bool:
    """A legacy no-active-loans shortcut is insufficient evidence for a zero debt."""
    facts = state.get("facts") or {}
    if get_fact(facts, "debts.total") != 0:
        return False
    if (state.get("fact_statuses") or {}).get("debts.total") == "confirmed":
        return False
    for event in reversed(state.get("fact_events") or []):
        if isinstance(event, dict) and event.get("path") == "debts.total":
            if event.get("source") == "system_derived":
                return True
            if event.get("value") == 0 and event.get("status") in {"known", "confirmed"}:
                if event.get("source") == "form":
                    return False
                evidence = detect_debt_observation(str(event.get("evidence") or ""), expected=True)
                if evidence is not None and evidence.kind == "exact" and evidence.amount == 0:
                    return False
            break
    if get_fact(facts, "debts.has_active_credits") is not False:
        return False
    # A labelled explicit zero in earlier dialogue also survives legacy mode's missing events.
    for item in reversed(state.get("chat_history") or []):
        if isinstance(item, dict) and item.get("role") == "user":
            observation = detect_debt_observation(str(item.get("content") or ""), expected=False)
            if observation is not None and observation.kind == "exact":
                return observation.amount != 0
    return True


def reconcile_extraction(previous: dict[str, Any], updated: dict[str, Any], message: str) -> frozenset[str]:
    """Only answers to this short policy can change facts; money is locally grounded.

    The generic model/reducer may derive zero from no active loans, default an
    asset to false, or promote an approximate range. None of those operations is
    an authorized change to this questionnaire's facts.
    """
    policy = parse_policy(updated)
    if policy is None:
        return frozenset()
    original = previous.get("facts") or {}
    if policy.mode != "debt":
        # Clarification/cancellation changes the scenario state while retaining known facts.
        updated["facts"] = deepcopy(original)
        updated["fact_statuses"] = deepcopy(previous.get("fact_statuses") or {})
        updated["fact_events"] = list(previous.get("fact_events") or [])[-200:]
        updated[POLICY_KEY] = policy.as_state()
        return frozenset()
    facts = deepcopy(original)
    # The live selection may have replaced the old question before extraction began.
    asked = set((updated.get("script") or {}).get("asked") or [])
    accepted_paths: set[str] = set()

    location = _region_answer(message, asked="client.region_class" in asked)
    if location is not None:
        # Unlike the generic normalizer this helper has no default rf_other.
        # A refusal or unrecognized place must remain unknown despite model proposals.
        region, raw_region = location
        set_by_path(facts, "client.region_class", region)
        set_by_path(facts, "client.region", raw_region)
        accepted_paths.add("client.region_class")

    # A volunteered phone/name can be accepted through the same local PII parser,
    # independently of an untrusted model's proposed contact value.
    for update in extract_local_pii_updates(message, expected_paths=list(asked), existing_facts=facts):
        path = update.get("path")
        if path in {"client.name", "client.phone"}:
            set_by_path(facts, str(path), update["value"])
            accepted_paths.add(str(path))
            if path == "client.phone":
                # Only a locally parsed new phone can reopen a previously declined contact task.
                policy = policy.model_copy(update={"phone_collection": "open"})

    # A quiet fallback records no question, but does not close an uncertain
    # debt task. The client can independently supply its missing exact value.
    pending_amount = (not asked and "client.phone" not in accepted_paths
                      and policy.amount_observation is not None
                      and policy.amount_observation.kind in {"unknown", "range"}
                      and plan_debt(get_matrix(), original, policy,
                                    fact_statuses=previous.get("fact_statuses")).field_paths == ["debts.total"])
    observation = detect_debt_observation(message, expected="debts.total" in asked or pending_amount)
    if observation is not None and policy.mode == "debt":
        policy = policy.with_observation(observation)
        if observation.kind == "exact":
            set_by_path(facts, "debts.total", observation.amount)
        # Range/unknown retains the previous verified value as history, while
        # the policy observation forces clarification and blocks current eligibility.
    updated[POLICY_KEY] = policy.as_state()

    statuses = deepcopy(previous.get("fact_statuses") or {})
    for path in accepted_paths:
        statuses[path] = "known"
    if ("client.region_class" in asked and "client.region_class" not in accepted_paths
            and (get_fact(original, "client.region_class") is None or _SELF_LOCATION.search(message))):
        # The previous cursor survives follow-up questions. Those questions do not
        # revoke an already grounded region or restart a completed questionnaire.
        statuses["client.region_class"] = "needs_confirmation"
    if policy.amount_observation is not None:
        statuses["debts.total"] = (
            "known" if policy.amount_observation.kind == "exact" else "needs_confirmation"
        )
    updated["fact_statuses"] = statuses
    # Discard generic reducer provenance for rejected/defaulted paths as well.
    # Otherwise diagnostics could claim an invented zero/asset even though facts reject it.
    updated["fact_events"] = list(previous.get("fact_events") or [])[-200:]
    # No active-credit inference, invented credit history, or asset defaults here.
    updated["facts"] = derive_debt_facts(facts)
    _clear_fulfilled_cursor(updated, accepted_paths)
    # Report only facts accepted from this message, without exposing their values.
    if observation is not None and observation.kind == "exact":
        accepted_paths.add("debts.total")
    return frozenset(accepted_paths)


def _clear_fulfilled_cursor(state: dict[str, Any], accepted_paths: set[str]) -> None:
    """Do not bind later FAQ answers to an already completed fact question."""
    script = state.setdefault("script", {})
    policy = parse_policy(state)
    fulfilled = set(accepted_paths)
    if policy is not None and policy.amount_observation is not None and policy.amount_observation.kind == "exact":
        fulfilled.add("debts.total")
    script["asked"] = [path for path in script.get("asked", []) if path not in fulfilled]
    pending = script.get("units_pending")
    if isinstance(pending, dict) and pending.get("path") in fulfilled:
        script.pop("units_pending", None)
    if not script["asked"]:
        state["pending_question"] = None


def public_policy_metadata(state: dict[str, Any]) -> dict[str, str | int] | None:
    """Closed metadata only; no raw input, contacts or source identifiers."""
    policy = parse_policy(state)
    if policy is None:
        return None
    return {"version": policy.version, "mode": policy.mode, "reason": policy.reason}
