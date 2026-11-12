"""Prepare a separate site process pointing at the native-API CRM lab branch."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "logs/local-stack-2026-11-06/runtime"


def main() -> None:
    source, target = RUNTIME / "site.env", RUNTIME / "site-genuine.env"
    manifest = json.loads((RUNTIME / "ENV-MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("customer_env_read") is not False or manifest.get("generated_credentials") is not True:
        raise ValueError("Expected the previously generated synthetic lab configuration")
    if target.exists():
        raise FileExistsError("Genuine-site configuration already exists; do not rotate it")
    # Reuse only our existing artificial site schema; old processes and queues stay intact.
    values = dict(line.split("=", 1) for line in source.read_text(encoding="utf-8").splitlines() if "=" in line)
    if (values.get("DB_DATABASE") != "site" or values.get("DB_HOST") != "database"
            or values.get("BITRIX24_URL") != "http://crm:8000"):
        raise ValueError("Unexpected local site source configuration")
    values.update({
        "APP_NAME": "MBK-genuine-site-local",
        "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "APP_URL": "http://site-genuine:8000", "ASSET_URL": "http://site-genuine:8000",
        "BITRIX24_URL": "http://crm-genuine:8000",
        "SESSION_COOKIE": "mbk_genuine_site_session", "LOG_LEVEL": "warning",
    })
    # An exclusive create prevents a second invocation from changing a running app's key.
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in sorted(values.items())))
    report = {"status": "prepared_no_start", "schema": "site", "schema_origin": "existing_synthetic_lab",
              "source_env_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "target_env_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "variables": sorted(values), "customer_env_read": False, "old_service_changed": False,
              "leads_submitted": 0, "workers_started": 0,
              "receiver_boundary": "native_api_harness_not_customer_receiver_or_rest_oauth"}
    (RUNTIME / "genuine-site-env-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "secret_values_printed": False}))


if __name__ == "__main__":
    main()
