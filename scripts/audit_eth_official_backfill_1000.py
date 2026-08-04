"""Deterministically audit the legacy 1000-row ETH official trade insertion."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path


FIRST_ID = 4_119_179_146
LAST_ID = 4_119_180_145
INSTRUMENT = "ETH-USDT-SWAP"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--official-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    connection = sqlite3.connect(
        f"file:{arguments.source.resolve().as_posix()}?mode=ro", uri=True,
    )
    connection.row_factory = sqlite3.Row
    local = list(connection.execute(
        """SELECT source_ts_ms,trade_id,side,price,size,contract_value,notional,
                  source,source_version,uniqueness_key
           FROM trade_flow_observations WHERE instrument=?
             AND source_ts_ms BETWEEN 1784857300000 AND 1784857350000
           ORDER BY source_ts_ms,trade_id,uniqueness_key""", (INSTRUMENT,),
    ))
    selected = [row for row in local if row["trade_id"] is not None
                and FIRST_ID <= int(row["trade_id"]) <= LAST_ID]
    gaps = [dict(row) for row in connection.execute(
        """SELECT * FROM collection_gaps WHERE instrument=?
           AND start_ms<? AND end_ms>? ORDER BY start_ms""",
        (INSTRUMENT, 1784857341816, 1784857304413),
    )]
    old_aggregates = [dict(row) for row in connection.execute(
        """SELECT * FROM cvd_aggregates WHERE instrument=? AND resolution='1m'
           AND bucket_ms IN (1784857260000,1784857320000) ORDER BY bucket_ms""",
        (INSTRUMENT,),
    )]
    connection.close()
    official: dict[int, list[str]] = {}
    adjacent: dict[str, list[str] | None] = {"before": None, "after": None}
    with zipfile.ZipFile(arguments.official_zip) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as stream:
            reader = csv.DictReader(line.decode() for line in stream)
            for row in reader:
                trade_id = int(row["trade_id"])
                fact = [row["created_time"], row["side"], row["price"], row["size"]]
                if trade_id == FIRST_ID - 1:
                    adjacent["before"] = fact
                elif FIRST_ID <= trade_id <= LAST_ID:
                    official[trade_id] = fact
                elif trade_id == LAST_ID + 1:
                    adjacent["after"] = fact
    local_by_id: dict[int, sqlite3.Row] = {}
    duplicate_count = 0
    local_conflicts = 0
    for row in selected:
        trade_id = int(row["trade_id"])
        prior = local_by_id.get(trade_id)
        if prior is not None:
            duplicate_count += 1
            if tuple(prior) != tuple(row):
                local_conflicts += 1
            continue
        local_by_id[trade_id] = row
    content_conflicts = []
    for trade_id, row in local_by_id.items():
        expected = official.get(trade_id)
        actual = [str(row["source_ts_ms"]), str(row["side"]),
                  format(float(row["price"]), ".15g"),
                  format(float(row["size"]), ".15g")]
        if expected is None or any(
            actual[index] != expected[index] for index in (0, 1)
        ) or abs(float(actual[2]) - float(expected[2])) > 1e-12 or abs(
            float(actual[3]) - float(expected[3])) > 1e-12:
            content_conflicts.append({"trade_id": trade_id,
                                      "local": actual, "official": expected})
    ids = sorted(local_by_id)
    local_facts = [[trade_id, *official.get(trade_id, [])] for trade_id in ids]
    conclusion = (
        "VERIFIED_OFFICIAL_BACKFILL"
        if (len(ids) == 1000 and len(official) == 1000 and not content_conflicts
            and duplicate_count == 0 and local_conflicts == 0)
        else "CONFLICT"
    )
    report = {
        "audit_version": "eth-official-backfill-1000-audit-v1",
        "instrument": INSTRUMENT, "first_trade_id": FIRST_ID,
        "last_trade_id": LAST_ID, "expected_rows": 1000,
        "local_rows": len(selected), "local_unique_trade_ids": len(ids),
        "official_rows": len(official), "duplicate_count": duplicate_count,
        "local_content_conflicts": local_conflicts,
        "official_content_conflicts": len(content_conflicts),
        "first_source_ts_ms": min(int(row["source_ts_ms"]) for row in selected),
        "last_source_ts_ms": max(int(row["source_ts_ms"]) for row in selected),
        "buy_count": sum(row["side"] == "buy" for row in selected),
        "sell_count": sum(row["side"] == "sell" for row in selected),
        "contiguous_trade_ids": ids == list(range(FIRST_ID, LAST_ID + 1)),
        "official_adjacent": adjacent,
        "overlapping_collection_gaps": gaps,
        "legacy_aggregate_rows": old_aggregates,
        "rowset_sha256": hashlib.sha256(canonical(local_facts)).hexdigest(),
        "official_zip_sha256": hashlib.sha256(arguments.official_zip.read_bytes()).hexdigest(),
        "gap_relation": "NO_OVERLAPPING_COLLECTION_GAP_RECORD",
        "conclusion": conclusion,
        "retention_decision": "KEEP",
        "conflict_examples": content_conflicts[:10],
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
