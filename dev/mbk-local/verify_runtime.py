"""Verify live host control, Docker isolation, and each selected PHP runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import subprocess


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "dev/mbk-local/compose.yaml"),
           "-f", str(ROOT / "dev/mbk-local/apps.compose.yaml"), "--profile", "apps"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="77.88.55.242")
    parser.add_argument("services", nargs="*", choices=["site", "crm", "partners", "protocol-receiver", "site-genuine"])
    options = parser.parse_args()
    compose = list(COMPOSE)
    config_files = ["compose.yaml", "apps.compose.yaml", "runtime-probe.php"]
    if "protocol-receiver" in options.services:
        compose = COMPOSE[:-2] + ["-f", str(ROOT / "dev/mbk-local/protocol.compose.yaml"),
                                 "--profile", "apps", "--profile", "protocol"]
        config_files.extend(["protocol.compose.yaml", "protocol_receiver.py", "probe.py"])
    if "site-genuine" in options.services:
        # This overlay adds a process; it does not redirect/recreate the previous site.
        compose.extend(["-f", str(ROOT / "dev/mbk-local/genuine-site.compose.yaml"),
                        "--profile", "genuine-site"])
        config_files.append("genuine-site.compose.yaml")
    destination = ROOT / "logs/local-stack-2026-11-06/runtime-isolation.json"
    if "site-genuine" in options.services:
        destination = destination.with_name("genuine-site-isolation.json")
    # Invalidate an older pass before any operation which may fail on this run.
    destination.write_text(json.dumps({
        "status": "checking", "scope": options.services,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }) + "\n", encoding="utf-8")
    address = ipaddress.ip_address(options.target)
    if not address.is_global:
        raise ValueError("Host positive control requires a public IP address")
    # No container negative result can pass unless this same destination is live now.
    with socket.create_connection((str(address), 443), timeout=4):
        pass
    control_id = secrets.token_hex(12)
    report: dict[str, object] = {
        "status": "checking",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "control_id": control_id,
        "host_positive_control": {"ip": str(address), "port": 443, "reachable": True},
    }
    network_json = subprocess.check_output(
        ["docker", "network", "inspect", "mbk-local-lab_isolated"], text=True, timeout=10
    )
    if json.loads(network_json)[0]["Internal"] is not True:
        raise RuntimeError("Docker network is not internal")
    perimeter = subprocess.run(
        compose + ["--profile", "verify", "run", "--rm", "-T", "-e", f"MBK_EGRESS_TEST_HOST={address}",
                   "-e", f"MBK_EGRESS_CONTROL_ID={control_id}", "probe"],
        capture_output=True, text=True, timeout=45, check=False,
    )
    if perimeter.returncode:
        raise RuntimeError("Perimeter probe failed")
    perimeter_payload = json.loads(perimeter.stdout)
    if perimeter_payload.get("control_id") != control_id:
        raise RuntimeError("Perimeter response did not match current host control")
    report["perimeter"] = perimeter_payload
    runs: dict[str, object] = {}
    for service in options.services:
        container_id = subprocess.check_output(compose + ["ps", "-q", service], text=True, timeout=10).strip()
        if not container_id:
            raise RuntimeError(f"{service} is not running")
        # Inspect only selected fields; never echo Config.Env containing local secrets.
        info = json.loads(subprocess.check_output(
            ["docker", "inspect", container_id], text=True, timeout=10
        ))[0]
        active_files = info["Config"].get("Labels", {}).get("com.docker.compose.project.config_files", "")
        if "protocol.compose.yaml" in active_files and "protocol-receiver" not in options.services:
            raise RuntimeError("Active protocol overlay requires the protocol-receiver verification scope")
        host_config = info["HostConfig"]
        networks = list(info["NetworkSettings"]["Networks"])
        if networks != ["mbk-local-lab_isolated"] or host_config["PortBindings"]:
            raise RuntimeError(f"{service} has unexpected networks or published ports")
        if host_config["Privileged"] or not host_config["ReadonlyRootfs"]:
            raise RuntimeError(f"{service} has unsafe filesystem/privilege settings")
        if host_config.get("ExtraHosts"):
            raise RuntimeError(f"{service} has host address overrides")
        if any("docker.sock" in mount.get("Source", "") for mount in info["Mounts"]):
            raise RuntimeError(f"{service} can access the Docker socket")
        for variable in info["Config"].get("Env", []):
            key, _, value = variable.partition("=")
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"} and value:
                raise RuntimeError(f"{service} has an outbound proxy configured")
        probe_command = ["python", "/app/network-probe.py"] if service == "protocol-receiver" else ["php", "/local/runtime-probe.php"]
        command = compose + ["exec", "-T", "-e", f"MBK_EGRESS_TEST_HOST={address}",
                             "-e", f"MBK_EGRESS_CONTROL_ID={control_id}",
                             service] + probe_command
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode:
            raise RuntimeError(f"{service} runtime isolation probe failed")
        payload = json.loads(result.stdout)
        if payload.get("control_id") != control_id:
            raise RuntimeError("Runtime response did not match the current host control")
        config_bytes = b"".join((ROOT / "dev/mbk-local" / name).read_bytes() for name in config_files)
        runs[service] = {"container_id": info["Id"], "started_at": info["State"]["StartedAt"],
                         "image_id": info["Image"], "local_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                         "actual_env_sha256": hashlib.sha256(json.dumps(sorted(info["Config"].get("Env", [])),
                                                                          separators=(",", ":")).encode()).hexdigest(),
                         "networks": networks, "published_ports": False,
                         "read_only_rootfs": True, "probe": payload}
    report["runtimes"] = runs
    report["config_files_hashed"] = config_files
    report["status"] = "passed"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
