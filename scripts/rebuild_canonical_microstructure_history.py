"""Build canonical microstructure history from a verified frozen source."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from dashboard.canonical_microstructure_history import (
    BuildIdentity,
    CanonicalHistoryBuilder,
    now_ms,
)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def source_watermark(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        values = []
        for table in ("trade_flow_observations", "oi_observations"):
            row = connection.execute(f"SELECT MAX(source_ts_ms) FROM {table}").fetchone()
            if row and row[0] is not None:
                values.append(int(row[0]))
        if not values:
            raise ValueError("source contains no CVD/OI raw observations")
        return max(values)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-sha256")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    source_hash = sha256_file(arguments.source)
    if arguments.source_sha256 and source_hash != arguments.source_sha256.lower():
        raise SystemExit("source SHA-256 does not match verified manifest")
    identity = BuildIdentity(
        source_sha256=source_hash,
        generated_commit=arguments.commit,
        source_watermark_ms=source_watermark(arguments.source),
        generated_at_ms=now_ms(),
    )
    report = CanonicalHistoryBuilder(
        arguments.source, arguments.destination, identity,
    ).rebuild()
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
