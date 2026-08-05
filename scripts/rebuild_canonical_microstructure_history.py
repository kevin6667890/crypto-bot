"""Build canonical microstructure history from a verified frozen source."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from dashboard.canonical_microstructure_history import (
    BuildIdentity,
    INSTRUMENTS,
    SOURCE_TABLES,
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
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument(
        "--series-only", action="store_true",
        help="resume aggregate construction from an existing coverage ledger",
    )
    parser.add_argument(
        "--oi-only", action="store_true",
        help="apply OI overlay and rederive higher timeframes without rebuilding CVD",
    )
    parser.add_argument("--instrument", action="append", choices=INSTRUMENTS)
    parser.add_argument("--source-name", action="append", choices=SOURCE_TABLES)
    parser.add_argument("--official-trade-manifest", type=Path)
    parser.add_argument("--official-oi-manifest", type=Path)
    parser.add_argument("--official-price-manifest", type=Path)
    parser.add_argument("--apply-price-overlay-only", action="store_true")
    parser.add_argument(
        "--contract-value", action="append", default=[],
        help="verified instrument=value pair, e.g. BTC-USDT-SWAP=0.01",
    )
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
    contract_values = dict(item.split("=", 1) for item in arguments.contract_value)
    builder = CanonicalHistoryBuilder(
        arguments.source, arguments.destination, identity,
        official_trade_manifest_path=arguments.official_trade_manifest,
        contract_values=contract_values,
        official_oi_manifest_path=arguments.official_oi_manifest,
        official_price_manifest_path=arguments.official_price_manifest,
    )
    if arguments.apply_price_overlay_only:
        report = {"price_overlay": []}
        for instrument in arguments.instrument or INSTRUMENTS:
            for source_name in arguments.source_name or ("mark", "index"):
                if source_name not in {"mark", "index"}:
                    continue
                report["price_overlay"].append(
                    builder.apply_official_price_overlay(source_name, instrument)
                )
    elif arguments.audit_only:
        report: dict[str, object] = {"coverage": []}

        def progress(payload: dict[str, object]) -> None:
            print(json.dumps({"progress": payload}, sort_keys=True), flush=True)

        for instrument in arguments.instrument or INSTRUMENTS:
            for source_name in arguments.source_name or SOURCE_TABLES:
                print(json.dumps({"start": [instrument, source_name]}), flush=True)
                report["coverage"].append(builder.build_coverage(
                    source_name, instrument, progress))
                print(json.dumps({"complete": [instrument, source_name]}), flush=True)
    elif arguments.oi_only:
        report = {"series": []}
        for instrument in arguments.instrument or INSTRUMENTS:
            report["series"].append(builder.build_oi_1m(instrument))
            report["series"].append({
                "instrument": instrument,
                **builder.derive_higher_timeframes(instrument),
            })
    elif arguments.series_only:
        report = {"series": []}
        for instrument in arguments.instrument or INSTRUMENTS:
            print(json.dumps({"start": [instrument, "cvd_1m"]}), flush=True)
            report["series"].append(builder.build_cvd_1m(instrument))
            print(json.dumps({"complete": [instrument, "cvd_1m"]}), flush=True)
            report["series"].append(builder.build_oi_1m(instrument))
            report["series"].append({
                "instrument": instrument,
                **builder.derive_higher_timeframes(instrument),
            })
    else:
        report = builder.rebuild()
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
