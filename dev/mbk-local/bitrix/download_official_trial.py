"""Download a public official trial archive; never register or activate a license."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "logs/local-stack-2026-11-06/official-trial"
# Derived from the official bitrixsetup.php demo branch, not a private download URL.
URL = "https://www.1c-bitrix.ru/download/portal/bitrix24_encode.tar.gz"
MAX_ARCHIVE = 1024 * 1024 * 1024


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = OUTPUT / "bitrix24_encode.tar.gz"
    partial = OUTPUT / "bitrix24_encode.tar.gz.part"
    if archive.exists() or partial.exists():
        raise RuntimeError("Inspect existing download; refusing to overwrite an archive")
    if shutil.disk_usage(OUTPUT).free < 3 * MAX_ARCHIVE:
        raise RuntimeError("Not enough space for a bounded download and source preparation")
    digest = hashlib.sha256()
    total = 0
    with urlopen(URL, timeout=30) as response, partial.open("xb") as stream:
        expected = int(response.headers.get("Content-Length", "0"))
        if expected <= 0 or expected > MAX_ARCHIVE:
            raise RuntimeError("Unexpected official archive length")
        effective_url = response.url
        if not effective_url.startswith("https://www.1c-bitrix.ru/"):
            raise RuntimeError("Unexpected official download redirect")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > expected or total > MAX_ARCHIVE:
                raise RuntimeError("Download exceeded the declared size")
            stream.write(chunk)
            digest.update(chunk)
            if total % (64 * 1024 * 1024) == 0:
                print(json.dumps({"downloaded_bytes": total, "expected_bytes": expected}), flush=True)
    if total != expected:
        raise RuntimeError("Incomplete official archive; partial file is not usable")
    partial.replace(archive)
    manifest = {
        "source_url": URL, "effective_url": effective_url,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "bytes": total, "sha256": digest.hexdigest(),
        "distribution": "official_public_trial", "registration_sent": False,
        "production_license_used": False, "installer_executed": False,
    }
    (OUTPUT / "DOWNLOAD-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
