"""Frozen stage-six persistence guard mutations, with fixture-only subprocess networking."""
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
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from evaluation.site_context_guard_mutations import prepare_copy

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("C:/Users/zvp/.codex/visualizations/2026/10/31/01a19152-2a77-78d1-97cb-6d664000dd86/stage6-durable")
ACK = "tests/test_onloader_durable_ack.py"
PROCESS = "tests/test_onloader_process_restart.py"
FULL = ("tests/test_onloader_local_http.py", ACK, PROCESS,
        "tests/test_sqlite_sessions.py", "tests/test_preview_persistent_keys.py")


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    before: str
    after: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation('store_accepts_foreign_session_ack', 'session_store.py', 'or acknowledgment.session_id != session.id', 'or False', ('tests/test_onloader_durable_ack.py::test_the_store_itself_never_accepts_a_foreign_or_uncommitted_receipt',)),
    Mutation('store_accepts_nonincrementing_revision', 'session_store.py', 'or acknowledgment.revision != record.revision + 1', 'or False', ('tests/test_onloader_durable_ack.py::test_the_store_itself_never_accepts_a_foreign_or_uncommitted_receipt',)),
    Mutation('store_accepts_unbound_digest', 'session_store.py', 'or acknowledgment.state_digest != session_record_digest(record)', 'or False', ('tests/test_onloader_durable_ack.py::test_the_store_itself_never_accepts_a_foreign_or_uncommitted_receipt',)),
    Mutation('digest_does_not_cover_conversation_state', 'storage/sessions.py', '"anketa": record.anketa, "state": record.state, "usage": record.usage,', '"anketa": record.anketa, "state": {}, "usage": record.usage,', ('tests/test_onloader_durable_ack.py::test_digest_binds_entire_record_but_not_the_database_revision',)),
    Mutation('service_accepts_an_uncommitted_callback', 'onloader/service.py', '            if (not isinstance(acknowledgment, SessionCommit)\n                    or type(acknowledgment.session_id) is not str\n                    or type(acknowledgment.state_digest) is not str\n                    or acknowledgment.session_id != session.id\n                    or type(acknowledgment.revision) is not int\n                    or acknowledgment.revision != expected_revision\n                    or acknowledgment.revision != session.storage_revision\n                    or acknowledgment.state_digest != digest):', '            if False:', ('tests/test_onloader_durable_ack.py::test_the_onloader_service_itself_requires_a_committed_callback_ack',)),
    Mutation('receiver_called_without_intent_checkpoint', 'onloader/service.py', '        OnloaderService._checkpoint(session, persist)', '        if value.status != "signal_pending":\n            OnloaderService._checkpoint(session, persist)', ('tests/test_onloader_durable_ack.py::test_receiver_observes_committed_intent_before_any_operation',)),
    Mutation('stale_full_session_overwrites_the_decision', 'storage/sqlite_sessions.py', 'if type(record.revision) is not int or record.revision != expected:', 'if False:', ('tests/test_onloader_durable_ack.py::test_stale_full_session_save_cannot_erase_an_acknowledged_decision',)),
    Mutation('get_publishes_provisional_worker_state', 'session_store.py', 'return (Session.from_record(record) if record is not None else None), busy', 'return active, busy', ('tests/test_onloader_durable_ack.py::test_get_during_a_blocked_checkpoint_returns_only_the_previous_committed_state',)),
    Mutation('unreadable_storage_becomes_missing_session', 'session_store.py', 'if required or self.require_durable:\n                raise SessionPersistenceError("unavailable") from None', 'if required or self.require_durable:\n                return None', ('tests/test_onloader_durable_ack.py::test_storage_read_failure_returns_unavailable_without_falling_back_to_cached_authority',)),
    Mutation('sqlite_ack_is_returned_without_commit', 'storage/sqlite_sessions.py', 'stored = self._write(connection, record, current, lead_owner=lead_owner)\n            connection.commit()', 'stored = self._write(connection, record, current, lead_owner=lead_owner)\n            connection.rollback()', ('tests/test_sqlite_sessions.py::test_reopen_preserves_full_record_and_fingerprint',)),
    Mutation('waiting_decision_uses_uncommitted_memory', 'main.py', '        _store().refresh_locked(session)\n        pending = asyncio.get_running_loop().run_in_executor(_store().executor, execute)', '        # mutation: use the session fetched before waiting\n        pending = asyncio.get_running_loop().run_in_executor(_store().executor, execute)', ('tests/test_onloader_durable_ack.py::test_a_waiting_request_rechecks_committed_state_after_the_first_checkpoint_fails',)),
    Mutation('waiting_opening_stream_publishes_uncommitted_memory', 'main.py', '        _store().refresh_locked(session)\n        effective_is_opening = (', '        # mutation: publish the session fetched before waiting\n        effective_is_opening = (', ('tests/test_onloader_durable_ack.py::test_a_waiting_request_rechecks_committed_state_after_the_first_checkpoint_fails',)),
    Mutation('restart_replaces_private_signing_keys', 'site_widget/stand/debt_preview.py', 'value = json.loads(path.read_text(encoding="utf-8"))', 'value = value', ('tests/test_onloader_process_restart.py::test_actual_restart_preserves_onloader_authority_and_never_renews_it_by_get',)),
    Mutation('persistent_preview_uses_memory_sessions', 'site_widget/stand/debt_preview.py', 'main.store = SessionStore(max_extract_workers=2,\n                                  repository=SQLiteSessionRepository(state_directory / "sessions.sqlite"),\n                                  require_durable=True)', 'main.store = SessionStore(max_extract_workers=2)', ('tests/test_onloader_process_restart.py::test_actual_process_restart_restores_the_same_chat_history_cursor_and_owner',)),
    Mutation('restart_erases_the_authoritative_source_registry', 'onloader/local_backend.py', 'snapshot_json TEXT NOT NULL, UNIQUE(application_id, subject_id)\n            )""")', 'snapshot_json TEXT NOT NULL, UNIQUE(application_id, subject_id)\n            )""")\n            db.execute("DELETE FROM onloader_sources")', ('tests/test_onloader_process_restart.py::test_actual_restart_preserves_onloader_authority_and_never_renews_it_by_get',)),
    Mutation('hydration_silently_extends_expired_authority', 'session_store.py', 'state=record.state,', 'state=({**record.state, "onloader": {**record.state["onloader"], "authorization_expires_at": datetime.now(timezone.utc).timestamp() + 300}} if isinstance(record.state.get("onloader"), dict) else record.state),', ('tests/test_onloader_process_restart.py::test_restart_keeps_expired_authority_expired_until_an_explicit_fresh_capability',)),
    Mutation('read_renewal_changes_the_recorded_command', 'onloader/service.py', 'authorization_expires_at=current.intent_expires_at,', 'authorization_expires_at=current.authorization_expires_at,', ('tests/test_onloader_local_http.py::test_fresh_capability_renews_read_access_without_changing_the_existing_intent',)),
    Mutation('private_authority_enters_diagnostic_snapshots', 'session_store.py', '"state": safe_state,', '"state": self.state,', ('tests/test_onloader_process_restart.py::test_restored_public_diagnostics_and_logs_never_contain_private_keys_or_contact_handles',)),
    Mutation('driver_exception_text_enters_critical_logs', 'session_store.py', 'logger.warning("sessions: required checkpoint failed reason=%s", reason)', 'logger.warning("sessions: checkpoint failed: %s", error)', ('tests/test_onloader_durable_ack.py::test_failed_storage_before_intent_ack_forbids_receiver_operation',)),
    Mutation('authoritative_hold_grants_a_stale_delivery_claim', 'storage/sqlite_sessions.py', 'return LeadClaim(granted=False, lead_state=deepcopy(lead), revision=authoritative.revision)', 'return LeadClaim(granted=True, lead_state=deepcopy(lead), revision=authoritative.revision)', ('tests/test_sqlite_sessions.py::test_authoritative_hold_cannot_be_removed_by_a_stale_lead_claim',)),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(workspace: Path, selectors: tuple[str, ...]) -> tuple[dict[str, object], list[tuple[str, str]]]:
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(("MBK_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "DADATA_"))
        and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    environment.update({"PYTHONPATH": str(workspace), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "NO_PROXY": "*"})
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=result.xml", *selectors],
        cwd=workspace, env=environment, capture_output=True, text=True, timeout=150, check=False)
    cases: list[tuple[str, str]] = []
    failures: list[str] = []
    errors, assertions = 0, 0
    report_path = workspace / "result.xml"
    if report_path.exists():
        for case in ET.parse(report_path).getroot().iter("testcase"):
            cases.append((case.get("classname", ""), case.get("name", "")))
            if case.find("error") is not None:
                errors += 1
            failure = case.find("failure")
            if failure is not None:
                failures.append(case.get("name", ""))
                if (re.search(r"^E\s+(?:assert\s|AssertionError\b)", failure.text or "", re.MULTILINE)
                        or failure.get("message", "").startswith("AssertionError")):
                    assertions += 1
    return {"exit_code": result.returncode, "tests": len(cases), "failing_test_names": failures,
            "errors": errors + int(not report_path.exists()), "assertion_failures": assertions,
            "elapsed_seconds": round(time.monotonic() - started, 3)}, cases


def prepare(destination: Path) -> dict[str, str]:
    copied = prepare_copy(destination)
    hashes = {relative: digest for relative, digest in copied.items()
              if (ROOT / relative).is_file() and sha(ROOT / relative) == digest}
    for relative in (*FULL, "site_widget/stand/debt_preview.py", "evaluation/onloader_durable_guard_mutations.py"):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        hashes[relative] = sha(ROOT / relative)
    return hashes


def expected_count(cases: list[tuple[str, str]], selectors: tuple[str, ...]) -> int:
    count = 0
    for classname, name in cases:
        for selector in selectors:
            path, _, function = selector.partition("::")
            if classname in {path.removesuffix(".py").replace("/", "."), Path(path).stem} and (
                    not function or name.split("[", 1)[0] == function):
                count += 1
                break
    if not count:
        raise RuntimeError("Mutation selection has no baseline cases")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=EVIDENCE / "guard-mutations.json")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--targeted-baseline", action="store_true")
    args = parser.parse_args()
    output = args.out.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a fresh evidence filename outside the repository")
    selected = [item for item in MUTATIONS if not args.only or item.name in args.only]
    if args.only and set(args.only) - {item.name for item in selected}:
        raise ValueError("Unknown mutation target")
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"status": "checking", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_checkout_modified": False, "production_connections_allowed": False,
        "network_policy": "fixture_owned_loopback_only_in_parent_and_children"}
    def save() -> None:
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    save()
    with tempfile.TemporaryDirectory(prefix="mbk-durable-mutants-") as temporary:
        scope = Path(temporary).resolve()
        if scope.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Unexpected temporary cleanup scope")
        template = scope / "template"
        hashes = prepare(template)
        # Every anchor and generated source must be valid before paying for process tests.
        for item in selected:
            source = (template / item.file).read_text(encoding="utf-8")
            if source.count(item.before) != 1:
                raise RuntimeError("Mutation anchor is not unique: " + item.name)
            compile(source.replace(item.before, item.after, 1), item.file, "exec")
        snapshot = output.parent / (output.stem + "-sources")
        snapshot.mkdir()
        for relative in hashes:
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        report["source_snapshot_directory"] = str(snapshot)
        report["source_snapshot_sha256"] = hashes
        report["execution_fixture_sha256"] = {relative: sha(template / relative)
            for relative in ("sitecustomize.py", "tests/conftest.py")}
        baseline_selectors = tuple(dict.fromkeys(selector for item in selected for selector in item.tests)) if args.targeted_baseline else FULL
        report["baseline_selectors"] = baseline_selectors
        baseline, cases = execute(template, baseline_selectors)
        report["baseline"] = baseline
        save()
        if baseline["exit_code"] != 0 or baseline["errors"] or baseline["failing_test_names"]:
            report["status"] = "baseline_failed"
            save()
            raise RuntimeError("Isolated complete baseline failed")
        print(json.dumps({"baseline": baseline["tests"]}), flush=True)
        rows = []
        for item in selected:
            workspace = scope / item.name
            shutil.copytree(template, workspace, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "result.xml", "logs"))
            target = workspace / item.file
            source = target.read_text(encoding="utf-8")
            target.write_text(source.replace(item.before, item.after, 1), encoding="utf-8")
            result, _ = execute(workspace, item.tests)
            count = expected_count(cases, item.tests)
            killed = (result["exit_code"] == 1 and result["assertion_failures"] and not result["errors"]
                      and result["tests"] == count)
            rows.append({"name": item.name, "file": item.file, "selectors": item.tests,
                         "expected_tests": count, "status": "killed" if killed else "survived_or_invalid", **result})
            report["mutations"] = rows
            save()
            print(json.dumps({"mutation": item.name, "status": rows[-1]["status"]}), flush=True)
        report["source_drift"] = [relative for relative, digest in hashes.items()
            if sha(ROOT / relative) != digest or sha(snapshot / relative) != digest]
    success = bool(rows) and all(row["status"] == "killed" for row in rows) and not report["source_drift"]
    report.update({"status": "passed" if success else "failed", "total": len(rows),
                   "killed": sum(row["status"] == "killed" for row in rows)})
    save()
    print(json.dumps({key: report[key] for key in ("status", "killed", "total")}))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
