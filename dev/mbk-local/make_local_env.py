"""Generate fresh isolated-runtime configuration without reading customer env."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import secrets


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs" / "local-stack-2026-11-06"
ENV_NAME = re.compile(r"\benv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]")
SENSITIVE_NAME = re.compile(
    r"TOKEN|SECRET|KEY|PASSWORD|URL|HOST|DSN|PROXY|WEBHOOK|LOGIN|USERNAME|MAIL_"
)


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write only newly generated values and never display them in output."""
    if path.exists():
        raise ValueError("Local env already exists; refusing to rotate database credentials")
    path.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8")


def main() -> None:
    runtime = LAB / "runtime"
    if runtime.exists():
        raise ValueError("Runtime directory exists; inspect it instead of overwriting credentials")
    runtime.mkdir(parents=True)
    database_password = secrets.token_hex(24)
    root_password = secrets.token_hex(24)
    write_env(runtime / "mysql.env", {
        "MYSQL_DATABASE": "site",
        "MYSQL_USER": "mbk",
        "MYSQL_PASSWORD": database_password,
        "MYSQL_ROOT_PASSWORD": root_password,
    })
    variables: dict[str, list[str]] = {}
    for name, project, directory in [("site", 1, "www"), ("crm", 2, "lara"), ("partners", 3, "www")]:
        source = LAB / "sources" / f"project-{project}" / directory
        names: set[str] = set()
        # Read variable names only; customer default values are never imported.
        for folder in [source / "config", source / "app"]:
            for path in folder.rglob("*.php"):
                names.update(ENV_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))
        values = {key: "" for key in names if SENSITIVE_NAME.search(key)}
        values.update({
            "APP_NAME": "MBK-local",
            "APP_ENV": "local",
            "APP_DEBUG": "false",
            "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            "APP_URL": f"http://{name}:8000",
            "ASSET_URL": f"http://{name}:8000",
            "BITRIX24_URL": "http://crm:8000" if name == "site" else "http://capture:8000",
            "BITRIX_REST_URL": "http://capture:8000",
            "ELASTICSEARCH_HOSTS": "http://capture:8000",
            "LOG_CHANNEL": "single",
            "LOG_LEVEL": "warning",
            "DB_CONNECTION": "mysql",
            "DB_HOST": "database",
            "DB_PORT": "3306",
            "DB_DATABASE": name,
            "DB_USERNAME": "mbk",
            "DB_PASSWORD": database_password,
            "DB_PROD_HOST": "database",
            "DB_PROD_DATABASE": "crm",
            "DB_PROD_USERNAME": "mbk",
            "DB_PROD_PASSWORD": database_password,
            "CACHE_DRIVER": "file",
            "CACHE_STORE": "file",
            "SESSION_DRIVER": "file",
            "SESSION_DOMAIN": "null",
            "SESSION_SECURE_COOKIE": "false",
            "QUEUE_CONNECTION": "database",
            "BROADCAST_DRIVER": "log",
            "BROADCAST_CONNECTION": "log",
            "FILESYSTEM_DISK": "local",
            "MAIL_MAILER": "array",
            "MAIL_DRIVER": "array",
            "MAIL_HOST": "capture",
            "MAIL_PORT": "8000",
            "MAIL_FROM_ADDRESS": "local@example.invalid",
            "SENTRY_LARAVEL_DSN": "",
            "TELESCOPE_ENABLED": "false",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "NO_PROXY": "*",
            "no_proxy": "*",
        })
        write_env(runtime / f"{name}.env", values)
        variables[name] = sorted(values)
    # The review manifest contains variable names and provenance, never values.
    (runtime / "ENV-MANIFEST.json").write_text(json.dumps({
        "customer_env_read": False,
        "generated_credentials": True,
        "database_initial_data": "empty-schemas-only",
        "workers_enabled": False,
        "variables": variables,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"created_local_env_files": 4, "printed_secret_values": False}))


if __name__ == "__main__":
    main()
