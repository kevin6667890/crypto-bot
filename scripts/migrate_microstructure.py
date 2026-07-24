"""Idempotently migrate genuine flow observations from paper_trades.db."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure import MicrostructureMigration, MicrostructureStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    print(json.dumps(MicrostructureMigration(
        MicrostructureStore(args.database)).migrate(args.source), indent=2))


if __name__ == "__main__":
    main()
