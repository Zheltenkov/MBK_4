"""Guard fresh native schema and initialize only new, owned lab volumes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "logs/local-stack-2026-11-06/bitrix-genuine-v1"


def mysql(content: bytes) -> bytes:
    # The existing local DB container supplies its own generated root credential.
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-database-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -B -uroot'],
                            input=content, capture_output=True, timeout=90, check=False)
    if result.returncode:
        # Persist private diagnostics, never echo generated credentials or SQL payloads.
        (TARGET / "mysql-private-error.log").write_bytes(result.stderr)
        raise RuntimeError("Native MySQL step failed; private diagnostic retained")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    if not options.apply:
        raise ValueError("Explicit reviewed --apply required")
    if (TARGET / "APPLIED.json").exists():
        raise ValueError("Native stage already applied; never overlay existing state")
    check = mysql(b"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='bitrix_genuine_v1'; SELECT COUNT(*) FROM mysql.user WHERE User='mbk_bitrix_genuine';")
    if check.split() != [b"0", b"0"]:
        raise ValueError("Native schema or actor already exists; do not overlay")
    provision = (TARGET / "provision-new-schema.sql").read_bytes()
    mysql(provision)
    schema = (TARGET / "schema.sql").read_bytes()
    metadata = (TARGET / "metadata.sql").read_bytes()
    mysql(b"USE bitrix_genuine_v1;\n" + schema + metadata)
    counts = mysql(b"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='bitrix_genuine_v1'; SELECT COUNT(*) FROM bitrix_genuine_v1.b_agent; SELECT COUNT(*) FROM bitrix_genuine_v1.b_event; SELECT COUNT(*) FROM bitrix_genuine_v1.b_module_to_module; SELECT COUNT(*) FROM bitrix_genuine_v1.b_crm_lead;").split()
    if counts != [b"373", b"0", b"0", b"0", b"0"]:
        raise ValueError("Unexpected native initial schema/automation/lead state")
    volumes = ["mbk-genuine-bitrix-modules-20261106-v1", "mbk-genuine-bitrix-upload-20261106-v1"]
    for volume in volumes:
        if subprocess.run(["docker", "volume", "inspect", volume], capture_output=True, check=False).returncode == 0:
            raise ValueError("Native owned volume already exists")
        subprocess.run(["docker", "volume", "create", "--label", "mbk.local.data-origin=synthetic-native-bootstrap", volume], capture_output=True, check=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                    "--mount", f"type=bind,source={TARGET / 'modules.tar'},target=/modules.tar,readonly",
                    "--mount", f"type=volume,source={volumes[0]},target=/modules",
                    "--mount", f"type=volume,source={volumes[1]},target=/upload",
                    "--user", "0:0", "mbk-local/bitrix:8.4", "sh", "-eu", "-c",
                    "tar -xf /modules.tar -C /modules --no-same-owner\nchown 1000:1000 /upload"],
                   capture_output=True, check=True, timeout=90)
    report = {"status": "applied", "schema": "bitrix_genuine_v1", "table_count": 373,
              "initial_agents": 0, "initial_mail_events": 0, "initial_persistent_handlers": 0,
              "initial_native_leads": 0, "volumes": volumes,
              "schema_sha256": hashlib.sha256(schema).hexdigest(),
              "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
              "modules_archive_sha256": hashlib.sha256((TARGET / "modules.tar").read_bytes()).hexdigest()}
    (TARGET / "APPLIED.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
