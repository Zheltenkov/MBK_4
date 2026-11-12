"""Verify internal capture connectivity and lack of external network access."""

from __future__ import annotations

import json
import os
import socket
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> None:
    # A negative socket result has meaning only alongside a fresh host-side control.
    target = os.environ.get("MBK_EGRESS_TEST_HOST")
    control_id = os.environ.get("MBK_EGRESS_CONTROL_ID")
    if not target or not control_id:
        raise RuntimeError("Run the host verification runner; positive control is required")
    # Poll the service condition instead of sleeping for an assumed startup time.
    for attempt in range(20):
        try:
            with urlopen("http://capture:8000/health", timeout=2) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError):
            if attempt == 19:
                raise
            time.sleep(0.2)
    else:
        raise RuntimeError("Local capture service did not become ready")

    request = Request(
        "http://capture:8000/local-network-check",
        data=b'{"case":"network-isolation-check"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("Internal capture request failed")

    # A separate host-side control checks this public TCP endpoint is reachable.
    try:
        connection = socket.create_connection(
            (target, 443), timeout=3
        )
    except OSError:
        blocked = True
    else:
        connection.close()
        blocked = False
    if not blocked:
        raise RuntimeError("External egress is available; do not start MBK apps")
    host_checks = []
    for host, port in [("172.18.0.1", 80), ("172.18.0.1", 443), ("172.18.0.1", 2082),
                       ("host.docker.internal", 2082), ("gateway.docker.internal", 2082)]:
        try:
            connection = socket.create_connection((host, port), timeout=2)
        except OSError:
            host_checks.append({"host": host, "port": port, "blocked": True})
        else:
            connection.close()
            raise RuntimeError("A host or gateway service is reachable")
    print(json.dumps({"control_id": control_id, "internal_capture": True,
                      "external_tcp_blocked": blocked, "host_checks": host_checks}))


if __name__ == "__main__":
    main()
