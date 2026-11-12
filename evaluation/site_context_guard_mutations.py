"""Isolated source mutations for stage-one context guards; never edit the running checkout.

Unlike run_mutation_eval.py (LLM paraphrases), these mutations delete/change real
authorization, freshness, quota and projection guards. Only fixture-owned loopback
servers are reachable; no upstream production connection can be attempted.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = "tests/test_site_context_acceptance.py"
REDIRECT = "tests/test_customer_context.py::test_actual_http_redirect_cannot_forward_bearer"
MODEL_ACCEPTANCE = "tests/test_site_context_llm_acceptance.py"
SUBMISSION_ACCEPTANCE = "tests/test_site_context_submission_acceptance.py"


@dataclass(frozen=True)
class GuardMutation:
    name: str
    file: str
    before: str
    after: str
    tests: tuple[str, ...] = (ACCEPTANCE,)
    module_dir: str = "customer_context"


MUTATIONS = (
    GuardMutation("signature_not_verified", "capability.py", "if not hmac.compare_digest(_decode(signature), expected):", "if False:"),
    GuardMutation("session_binding_removed", "capability.py", "or not hmac.compare_digest(claims.session_id.encode(), session_id.encode())", "or False"),
    GuardMutation("expiry_off_by_one", "capability.py", "if now >= claims.expires_at:", "if now > claims.expires_at:"),
    GuardMutation("future_capability_accepted", "capability.py", "or claims.issued_at > now or not 0 <", "or False or not 0 <"),
    GuardMutation("snapshot_reference_not_checked", "service.py", "or snapshot.reference != claims.reference:", "or False:"),
    GuardMutation("inactive_snapshot_accepted", "service.py", "or not snapshot.active", "or False"),
    GuardMutation("snapshot_freshness_removed", "service.py", "age < 0 or age >= self._max_snapshot_age", "False"),
    GuardMutation("bound_application_swap_allowed", "service.py", "if existing_binding is not None and not (", "if False and not ("),
    GuardMutation("source_latency_extends_capability", "service.py", "result.assert_authorized(self._clock())", "# mutation: post-fetch expiry omitted"),
    GuardMutation("new_capability_extends_stale_snapshot", "service.py",
                  "expires_at = min(claims.expires_at, snapshot.observed_at.timestamp() + self._max_snapshot_age)",
                  "expires_at = claims.expires_at"),
    GuardMutation("expired_handle_accepted", "service.py", " or now >= self._expires_at", ""),
    GuardMutation("authorization_seal_ignored", "service.py", "self._seal is not _AUTHORIZATION_SEAL or ", ""),
    GuardMutation("budget_one_request_over_limit", "budget.py", "if count >= self._limit:", "if count > self._limit:"),
    GuardMutation("budget_reservation_bypassed", "crm_lookup.py", "if not self._budget.reserve(now):", "if False:"),
    GuardMutation("timeout_means_no_history", "crm_lookup.py", 'return LookupResult(LookupStatus.UNAVAILABLE, reason="transport_unavailable")',
                  'return LookupResult(LookupStatus.NOT_FOUND_OR_OUTSIDE_COVERAGE, reason="transport_unavailable")'),
    GuardMutation("forbidden_means_no_history", "crm_lookup.py", 'return LookupResult(LookupStatus.UNAUTHORIZED, reason="upstream_authorization")',
                  'return LookupResult(LookupStatus.NOT_FOUND_OR_OUTSIDE_COVERAGE, reason="upstream_authorization")'),
    GuardMutation("retry_429_without_a_reservation", "crm_lookup.py", "if response.status == 429:\n                return",
                  "if response.status == 429:\n                self._transport.post(phone=phone, token=self._token.get_secret_value(), timeout=self._timeout, max_bytes=self._max_bytes)\n                return"),
    GuardMutation("raw_history_in_public_projection", "crm_lookup.py", 'return {"history_status": self.status.value}',
                  'return {"history_status": self.status.value, "unsafe_history": self.history.model_dump(mode="json") if self.history else None}'),
    GuardMutation("loan_amount_becomes_debt", "service.py", 'values["phone"] = self._snapshot.phone.get_secret_value()',
                  'if "desired_amount" in values:\n            values["debts_total"] = values.pop("desired_amount")\n        values["phone"] = self._snapshot.phone.get_secret_value()'),
    GuardMutation("missing_debt_becomes_zero", "service.py", 'values["phone"] = self._snapshot.phone.get_secret_value()',
                  'values.setdefault("debts_total", 0)\n        values["phone"] = self._snapshot.phone.get_secret_value()'),
    GuardMutation("missing_source_creates_a_replacement", "service.py", 'if snapshot is None:\n            raise ContextAccessError("source_missing")',
                  'if snapshot is None:\n            self._source.create(claims.reference)\n            raise ContextAccessError("source_missing")'),
    GuardMutation("bearer_forwarded_on_redirect", "crm_lookup.py", "urllib.request.ProxyHandler({}), _NoRedirect()", "urllib.request.ProxyHandler({})", (REDIRECT,)),
    GuardMutation("context_delivery_hold_removed", "context_policy.py", "return CONTEXT_STATE_KEY in state", "return False",
                  (SUBMISSION_ACCEPTANCE,), "."),
    GuardMutation("raw_context_facts_reach_model", "pii_masking.py",
                  'masked["facts"] = project_context_state({"facts": typed})["facts"]',
                  'masked["facts"] = typed', (MODEL_ACCEPTANCE,), "."),
    GuardMutation("context_projection_allows_free_text", "context_projection.py",
                  'if spec.kind not in {"money", "int", "bool", "enum"}:',
                  'if False:', (MODEL_ACCEPTANCE,), "."),
    GuardMutation("known_contact_redaction_loses_original_facts", "core.py",
                  '"facts": state.get("facts", {}) if context_readonly else model_state.get("facts", {}),',
                  '"facts": model_state.get("facts", {}),', (MODEL_ACCEPTANCE,), "."),
    GuardMutation("read_context_marker_downgraded", "core.py",
                  'result["current_context_readonly"] = True',
                  'result["current_context_readonly"] = False',
                  (MODEL_ACCEPTANCE,), "."),
)

# This installs before pytest collection/imports. Ports become eligible only after
# this very subprocess successfully bound a temporary local fixture listener.
SITE_CUSTOMIZE = r'''
import socket
_ports=set()
_bind=socket.socket.bind
_connect=socket.socket.connect
_connect_ex=socket.socket.connect_ex
_getaddrinfo=socket.getaddrinfo
def _allowed(address):
 return isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') and address[1] in _ports
def _fixture_bind(self,address):
 if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
  raise OSError('Only fixture-owned loopback listeners are allowed')
 result=_bind(self,address)
 _ports.add(self.getsockname()[1])
 return result
def _fixture_connect(self,address):
 if not _allowed(address):raise OSError('Production and ambient local services are blocked')
 return _connect(self,address)
def _fixture_connect_ex(self,address):
 if not _allowed(address):raise OSError('Production and ambient local services are blocked')
 return _connect_ex(self,address)
def _fixture_dns(host,port,*args,**kwargs):
 if host not in ('127.0.0.1','::1','localhost'):raise OSError('External DNS is blocked')
 return _getaddrinfo('127.0.0.1' if host=='localhost' else host,port,*args,**kwargs)
socket.socket.bind=_fixture_bind
socket.socket.connect=_fixture_connect
socket.socket.connect_ex=_fixture_connect_ex
socket.getaddrinfo=_fixture_dns
socket._mbk_fixture_only=True
'''

CONFTEXT = '''from pathlib import Path
import socket
import customer_context
def pytest_sessionstart(session):
 assert getattr(socket,"_mbk_fixture_only",False), "Pre-import production network guard did not load"
 assert Path(customer_context.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1]), "Imported the live checkout instead of the isolated copy"
'''


def prepare_copy(destination: Path) -> dict[str, str]:
    destination.mkdir()
    # Copy source and explicit package data only: never .env, logs, databases or
    # access files. The model/coordinator boundary needs the normal app imports.
    for source in ROOT.glob("*.py"):
        shutil.copyfile(source, destination / source.name)
    for package in ROOT.iterdir():
        if not package.is_dir() or not (package / "__init__.py").is_file():
            continue
        for source in package.rglob("*"):
            if not source.is_file() or source.suffix not in {".py", ".yaml", ".yml", ".json", ".csv", ".md"}:
                continue
            relative = source.relative_to(ROOT)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (destination / "tests").mkdir()
    for name in ("test_site_context_acceptance.py", "test_customer_context.py",
                 "test_site_context_llm_acceptance.py", "test_site_context_submission_acceptance.py"):
        shutil.copyfile(ROOT / "tests" / name, destination / "tests" / name)
    (destination / "sitecustomize.py").write_text(SITE_CUSTOMIZE, encoding="utf-8")
    (destination / "tests/conftest.py").write_text(CONFTEXT, encoding="utf-8")
    return {str(path.relative_to(destination)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in destination.rglob("*") if path.is_file()}


def run_pytest(workspace: Path, selectors: tuple[str, ...]) -> dict[str, object]:
    # No inherited credentials/proxy settings; .env is neither copied nor discovered.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MBK_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "DADATA_"))
                   and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    environment.update({"PYTHONPATH": str(workspace), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "NO_PROXY": "*"})
    result_file = workspace / "result.xml"
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=" + str(result_file), *selectors],
                            cwd=workspace, env=environment, capture_output=True, text=True, timeout=90, check=False)
    failures: list[str] = []
    errors = 0
    tests = 0
    if result_file.exists():
        report = ET.parse(result_file).getroot()
        for case in report.iter("testcase"):
            tests += 1
            if case.find("failure") is not None:
                failures.append(str(case.get("name")))
            if case.find("error") is not None:
                errors += 1
    return {"exit_code": result.returncode, "tests": tests, "failing_test_names": failures,
            "errors": errors, "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "logs/site-context-2026-11-06/guard-mutations.json")
    parser.add_argument("--only", nargs="*")
    options = parser.parse_args()
    selected = [item for item in MUTATIONS if not options.only or item.name in options.only]
    if options.only and set(options.only) - {item.name for item in selected}:
        raise ValueError("Unknown mutation name")
    output: dict[str, object] = {"status": "checking", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                               "live_checkout_modified": False, "production_connections_allowed": False,
                               "network_policy": "only_ports_bound_by_this_subprocess_loopback_fixtures"}
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(output) + "\n", encoding="utf-8")
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="mbk-context-guard-mutants-") as temporary:
        scope = Path(temporary).resolve()
        # Verify the final cleanup target belongs to the explicitly created temp scope.
        if scope.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Unexpected temporary workspace path")
        template = scope / "template"
        output["source_snapshot_sha256"] = prepare_copy(template)
        baseline = run_pytest(template, (ACCEPTANCE, REDIRECT, MODEL_ACCEPTANCE, SUBMISSION_ACCEPTANCE))
        output["baseline"] = baseline
        if baseline["exit_code"] != 0 or baseline["errors"] or baseline["failing_test_names"]:
            output["status"] = "baseline_failed"
            options.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError("Isolated baseline did not pass; mutation scores would be meaningless")
        print(json.dumps({"baseline": "passed", "tests": baseline["tests"]}), flush=True)
        for mutation in selected:
            workspace = scope / mutation.name
            shutil.copytree(template, workspace, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "result.xml"))
            target = workspace / mutation.module_dir / mutation.file
            source = target.read_text(encoding="utf-8")
            if source.count(mutation.before) != 1:
                row: dict[str, object] = {"name": mutation.name, "status": "invalid_mutation_anchor"}
            else:
                target.write_text(source.replace(mutation.before, mutation.after, 1), encoding="utf-8")
                run = run_pytest(workspace, mutation.tests)
                killed = run["exit_code"] == 1 and bool(run["failing_test_names"]) and run["errors"] == 0
                row = {"name": mutation.name, "file": mutation.file, "status": "killed" if killed else "survived_or_invalid", **run}
            rows.append(row)
            output["mutations"] = rows
            options.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"mutation": row["name"], "status": row["status"]}), flush=True)
    passed = bool(rows) and all(row["status"] == "killed" for row in rows)
    output.update({"status": "passed" if passed else "failed", "killed": sum(row["status"] == "killed" for row in rows), "total": len(rows)})
    options.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "killed": output["killed"], "total": len(rows)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
