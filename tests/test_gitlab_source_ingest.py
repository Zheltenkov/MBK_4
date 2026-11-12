"""Source download integrity and filesystem boundary checks."""

import base64
from collections.abc import Iterator
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def ingest() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location(
        "mbk_source_ingest_test", Path(__file__).resolve().parents[1] / "dev/mbk-local/gitlab_source_ingest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def envelope(ingest: ModuleType, content: bytes, path: str = "bitrix/modules/main/lib/example.php") -> dict[str, object]:
    blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    return {"ref": ingest.REF, "encoding": "base64", "file_path": path,
            "blob_id": blob, "content": base64.b64encode(content).decode()}


def test_preserves_arbitrary_source_bytes_and_replay(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    content = b"<?php /* \xe1\xe8\xf2\xf0\xe8\xea\xf1 */\r\n"
    item = envelope(ingest, content)
    assert store.ingest(item)["status"] == "written"
    assert (tmp_path / "source" / str(item["file_path"])).read_bytes() == content
    assert store.ingest(item)["status"] == "cached"


@pytest.mark.parametrize("path", [
    "bitrix/modules/main/../../outside.php", "/bitrix/modules/main/a.php",
    "bitrix/modules/main/C:/outside.php", "bitrix/php_interface/dbconn.php",
    "local/.env.json", "bitrix/modules/main/license_key.php",
    "bitrix/.settings.php", "local/.settings.php", "bitrix/modules/crm/.settings_extra.php",
    "local/cache/user.php", "local/keys/private.php", "local/private.key",
])
def test_rejects_paths_and_private_configuration(ingest: ModuleType, tmp_path: Path, path: str) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    with pytest.raises(ingest.SourceRejected):
        store.ingest(envelope(ingest, b"<?php", path))
    assert not (tmp_path / "manifest.jsonl").exists()


def test_library_di_settings_are_distinct_from_deployment_config(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    path = "bitrix/modules/crm/.settings.php"
    assert store.ingest(envelope(ingest, b"<?php return ['services' => []];", path))["status"] == "written"


def test_cache_library_class_is_not_runtime_cache_data(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    path = "bitrix/modules/main/lib/data/cache/cacheentry.php"
    assert store.ingest(envelope(ingest, b"<?php class CacheEntry {}", path))["status"] == "written"
    assert not ingest.allowed_path("bitrix/php_interface/cache/private.php")


def test_bad_blob_hash_cannot_write(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    item = {**envelope(ingest, b"<?php"), "blob_id": "0" * 40}
    with pytest.raises(ingest.SourceRejected):
        store.ingest(item)
    assert not (tmp_path / "source").exists()


def test_other_revision_cannot_write(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    with pytest.raises(ingest.SourceRejected):
        store.ingest({**envelope(ingest, b"<?php"), "ref": "main"})


def test_existing_modified_source_is_not_overwritten(ingest: ModuleType, tmp_path: Path) -> None:
    store = ingest.Store(tmp_path / "source", tmp_path / "manifest.jsonl")
    original = envelope(ingest, b"<?php /* original */")
    store.ingest(original)
    with pytest.raises(ingest.SourceRejected):
        store.ingest(envelope(ingest, b"<?php /* different */"))
    assert (tmp_path / "source" / str(original["file_path"])).read_bytes() == b"<?php /* original */"
