"""Prepare generated credentials and bounded safe extraction for a fresh official trial."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import re
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TRIAL = ROOT / "logs/local-stack-2026-11-06/official-trial"
TARGET = TRIAL / "runtime-v1"
ARCHIVE_SHA = "e379b86869d4904d919ba1e2daffbaac8be3e15995a67f8b87ada85c7b8692d0"
VOLUME = "mbk-bitrix-official-webroot-20261106-v1"
EXTRACTOR = '''import hashlib,json,tarfile
from pathlib import Path,PurePosixPath
archive=Path('/official.tar.gz')
count=0; total=0; members=[]
with tarfile.open(archive,'r|gz') as source:
 for item in source:
  path=PurePosixPath(item.name)
  if path.is_absolute() or '..' in path.parts or item.isdev() or item.isfifo():
   raise ValueError('Unsafe official archive member')
  if not (item.isdir() or item.isfile()):
   raise ValueError('Unreviewed archive link or special member')
  count+=1; total+=item.size
  if count>500000 or total>8*1024**3:
   raise ValueError('Official extraction exceeded resource bounds')
  source.extract(item,'/destination',filter='data')
for relative in ['bitrix/cache','bitrix/managed_cache','bitrix/stack_cache','upload']:
 (Path('/destination')/relative).mkdir(parents=True,exist_ok=True)
print(json.dumps({'status':'extracted','members':count,'expanded_bytes':total,'links_allowed':False}))
'''


def main() -> None:
    if TARGET.exists():
        raise ValueError("Official runtime already prepared; do not overwrite its generated state")
    manifest = json.loads((TRIAL / "DOWNLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["sha256"] != ARCHIVE_SHA or manifest["distribution"] != "official_public_trial":
        raise ValueError("Unexpected official archive provenance")
    digest = hashlib.sha256()
    with (TRIAL / "bitrix24_encode.tar.gz").open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA:
        raise ValueError("Official archive no longer matches downloaded bytes")
    if subprocess.run(["docker", "volume", "inspect", VOLUME], capture_output=True, check=False).returncode == 0:
        raise ValueError("Official volume already exists")
    TARGET.mkdir()
    password = secrets.token_hex(24)
    wire_source = ROOT / "logs/local-stack-2026-11-06/sources/project-2/lara/app/Services/Bitrix24Api.php"
    route = re.search(r"const\s+string\s+METHOD_REQUEST\s*=\s*'([^']+)'", wire_source.read_text(encoding="utf-8"))
    if route is None or not route[1].startswith("/") or "?" in route[1]:
        raise ValueError("Cannot derive the actual legacy receiver contract")
    values = {"MBK_NATIVE_DB_HOST": "database", "MBK_NATIVE_DB_NAME": "bitrix_official_v1",
              "MBK_NATIVE_DB_USER": "mbk_bitrix_official", "MBK_NATIVE_DB_PASSWORD": password,
              "MBK_NATIVE_HTTP_USER": "official-native-lab", "MBK_NATIVE_HTTP_PASSWORD": secrets.token_hex(24),
              "MBK_NATIVE_ADMIN_USER": "local_native_admin", "MBK_NATIVE_ADMIN_PASSWORD": secrets.token_hex(24),
              "MBK_NATIVE_RECEIVER_PATH": route[1], "MBK_NATIVE_WRITE_ENABLED": "0",
              "MBK_OFFICIAL_ARCHIVE_SHA256": ARCHIVE_SHA,
              "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "http_proxy": "", "https_proxy": "", "all_proxy": "", "NO_PROXY": "*", "no_proxy": "*"}
    (TARGET / "native.env").write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    (TARGET / "provision-new-schema.sql").write_text(
        "CREATE DATABASE bitrix_official_v1 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER 'mbk_bitrix_official'@'%' IDENTIFIED BY '{password}';\n"
        "GRANT ALL ON bitrix_official_v1.* TO 'mbk_bitrix_official'@'%';\n", encoding="utf-8")
    (TARGET / "extract.py").write_text(EXTRACTOR, encoding="utf-8")
    report = {"status": "prepared_not_provisioned", "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
              "archive_sha256": ARCHIVE_SHA, "source": "official_public_trial", "volume": VOLUME,
              "schema": "bitrix_official_v1", "credentials_generated_locally": True,
              "registration_sent": False, "installer_executed": False}
    (TARGET / "PREPARED.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
