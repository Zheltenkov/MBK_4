"""Prepare native-partner DDL and synthetic dictionaries offline; never connect to a DB."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "logs/local-stack-2026-11-06/sources/project-3"
MIGRATIONS = SOURCES / "www/database/migrations"
OUTPUT = Path(__file__).resolve().parent / "partner-native-prepared"
CREATE = re.compile(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?`?(\w+)`?\s*\(", re.I)
REFERENCE = re.compile(r"REFERENCES\s+`?(\w+)`?\s*\(", re.I)


@dataclass(frozen=True)
class Source:
    path: str
    sha256: str


def read_source(path: Path, seen: list[Source], *, migration: bool = False) -> str:
    """Bind generated schema to the cached source and exclude migration down()."""
    body = path.read_bytes()
    seen.append(Source(str(path.relative_to(ROOT)), hashlib.sha256(body).hexdigest()))
    text = body.decode("utf-8")
    return text.split("public function down", 1)[0] if migration else text


def statement_at(text: str, start: int) -> str:
    """Find a SQL terminator without splitting quoted COMMENT/default strings."""
    quote: str | None = None
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == ";":
            return text[start:index + 1]
        index += 1
    raise ValueError("Unterminated source SQL statement")


def collect_creates(text: str, tables: dict[str, str]) -> None:
    for match in CREATE.finditer(text):
        name = match.group(1)
        if name in tables:
            raise ValueError(f"Duplicate table definition: {name}")
        sql = statement_at(text, match.start())
        # Remove snapshot counters only; column AUTO_INCREMENT remains native.
        sql = re.sub(r"\s+AUTO_INCREMENT=\d+", "", sql, flags=re.I)
        sql = re.sub(r"CREATE TABLE\s+IF NOT EXISTS", "CREATE TABLE", sql, count=1, flags=re.I)
        if re.search(r"FEDERATED|DATA\s+DIRECTORY|INTO\s+OUTFILE|LOAD\s+DATA|https?://", sql, re.I):
            raise ValueError("Unsafe/external source DDL option")
        tables[name] = sql


def sorted_tables(tables: dict[str, str]) -> list[str]:
    """Create foreign-key parents first; do not disable foreign-key checking."""
    pending = dict(tables)
    completed: list[str] = []
    while pending:
        ready = sorted(name for name, sql in pending.items()
                       if set(REFERENCE.findall(sql)) - {name} <= set(completed))
        if not ready:
            raise ValueError("Missing/cyclic native foreign-key dependency")
        for name in ready:
            completed.append(name)
            del pending[name]
    return completed


def alters_for(text: str, allowed_tables: set[str]) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"ALTER TABLE\s+`?(\w+)`?", text, re.I):
        if match.group(1) in allowed_tables:
            sql = statement_at(text, match.start())
            probe = re.sub(r"\bON\s+DELETE\s+(?:CASCADE|RESTRICT|SET\s+NULL|NO\s+ACTION)", "", sql, flags=re.I)
            if re.search(r"\b(?:DROP|DELETE|TRUNCATE|REPLACE|https?)\b", probe, re.I):
                raise ValueError("Only reviewed additive native ALTER statements are allowed")
            result.append(sql)
    return result


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def fixture_sql(run_id: str, calendar_day: date, buyer_url: str) -> str:
    """Seed dictionaries and one buyer/campaign; never seed delivery evidence."""
    label = quoted("local-native-" + run_id)
    day = quoted(calendar_day.isoformat())
    buyer = quoted(buyer_url)
    return f"""-- data_source=user_fixture; local test parameters, not production settings.
-- EMPTY dedicated native-partner schema only. NO leads/sent/queues/history seed.
SET NAMES utf8mb4;
START TRANSACTION;
INSERT INTO partner_programs (id,name) VALUES (3,'Local BFL'),(5,'Local BFL Regions');
INSERT INTO tag_types (id,name) VALUES
 (1,'Программа'),(2,'Регион'),(3,'Источник'),(4,'День'),(5,'Сумма долга'),(6,'Время');
