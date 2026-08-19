"""Evidence-bound operator transition for an AI6B provider kill switch."""
from __future__ import annotations

import argparse
import json

from dashboard.ai_market_analysis.live_provider_guard import recover


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kill-switch-file", required=True)
    parser.add_argument("--expected-event", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    value = recover(path=args.kill_switch_file, expected_event=args.expected_event,
                    expected_sha256=args.expected_sha256, approval_id=args.approval_id,
                    evidence_id=args.evidence_id)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
