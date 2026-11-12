"""Run independent debt acceptance against isolated behavioural source mutations.

No app, DB, sender or model connection is allowed. Shared execution helpers keep
the pre-import fixture-only network guard used in the previous context stage.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from evaluation.site_context_guard_mutations import GuardMutation, prepare_copy, run_pytest


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = "tests/test_debt_policy_acceptance.py"


def mutation(name: str, file: str, before: str, after: str) -> GuardMutation:
    return GuardMutation(name, file, before, after, (ACCEPTANCE,), ".")


MUTATIONS = (
    mutation("local_190k_floor_off_by_one", "debt_policy.py", "if value < 190000:", "if value <= 190000:"),
    mutation("local_300k_boundary_off_by_one", "debt_policy.py", "if value < 300000:", "if value <= 300000:"),
    mutation("boolean_becomes_a_debt_amount", "debt_policy.py", "isinstance(value, bool) or ", ""),
    mutation("interval_no_longer_forces_exact_amount", "debt_policy.py",
             'if observation is not None and observation.kind != "exact":', "if False:"),
    mutation("malformed_restored_policy_fails_open", "debt_policy.py",
             'return DebtPolicyState(mode="clarify", reason="restored_invalid")', "return None"),
    mutation("debt_presence_no_longer_holds_delivery", "debt_policy.py", "return POLICY_KEY in state", "return False"),
    mutation("feature_off_starts_new_debt_sessions", "debt_policy.py", "if not enabled:\n        return None", "if False:\n        return None"),
    mutation("untrusted_model_facts_replace_verified_debt_facts", "debt_workflow.py", "facts = deepcopy(original)",
             'facts = deepcopy(updated.get("facts") or {})'),
    mutation("interval_lower_bound_becomes_canonical_debt", "debt_workflow.py",
             'if observation.kind == "exact":\n            set_by_path(facts, "debts.total", observation.amount)',
             'if observation.kind in {"exact", "range"}:\n            set_by_path(facts, "debts.total", observation.amount if observation.kind == "exact" else observation.lower)'),
    mutation("initial_turn_omits_policy_selection", "core.py",
             "debt_workflow.select_for_turn(new_state, user_message, enabled=config.debt_policy_enabled)",
             "# mutation: no deterministic entry before extraction"),
    mutation("generic_extractor_bypasses_debt_reconciliation", "core.py",
             "debt_workflow.reconcile_extraction(state, new_state, user_message)",
             "pass  # mutation: generic extractor facts pass through"),
    mutation("flag_off_restores_legacy_credit_planner", "core.py",
             "if policy is not None:\n        return debt_policy.plan_debt(matrix,",
             "if policy is not None and config.debt_policy_enabled:\n        return debt_policy.plan_debt(matrix,"),
    mutation("debt_question_goes_to_generic_conversation_model", "core.py",
             "config = config or load_config()\n    if debt_policy.debt_delivery_held(state):\n        yield _debt_reply(state)\n        return\n    payload = build_runtime_payload(state, user_message)",
             "config = config or load_config()\n    payload = build_runtime_payload(state, user_message)"),
    mutation("no_active_credits_invent_zero_and_good_history", "debt_policy.py", "return deepcopy(dict(facts))",
             "from routing.derive import derive_facts\n    return derive_facts(dict(facts))"),
    mutation("contacts_are_requested_before_total_debt", "debt_policy.py",
             'return _planned(matrix, ["debts.total"], confirmation=needs_confirmation)',
             'return _planned(matrix, ["client.phone"], confirmation=needs_confirmation)'),
    mutation("trusted_hint_seal_not_checked", "debt_policy.py", "and trusted_signal._seal is _TRUSTED_SEAL", "and True"),
    mutation("trusted_hint_binding_not_checked", "debt_policy.py",
             "hmac.compare_digest(digest, trusted_signal._binding_digest)", "True"),
    mutation("expired_authority_can_create_a_trusted_hint", "debt_policy.py",
             "authorized.assert_authorized(now)", "# mutation: expired context accepted for a new hint"),
    mutation("old_trusted_goal_overrides_current_refusal", "debt_policy.py",
             'if _DEBT_NEGATED.search(text) or (_CASH_REQUEST.search(text) and not _CASH_NEGATED.search(text)):', "if False:"),
    mutation("explicit_refusal_does_not_stop_debt_questions", "debt_policy.py",
             'if existing.mode == "debt" and _DEBT_NEGATED.search(text):', "if False:"),
    mutation("private_debt_facts_go_to_model_when_global_flag_off", "core.py",
             "context_readonly = context_delivery_held(state) or debt_policy.debt_delivery_held(state)",
             "context_readonly = context_delivery_held(state)"),
    mutation("corrupt_policy_metadata_escapes_to_diagnostic_snapshot", "context_projection.py",
             "safe[POLICY_KEY] = policy.as_state()", "pass  # mutation: leave raw policy metadata untouched"),
    mutation("foreign_currency_is_silently_treated_as_rubles", "debt_policy.py",
             'if re.search(r"[$€]|\\b(?:usd|eur)\\b|доллар|евро", text, re.IGNORECASE):', "if False:"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "logs/debt-policy-2026-11-06/guard-mutations-final.json")
    parser.add_argument("--only", nargs="*")
    options = parser.parse_args()
    selected = [item for item in MUTATIONS if not options.only or item.name in options.only]
    if options.only and set(options.only) - {item.name for item in selected}:
        raise ValueError("Unknown mutation name")
    report: dict[str, object] = {"status": "checking", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                                "network_policy": "only_ports_bound_by_this_subprocess_loopback_fixtures",
                                "live_checkout_modified": False}
    rows: list[dict[str, object]] = []
    options.out.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        options.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    with tempfile.TemporaryDirectory(prefix="mbk-debt-policy-mutants-") as temporary:
        scope = Path(temporary).resolve()
        if scope.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Unexpected cleanup target outside the created temporary scope")
        template = scope / "template"
        hashes = prepare_copy(template)
        target_test = template / ACCEPTANCE
        shutil.copyfile(ROOT / ACCEPTANCE, target_test)
        hashes[ACCEPTANCE] = hashlib.sha256(target_test.read_bytes()).hexdigest()
        report["source_snapshot_sha256"] = hashes
        baseline = run_pytest(template, (ACCEPTANCE,))
        report["baseline"] = baseline
        if baseline["exit_code"] != 0 or baseline["errors"] or baseline["failing_test_names"]:
            report["status"] = "baseline_failed"
            save()
            raise RuntimeError("Isolated baseline failed; no mutation score is valid")
        print(json.dumps({"baseline": "passed", "tests": baseline["tests"]}), flush=True)
        for item in selected:
            workspace = scope / item.name
            shutil.copytree(template, workspace, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "result.xml"))
            target = workspace / item.file
            original = target.read_text(encoding="utf-8")
            if original.count(item.before) != 1:
                row: dict[str, object] = {"name": item.name, "status": "invalid_mutation_anchor"}
            else:
                target.write_text(original.replace(item.before, item.after, 1), encoding="utf-8")
                outcome = run_pytest(workspace, item.tests)
                killed = outcome["exit_code"] == 1 and bool(outcome["failing_test_names"]) and outcome["errors"] == 0
                row = {"name": item.name, "file": item.file, "status": "killed" if killed else "survived_or_invalid", **outcome}
            rows.append(row)
            report["mutations"] = rows
            save()
            print(json.dumps({"mutation": row["name"], "status": row["status"]}), flush=True)
    passed = bool(rows) and all(row["status"] == "killed" for row in rows)
    report.update({"status": "passed" if passed else "failed", "killed": sum(row["status"] == "killed" for row in rows), "total": len(rows)})
    save()
    print(json.dumps({"status": report["status"], "killed": report["killed"], "total": len(rows)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
