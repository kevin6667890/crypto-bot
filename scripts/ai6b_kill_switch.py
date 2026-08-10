"""Inspect or irreversibly trip the candidate AI-6B live-provider switch."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.ai_market_analysis.live_provider_guard import HARD_STOP_EVENTS, status, trip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "trip"))
    parser.add_argument("--event", choices=sorted(HARD_STOP_EVENTS))
    parser.add_argument("--evidence-id")
    parser.add_argument("--path")
    args = parser.parse_args()
    if args.action == "trip" and not args.event:
        parser.error("trip requires --event")
    result = trip(args.event, path=args.path, evidence_id=args.evidence_id) if args.action == "trip" else status(args.path)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
