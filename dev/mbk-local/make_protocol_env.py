"""Generate simulator-only credentials/configuration without exposing values."""

from __future__ import annotations

import json
from pathlib import Path
import re
import secrets


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = root / "logs/local-stack-2026-11-06/runtime"
    destination = runtime / "protocol.env"
    crm_override = runtime / "protocol-crm.env"
    if destination.exists() or crm_override.exists():
        raise ValueError("Protocol env exists; do not rotate a running fixture's credentials")
    source = root / "logs/local-stack-2026-11-06/sources/project-2/lara/app/Services/Bitrix24Api.php"
    match = re.search(r"const\s+string\s+METHOD_REQUEST\s*=\s*'([^']+)'", source.read_text(encoding="utf-8"))
    if match is None or not match[1].startswith("/") or "?" in match[1]:
        raise ValueError("Could not derive the reviewed receiver route")
    username, password = "local-protocol", secrets.token_urlsafe(32)
    values = {
        "MBK_PROTOCOL_USER": username, "MBK_PROTOCOL_PASSWORD": password,
        "MBK_PROTOCOL_PATH": match[1], "MBK_PROTOCOL_DB": "/data/ledger.sqlite3",
        "MBK_PROTOCOL_RUN_ID": "mbk-local-20261106-v1", "MBK_PROTOCOL_DEDUP_SECONDS": "900",
        "MBK_PROTOCOL_BASE_URL": "http://protocol-receiver:8000",
    }
    destination.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    override = {"BITRIX24_URL": values["MBK_PROTOCOL_BASE_URL"], "BITRIX24_LOGIN": username,
                "BITRIX24_PASSWORD": password, "BITRIX_REST_URL": "http://capture:8000"}
    crm_override.write_text("".join(f"{key}={value}\n" for key, value in override.items()), encoding="utf-8")
    (runtime / "PROTOCOL-ENV-MANIFEST.json").write_text(json.dumps({
        "evidence_level": "protocol_simulation", "production_credentials_read": False,
        "protocol_variables": sorted(values), "crm_override_variables": sorted(override),
        "credentials_generated_locally": True,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence_level": "protocol_simulation", "env_files_created": 2,
                      "secret_values_printed": False}))


if __name__ == "__main__":
    main()
