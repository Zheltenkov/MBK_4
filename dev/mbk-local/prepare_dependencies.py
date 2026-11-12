"""Prepare dependency-only build inputs; application code/env stay outside."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs" / "local-stack-2026-11-06"
GITHUB_DIST = re.compile(r"https://api\.github\.com/repos/([^/]+/[^/]+)/zipball/([a-f0-9]{40})$")


def prepare(project: int) -> dict[str, object]:
    source = LAB / "sources" / f"project-{project}" / ("lara" if project == 2 else "www")
    destination = LAB / "dependencies" / f"project-{project}"
    if destination.exists():
        raise ValueError("Dependency workspace already exists")
    original_lock = (source / "composer.lock").read_bytes()
    lock = json.loads(original_lock)
    if not isinstance(lock, dict) or not isinstance(lock.get("packages"), list):
        raise ValueError("Unexpected Composer lock schema")
    destination.mkdir(parents=True)
    # Empty mountpoints are required before layering writable tmpfs on RO sources.
    for directory in ["storage", "bootstrap/cache", "vendor", "database/csv"]:
        (source / directory).mkdir(parents=True, exist_ok=True)
    (destination / "database").mkdir()
    shutil.copyfile(source / "composer.json", destination / "composer.json")
    mirrored: list[str] = []
    codeload_count = 0
    for package in lock["packages"] + lock.get("packages-dev", []):
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise ValueError("Unexpected Composer package schema")
        # Skip a failing GitHub API redirect while preserving the exact commit.
        dist = package.get("dist")
        if isinstance(dist, dict) and isinstance(dist.get("url"), str):
            match = GITHUB_DIST.fullmatch(dist["url"])
            if match:
                dist["url"] = f"https://codeload.github.com/{match[1]}/legacy.zip/{match[2]}"
                codeload_count += 1
        if package["name"].startswith("mbk/"):
            # Only this reviewed private package is supported; do not fall back to Git auth.
            if package["name"] != "mbk/fraud-monitoring":
                raise ValueError("Unreviewed private dependency")
            private_source = source / "vendor" / "mbk" / "fraud-monitoring"
            if not (private_source / "composer.json").is_file():
                raise ValueError("Private package source has not been fetched")
            shutil.copytree(private_source, destination / "private" / "fraud-monitoring")
            package["dist"] = {"type": "path", "url": "/inputs/private/fraud-monitoring"}
            package["transport-options"] = {"symlink": False}
            mirrored.append(package["name"])
    local_lock = (json.dumps(lock, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (destination / "composer.lock").write_bytes(local_lock)
    manifest: dict[str, object] = {
        "project": project,
        "original_lock_sha256": hashlib.sha256(original_lock).hexdigest(),
        "local_lock_sha256": hashlib.sha256(local_lock).hexdigest(),
        "private_packages_local_dist": mirrored,
        "github_dist_direct_codeload": codeload_count,
        "version_constraints_changed": False,
        "application_code_copied": False,
        "env_copied": False,
    }
    (destination / "DEPENDENCY-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=int, choices=[1, 2, 3])
    options = parser.parse_args()
    print(json.dumps(prepare(options.project)))


if __name__ == "__main__":
    main()
