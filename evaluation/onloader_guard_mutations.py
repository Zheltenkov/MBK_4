"""Reproduce local onloader guard mutations in isolated Python and jsdom copies."""
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
EVIDENCE = Path("C:/Users/zvp/.codex/visualizations/2026/10/31/01a19152-2a77-78d1-97cb-6d664000dd86/stage5-onloader")
HTTP_TEST = "tests/test_onloader_local_http.py"
JS_TESTS = ("site_widget/test/ai-chat.test.mjs", "site_widget/test/debt-chat.test.mjs",
            "site_widget/test/same-chat-debt.test.mjs", "site_widget/test/onloader.test.mjs")
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
    backend('capability_signature_bypassed', 'customer_context/capability.py', 'if not hmac.compare_digest(_decode(signature), expected):', 'if False:'),
    backend('capability_sid_binding_omitted', 'customer_context/capability.py', 'or not hmac.compare_digest(claims.session_id.encode(), session_id.encode())', 'or False'),
    backend('current_context_capability_accepted_as_onloader', 'customer_context/capability.py', 'claims.audience != self._audience', 'False'),
    backend('owner_grant_not_required', 'main.py', '# A known SID, operator login or raw form-id grants no onloader authority.\n    if not _session_grant_matches(request, request.url.path):', '# mutation: owner guard omitted\n    if False:'),
    backend('production_intake_can_mix_with_local_onloader', 'main.py', 'def _onloader_available() -> bool:\n    return (_env_flag("MBK_ONLOADER_LOCAL_ENABLED") and onloader_service is not None\n            and not _env_flag("MBK_CRM_LEAD_INTAKE_ENABLED") and delivery.is_mocked())', 'def _onloader_available() -> bool:\n    return (_env_flag("MBK_ONLOADER_LOCAL_ENABLED") and onloader_service is not None\n            and delivery.is_mocked())'),
    backend('onloader_marker_does_not_hold_legacy_delivery', 'context_policy.py', 'return CONTEXT_STATE_KEY in state or "onloader" in state', 'return CONTEXT_STATE_KEY in state'),
    backend('bound_application_can_be_replaced', 'onloader/service.py', 'if previous is not None and not hmac.compare_digest(previous.binding_digest, digest):', 'if False:'),
    backend('central_debt_plan_reasks_opaque_known_phone', 'core.py', 'fact_statuses=state.get("fact_statuses"), attempts=_field_attempts(state),\n                                     contact_known=onloader_policy.contact_known(state))', 'fact_statuses=state.get("fact_statuses"), attempts=_field_attempts(state),\n                                     contact_known=False)'),
    backend('fallback_debt_renderer_reasks_opaque_known_phone', 'core.py', 'attempts=_field_attempts(state),\n                                             contact_known=onloader_policy.contact_known(state))', 'attempts=_field_attempts(state),\n                                             contact_known=False)'),
    backend('decision_ignores_presented_offer_tuple', 'onloader/service.py', 'if (action.presentation_id, action.offer_id, action.offer_version, action.consent_version) != expected:', 'if False:'),
    backend('opposite_decision_silently_succeeds', 'onloader/service.py', 'if current.decision != action.decision:', 'if False:'),
    backend('idempotent_action_repeats_backend_submission', 'onloader/service.py', '# An uncertain outcome is reconciled only by the separate read operation.\n            return', '# mutation: replay an already recorded command\n            self._backend.submit(current.delivery_handle.get_secret_value(), self._command(current))\n            return'),
    backend('receipt_can_belong_to_another_command', 'onloader/service.py', 'result.model_dump(exclude={"status", "receipt_id"}) != command.model_dump()', 'False'),
    backend('typed_receipt_skips_closed_schema_validation', 'onloader/service.py', 'result = OnloaderReceipt.model_validate(result.model_dump())', 'result = result'),
    backend('renewal_changes_the_already_recorded_intent', 'onloader/service.py', '"delivery_handle": snapshot.delivery_handle,\n                    "authorization_expires_at": expires_at,', '"delivery_handle": snapshot.delivery_handle,\n                    "intent_expires_at": expires_at,\n                    "authorization_expires_at": expires_at,'),
    backend('read_outage_erases_validated_queue_receipt', 'onloader/service.py', 'final = current if current.status == "queued" else current.model_copy(update={"status": "outcome_unknown"})', 'final = current.model_copy(update={"status": "outcome_unknown"})'),
    backend('receiver_deduplicates_event_instead_of_existing_operation', 'onloader/local_backend.py', '(command.operation_ref, serialized, receipt.model_dump_json()))', '(command.event_id, serialized, receipt.model_dump_json()))'),
    backend('receiver_ignores_revocation_after_service_inspection', 'onloader/local_backend.py', 'if snapshot.active is not True or snapshot.contact_known is not True or snapshot.delivery_owner != "site_backend":', 'if False:'),
    backend('receiver_ignores_offer_change_before_commit', 'onloader/local_backend.py', 'if (snapshot.offer.offer_id, snapshot.offer.version, snapshot.offer.consent_version) != (\n                command.offer_id, command.offer_version, command.consent_version\n            ) or snapshot.offer.digest() != command.offer_digest:', 'if False:'),
    backend('receiver_ignores_expiry_before_commit', 'onloader/local_backend.py', 'if (not 0 <= now - snapshot.observed_at.timestamp() < 300\n                    or now >= min(snapshot.expires_at.timestamp(), command.authorization_expires_at)):', 'if False:'),
    backend('cancelled_client_releases_a_live_receiving_worker', 'main.py', '# An HTTP disconnect cannot unlock a worker that is still committing its intent.\n            release_lock = False', '# mutation: disconnect releases the still-running receiver\n            release_lock = True'),
    widget('new_onloader_omits_pending_hold_choice', 'if (this.onloaderExpected_) body.onloader_expected = true;', '// mutation: onloader expectation omitted'),
    widget('capability_is_persisted_in_browser_storage', 'return {capability};', "localStorage.setItem('unsafe-onloader-capability', capability); return {capability};"),
    widget('decision_uses_a_different_consent_version', "this.onloaderOperation_('decision', {...binding, decision});", "this.onloaderOperation_('decision', {...binding, consent_version: 'browser-other-version', decision});"),
    widget('decline_button_submits_acceptance', "this.onloaderOperation_('decision', {...binding, decision});", "this.onloaderOperation_('decision', {...binding, decision: 'accept'});"),
    widget('double_click_is_not_held_busy', 'this.state_.busy = true; this.state_.recovering = false; this.setBusyUi_(true);', 'this.state_.busy = false; this.state_.recovering = false; this.setBusyUi_(false);'),
    widget('onloader_moves_existing_identity_to_debt_storage', 'this.onloaderState_ = onloader;', 'this.onloaderState_ = onloader; if (onloader) { this.storeKey_ = DEBT_STORE_KEY; this.saveSid_(this.state_.sid); }'),
    widget('partner_display_name_is_executed_as_html', 'element.textContent = content;', 'element.innerHTML = content;'),
    widget('closed_chat_disables_the_separate_consent_action', 'for (const action of this.root_.querySelectorAll(\'[data-action^="onloader-"]\')) action.disabled = !!busy || this.state_.recovering;', 'for (const action of this.root_.querySelectorAll(\'[data-action^="onloader-"]\')) action.disabled = !!busy || this.state_.recovering || this.state_.closed;'),
    widget('restored_known_onloader_is_automatically_rebound', 'if (!this.onloaderState_ && this.onloaderExpected_ && !this.onloaderBootstrapAttempted_) {', 'if (this.onloaderExpected_ && !this.onloaderBootstrapAttempted_) {'),
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
    explicit = (HTTP_TEST, "evaluation/onloader_guard_mutations.py", *JS_TESTS, WIDGET,
                "site_widget/debt/index.html", "site_widget/debt/debt-page.css",
                "site_widget/test/ai-chat-widget.blade.php")
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
            # Pytest's rewritten comparisons can omit the exception name entirely.
            # Only the terminal assertion line qualifies, never arbitrary traceback source.
            detail = failure.text or ""
            message = failure.get("message", "")
            if (re.search(r"^E\s+(?:assert\s|AssertionError\b)", detail, re.MULTILINE)
                    or message.startswith("AssertionError")):
                assertions += 1
    return {**result, "assertion_failures": assertions}


def run_node(workspace: Path) -> dict[str, object]:
    if not JS_TESTS:
        return {"exit_code": 0, "tests": 0, "failing_test_names": [], "errors": 0,
                "assertion_failures": 0, "skipped": True}
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
    with tempfile.TemporaryDirectory(prefix="mbk-onloader-mutants-") as temporary:
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
