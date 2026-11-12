"""Create fresh CRM config/read user + Laravel queues only inside the official local DB."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs/local-stack-2026-11-06"
ENV = LAB / "runtime/crm-genuine.env"
NATIVE_ENV = LAB / "official-trial/runtime-v1/native.env"
SCHEMA = "bitrix_official_v1"
USER = "mbk_crm_genuine_v1"
ENV_NAME = re.compile(r"\benv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]")
SENSITIVE = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|URL|HOST|DSN|PROXY|WEBHOOK|LOGIN|USERNAME|MAIL_")


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def prepare() -> None:
    if ENV.exists():
        raise FileExistsError("Genuine CRM env exists; do not overwrite credentials")
    native = read_env(NATIVE_ENV)
    if native["MBK_NATIVE_DB_NAME"] != SCHEMA:
        raise ValueError("Unexpected official native schema")
    names: set[str] = set()
    source = LAB / "sources/project-2/lara"
    for folder in (source / "app", source / "config"):
        for path in folder.rglob("*.php"):
            names.update(ENV_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))
    values = {name: "" for name in names if SENSITIVE.search(name)}
    username, password = native["MBK_NATIVE_HTTP_USER"], native["MBK_NATIVE_HTTP_PASSWORD"]
    # BitrixRest has no auth options; local-only URI userinfo uses libcurl Basic.
    rest = "http://" + quote(username, safe="") + ":" + quote(password, safe="") + "@bitrix-official:8000/__lab/rest/"
    db_password = secrets.token_hex(24)
    values.update({
        "APP_NAME": "MBK-genuine-CRM-local", "APP_ENV": "local", "APP_DEBUG": "true",
        "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "APP_URL": "http://crm-genuine:8000", "ASSET_URL": "http://crm-genuine:8000",
        "DB_CONNECTION": "mysql", "DB_HOST": native["MBK_NATIVE_DB_HOST"], "DB_PORT": "3306",
        "DB_DATABASE": SCHEMA, "DB_USERNAME": USER, "DB_PASSWORD": db_password,
        "DB_PROD_HOST": native["MBK_NATIVE_DB_HOST"], "DB_PROD_PORT": "3306",
        "DB_PROD_DATABASE": SCHEMA, "DB_PROD_USERNAME": USER, "DB_PROD_PASSWORD": db_password,
        "BITRIX24_URL": "http://bitrix-official:8000", "BITRIX24_LOGIN": username,
        "BITRIX24_PASSWORD": password, "BITRIX_REST_URL": rest,
        "MICROSERVICE_PARTNER_URL": "http://partners-native:8000",
        "QUEUE_CONNECTION": "database", "CACHE_DRIVER": "file", "CACHE_STORE": "file",
        "SESSION_DRIVER": "file", "SESSION_DOMAIN": "null", "SESSION_SECURE_COOKIE": "false",
        "LOG_CHANNEL": "single", "LOG_LEVEL": "warning", "BROADCAST_DRIVER": "log",
        "BROADCAST_CONNECTION": "log", "FILESYSTEM_DISK": "local", "MAIL_MAILER": "array",
        "MAIL_DRIVER": "array", "MAIL_HOST": "capture", "MAIL_PORT": "8000",
        "MAIL_FROM_ADDRESS": "genuine-crm@example.invalid", "SENTRY_LARAVEL_DSN": "",
        "TELESCOPE_ENABLED": "false", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
        "http_proxy": "", "https_proxy": "", "all_proxy": "", "NO_PROXY": "*", "no_proxy": "*",
    })
    with ENV.open("x", encoding="utf-8") as target:
        target.write("".join(f"{key}={value}\n" for key, value in sorted(values.items())))
    report = {"customer_env_read": False, "official_local_env_read": True, "schema": SCHEMA,
              "db_user": USER, "business_orm_select_only": True, "queue_write_tables": ["jobs", "failed_jobs"],
              "native_adapter_auth": "generated_local_basic", "rest_auth_transport": "local_uri_userinfo",
              "uri_or_secret_values_printed": False, "variables": sorted(values), "workers_started": 0}
    (ENV.parent / "crm-genuine-env-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "crm_genuine_env_prepared_no_start", "schema": SCHEMA,
                      "secret_values_printed": False}))


def mysql(sql: str) -> str:
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-database-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --batch --skip-column-names'],
                            input=sql, text=True, capture_output=True, check=False, timeout=60)
    if result.returncode:
        raise RuntimeError("Scoped CRM provisioning failed; SQL/credential details suppressed")
    return result.stdout.strip()


def provision() -> None:
    """Take the coordinated root-DDL slot; do not touch any native b_ table."""
    values = read_env(ENV)
    if values["DB_DATABASE"] != SCHEMA or values["DB_USERNAME"] != USER:
        raise ValueError("Unexpected CRM database scope")
    if mysql(f"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{SCHEMA}';") != "1":
        raise ValueError("Official schema is absent")
    if mysql(f"SELECT COUNT(*) FROM mysql.user WHERE User='{USER}';") != "0":
        raise ValueError("CRM read user already exists; refusing to rotate/regrant")
    queue_tables = mysql(f"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='{SCHEMA}' "
                         "AND TABLE_NAME IN ('jobs','failed_jobs');")
    if queue_tables != "0":
        raise ValueError("Queue tables already exist; inspect them before any reuse")
    # These definitions mirror the two native Laravel migrations, not Bitrix lead tables.
    mysql(f"""CREATE TABLE `{SCHEMA}`.jobs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 queue VARCHAR(255) NOT NULL, payload LONGTEXT NOT NULL,
 attempts TINYINT UNSIGNED NOT NULL, reserved_at INT UNSIGNED NULL,
 available_at INT UNSIGNED NOT NULL, created_at INT UNSIGNED NOT NULL,
 INDEX jobs_queue_index(queue)
) ENGINE=InnoDB;
CREATE TABLE `{SCHEMA}`.failed_jobs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 uuid VARCHAR(255) NOT NULL UNIQUE, connection TEXT NOT NULL, queue TEXT NOT NULL,
 payload LONGTEXT NOT NULL, exception LONGTEXT NOT NULL,
 failed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;""")
    password = values["DB_PASSWORD"]
    if not re.fullmatch(r"[0-9a-f]{48}", password):
        raise ValueError("Unexpected generated password shape")
    mysql(f"CREATE USER '{USER}'@'%' IDENTIFIED BY '{password}';\n"
          f"GRANT SELECT ON `{SCHEMA}`.* TO '{USER}'@'%';\n"
          f"GRANT INSERT, UPDATE, DELETE ON `{SCHEMA}`.jobs TO '{USER}'@'%';\n"
          f"GRANT INSERT, UPDATE, DELETE ON `{SCHEMA}`.failed_jobs TO '{USER}'@'%';")
    print(json.dumps({"status": "crm_genuine_read_user_and_empty_queues_ready", "schema": SCHEMA,
                      "native_business_tables_modified": False, "workers_started": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "provision"])
    options = parser.parse_args()
    prepare() if options.action == "prepare" else provision()
