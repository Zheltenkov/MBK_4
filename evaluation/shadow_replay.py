"""Независимый накопительный replay сохранённых shadow-кандидатов.

Обычный runtime shadow сравнивает один ход, начиная с legacy facts. Replay строит
собственное состояние с нуля и поэтому обнаруживает пропуски, которые проявляются
только через несколько ходов.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core import apply_fact_updates
from interpretation import FactCandidate, FactStatus, reduce_fact_candidates
from pii_masking import extract_local_pii_updates, mask_payload_for_llm
from routing import Matrix, get_fact, plan
from utils import set_by_path


JsonDict = dict[str, Any]

_TRUSTED_DERIVED_INPUTS: dict[str, tuple[str, ...]] = {
    "client.region_class": ("client.region", "client.city"),
    "assets.realty.region_class": (
        "assets.realty.region",
        "assets.realty.city",
    ),
}


@dataclass(frozen=True)
class ShadowPlanMismatch:
    turn: int
    user: str
    legacy: tuple[str | None, tuple[str, ...], int | None, str | None]
    shadow: tuple[str | None, tuple[str, ...], int | None, str | None]


@dataclass(frozen=True)
class ShadowReplayResult:
    scenario_id: str
    user_turns: int
    matching_turns: int
    expected_route_id: str
    independent_route_id: str | None
    independent_missing_fields: list[str]
    mismatches: list[ShadowPlanMismatch]

    @property
    def route_matches(self) -> bool:
        return self.independent_route_id == self.expected_route_id

    def as_dict(self) -> JsonDict:
        return {
            **asdict(self),
            "route_matches": self.route_matches,
        }


def replay_dialog_shadow(
    artifact: JsonDict,
    *,
    expected_route_id: str,
    matrix: Matrix,
) -> ShadowReplayResult:
    """Воспроизводит новый reducer по всем ходам, не обращаясь к LLM/CRM/DaData."""
    facts: JsonDict = {}
    statuses: dict[str, FactStatus] = {}
    mismatches: list[ShadowPlanMismatch] = []
    user_turns = 0

    turns = artifact.get("turns")
    if not isinstance(turns, list):
        raise ValueError("dialog artifact не содержит turns")

    for raw_turn in turns:
        if not isinstance(raw_turn, dict):
            continue
        user = raw_turn.get("user")
        if not isinstance(user, str) or not user:
            continue
        user_turns += 1
        public_state = raw_turn.get("public_state")
        if not isinstance(public_state, dict):
            raise ValueError("dialog turn не содержит public_state")

        # Телефон и другие локальные PII принадлежат общему trusted adapter и
        # должны быть одинаковы для legacy и нового контура.
        facts = apply_fact_updates(facts, extract_local_pii_updates(user))
        interpretation = public_state.get("interpretation")
        last_result = (
            interpretation.get("last_result")
            if isinstance(interpretation, dict)
            else None
        )
        raw_candidates_value = (
            last_result.get("candidates")
            if isinstance(last_result, dict)
            else []
        )
        raw_candidates = (
            raw_candidates_value
            if isinstance(raw_candidates_value, list)
            else []
        )
        candidates = [
            FactCandidate.model_validate(item)
            for item in raw_candidates
            if isinstance(item, dict)
        ]
        safe_payload = mask_payload_for_llm(
            {
                "facts": facts,
                "short_history": [],
                "latest_user_message": user,
            },
            True,
        )
        reduction = reduce_fact_candidates(
            facts,
            statuses,
            candidates,
            evidence_text=str(
                safe_payload.get("latest_user_message", "")
            ),
            matrix=matrix,
        )
        facts = reduction.facts
        statuses = reduction.statuses

        # Геоклассификация — общий deterministic adapter. Берём её сохранённый
        # результат только если независимый контур сам извлёк исходный адрес.
        legacy_facts = public_state.get("facts")
        if isinstance(legacy_facts, dict):
            _copy_trusted_derived_facts(facts, legacy_facts)

        shadow_signature = _plan_signature(plan(matrix, facts))
        legacy_signature = _snapshot_signature(
            public_state.get("plan")
        )
        if shadow_signature != legacy_signature:
            mismatches.append(
                ShadowPlanMismatch(
                    turn=user_turns,
                    user=user,
                    legacy=legacy_signature,
                    shadow=shadow_signature,
                )
            )

    final_plan = plan(matrix, facts)
    scenario_id = str(artifact.get("scenario_id") or "unknown")
    return ShadowReplayResult(
        scenario_id=scenario_id,
        user_turns=user_turns,
        matching_turns=user_turns - len(mismatches),
        expected_route_id=expected_route_id,
        independent_route_id=(
            final_plan.matched.id if final_plan.matched else None
        ),
        independent_missing_fields=final_plan.field_paths,
        mismatches=mismatches,
    )


def aggregate_shadow_replays(
    results: list[ShadowReplayResult],
) -> JsonDict:
    """Сводит независимый replay в отдельный migration gate."""
    total_turns = sum(result.user_turns for result in results)
    matching_turns = sum(result.matching_turns for result in results)
    matching_routes = sum(result.route_matches for result in results)
    return {
        "scenarios": len(results),
        "matching_routes": matching_routes,
        "route_match_rate": _ratio(matching_routes, len(results)),
        "user_turns": total_turns,
        "matching_turns": matching_turns,
        "turn_plan_match_rate": _ratio(matching_turns, total_turns),
        "failed_scenario_ids": [
            result.scenario_id
            for result in results
            if not result.route_matches
        ],
        "results": [result.as_dict() for result in results],
    }


def _copy_trusted_derived_facts(
    facts: JsonDict,
    legacy_facts: JsonDict,
) -> None:
    for derived_path, input_paths in _TRUSTED_DERIVED_INPUTS.items():
        if not any(get_fact(facts, path) is not None for path in input_paths):
            continue
        value = get_fact(legacy_facts, derived_path)
        if value is not None:
            set_by_path(facts, derived_path, value)


def _plan_signature(
    current_plan: Any,
) -> tuple[str | None, tuple[str, ...], int | None, str | None]:
    return (
        current_plan.action,
        tuple(current_plan.field_paths),
        current_plan.step,
        current_plan.matched.id if current_plan.matched else None,
    )


def _snapshot_signature(
    snapshot: Any,
) -> tuple[str | None, tuple[str, ...], int | None, str | None]:
    if not isinstance(snapshot, dict):
        return None, (), None, None
    fields = snapshot.get("fields")
    step = snapshot.get("step")
    return (
        str(snapshot["action"]) if snapshot.get("action") else None,
        tuple(str(item) for item in fields)
        if isinstance(fields, list)
        else (),
        step if isinstance(step, int) else None,
        str(snapshot["matched"]) if snapshot.get("matched") else None,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