INSERT INTO tags (id,tag_type_id,value) VALUES
 (1,4,'Рабочий день'),(2,4,'Выходной'),(3,2,'Москва'),(6,2,'Московская область'),
 (92,2,'Москва и область'),(93,2,'Все, кроме Москвы и области'),(94,5,'От 400к'),
 (100,6,'Дневная кампания'),(950,1,'БФЛ');
INSERT INTO regions (id,tag_id,code,aliases,pattern) VALUES
 (1,3,'77','["г Москва"]','^москва$'),
 (2,6,'50','["Московская обл"]','московск');
INSERT INTO tag_settings (id,tag_id,hands_count,delay_minutes,frozen_days,duplicate_days)
 VALUES (200,950,1,0,1,2);
INSERT INTO campaign_statuses (id,name) VALUES
 (1,'Draft local'),(2,'Active local'),(3,'Paused local'),(4,'Archived local');
INSERT INTO queue_statuses (id,name) VALUES (1,'Queued local'),(2,'Delivered local');
INSERT INTO queue_tag_sources (id,name) VALUES (1,'Lead local'),(2,'Campaign local');
INSERT INTO transaction_types (id,sign,name) VALUES
 (1,1,'Fixture topup'),(2,1,'Fixture correction up'),(3,-1,'Fixture correction down'),(4,-1,'Fixture lead charge');
INSERT INTO partners (id,name,balance,campaign_name)
 VALUES (991001,'LOCAL_NATIVE_BUYER_FIXTURE',100.00,{label});
INSERT INTO partner_settings (id,partner_id,is_active,partner_program_id,callback_url,email_to_send)
 VALUES (110,991001,1,3,{buyer},NULL);
INSERT INTO campaigns (id,partner_id,status_id,name,label,priority,price,daily_limit)
 VALUES (950,991001,2,'Local native positive BFL',{label},1,10.00,2);
INSERT INTO campaign_tags (campaign_id,tag_id) VALUES (950,950),(950,92),(950,94);
INSERT INTO balance_transactions (id,partner_id,type_id,amount,comment)
 VALUES (950001,991001,1,100.00,'source=user_fixture; local notional opening balance');
