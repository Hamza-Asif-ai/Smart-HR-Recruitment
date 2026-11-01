"""Generate the four-fifths adverse-impact report from synthetic group counts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fairness import adverse_impact_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default="tests/eval/adverse_impact.json",
        help="JSON file containing a groups mapping and optional four_fifths value.",
    )
    parser.add_argument("--out", help="Optional path to write the report as JSON.")
    args = parser.parse_args()

    payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    report = adverse_impact_report(
        payload["groups"],
        four_fifths=float(payload.get("four_fifths", 0.8)),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
