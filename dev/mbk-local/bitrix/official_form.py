"""Save private installer HTML and emit only safe structural form metadata."""

from __future__ import annotations

import argparse
import base64
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "logs/local-stack-2026-11-06/official-trial/runtime-v1"


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: list[dict[str, object]] = []
        self.actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.actions.append((attributes.get("action") or "").split("?", 1)[0])
        if tag in {"input", "select", "textarea", "button"}:
            kind = attributes.get("type") or tag
            item: dict[str, object] = {"type": kind, "name": attributes.get("name"), "checked": "checked" in attributes}
            # Show only non-sensitive choice values; text/password/hidden values remain private.
            value = attributes.get("value") or ""
            if kind in {"checkbox", "radio", "submit", "button"} and len(value) < 100 and not re.search(r"pass|secret|key|token", attributes.get("name") or "", re.I):
                item["value"] = value
            self.fields.append(item)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--request", type=Path)
    options = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", options.label):
        raise ValueError("Invalid local artifact label")
    request = options.request.read_bytes() if options.request else b"{}"
    result = subprocess.run(["docker", "exec", "-i", "mbk-local-lab-bitrix-official-1", "php", "/lab/official_http.php"],
                            input=request, capture_output=True, check=True, timeout=40)
    response = json.loads(result.stdout)
    html = base64.b64decode(response.pop("body_base64"))
    location = base64.b64decode(response.pop("redirect_private_base64", "")).decode("utf-8")
    if location:
        (TARGET / f"{options.label}-redirect-private.txt").write_text(location, encoding="utf-8")
        parsed = urlsplit(location)
        response["redirect"] = {"host": parsed.hostname, "path": parsed.path}
    (TARGET / f"{options.label}-private.html").write_bytes(html)
    form = FormParser()
    form.feed(html.decode("utf-8", errors="replace"))
    report = {"http": response, "forms": form.actions, "fields": form.fields,
              "host_browser_used": False, "redirects_followed": False,
              "secret_values_printed": False}
    (TARGET / f"{options.label}-form.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if int(response["status"]) >= 400:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
