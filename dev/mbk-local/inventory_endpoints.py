"""List literal HTTP hosts in cached source without URL secrets or source snippets."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs" / "local-stack-2026-11-06"
URL_LITERAL = re.compile(r"https?://[^\s\"'<>`\\]+", re.IGNORECASE)
TEXT_EXTENSIONS = {".php", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json"}


@dataclass(frozen=True)
class HostEvidence:
    source: str
    line: int
    layer: str
    scheme: str
    host: str


def literal_hosts(relative_path: str, text: str) -> list[HostEvidence]:
    """Return host evidence only; userinfo, paths, query and fragments are discarded."""
    if relative_path.endswith(".json"):
        layer = "metadata"
    elif relative_path.endswith((".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".blade.php")):
        layer = "frontend"
    else:
        layer = "server"
    evidence: set[HostEvidence] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in URL_LITERAL.finditer(line):
            try:
                parsed = urlsplit(match.group())
                host = parsed.hostname
            except ValueError:
                continue
            # Interpolated hosts require manual/runtime inspection; do not expose variables.
            if host and re.fullmatch(r"[a-z0-9.-]+|\[[a-f0-9:]+\]", host, re.IGNORECASE):
                evidence.add(HostEvidence(relative_path, line_number, layer, parsed.scheme, host))
    return sorted(evidence, key=lambda row: (row.line, row.scheme, row.host))


def collect(sources: Path) -> dict[str, object]:
    """Inventory application/config/router/frontend literals, never env or datasets."""
    evidence: list[HostEvidence] = []
    snapshot_hash = hashlib.sha256()
    scanned = 0
    for project, directory in [(1, "www"), (2, "lara"), (3, "www")]:
        base = sources / f"project-{project}" / directory
        # Vendor is excluded except the reviewed private package used by partners.
        for folder in ["app", "config", "routes", "resources", "public", "vendor/mbk"]:
            for path in sorted((base / folder).rglob("*")):
                if not path.is_file() or path.is_symlink() or path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                if any(part.startswith(".env") for part in path.parts):
                    continue
                relative = path.relative_to(sources).as_posix()
                content = path.read_bytes()
                snapshot_hash.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(content).digest())
                evidence.extend(literal_hosts(relative, content.decode("utf-8", errors="replace")))
                scanned += 1
    return {
        "scope": "literal HTTP hosts in the available source subset",
        "not_proven": ["dynamic URLs", "SDK defaults", "runtime redirects", "complete source coverage", "delivery interception"],
        "secret_url_components_recorded": False,
        "snapshot_sha256": snapshot_hash.hexdigest(),
        "scanned_files": scanned,
        "evidence": [asdict(item) for item in evidence],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LAB / "endpoint-hosts.json")
    options = parser.parse_args()
    report = collect(LAB / "sources")
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Print counts only: detailed evidence stays in the ignored local artifact.
    evidence = report["evidence"]
    assert isinstance(evidence, list)
    print(json.dumps({"scanned_files": report["scanned_files"], "host_references": len(evidence)}))


if __name__ == "__main__":
    main()
