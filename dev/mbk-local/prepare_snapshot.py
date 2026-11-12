"""Extract a trusted GitLab archive while excluding known runtime artifacts.

The caller binds the download to a commit; --revision is provenance metadata,
not cryptographic proof that GitLab served that commit. The archive hash records
the actual received bytes. Hardcoded secrets in source require separate review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024


def blocked_path(path: PurePosixPath) -> bool:
    """Keep source code, but omit deployment state and sensitive data artifacts."""
    parts = path.parts
    return (
        any(part in {".git", "node_modules", "storage", "backups"} for part in parts)
        or any(
            part.startswith(".env") or part.endswith(".env") or ".env." in part
            for part in parts
        )
        or any(part in {"mail.private", "auth.json"} for part in parts)
        or path.suffix.lower() in {".sql", ".pem", ".key", ".p12", ".pfx", ".log"}
        or "bootstrap/cache" in path.as_posix()
    )


def extract_snapshot(archive: Path, destination: Path, revision: str) -> dict[str, object]:
    """Copy regular archive files into a new directory, with bounded disk use."""
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the allowed size")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("A complete lowercase Git commit SHA is required")
    # A new directory prevents accidentally replacing a previously reviewed snapshot.
    if destination.exists():
        raise ValueError("Destination already exists; use a new snapshot directory")
    base = destination.resolve()
    included: dict[str, str] = {}
    omitted = 0
    total_bytes = 0
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members if member.name}
        if len(roots) != 1:
            raise ValueError("Expected one GitLab archive root")
        # Validate every member before creating any output directory.
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError("Unsafe archive member path")
            if member.isfile() and member.size > MAX_FILE_BYTES:
                raise ValueError("An archive member exceeds the allowed size")
            total_bytes += member.size if member.isfile() else 0
        if total_bytes > MAX_EXTRACTED_BYTES:
            raise ValueError("Expanded archive exceeds the allowed size")
        base.mkdir(parents=True)
        for member in members:
            relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            if not member.isfile() or not relative.parts or blocked_path(relative):
                omitted += 1
                continue
            output = base.joinpath(*relative.parts).resolve()
            if not output.is_relative_to(base):
                raise ValueError("Archive member escapes the destination")
            # Never follow archive symlinks/hard links or execute source scripts.
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("Could not read an archive member")
            with stream:
                body = stream.read(MAX_FILE_BYTES + 1)
            if len(body) != member.size:
                raise ValueError("Archive member length does not match its metadata")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(body)
            included[relative.as_posix()] = hashlib.sha256(body).hexdigest()
    with archive.open("rb") as archive_stream:
        archive_digest = hashlib.file_digest(archive_stream, "sha256").hexdigest()
    manifest: dict[str, object] = {
        "revision": revision,
        "archive_sha256": archive_digest,
        "regular_files": len(included),
        "omitted_members": omitted,
        "files_sha256": included,
        "known_runtime_artifacts_omitted": True,
        "sql_imported": False,
        "symlinks_imported": False,
    }
    (base / "SNAPSHOT-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {key: value for key, value in manifest.items() if key != "files_sha256"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--revision", required=True)
    options = parser.parse_args()
    print(json.dumps(extract_snapshot(options.archive, options.destination, options.revision)))


if __name__ == "__main__":
    main()
