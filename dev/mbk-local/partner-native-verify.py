"""Verify only the two new native-partner services; never submit leads or run workers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "dev/mbk-local/partner-native-prepared/native20261106v1"
NETWORK = "mbk-local-lab_isolated"
APP = "mbk-partner-native-v1-partners-native-1"
BUYER = "mbk-partner-native-v1-partner-native-buyer-1"


def run(command: list[str], *, timeout: int = 40) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Native-partner verification command failed; no env was printed")
    return result.stdout.strip()


def inspect_service(name: str) -> dict[str, object]:
    value = json.loads(run(["docker", "inspect", name]))[0]
    config, host = value["Config"], value["HostConfig"]
    networks = list(value["NetworkSettings"]["Networks"])
    if (networks != [NETWORK] or host["ReadonlyRootfs"] is not True
            or config["User"] != "1000:1000" or host.get("PortBindings")
            or value["State"]["Running"] is not True or "ALL" not in host["CapDrop"]):
        raise ValueError("Native service isolation metadata mismatch")
    environment = dict(item.split("=", 1) for item in config["Env"] if "=" in item)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if environment.get(key):
            raise ValueError("Unexpected proxy in native service")
    mounts = [{"destination": item["Destination"], "read_write": item["RW"], "type": item["Type"],
               "volume_name": item.get("Name")} for item in value["Mounts"]]
    if name == APP:
        for path in ("/app", "/app/vendor"):
            if not any(item["destination"] == path and item["read_write"] is False for item in mounts):
                raise ValueError("Native source/vendor mount is not read-only")
        if (environment.get("DB_DATABASE") != "partners_genuine_v1"
                or environment.get("QUEUE_CONNECTION") != "database"):
            raise ValueError("Native database/queue scope mismatch")
    # Never persist Config.Env, raw inspect output or host filesystem paths.
    return {"container": name, "image_id": value["Image"], "running": True, "networks": networks,
            "nonroot": True, "read_only_root": True, "published_ports": False, "mounts": mounts}


def main() -> None:
    network = json.loads(run(["docker", "network", "inspect", NETWORK]))[0]
    if network["Internal"] is not True:
        raise ValueError("Lab network is not internal")
    services = [inspect_service(name) for name in (APP, BUYER)]
    target = "77.88.55.242"
    # A fresh positive host control makes the negative container sockets meaningful.
    with socket.create_connection((target, 443), timeout=5):
        pass
    control_id = uuid4().hex
    probes: dict[str, object] = {}
    for name, command in [
        (APP, ["php", "/local/runtime-probe.php"]),
        (BUYER, ["python", "/app/network-probe.py"]),
    ]:
        output = run(["docker", "exec", "-e", "MBK_EGRESS_TEST_HOST=" + target,
                      "-e", "MBK_EGRESS_CONTROL_ID=" + control_id, name, *command], timeout=55)
        probes[name] = json.loads(output)
    # Only GET readiness checks; the buyer remains unarmed and receives no lead.
    code = """
import json
from urllib.request import Request,urlopen
out={}
for label,url in [('buyer','http://partner-native-buyer:8000/health'),('capacity','http://partners-native:8000/api/day-limit-distribution/bankruptcy')]:
    with urlopen(Request(url,headers={'Accept':'application/json'}),timeout=15) as response:
        out[label]={'status':response.status,'body':json.loads(response.read())}
print(json.dumps(out))
"""
    health = json.loads(run(["docker", "exec", BUYER, "python", "-c", code]))
    if (health["buyer"]["body"]["armed"] is not False or health["buyer"]["body"]["receipt_count"] != 0
            or health["capacity"]["body"]["more"] is not True):
        raise ValueError("Unexpected pre-lead health/capacity result")
    report = {"status": "native_partners_ready_awaiting_genuine_id", "evidence_level": "native_apps_reader",
              "data_source": "user_fixture", "verified_at": datetime.now(timezone.utc).isoformat(),
              "network_internal": True, "host_positive_control": {"control_id": control_id, "target": target},
              "services": services, "probes": probes, "health": health,
              "lead_posts": 0, "buyer_armed": False, "workers_started": 0}
    (FOLDER / "pre-lead-runtime-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "network_internal": True,
                      "containers_checked": len(services), "host_positive_control": True,
                      "egress_checks_passed": True, "buyer_armed": False, "lead_posts": 0}))


if __name__ == "__main__":
    main()
