"""Read-only inventory for the bounded realtime aggregation range."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from dashboard.microstructure import INSTRUMENTS, MicrostructureStore
from dashboard.realtime_aggregation import RealtimeAggregationEngine


def timestamp_ms(value: str) -> int:
    if value.isdigit():
        return int(value)
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--database", required=True)
    value.add_argument("--instrument", required=True, choices=INSTRUMENTS)
    value.add_argument("--start", required=True, type=timestamp_ms)
    value.add_argument("--end", required=True, type=timestamp_ms)
    return value


def main() -> None:
    args = parser().parse_args()
    store = MicrostructureStore(args.database)
    result = RealtimeAggregationEngine(store).dry_run(
        args.instrument, args.start, args.end)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
