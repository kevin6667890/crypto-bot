"""Run bounded resumable Phase 6B discovery against a disposable DB copy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dashboard.automatic_discovery_v2 import run_automatic_discovery_v2
from dashboard.strategy_program_v2 import SEARCH_BUDGETS

EXPECTED_FROZEN_SHA256 = "9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-programs", type=int, default=SEARCH_BUDGETS["raw_structurally_valid"])
    parser.add_argument("--max-btc-backtests", type=int, default=SEARCH_BUDGETS["btc_backtest"])
    parser.add_argument("--max-cross-asset-programs", type=int, default=SEARCH_BUDGETS["cross_asset"])
    parser.add_argument("--checkpoint-db", type=Path, required=True)
    parser.add_argument("--cancel-file", type=Path)
    arguments = parser.parse_args()

    source, database = arguments.source.resolve(), arguments.database.resolve()
    if source == database:
        raise ValueError("working database must differ from frozen canonical database")
    before_hash = file_hash(source)
    if before_hash != EXPECTED_FROZEN_SHA256:
        raise ValueError(f"frozen database SHA-256 mismatch: {before_hash}")
    database.parent.mkdir(parents=True, exist_ok=True)
    if not database.exists():
        shutil.copy2(source, database)
    result = run_automatic_discovery_v2(
        database, arguments.checkpoint_db.resolve(), workers=arguments.workers,
        resume=arguments.resume, max_programs=arguments.max_programs,
        max_btc_backtests=arguments.max_btc_backtests,
        max_cross_asset_programs=arguments.max_cross_asset_programs,
        cancel_file=arguments.cancel_file.resolve() if arguments.cancel_file else None,
    )
    after_hash = file_hash(source)
    if after_hash != before_hash:
        raise AssertionError("frozen canonical database changed")
    result["frozen_database_sha256"] = after_hash
    result["working_database_is_separate_copy"] = True
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(arguments.report.resolve())


if __name__ == "__main__":
    main()
