"""Record native stock automation metadata and optionally apply scoped execution guards."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "logs/local-stack-2026-11-06/official-trial/runtime-v1"


def query(sql: str) -> list[list[str]]:
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-database-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -B -r -uroot bitrix_official_v1'],
                            input=sql.encode(), capture_output=True, check=True, timeout=20)
    return [line.split("\t") for line in result.stdout.decode().splitlines()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--apply-guards", action="store_true")
    options = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", options.label):
        raise ValueError("Invalid inventory label")
    tables = [item[0] for item in query("SHOW TABLES")]
    names = ["b_crm_lead", "b_agent", "b_event", "b_module_to_module", "b_bp_workflow_template", "b_bp_workflow_instance", "b_bp_workflow_state"]
    counts = {name: int(query(f"SELECT COUNT(*) FROM {name}")[0][0]) if name in tables else None for name in names}
    # Handler class/method identifiers are metadata, not raw event payloads or user fields.
    handlers = query("SELECT FROM_MODULE_ID,MESSAGE_ID,TO_MODULE_ID,TO_CLASS,TO_METHOD FROM b_module_to_module ORDER BY ID")
    agents = query("SELECT ID,ACTIVE,COALESCE(LAST_EXEC,''),COALESCE(NEXT_EXEC,''),NAME FROM b_agent ORDER BY ID")
    agent_state = [{"id": int(row[0]), "active": row[1], "last_exec": row[2], "next_exec": row[3], "callback_sha256": hashlib.sha256(row[4].encode()).hexdigest()} for row in agents]
    report: dict[str, object] = {"scope": "official_native_installed_metadata", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                                "table_count": len(tables), "counts": counts, "agents": agent_state,
                                "persistent_handlers": handlers, "business_rows_deleted": False,
                                "actor_ids": query("SELECT ID,ACTIVE FROM b_user WHERE LOGIN='local_native_admin'")}
    if options.apply_guards:
        timestamp = int(time.time())
        query("INSERT INTO b_option(MODULE_ID,NAME,VALUE) VALUES('main','check_agents','N'),('main','check_events','N'),"
              f"('main_sec','WWALL_ACTUALIZE_RULES','{timestamp}'),('main_sec','SEC_ACTUALIZE_VENDOR_NOTIFICATIONS','{timestamp}') "
              "ON DUPLICATE KEY UPDATE VALUE=VALUES(VALUE)")
        report["guards_applied"] = {"main_check_agents": "N", "main_check_events": "N", "offline_freshness_timestamp": timestamp,
                                    "security_updates_performed": False, "license_fields_modified": False}
    report["main_execution_options"] = query("SELECT NAME,VALUE FROM b_option WHERE MODULE_ID='main' AND NAME IN('check_agents','check_events') ORDER BY NAME")
    (TARGET / f"{options.label}-inventory.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scope": report["scope"], "table_count": len(tables), "counts": counts, "guards_applied": options.apply_guards}))


if __name__ == "__main__":
    main()
