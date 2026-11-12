"""Create DDL-only local schemas plus validated migration filenames, never leads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs" / "local-stack-2026-11-06"
CREATE_TABLE = re.compile(r"^CREATE TABLE (?:IF NOT EXISTS )?`([a-zA-Z0-9_]+)` \([\s\S]*?^\) ENGINE=[^;]+;", re.MULTILINE)
MIGRATION_NAME = re.compile(r"\d{4}_\d{2}_\d{2}_\d{6}_[a-z0-9_]+")
MIGRATION_ROW = re.compile(r"\((\d+),\s*'([a-z0-9_]+)',\s*(\d+)\)")


def prepare(project: int) -> dict[str, object]:
    if project == 3:
        source = LAB / "sources/project-3/auto_test_mbk_partners/auto_test_data/dump_partners_gitlab.sql"
        body = source.read_text(encoding="utf-8")
        migration_statements = re.findall(r"^INSERT INTO `migrations` VALUES (.*);$", body, re.MULTILINE)
        rows: list[dict[str, object]] = []
        for statement in migration_statements:
            matches = list(MIGRATION_ROW.finditer(statement))
            remainder = MIGRATION_ROW.sub("", statement).strip().strip(",")
            if remainder.replace(",", "").strip():
                raise ValueError("Unexpected migration INSERT syntax")
            rows.extend({"id": int(match[1]), "migration": match[2], "batch": int(match[3])} for match in matches)
    else:
        source = LAB / "site-ddl-only.sql"
        body = source.read_text(encoding="utf-8")
        rows = json.loads((LAB / "site-migration-history.json").read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("Expected migration history list")
    definitions = list(CREATE_TABLE.finditer(body))
    if not definitions:
        raise ValueError("No reviewed CREATE TABLE definitions found")
    statements = ["SET FOREIGN_KEY_CHECKS=0;"]
    for definition in definitions:
        ddl = re.sub(r"\bAUTO_INCREMENT=\d+", "AUTO_INCREMENT=1", definition[0])
        if re.search(r"FEDERATED|DATA\s+DIRECTORY|INTO\s+OUTFILE|LOAD\s+DATA", ddl, re.IGNORECASE):
            raise ValueError("Unexpected external-data SQL option")
        statements.append(ddl)
    for row in rows:
        migration = row.get("migration") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("id"), int)
                or not isinstance(row.get("batch"), int) or not isinstance(migration, str)
                or not MIGRATION_NAME.fullmatch(migration)):
            raise ValueError("Migration history contains unexpected fields")
        statements.append(f"INSERT INTO `migrations` (`id`,`migration`,`batch`) VALUES ({row['id']},'{row['migration']}',{row['batch']});")
    statements.append("SET FOREIGN_KEY_CHECKS=1;")
    output = ("\n\n".join(statements) + "\n").encode("utf-8")
    target = LAB / f"project-{project}-schema-only.sql"
    target.write_bytes(output)
    result: dict[str, object] = {
        "project": project,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "schema_only_sha256": hashlib.sha256(output).hexdigest(),
        "tables": [definition[1] for definition in definitions],
        "migration_history_rows": len(rows),
        "customer_rows_copied": 0,
        "campaign_rows_copied": 0,
        "auth_rows_copied": 0,
    }
    (LAB / f"project-{project}-schema-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=int, choices=[1, 3])
    options = parser.parse_args()
    print(json.dumps(prepare(options.project)))


if __name__ == "__main__":
    main()
