"""Persist and print the exploratory-only report and forward manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure import (
    MicrostructureStore,
    exploratory_report,
    forward_validation_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    store = MicrostructureStore(args.database)
    payload = {"manifest": forward_validation_manifest(store),
               "report": exploratory_report(store)}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
