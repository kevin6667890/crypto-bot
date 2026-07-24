"""Run bounded, resumable official OKX public backfill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_backfill import OfficialBackfill


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--trade-pages", type=int, default=1000)
    parser.add_argument("--price-pages", type=int, default=1500)
    args = parser.parse_args()
    store = MicrostructureStore(args.database)
    print(json.dumps(OfficialBackfill(store).run(
        trade_pages=args.trade_pages, price_pages=args.price_pages), indent=2))


if __name__ == "__main__":
    main()
