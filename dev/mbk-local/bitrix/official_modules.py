"""Follow only literal native installer module transitions; never execute returned JavaScript."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "logs/local-stack-2026-11-06/official-trial/runtime-v1"


def next_fields(text: str) -> dict[str, str] | None:
    match = re.search(r"ajaxForm\.Post\((\{[^{}]{0,500}\})", text)
    if match is None:
        return None
    value = ast.literal_eval(match.group(1))
    if not isinstance(value, dict) or not value or set(value) - {"nextStep", "nextStepStage"}:
        raise ValueError("Unknown native module transition")
    output: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{0,80}", item):
            raise ValueError("Invalid native module transition")
        output[key] = item
    return output


def main() -> None:
    destination = TARGET / "MODULES-FLOW.json"
    if destination.exists():
        raise ValueError("Module flow artifact exists; inspect actual stage before an explicit resume")
    request = json.loads((TARGET / "module-main-database-request.json").read_text(encoding="utf-8"))
    text = (TARGET / "module-main-database-private.html").read_text(encoding="utf-8")
    report: dict[str, object] = {"status": "checking", "scope": "official_native_installer", "javascript_executed": False}
    steps: list[dict[str, object]] = []
    started = time.monotonic()
    for number in range(1, 501):
        if time.monotonic() - started > 1800:
            raise TimeoutError("Bounded native installation flow exceeded 30 minutes")
        update = next_fields(text)
        if update is None:
            report.update({"status": "awaiting_next_native_form", "steps": steps,
                           "last_response_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "stop_ajax_advertised": "ajaxForm.StopAjax(" in text})
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": report["status"], "steps": len(steps), "stop_ajax_advertised": report["stop_ajax_advertised"]}))
            return
        for key, value in update.items():
            request["fields"]["__wiz_" + key] = value
        report["request_in_flight"] = {"number": number, "transition": update}
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-bitrix-official-1", "php", "/lab/official_http.php"],
                                    input=json.dumps(request, ensure_ascii=False).encode(), capture_output=True, check=True, timeout=70)
        except (subprocess.SubprocessError, OSError) as error:
            report.update({"status": "failed_transport_outcome_unconfirmed", "error_class": type(error).__name__})
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError("Native installation HTTP failed; do not blindly replay an unconfirmed write") from None
        response = json.loads(result.stdout)
        content = base64.b64decode(response.pop("body_base64"))
        private = TARGET / f"module-{number:03d}-private.html"
        private.write_bytes(content)
        text = content.decode("utf-8", errors="replace")
        step = {"number": number, "request_transition": update, "http_status": response["status"],
                "bytes": len(content), "sha256": response["body_sha256"]}
        steps.append(step)
        report.pop("request_in_flight", None)
        report.update({"steps": steps, "last_private_artifact": private.name})
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if response["status"] == 200 and re.search(r'name=["\']CurrentStepID["\']', text):
            report["status"] = "awaiting_next_native_form"
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": report["status"], "steps": len(steps)}))
            return
        if response["status"] != 200 or not text.startswith("[response]"):
            report["status"] = "failed_native_response"
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": report["status"], "step": step}))
            raise SystemExit(1)
        if number % 10 == 0:
            print(json.dumps({"status": "installing", "steps": number, "transition": update}), flush=True)
    raise RuntimeError("Bounded native module installation exceeded 500 requests")


if __name__ == "__main__":
    main()
