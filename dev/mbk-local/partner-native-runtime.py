"""Prepare/provision only the new native-partner scope; never submit leads or start workers."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs/local-stack-2026-11-06"
RUNTIME = LAB / "runtime"
APP_ENV = RUNTIME / "partner-native.env"
BUYER_ENV = RUNTIME / "partner-native-buyer.env"
SCHEMA = "partners_genuine_v1"
DB_USER = "mbk_partner_native_v1"
DB_CONTAINER = "mbk-local-lab-database-1"
ENV_NAME = re.compile(r"\benv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]")
SENSITIVE = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|URL|HOST|DSN|PROXY|WEBHOOK|LOGIN|USERNAME|MAIL_")


def write_env(path: Path, values: dict[str, str]) -> None:
    with path.open("x", encoding="utf-8") as target:
        target.write("".join(f"{key}={value}\n" for key, value in sorted(values.items())))


def own_env() -> dict[str, str]:
    values = dict(line.split("=", 1) for line in APP_ENV.read_text(encoding="utf-8").splitlines() if "=" in line)
    if values.get("DB_DATABASE") != SCHEMA or values.get("DB_USERNAME") != DB_USER:
        raise ValueError("Unexpected native-partner scope")
    return values


def prepare_env(run_id: str) -> None:
    """Discover configuration NAMES only, then assign fresh isolated values."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid native run ID")
    if APP_ENV.exists() or BUYER_ENV.exists():
        raise FileExistsError("Native env exists; do not rotate credentials or overwrite configuration")
    source = LAB / "sources/project-3/www"
    names: set[str] = set()
    for folder in (source / "config", source / "app"):
        for file in folder.rglob("*.php"):
            names.update(ENV_NAME.findall(file.read_text(encoding="utf-8", errors="replace")))
    values = {name: "" for name in names if SENSITIVE.search(name)}
    values.update({
        "APP_NAME": "MBK-native-partner-local", "APP_ENV": "local", "APP_DEBUG": "true",
        "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "APP_URL": "http://partners-native:8000", "ASSET_URL": "http://partners-native:8000",
        "DB_CONNECTION": "mysql", "DB_HOST": "database", "DB_PORT": "3306", "DB_DATABASE": SCHEMA,
        "DB_USERNAME": DB_USER, "DB_PASSWORD": secrets.token_hex(24),
        "QUEUE_CONNECTION": "database", "CACHE_DRIVER": "file", "CACHE_STORE": "file",
        "SESSION_DRIVER": "file", "SESSION_DOMAIN": "null", "SESSION_SECURE_COOKIE": "false",
        "LOG_CHANNEL": "single", "LOG_LEVEL": "warning", "BROADCAST_DRIVER": "log",
        "BROADCAST_CONNECTION": "log", "FILESYSTEM_DISK": "local", "MAIL_MAILER": "array",
        "MAIL_DRIVER": "array", "MAIL_HOST": "capture", "MAIL_PORT": "8000",
        "MAIL_FROM_ADDRESS": "native-local@example.invalid", "BITRIX24_URL": "http://capture:8000",
        "BITRIX24_ENDPOINT_ERRORS": "/native-partner-error", "BITRIX_REST_URL": "http://capture:8000",
        "SENTRY_LARAVEL_DSN": "", "TELESCOPE_ENABLED": "false", "MBK_NATIVE_RUN_ID": run_id,
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "http_proxy": "",
        "https_proxy": "", "all_proxy": "", "NO_PROXY": "*", "no_proxy": "*",
    })
    RUNTIME.mkdir(parents=True, exist_ok=True)
    write_env(APP_ENV, values)
    write_env(BUYER_ENV, {"MBK_BUYER_DATA": "/data", "MBK_BUYER_RUN_ID": run_id})
    report = {"status": "env_prepared_no_start", "run_id": run_id, "schema": SCHEMA, "db_user": DB_USER,
              "customer_env_read": False, "generated_local_credentials": True, "variables": sorted(values),
              "leads_submitted": 0, "workers_started": 0, "buyer_initially_armed": False}
    (RUNTIME / "partner-native-env-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "run_id": run_id, "secret_values_printed": False}))


def mysql(sql: str, *, ddl_diagnostics: bool = False) -> str:
    """Root secret stays in the existing container env, never in CLI arguments."""
    command = ["docker", "exec", "-i", DB_CONTAINER, "sh", "-c",
               'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --batch --skip-column-names --default-character-set=utf8mb4']
    result = subprocess.run(command, input=sql, text=True, capture_output=True, check=False, timeout=120)
    if result.returncode:
        # Provision errors may contain a password-bearing SQL statement: suppress those.
        detail = result.stderr[:1200] if ddl_diagnostics else "details suppressed for credential safety"
        raise RuntimeError("Native-partner MySQL command failed: " + detail)
    return result.stdout.strip()


def provision() -> None:
    """Run once, while holding the coordinator's shared-MySQL DDL slot."""
    values = own_env()
    existing = mysql(f"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{SCHEMA}';")
    users = mysql(f"SELECT COUNT(*) FROM mysql.user WHERE User='{DB_USER}';")
    if existing != "0" or users != "0":
        raise ValueError("Dedicated schema/user already exists; no reuse, replacement or cleanup allowed")
    password = values["DB_PASSWORD"]
    if not re.fullmatch(r"[0-9a-f]{48}", password):
        raise ValueError("Unexpected generated local password shape")
    mysql(f"CREATE DATABASE `{SCHEMA}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
          f"CREATE USER '{DB_USER}'@'%' IDENTIFIED BY '{password}';\n"
          f"GRANT ALL PRIVILEGES ON `{SCHEMA}`.* TO '{DB_USER}'@'%';")
    print(json.dumps({"status": "new_empty_schema_and_scoped_user_created", "schema": SCHEMA, "db_user": DB_USER}))


def import_prepared() -> None:
    """Import only checksum-bound DDL/fixtures into an empty dedicated schema."""
    values = own_env()
    run_id = values["MBK_NATIVE_RUN_ID"]
    folder = Path(__file__).resolve().parent / "partner-native-prepared" / run_id
    report = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if report.get("schema") != SCHEMA or report.get("run_id") != run_id or len(report.get("tables", [])) != 37:
        raise ValueError("Prepared manifest scope mismatch")
    schema = (folder / "schema.sql").read_text(encoding="utf-8")
    fixture = (folder / "fixture.sql").read_text(encoding="utf-8")
    for body, field in [(schema, "schema_sha256"), (fixture, "fixture_sha256")]:
        if hashlib.sha256(body.encode()).hexdigest() != report[field]:
            raise ValueError("Prepared artifact hash mismatch")
    count = mysql(f"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='{SCHEMA}';")
    if count != "0":
        raise ValueError("Target is not empty; no import or cleanup was attempted")
    mysql(f"USE `{SCHEMA}`;\n" + schema, ddl_diagnostics=True)
    mysql(f"USE `{SCHEMA}`;\n" + fixture, ddl_diagnostics=True)
    # Independent post-import rows prove no receipt evidence was manufactured.
    tables = ["leads", "sent", "distribution_queue", "delivery_history", "campaign_daily_usage", "jobs"]
    results: dict[str, int] = {}
    for table in tables:
        results[table] = int(mysql(f"SELECT COUNT(*) FROM `{SCHEMA}`.`{table}`;"))
    if any(results.values()):
        raise ValueError("Unexpected business evidence rows after fixture import")
    ready = {"status": "schema_fixture_ready_no_leads_submitted", "schema": SCHEMA, "run_id": run_id,
             "business_counts": results, "workers_started": 0, "schema_sha256": report["schema_sha256"],
             "fixture_sha256": report["fixture_sha256"]}
    (folder / "import-result.json").write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ready))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare-env", "provision", "import"])
    parser.add_argument("--run-id")
    options = parser.parse_args()
    if options.action == "prepare-env":
        if not options.run_id:
            parser.error("--run-id is required for fresh env preparation")
        prepare_env(options.run_id)
    elif options.action == "provision":
        provision()
    else:
        import_prepared()


if __name__ == "__main__":
    main()
