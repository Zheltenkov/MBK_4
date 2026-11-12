"""Prepare byte-verified module archive and DDL-only fresh native bootstrap."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import re
import tarfile
import time


ROOT = Path(__file__).resolve().parents[3]
LOG = ROOT / "logs/local-stack-2026-11-06"
TARGET = LOG / "bitrix-genuine-v1"
SOURCE = LOG / "sources/project-2/bitrix/modules"
PIN = "240fd2ae748e1b383b7de0018824c0c01987f183"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-only", action="store_true")
    options = parser.parse_args()
    if (TARGET / "APPLIED.json").exists() and not options.sources_only:
        raise ValueError("Applied metadata is immutable; use --sources-only for source acquisition additions")
    records = [json.loads(line) for line in (LOG / "native-source-manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    selected: dict[str, dict[str, object]] = {}
    for item in records:
        path = str(item["path"])
        if not path.startswith("bitrix/modules/") or item["ref"] != PIN:
            raise ValueError("Unexpected source manifest scope/pin")
        selected[path] = item
    # Only exact manifest members enter the runtime volume, never other customer files.
    files: list[dict[str, object]] = []
    archive_name = "modules-refresh.tar" if options.sources_only else "modules.tar"
    with tarfile.open(TARGET / archive_name, "w") as archive:
        for path, item in sorted(selected.items()):
            relative = path.removeprefix("bitrix/modules/")
            source = SOURCE / relative
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("Source byte hash mismatch")
            if source.name in {"license_key.php", "dbconn.php"} or source.name.startswith(".env"):
                raise ValueError("Forbidden runtime configuration in module archive")
            archive.add(source, arcname=relative, recursive=False)
            files.append({"path": relative, "sha256": digest, "bytes": len(content)})
    if options.sources_only:
        refresh = {"source_pin": PIN, "files": files, "archive_sha256": hashlib.sha256((TARGET / archive_name).read_bytes()).hexdigest()}
        (TARGET / "SOURCE-REFRESH-MANIFEST.json").write_text(json.dumps(refresh, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "sources_prepared", "files": len(files), "metadata_changed": False}))
        return
    statements: list[str] = ["SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION';"]
    sources: list[dict[str, object]] = []
    table_count = 0
    for module in ["main", "currency", "crm"]:
        candidate = SOURCE / module / "install/db/mysql/install.sql"
        if not candidate.exists():
            candidate = SOURCE / module / "install/mysql/install.sql"
        content = candidate.read_bytes()
        text = content.decode("utf-8-sig")
        ddl = re.findall(r"\bCREATE\s+TABLE\s+.*?;", text, flags=re.IGNORECASE | re.DOTALL)
        if not ddl:
            raise ValueError("Missing native DDL")
        statements.extend(ddl)
        table_count += len(ddl)
        sources.append({"path": candidate.relative_to(SOURCE).as_posix(), "sha256": hashlib.sha256(content).hexdigest(), "tables": len(ddl)})
    (TARGET / "schema.sql").write_text("\n\n".join(statements) + "\n", encoding="utf-8")
    timestamp = int(time.time())
    # Minimal synthetic installation metadata; no lead, contact, workflow, or customer data.
    metadata = f"""INSERT INTO b_culture(ID,CODE,NAME,FORMAT_DATE,FORMAT_DATETIME,FORMAT_NAME,CHARSET,WEEK_START) VALUES(1,'ru','Local Russian','DD.MM.YYYY','DD.MM.YYYY HH:MI:SS','#NAME# #LAST_NAME#','UTF-8',1);
INSERT INTO b_language(LID,DEF,ACTIVE,NAME,CULTURE_ID,CODE) VALUES('ru','Y','Y','Local Russian',1,'ru');
INSERT INTO b_lang(LID,DEF,ACTIVE,NAME,DIR,LANGUAGE_ID,CULTURE_ID,SERVER_NAME,SITE_NAME,DOC_ROOT) VALUES('s1','Y','Y','Local synthetic site','/','ru',1,'bitrix-genuine','Local synthetic site','/srv/native');
INSERT INTO b_module(ID,DATE_ACTIVE) VALUES('main',NOW()),('currency',NOW()),('crm',NOW());
INSERT INTO b_option(MODULE_ID,NAME,VALUE) VALUES('main','check_agents','N'),('main','check_events','N'),('main','site_name','Local synthetic site'),('main','server_name','bitrix-genuine'),('main','email_from','lab@invalid.example'),('main_sec','WWALL_ACTUALIZE_RULES','{timestamp}'),('main_sec','SEC_ACTUALIZE_VENDOR_NOTIFICATIONS','{timestamp}');
INSERT INTO b_group(ID,ACTIVE,NAME,STRING_ID) VALUES(1,'Y','Local administrators','local_administrators'),(2,'Y','Local anonymous','local_anonymous');
INSERT INTO b_user(ID,LOGIN,PASSWORD,ACTIVE,NAME,LAST_NAME,EMAIL,DATE_REGISTER,LID,XML_ID,BLOCKED) VALUES(1,'local_native_actor','!disabled-generated-local-account','N','Local','Synthetic','lab@invalid.example',NOW(),'ru','synthetic_native_actor','Y');
INSERT INTO b_user_group(USER_ID,GROUP_ID) VALUES(1,1);
"""
    (TARGET / "metadata.sql").write_text(metadata, encoding="utf-8")
    manifest = {"status": "prepared_not_applied", "source_pin": PIN, "files": files,
                "ddl_sources": sources, "ddl_tables": table_count,
                "security_offline_freshness_timestamp": timestamp,
                "security_updates_performed": False, "customer_hooks_imported": False,
                "native_leads_inserted": 0}
    (TARGET / "PREPARE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared_not_applied", "files": len(files), "ddl_tables": manifest["ddl_tables"]}))


if __name__ == "__main__":
    main()
