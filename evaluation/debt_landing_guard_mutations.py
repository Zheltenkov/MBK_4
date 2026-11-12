"""Reproduce debt landing guard mutations in isolated Python and jsdom copies."""
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

from evaluation.site_context_guard_mutations import prepare_copy, run_pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("C:/Users/zvp/.codex/visualizations/2026/10/31/01a19152-2a77-78d1-97cb-6d664000dd86/stage3")
HTTP_TEST = "tests/test_debt_landing_http.py"
JS_TEST = "site_widget/test/debt-chat.test.mjs"
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
    backend("feature_off_allocates_debt_session", "main.py",
        'if not _env_flag("MBK_DEBT_POLICY_ENABLED"):', "if False:"),
    backend("browser_form_context_mix_accepted", "main.py",
        "if body.anketa is not None or body.context_expected:", "if False:"),
    backend("first_question_bootstrap_omitted", "main.py",
        "session.state = initialize_debt_questionnaire(session.state)", "# mutation: no voiced first question"),
    backend("debt_creation_overwrites_normal_grant", "main.py",
        'DEBT_SESSION_GRANT_COOKIE if body.requested_scenario == "debt" else SESSION_GRANT_COOKIE,',
        "SESSION_GRANT_COOKIE,"),
    backend("forged_owner_cookie_is_accepted", "main.py",
        "_secrets_match(granted, expected)\n        for name in (SESSION_GRANT_COOKIE, DEBT_SESSION_GRANT_COOKIE)",
        "True\n        for name in (SESSION_GRANT_COOKIE, DEBT_SESSION_GRANT_COOKIE)"),
    backend("busy_turn_reported_as_idle", "main.py",
        '"turn_in_progress": session.turn_lock.locked()', '"turn_in_progress": False'),
    backend("legacy_cash_chips_in_debt_flow", "quick_replies.py",
        "if not enabled or debt_delivery_held(state):", "if not enabled:"),
    widget("normal_and_debt_storage_keys_mixed", "this.storeKey_ = this.isDebt_ ? DEBT_STORE_KEY : STORE_KEY;",
        "this.storeKey_ = STORE_KEY;"),
    widget("explicit_debt_choice_missing_from_post", "if (this.isDebt_) body.requested_scenario = 'debt';",
        "// mutation: requested scenario omitted"),
    widget("restored_policy_mode_not_checked", "!['debt', 'clarify', 'cancelled'].includes(policy.mode) ||", "false ||"),
    widget("restore_response_sid_not_checked", "(expectedSid && data.session_id !== expectedSid)", "false"),
    widget("restore_worker_progress_type_not_checked",
        "if (typeof data.turn_in_progress !== 'boolean') throw new Error('session');", "// mutation: untyped progress"),
    widget("superseded_create_generation_ignored",
        "this.createSession_().then((data) => {\n      if (generation !== this.requestGeneration_) return;",
        "this.createSession_().then((data) => {\n      // mutation: generation guard omitted"),
    widget("late_create_writes_sid_before_generation_guard",
        "if (this.isDebt_) this.validateDebtEnvelope_(data);\n      else {",
        "if (this.isDebt_) { this.validateDebtEnvelope_(data); this.state_.sid = data.session_id; this.saveSid_(data.session_id); }\n      else {"),
    widget("live_sse_missing_sid_accepted", "if (payload.session_id !== sid) throw new Error('session');",
        "if (payload.session_id !== undefined && payload.session_id !== sid) throw new Error('session');"),
    widget("closed_old_stream_events_accepted",
        "const current = () => this.state_.es === es && generation === this.requestGeneration_;",
        "const current = () => true;"),
    widget("error_replays_message_instead_of_get",
        "// A GET can observe a committed answer. Replaying this message cannot.\n      this.recoverDebt_();",
        "// mutation: resend an uncertain request\n      this.openDebtStream_(message);"),
    widget("uncommitted_done_skips_reconciliation", "if (!committed) return this.recoverDebt_();",
        "if (!committed) { es.close(); this.state_.es = null; this.debtReady_(); return; }"),
    widget("busy_get_poll_budget_exceeded", "    read(2);", "    read(5);"),
    widget("uncommitted_draft_is_lost", "if (!pending || this.els_.input.value.trim()) return false;", "if (true) return false;"),
    widget("other_tab_text_acknowledges_our_pending_turn",
        "if (!turns.some((item) => item.content === pending.text) && !(ownCompletedTurn && turns.length)) return;",
        "if (!turns.length) return;"),
    widget("untrusted_message_rendered_as_html", "this.linkify_(p, text);", "p.innerHTML = text;"),
    widget("first_post_failure_loses_input_draft",
        "this.debtStatus_('Подключаемся к консультации…');\n    this.createSession_()",
        "this.debtStatus_('Подключаемся к консультации…');\n    this.els_.input.value = '';\n    this.createSession_()"),
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
    explicit = (HTTP_TEST, JS_TEST, WIDGET, "site_widget/debt/index.html", "site_widget/debt/debt-page.css")
    for relative in explicit:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        hashes[relative] = sha(ROOT / relative)
    # Only dependency import location changes. Widget/page and assertions remain the copied sources.
    test_path = destination / JS_TEST
    source = test_path.read_text(encoding="utf-8")
    dependency = (ROOT / "site_widget/node_modules/jsdom/lib/api.js").as_uri()
    if source.count("from 'jsdom';") != 1:
        raise RuntimeError("Unexpected jsdom fixture import")
    test_path.write_text(source.replace("from 'jsdom';", f"from '{dependency}';"), encoding="utf-8")
    (destination / "node-fixture-guard.cjs").write_text(NODE_GUARD, encoding="utf-8")
    return hashes


