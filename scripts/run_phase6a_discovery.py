"""Run deterministic Phase 6A discovery on a disposable canonical DB copy."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil

from dashboard.automatic_discovery import run_automatic_discovery, write_report

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
    arguments = parser.parse_args()
    source = arguments.source.resolve()
    database = arguments.database.resolve()
    report_path = arguments.report.resolve()
    if source == database:
        raise ValueError("Working database must differ from frozen source")
    before_hash = file_hash(source)
    if before_hash != EXPECTED_FROZEN_SHA256:
        raise ValueError(f"Frozen database SHA-256 mismatch: {before_hash}")
    if database.exists():
        raise ValueError("Working database already exists")
    database.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, database)
    result = run_automatic_discovery(database)
    after_hash = file_hash(source)
    if after_hash != before_hash:
        raise AssertionError("Frozen database changed")
    result.update({
        "frozen_database_sha256": after_hash,
        "working_database_is_separate_copy": True,
    })
    write_report(result, report_path)
    print(report_path)


if __name__ == "__main__":
    main()
