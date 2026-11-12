"""Exercise authenticated original native prolog and retain bounded diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "logs/local-stack-2026-11-06/bitrix-genuine-v1"
CLIENT = r"""<?php
$context=stream_context_create(['http'=>['header'=>'Authorization: Basic '.base64_encode(getenv('MBK_NATIVE_HTTP_USER').':'.getenv('MBK_NATIVE_HTTP_PASSWORD')),'ignore_errors'=>true,'follow_location'=>0,'timeout'=>20]]);
$body=file_get_contents('http://127.0.0.1:8000/__lab/kernel',false,$context);
$decoded=json_decode($body,true);
$safe=[];
foreach(['status','diagnostic_only','scope','lead_created','main_version','installed_crm','stage','error_class','source_file','source_line','missing_class'] as $key){if(is_array($decoded)&&array_key_exists($key,$decoded)){$safe[$key]=$decoded[$key];}}
$redirect=[];
foreach($http_response_header as $header){if(str_starts_with(strtolower($header),'location:')){$url=parse_url(trim(substr($header,9)));$redirect=['host'=>$url['host']??null,'page'=>basename($url['path']??'')];}}
echo json_encode(['http_status'=>$http_response_header[0],'response'=>$safe,'redirect'=>$redirect,'response_bytes'=>strlen($body),'response_sha256'=>hash('sha256',$body)]);
"""


def counts() -> list[int]:
    query = "SELECT COUNT(*) FROM bitrix_genuine_v1.b_crm_lead; SELECT COUNT(*) FROM bitrix_genuine_v1.b_agent; SELECT COUNT(*) FROM bitrix_genuine_v1.b_event; SELECT COUNT(*) FROM bitrix_genuine_v1.b_module_to_module;"
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-database-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -N -B -uroot'],
                            input=query.encode(), capture_output=True, check=True, timeout=10)
    return [int(value) for value in result.stdout.split()]


def main() -> None:
    report: dict[str, object] = {"status": "checking", "scope": "bootstrap_diagnostic", "checked_at_utc": datetime.now(timezone.utc).isoformat(), "before_lead_agent_mail_handler_counts": counts()}
    destination = TARGET / "kernel-smoke.json"
    destination.write_text(json.dumps(report), encoding="utf-8")
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-bitrix-genuine-1", "php"], input=CLIENT.encode(), capture_output=True, check=True, timeout=30)
    http = json.loads(result.stdout)
    report["http"] = http
    report["after_lead_agent_mail_handler_counts"] = counts()
    passed = http["http_status"].startswith("HTTP/1.1 200 ") and http["response"].get("status") == "kernel_loaded"
    report["status"] = "passed" if passed else "failed"
    report["genuine_lead_proven"] = False
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
