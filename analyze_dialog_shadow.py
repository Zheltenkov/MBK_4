"""CLI независимого replay сохранённых dialog shadow artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.dialogs import load_dialog_scenarios
from evaluation.shadow_replay import (
    aggregate_shadow_replays,
    replay_dialog_shadow,
)
from routing import get_matrix


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dialog-dir", type=Path, required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evaluation" / "dialog_cases.json",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    scenarios = load_dialog_scenarios(args.cases)
    matrix = get_matrix()
    results = []
    for scenario in scenarios:
        artifact_path = args.dialog_dir / f"{scenario.id}.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise ValueError(f"{artifact_path} не содержит JSON object")
        results.append(
            replay_dialog_shadow(
                artifact,
                expected_route_id=scenario.expected_route_id,
                matrix=matrix,
            )
        )

    summary = {
        "schema_version": "mbk.dialog-shadow-replay.v1",
        "source_dialog_dir": str(args.dialog_dir),
        "openrouter_calls": 0,
        "crm_called": False,
        "dadata_called": False,
        **aggregate_shadow_replays(results),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed_scenario_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
