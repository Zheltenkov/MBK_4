"""Fresh positive host control and isolation proof for the native Bitrix runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import socket
import subprocess


ROOT = Path(__file__).resolve().parents[3]
LOG = ROOT / "logs/local-stack-2026-11-06/bitrix-genuine-v1"
FILES = ["compose.yaml", "apps.compose.yaml", "protocol.compose.yaml", "bitrix/compose.yaml"]
COMPOSE = ["docker", "compose"]
for name in FILES:
    COMPOSE.extend(["-f", str(ROOT / "dev/mbk-local" / name)])
COMPOSE.extend(["--profile", "apps", "--profile", "protocol", "--profile", "genuine"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", action="store_true")
    options = parser.parse_args()
    service = "bitrix-official" if options.official else "bitrix-genuine"
    compose = list(COMPOSE)
    files = list(FILES)
    if options.official:
        files[-1] = "bitrix/official.compose.yaml"
        compose = ["docker", "compose"]
        for name in files:
            compose.extend(["-f", str(ROOT / "dev/mbk-local" / name)])
        compose.extend(["--profile", "apps", "--profile", "protocol", "--profile", "official"])
    log = ROOT / "logs/local-stack-2026-11-06/official-trial/runtime-v1" if options.official else LOG
    destination = log / "runtime-isolation.json"
    report: dict[str, object] = {"status": "checking", "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    destination.write_text(json.dumps(report), encoding="utf-8")
    target = "77.88.55.242"
    with socket.create_connection((target, 443), timeout=4):
        pass
    control_id = secrets.token_hex(12)
    container = subprocess.check_output(compose + ["ps", "-q", service], text=True, timeout=10).strip()
    if not container:
        raise RuntimeError("Native service is not running")
    info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True, timeout=10))[0]
    network = json.loads(subprocess.check_output(["docker", "network", "inspect", "mbk-local-lab_isolated"], text=True, timeout=10))[0]
    host = info["HostConfig"]
    if not network["Internal"] or list(info["NetworkSettings"]["Networks"]) != ["mbk-local-lab_isolated"]:
        raise RuntimeError("Native service escaped isolated network")
    if host["PortBindings"] or host["Privileged"] or host.get("ExtraHosts") or not host["ReadonlyRootfs"]:
        raise RuntimeError("Unsafe native Docker configuration")
    if any("docker.sock" in mount.get("Source", "") for mount in info["Mounts"]):
        raise RuntimeError("Native service has Docker socket access")
    if info["Config"]["User"] != "1000:1000" or host["CapDrop"] != ["ALL"]:
        raise RuntimeError("Native service has unexpected privilege identity")
    for variable in info["Config"].get("Env", []):
        key, _, value = variable.partition("=")
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"} and value:
            raise RuntimeError("Native service has an outbound proxy")
    result = subprocess.run(compose + ["exec", "-T", "-e", f"MBK_EGRESS_TEST_HOST={target}",
                                      "-e", f"MBK_EGRESS_CONTROL_ID={control_id}", service,
                                      "php", "/lab/runtime-probe.php"], capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise RuntimeError("Native runtime egress probe failed")
    probe = json.loads(result.stdout)
    if probe.get("control_id") != control_id:
        raise RuntimeError("Native runtime probe is stale")
    config_files = files + (["bitrix/official-router.php", "bitrix/official-start.sh", "bitrix/official_http.php", "bitrix/official.ini", "bitrix/native-bootstrap.php", "bitrix/native-metadata.php", "bitrix/native-api.php"] if options.official else ["bitrix/settings.php", "bitrix/dbconn.php", "bitrix/kernel.php", "bitrix/router.php"])
    config_files.extend(["bitrix/php.ini", "runtime-probe.php"])
    report.update({"status": "passed", "scope": service, "container_id": info["Id"],
                   "started_at": info["State"]["StartedAt"], "image_id": info["Image"],
                   "control_id": control_id, "host_positive_control": {"ip": target, "port": 443, "reachable": True},
                   "probe": probe, "local_config_sha256": hashlib.sha256(b"".join((ROOT / "dev/mbk-local" / name).read_bytes() for name in config_files)).hexdigest(),
                   "actual_env_sha256": hashlib.sha256(json.dumps(sorted(info["Config"].get("Env", []))).encode()).hexdigest()})
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "scope": service, "control_id": control_id}))


if __name__ == "__main__":
    main()
