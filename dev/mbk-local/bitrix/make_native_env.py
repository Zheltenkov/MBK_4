"""Generate new native-lab credentials and a schema-scoped provisioning script."""

from __future__ import annotations

import json
from pathlib import Path
import secrets


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    target = root / "logs/local-stack-2026-11-06/bitrix-genuine-v1"
    if target.exists():
        raise ValueError("Native runtime directory exists; do not rotate or overwrite its state")
    target.mkdir(parents=True)
    database, user = "bitrix_genuine_v1", "mbk_bitrix_genuine"
    password = secrets.token_hex(24)
    values = {
        "MBK_NATIVE_DB_HOST": "database", "MBK_NATIVE_DB_NAME": database,
        "MBK_NATIVE_DB_USER": user, "MBK_NATIVE_DB_PASSWORD": password,
        "MBK_NATIVE_CRYPTO_KEY": secrets.token_hex(32),
        "MBK_NATIVE_HTTP_USER": "native-lab", "MBK_NATIVE_HTTP_PASSWORD": secrets.token_hex(24),
        "MBK_NATIVE_SOURCE_SHA": "240fd2ae748e1b383b7de0018824c0c01987f183",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
        "http_proxy": "", "https_proxy": "", "all_proxy": "", "NO_PROXY": "*", "no_proxy": "*",
    }
    (target / "native.env").write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    # This script is not executed here and cannot touch any old lab schema.
    sql = (
        f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}';\n"
        f"GRANT ALL ON `{database}`.* TO '{user}'@'%';\n"
    )
    (target / "provision-new-schema.sql").write_text(sql, encoding="utf-8")
    for directory in ["webroot/bitrix/php_interface", "webroot/local/php_interface", "webroot/upload",
                      "webroot/bitrix/cache", "webroot/bitrix/managed_cache", "webroot/bitrix/stack_cache",
                      "webroot/bitrix/modules"]:
        (target / directory).mkdir(parents=True)
    # Readonly parent bind mounts require existing targets for nested file mounts.
    for placeholder in ["webroot/bitrix/.settings.php", "webroot/bitrix/php_interface/dbconn.php",
                        "webroot/bitrix/php_interface/init.php", "webroot/local/php_interface/init.php"]:
        (target / placeholder).touch(exist_ok=False)
    manifest = {"status": "prepared_not_executed", "intended_evidence_level": "genuine_bitrix_runtime",
                "database": database, "database_user": user, "credentials_generated_locally": True,
                "production_config_read": False, "variables": sorted(values),
                "source_sha": values["MBK_NATIVE_SOURCE_SHA"], "license_material_copied": False}
    (target / "ENV-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared_not_executed", "secret_values_printed": False,
                      "new_schema": database, "old_runtime_changed": False}))


if __name__ == "__main__":
    main()