def run_node(workspace: Path) -> dict[str, object]:
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("Node runtime is required")
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(("MBK_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "DADATA_", "NODE_"))
        and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    started = time.monotonic()
    result = subprocess.run([executable, "--require", str(workspace / "node-fixture-guard.cjs"),
        "--test", "--test-reporter=tap", JS_TEST], cwd=workspace, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    output = result.stdout + result.stderr
    failures = re.findall(r"^not ok \d+ - (.+)$", output, re.MULTILINE)
    count = re.search(r"^# tests (\d+)\s*$", output, re.MULTILINE)
    harness_error = any(value in output for value in ("ERR_MODULE_NOT_FOUND", "Cannot find module", "SyntaxError:", "ENOENT:"))
    return {"exit_code": result.returncode, "tests": int(count.group(1)) if count else 0,
        "failing_test_names": failures, "errors": int(harness_error or count is None),
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
    with tempfile.TemporaryDirectory(prefix="mbk-debt-landing-mutants-") as temporary:
        scope = Path(temporary).resolve()
        if scope.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Unexpected temporary cleanup scope")
        template = scope / "template"
        hashes = prepare_template(template)
        report["source_snapshot_sha256"] = hashes
        report["execution_fixture_sha256"] = {
            relative: sha(template / relative) for relative in
            ("sitecustomize.py", "tests/conftest.py", "node-fixture-guard.cjs", JS_TEST)
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
                    and outcome["errors"] == 0 and outcome["tests"] == expected_tests)
                row = {"name": item.name, "file": item.file, "runtime": item.runtime,
                       "status": "killed" if killed else "survived_or_invalid", **outcome}
            rows.append(row)
            report["mutations"] = rows
            save()
            print(json.dumps({"mutation": item.name, "status": row["status"]}), flush=True)
    success = bool(rows) and all(item["status"] == "killed" for item in rows)
    report.update({"status": "passed" if success else "failed", "total": len(rows),
                   "killed": sum(item["status"] == "killed" for item in rows)})
    save()
    print(json.dumps({"status": report["status"], "killed": report["killed"], "total": report["total"]}))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
