"""Provision an empty isolated trial schema and extract official source without executing it."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TRIAL = ROOT / "logs/local-stack-2026-11-06/official-trial"
TARGET = TRIAL / "runtime-v1"
VOLUME = "mbk-bitrix-official-webroot-20261106-v1"


def mysql(content: bytes) -> bytes:
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-database-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -B -uroot'],
                            input=content, capture_output=True, timeout=30, check=False)
    if result.returncode:
        (TARGET / "mysql-private-error.log").write_bytes(result.stderr)
        raise RuntimeError("Official trial MySQL provisioning failed; private diagnostic retained")
    return result.stdout


def main() -> None:
    if (TARGET / "APPLIED.json").exists():
        raise ValueError("Official trial was already provisioned; no overlay/reinstall")
    if subprocess.run(["docker", "volume", "inspect", VOLUME], capture_output=True, check=False).returncode == 0:
        raise ValueError("Official trial volume already exists")
    counts = mysql(b"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='bitrix_official_v1'; SELECT COUNT(*) FROM mysql.user WHERE User='mbk_bitrix_official';").split()
    if counts != [b"0", b"0"]:
        raise ValueError("Official trial schema or DB user already exists")
    mysql((TARGET / "provision-new-schema.sql").read_bytes())
    subprocess.run(["docker", "volume", "create", "--label", "mbk.local.data-origin=official-public-trial", VOLUME], capture_output=True, check=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                    "--user", "0:0", "--mount", f"type=volume,source={VOLUME},target=/destination",
                    "mbk-local/bitrix:8.4", "chown", "1000:1000", "/destination"], capture_output=True, check=True, timeout=15)
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--user", "1000:1000",
                             "--mount", f"type=volume,source={VOLUME},target=/destination",
                             "--mount", f"type=bind,source={TRIAL / 'bitrix24_encode.tar.gz'},target=/official.tar.gz,readonly",
                             "--mount", f"type=bind,source={TARGET / 'extract.py'},target=/extract.py,readonly",
                             "python:3.12-slim", "python", "-B", "/extract.py"], capture_output=True, check=False, timeout=300)
    if result.returncode:
        (TARGET / "extract-private-error.log").write_bytes(result.stderr)
        raise RuntimeError("Official extraction failed; retain partial own volume for diagnosis")
    extraction = json.loads(result.stdout)
    prepared = json.loads((TARGET / "PREPARED.json").read_text(encoding="utf-8"))
    prepared.update({"status": "provisioned_not_executed", "extraction": extraction, "initial_tables": 0})
    (TARGET / "APPLIED.json").write_text(json.dumps(prepared, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(prepared))


if __name__ == "__main__":
    main()
