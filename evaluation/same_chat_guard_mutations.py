"""Reproduce same-chat debt transition guard mutations in isolated Python and jsdom copies."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Literal
import xml.etree.ElementTree as ET

from evaluation.site_context_guard_mutations import prepare_copy, run_pytest as run_python_fixture

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("C:/Users/zvp/.codex/visualizations/2026/10/31/01a19152-2a77-78d1-97cb-6d664000dd86/stage4-same-chat")
HTTP_TEST = "tests/test_same_chat_debt_transition.py"
JS_TESTS = ("site_widget/test/ai-chat.test.mjs", "site_widget/test/debt-chat.test.mjs",
            "site_widget/test/same-chat-debt.test.mjs")
WIDGET = "site_widget/_ai-chat.js"


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    before: str
    after: str
    runtime: Literal["python", "node"]


def backend(name: str, file: str, before: str, after: str) -> Mutation:
    return Mutation(name, file, before, after, "python")


def widget(name: str, before: str, after: str) -> Mutation:
    return Mutation(name, WIDGET, before, after, "node")


MUTATIONS = (
    backend("debt_budget_reinitialized_each_turn", "debt_workflow.py",
        "if previous_policy is None:", "if True:"),
    backend("new_questionnaire_inherits_normal_budget", "debt_workflow.py",
        'state["debt_script"] = {"attempts": {}}',
        'state["debt_script"] = {"attempts": dict((state.get("script") or {}).get("attempts") or {})}'),
    backend("debt_budget_reads_normal_attempts", "core.py",
        'def _field_attempts(state: dict[str, Any]) -> dict[str, int]:\n    script_key = "debt_script" if debt_policy.debt_delivery_held(state) and "debt_script" in state else "script"',
        'def _field_attempts(state: dict[str, Any]) -> dict[str, int]:\n    script_key = "script"'),
    backend("debt_questions_count_against_normal_budget", "core.py",
        'script_key = "debt_script" if debt_policy.debt_delivery_held(state) and "debt_script" in state else "script"\n    attempts = state.setdefault(script_key, {}).setdefault("attempts", {})',
        'script_key = "script"\n    attempts = state.setdefault(script_key, {}).setdefault("attempts", {})'),
    backend("legacy_derived_zero_is_current_debt", "debt_workflow.py",
        'if _unproven_legacy_zero(state):', 'if False:'),
    backend("confirmed_zero_is_asked_again", "debt_workflow.py",
        'if (state.get("fact_statuses") or {}).get("debts.total") == "confirmed":', 'if False:'),
    backend("form_known_zero_is_asked_again", "debt_workflow.py",
        'if event.get("source") == "form":', 'if False:'),
    backend("explicit_zero_history_is_ignored", "debt_workflow.py",
        'for item in reversed(state.get("chat_history") or []):', 'for item in []:'),
    backend("normal_question_cursor_leaks_into_debt", "debt_workflow.py",
        'script["asked"] = []', '# mutation: stale voiced normal question retained'),
    backend("volunteered_own_region_is_ignored", "debt_workflow.py",
        'location = _region_answer(message, asked="client.region_class" in asked)',
        'location = _region_answer(message, asked=True) if "client.region_class" in asked else None'),
    backend("any_moscow_mention_becomes_own_region", "debt_workflow.py",
        'statement = _SELF_LOCATION.search(message)',
        'region = classify_region_confident(message, get_matrix())\n    if region is not None:\n        return region, message\n    statement = _SELF_LOCATION.search(message)'),
    backend("transition_discards_known_phone", "debt_workflow.py",
        'state["debt_script"] = {"attempts": {}}',
        'state["debt_script"] = {"attempts": {}}\n            state.setdefault("facts", {}).setdefault("client", {}).pop("phone", None)'),
    backend("transition_discards_chat_history", "debt_workflow.py",
        'state["debt_script"] = {"attempts": {}}',
        'state["debt_script"] = {"attempts": {}}\n            state["chat_history"] = []'),
    widget("server_policy_does_not_promote_live_mode", 'this.isDebt_ = true;', 'this.isDebt_ = false;'),
    widget("promotion_moves_sid_to_separate_storage", 'this.isDebt_ = true;',
        'this.isDebt_ = true; this.storeKey_ = DEBT_STORE_KEY; this.saveSid_(this.state_.sid);'),
    widget("promotion_runs_ordinary_consent_cookie_tail", 'if (this.isDebt_) return;',
        '// mutation: ordinary consent-cookie side effects after promotion'),
    widget("latched_debt_can_downgrade_to_credit", 'if (this.isDebt_ || this.hasDebtPolicy_(state)) {',
        'if (this.hasDebtPolicy_(state)) {'),
    widget("restored_session_is_reallocated", 'this.state_.sid = stored;\n      this.recoverDebt_(true);',
        'this.state_.sid = stored;\n      this.startDebtDialog_();'),
    widget("lost_first_policy_frame_replays_turn", '// The server may have pinned debt policy before the connection was lost.\n      this.recoverDebt_();',
        '// mutation: replay an uncertain normal turn\n      this.openStream_(message);'),
    widget("ordinary_state_accepts_foreign_sid", "if (payload.session_id !== this.state_.sid) throw new Error('session');",
        '// mutation: identity unchecked'),
    widget("ordinary_state_accepts_no_canonical_history", "} else if (!state || !Array.isArray(state.chat_history)) {\n      throw new Error('session');\n    }",
        '} else if (!state || !Array.isArray(state.chat_history)) {\n      state = {...state, chat_history: []};\n    }'),
    widget("closed_superseded_normal_stream_is_current",
        "let responseState = null;\n    const current = () => this.state_.es === es && generation === this.requestGeneration_;\n\n    es.addEventListener('chunk'",
        "let responseState = null;\n    const current = () => true;\n\n    es.addEventListener('chunk'"),
    widget("queued_stale_reveal_callback_survives_promotion", 'if (this.state_.revealSeq !== seq) return;',
        '// mutation: stale callback may reveal old response'),
)

NODE_GUARD = """// Loads before jsdom. Every UI API and SSE transport is replaced by the test fixture.
const net = require('node:net');
const tls = require('node:tls');
const http = require('node:http');
const https = require('node:https');
const dns = require('node:dns');
const deny = () => { throw new Error('Real network disabled in debt UI guard evaluation'); };
net.Socket.prototype.connect = deny;
tls.connect = deny;
http.request = deny; http.get = deny;
https.request = deny; https.get = deny;
dns.lookup = deny; dns.resolve = deny;
globalThis.fetch = deny;
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_template(destination: Path) -> dict[str, str]:
    copied = prepare_copy(destination)
    hashes = {relative: digest for relative, digest in copied.items()
              if (ROOT / relative).is_file() and sha(ROOT / relative) == digest}
    explicit = (HTTP_TEST, *JS_TESTS, WIDGET, "site_widget/debt/index.html",
                "site_widget/debt/debt-page.css", "site_widget/test/ai-chat-widget.blade.php",
                "evaluation/same_chat_guard_mutations.py")
    for relative in explicit:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        hashes[relative] = sha(ROOT / relative)
    # Only dependency import location changes. Widget/page and assertions remain the copied sources.
    for relative in JS_TESTS:
        test_path = destination / relative
        source = test_path.read_text(encoding="utf-8")
        dependency = (ROOT / "site_widget/node_modules/jsdom/lib/api.js").as_uri()
        if source.count("from 'jsdom';") != 1:
            raise RuntimeError("Unexpected jsdom fixture import")
        test_path.write_text(source.replace("from 'jsdom';", f"from '{dependency}';"), encoding="utf-8")
    (destination / "node-fixture-guard.cjs").write_text(NODE_GUARD, encoding="utf-8")
    return hashes


