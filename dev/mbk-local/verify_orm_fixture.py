"""Assert reviewed fixture columns/defaults/keys against live MySQL metadata."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs/local-stack-2026-11-06"


def main() -> None:
    fixture = (ROOT / "dev/mbk-local/synthetic-crm-write-fixture.sql").read_text(encoding="utf-8")
    metadata = json.loads((LAB / "write-orm-metadata.json").read_text(encoding="utf-8-sig"))
    expected_columns: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    expected_keys: set[tuple[str, str, int, int, str]] = set()
    definitions = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \(([\s\S]*?)\n\) ENGINE", fixture)
    for table, definition in definitions:
        for line in definition.splitlines():
            line = line.strip().rstrip(",")
            key = re.fullmatch(r"(PRIMARY KEY|UNIQUE|INDEX)\s*(\w+)?\s*\(([^)]+)\)", line)
            if key:
                kind, name, columns = key.groups()
                name = "PRIMARY" if kind == "PRIMARY KEY" else name
                for position, column in enumerate(columns.split(","), 1):
                    expected_keys.add((table, name, int(kind == "INDEX"), position, column.strip()))
                continue
            column = re.fullmatch(r"([A-Z_]+)\s+(INT|TIMESTAMP|DATETIME|TEXT|CHAR\(\d+\)|VARCHAR\(\d+\))(.*)", line)
            if column:
                name, data_type, options = column.groups()
                default_match = re.search(r"DEFAULT\s+(?:'([^']*)'|(\d+))", options)
                default = (default_match[1] if default_match[1] is not None else default_match[2]) if default_match else None
                expected_columns[table, name] = (data_type.lower(), "NO" if "NOT NULL" in options else "YES", default)
            elif line:
                raise ValueError("Unrecognised fixture DDL line; review before applying")
    actual_columns = {(row["table"], row["name"]): (row["type"], row["null"], row["default"])
                      for row in metadata["columns"]}
    actual_keys = {(row["table"], row["index"], row["non_unique"], row["position"], row["column"])
                   for row in metadata["indexes"]}
    if actual_columns != expected_columns or actual_keys != expected_keys:
        raise ValueError("Existing fixture schema differs in columns/defaults/nullability or keys")
    if metadata["group110_members"] != 0:
        raise ValueError("Group110 already has members; do not import or clean existing rows")
    result = {"status": "passed", "evidence_level": "native_apps_pre_receiver",
              "tables": sorted({table for table, _ in expected_columns}),
              "columns_checked": len(expected_columns), "index_columns_checked": len(expected_keys),
              "group110_empty_before_insert": True}
    (LAB / "write-orm-schema-assert.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
