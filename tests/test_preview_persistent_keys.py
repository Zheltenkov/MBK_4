"""The local preview cannot rotate an existing owner authority during a restart."""
from pathlib import Path

import pytest

from site_widget.stand.debt_preview import _persistent_keys


def test_existing_key_bundle_is_reused_without_rewriting(tmp_path: Path) -> None:
    first = _persistent_keys(tmp_path)
    path = tmp_path / "preview-keys.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    second = _persistent_keys(tmp_path)
    keys_match = second == first
    file_unchanged = (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert keys_match, "Persistent preview authority changed"
    assert file_unchanged, "Existing preview authority was rewritten"
    assert set(first) == {"auth_password", "capability_key"}


@pytest.mark.parametrize("contents", ["", "{}", '{"auth_password":"short","capability_key":"bad"}'])
def test_incomplete_existing_authority_fails_closed_without_generating_replacement(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "preview-keys.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        _persistent_keys(tmp_path)
    assert path.read_text(encoding="utf-8") == contents