def run_pytest(workspace: Path, selectors: tuple[str, ...]) -> dict[str, object]:
    """Require a behavioral assertion, excluding fixture/import/runtime errors from credit."""
    result = run_python_fixture(workspace, selectors)
    assertions = 0
    report_path = workspace / "result.xml"
    if report_path.exists():
        for failure in ET.parse(report_path).getroot().iter("failure"):
            if "AssertionError" in (failure.text or ""):
                assertions += 1
    return {**result, "assertion_failures": assertions}


def run_node(workspace: Path) -> dict[str, object]:
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("Node runtime is required")
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(("MBK_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "DADATA_", "NODE_"))
        and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    started = time.monotonic()
    result = subprocess.run([executable, "--require", str(workspace / "node-fixture-guard.cjs"),
        "--test", "--test-reporter=tap", *JS_TESTS], cwd=workspace, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    output = result.stdout + result.stderr
    failures = re.findall(r"^not ok \d+ - (.+)$", output, re.MULTILINE)
    count = re.search(r"^# tests (\d+)\s*$", output, re.MULTILINE)
    harness_error = any(value in output for value in ("ERR_MODULE_NOT_FOUND", "Cannot find module", "SyntaxError:", "ENOENT:"))
    return {"exit_code": result.returncode, "tests": int(count.group(1)) if count else 0,
        "failing_test_names": failures, "errors": int(harness_error or count is None),
        "assertion_failures": len(re.findall(r"code: 'ERR_ASSERTION'", output)),
        "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=EVIDENCE / "guard-mutations.json")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    output_path = args.out.resolve()
    if output_path.is_relative_to(ROOT):
        raise ValueError("Evaluation evidence belongs outside the repository")
    selected = [item for item in MUTATIONS if not args.only or item.name in args.only]
    if args.only and set(args.only) - {item.name for item in selected}:
        raise ValueError("Unknown mutation name")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError("Existing evidence is immutable; choose a fresh output filename")
    report: dict[str, object] = {"status": "checking", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_checkout_modified": False, "production_connections_allowed": False,
        "python_network": "fixture_owned_loopback_only", "node_network": "all_real_transports_disabled",
        "jsdom_dependency_sha256": sha(ROOT / "site_widget/node_modules/jsdom/lib/api.js")}
    output_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    def save() -> None:
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="mbk-same-chat-mutants-") as temporary:
        scope = Path(temporary).resolve()
        if scope.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Unexpected temporary cleanup scope")
        template = scope / "template"
        hashes = prepare_template(template)
        report["source_snapshot_sha256"] = hashes
        report["execution_fixture_sha256"] = {
            relative: sha(template / relative) for relative in
            ("sitecustomize.py", "tests/conftest.py", "node-fixture-guard.cjs", *JS_TESTS)
        }
        snapshot = output_path.parent / (output_path.stem + "-sources")
        snapshot.mkdir()
        # Preserve exact source bytes, including the test before dependency-location adaptation.
        for relative in hashes:
            original = ROOT / relative
            if not original.is_file():
                continue
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
        report["source_snapshot_directory"] = str(snapshot)
        baseline_py = run_pytest(template, (HTTP_TEST,))
        baseline_js = run_node(template)
        report["baseline_python"], report["baseline_node"] = baseline_py, baseline_js
        if any(item["exit_code"] != 0 or item["errors"] or item["failing_test_names"]
               for item in (baseline_py, baseline_js)):
            report["status"] = "baseline_failed"
            save()
            raise RuntimeError("Isolated baseline failed; no score is valid")
        print(json.dumps({"baseline_python": baseline_py["tests"], "baseline_node": baseline_js["tests"]}), flush=True)
        for item in selected:
            workspace = scope / item.name
            shutil.copytree(template, workspace,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "result.xml", "logs"))
            target = workspace / item.file
            original = target.read_text(encoding="utf-8")
            if original.count(item.before) != 1:
                row: dict[str, object] = {"name": item.name, "status": "invalid_mutation_anchor"}
            else:
                target.write_text(original.replace(item.before, item.after, 1), encoding="utf-8")
                outcome = run_pytest(workspace, (HTTP_TEST,)) if item.runtime == "python" else run_node(workspace)
                expected_tests = baseline_py["tests"] if item.runtime == "python" else baseline_js["tests"]
                killed = (outcome["exit_code"] == 1 and bool(outcome["failing_test_names"])
                    and bool(outcome["assertion_failures"])
                    and outcome["errors"] == 0 and outcome["tests"] == expected_tests)
                row = {"name": item.name, "file": item.file, "runtime": item.runtime,
                       "status": "killed" if killed else "survived_or_invalid", **outcome}
            rows.append(row)
            report["mutations"] = rows
            save()
            print(json.dumps({"mutation": item.name, "status": row["status"]}), flush=True)
    report["source_drift"] = [relative for relative, digest in hashes.items()
                              if sha(ROOT / relative) != digest or sha(snapshot / relative) != digest]
    success = (bool(rows) and all(item["status"] == "killed" for item in rows)
               and not report["source_drift"])
    report.update({"status": "passed" if success else "failed", "total": len(rows),
                   "killed": sum(item["status"] == "killed" for item in rows)})
    save()
    print(json.dumps({"status": report["status"], "killed": report["killed"], "total": report["total"]}))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
