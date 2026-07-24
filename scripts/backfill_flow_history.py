"""Run the resumable durable flow-history backfill."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.flow_history import FlowHistoryStore


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", type=Path, default=Path("data_cache/paper_trades.db")
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    store = FlowHistoryStore(args.database.resolve())
    store.initialize()
    print(json.dumps(store.backfill(force=args.force), indent=2))


if __name__ == "__main__":
    main()