INSERT INTO working_days (day,is_work) VALUES ({day},1);
COMMIT;
-- Counters below must all be zero before any native input is sent.
SELECT 'leads' AS evidence_table, COUNT(*) AS rows_present FROM leads
UNION ALL SELECT 'sent',COUNT(*) FROM sent
UNION ALL SELECT 'distribution_queue',COUNT(*) FROM distribution_queue
UNION ALL SELECT 'delivery_history',COUNT(*) FROM delivery_history
UNION ALL SELECT 'campaign_daily_usage',COUNT(*) FROM campaign_daily_usage
UNION ALL SELECT 'jobs',COUNT(*) FROM jobs;
"""


def prepare(run_id: str, calendar_day: date, buyer_url: str,
            schema_name: str = "partners_genuine_v1") -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid local run ID")
    if not re.fullmatch(r"partners_genuine_v[1-9]\d*", schema_name):
        raise ValueError("Only a dedicated versioned native-partner schema is allowed")
    target = urlsplit(buyer_url)
    if (target.scheme != "http" or target.hostname != "partner-native-buyer" or target.port != 8000
            or target.path != "/receive" or target.username or target.password or target.query or target.fragment):
        raise ValueError("Buyer must be the approved internal local service")
    seen: list[Source] = []
    tables: dict[str, str] = {}
    collect_creates(read_source(SOURCES / "auto_test_mbk_partners/auto_test_data/dump_partners_gitlab.sql", seen), tables)
    definitions = ["2025_04_12_000000_working_days.php", "2026_07_17_000000_bfl_tag_distribution.php",
                   "2026_08_19_000000_partner_adjacents.php", "2026_09_14_000000_partner_logo_send_plan.php"]
    alterations: list[str] = []
    for filename in definitions:
        body = read_source(MIGRATIONS / filename, seen, migration=True)
        collect_creates(body, tables)
        alterations.extend(alters_for(body, {"partners", "sent"}))
    for filename, names in [
        ("2025_10_20_000000_calendar.php", {"sent"}),
        ("2026_09_02_000000_campaign_label.php", {"campaigns"}),
        ("2026_09_25_000000_partner_campaign_name.php", {"partners"}),
    ]:
        alterations.extend(alters_for(read_source(MIGRATIONS / filename, seen, migration=True), names))
    # Laravel's native jobs migration uses Schema::create rather than raw SQL.
    job_source = read_source(MIGRATIONS / "2025_04_27_135716_create_jobs_table.php", seen, migration=True)
    if "Schema::create('jobs'" not in job_source:
        raise ValueError("Unexpected native jobs migration")
    tables["jobs"] = """CREATE TABLE jobs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, queue VARCHAR(255) NOT NULL,
 payload LONGTEXT NOT NULL, attempts TINYINT UNSIGNED NOT NULL,
 reserved_at INT UNSIGNED NULL, available_at INT UNSIGNED NOT NULL,
 created_at INT UNSIGNED NOT NULL, INDEX jobs_queue_index (queue)
) ENGINE=InnoDB;"""
    order = sorted_tables(tables)
    # Approved local compatibility inference; native current DDL is unavailable.
    inferred = "ALTER TABLE leads ADD COLUMN double_count INT NOT NULL DEFAULT 999;"
    schema = "-- OFFLINE PREPARED; EMPTY dedicated native-partner schema ONLY.\nSET NAMES utf8mb4;\n\n"
    schema += "\n\n".join(tables[name] for name in order) + "\n\n"
    schema += "\n\n".join(alterations) + "\n\n"
    schema += "-- source=inferred_from_native_code + user_fixture; NOT native DB parity.\n"
    schema += "-- No unknown phone_uniq index is invented.\n" + inferred + "\n"
    fixture = fixture_sql(run_id, calendar_day, buyer_url)
    # Never overwrite a prepared run: its hashes may already be runtime evidence.
    run_output = OUTPUT / run_id
    run_output.mkdir(parents=True, exist_ok=False)
    (run_output / "schema.sql").write_bytes(schema.encode("utf-8"))
    (run_output / "fixture.sql").write_bytes(fixture.encode("utf-8"))
    manifest: dict[str, object] = {
        "status": "prepared_not_executed", "data_source": "user_fixture", "run_id": run_id,
        "output_hash_basis": "utf8_bytes_lf",
        "schema": schema_name, "tables": order, "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
        "fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
        "sources": [{"path": item.path, "sha256": item.sha256} for item in seen],
        "schema_inferences": ["leads.double_count INT NOT NULL DEFAULT999; no phone_uniq/race parity"],
        "customer_rows_copied": 0, "lead_sent_queue_history_rows_seeded": 0,
        "fixture_parameters": {"hands_count": 1, "delay_minutes": 0, "frozen_days": 1,
                               "duplicate_days": 2, "price": 10, "daily_limit": 2, "opening_balance": 100},
        "ids": {"partner": 991001, "campaign": 950, "program_tag": 950, "program": 3},
        "calendar_day": calendar_day.isoformat(), "execution_requires_critic_and_empty_schema_check": True,
    }
    (run_output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--buyer-url", default="http://partner-native-buyer:8000/receive")
    parser.add_argument("--schema", default="partners_genuine_v1")
    options = parser.parse_args()
    result = prepare(options.run_id, options.day, options.buyer_url, options.schema)
    table_names = result["tables"]
    if not isinstance(table_names, list):
        raise TypeError("Prepared table manifest must be a list")
    print(json.dumps({"status": result["status"], "table_count": len(table_names),
                      "schema_sha256": result["schema_sha256"]}))


if __name__ == "__main__":
    main()
