"""Privacy and scope checks for the local source endpoint inventory."""

from collections.abc import Iterator
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def inventory() -> Iterator[ModuleType]:
    path = Path(__file__).resolve().parents[1] / "dev/mbk-local/inventory_endpoints.py"
    spec = importlib.util.spec_from_file_location("mbk_local_endpoint_inventory_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def test_url_credentials_and_payload_never_enter_evidence(inventory: ModuleType) -> None:
    text = 'send("https://user-secret:pass-secret@api.example.invalid/token-secret?key=query-secret#fragment-secret")'
    rows = inventory.literal_hosts("project-2/lara/app/Job.php", text)
    serialized = json.dumps([inventory.asdict(row) for row in rows])
    assert len(rows) == 1 and rows[0].host == "api.example.invalid"
    assert all(secret not in serialized for secret in ["user-secret", "pass-secret", "token-secret", "query-secret", "fragment-secret"])


def test_invalid_and_interpolated_hosts_do_not_become_confirmed_endpoints(inventory: ModuleType) -> None:
    text = '"https://[invalid/"; "https://{HOST}/path"; "https://api.example.invalid/path"'
    rows = inventory.literal_hosts("project-1/www/resources/views/form.blade.php", text)
    assert [(row.host, row.layer) for row in rows] == [("api.example.invalid", "frontend")]


def test_inventory_does_not_scan_env_or_database_datasets(inventory: ModuleType, tmp_path: Path) -> None:
    base = tmp_path / "project-1/www"
    inputs = {
        "app/Job.php": '"https://allowed.example.invalid/path"',
        "app/.env.json": '"https://env-secret.example.invalid/path"',
        "database/customer.json": '"https://customer-secret.example.invalid/path"',
        "vendor/other/package/a.php": '"https://unreviewed.example.invalid/path"',
    }
    for relative, text in inputs.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    report = inventory.collect(tmp_path)
    assert report["scanned_files"] == 1
    assert [row["host"] for row in report["evidence"]] == ["allowed.example.invalid"]
