"""Phase-A regressions for canonical Workspace/AI CVD and OI evidence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dashboard.ai_market_analysis.readonly_adapter import ReadOnlyOrderflowAdapter
from dashboard.canonical_microstructure_history import BuildIdentity, CanonicalHistoryStore
from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_gap_repair import AggregateGapRepair


INSTRUMENT = "ETH-USDT-SWAP"
# Deliberately aligned to a 4H boundary so a 240-minute fixture is one bucket.
START = 1_700_006_400


def canonical(path: Path, *, missing_cvd: set[int] | None = None,
              missing_oi: set[int] | None = None) -> Path:
    missing_cvd = missing_cvd or set(); missing_oi = missing_oi or set()
    store = CanonicalHistoryStore(path)
    store.initialise(BuildIdentity("a" * 64, "test", (START + 240) * 1000, 1))
    with store.connect() as connection:
        for minute in range(240):
            bucket = (START + minute * 60) * 1000
            cvd_status = "MISSING" if minute in missing_cvd else "VALID"
            oi_status = "MISSING" if minute in missing_oi else "VALID"
            connection.execute(
                "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (INSTRUMENT, bucket, "1m", None if minute in missing_cvd else 11.0,
                 None if minute in missing_cvd else 1.0,
                 None if minute in missing_cvd else 10.0,
                 0 if minute in missing_cvd else 2,
                 None if minute in missing_cvd else bucket + 1,
                 None if minute in missing_cvd else bucket + 59_000,
                 0 if minute in missing_cvd else 2, "h" if minute not in missing_cvd else None,
                 None if minute in missing_cvd else (minute + 1) * 10.0,
                 "2023-11-14", cvd_status,
                 "TRUE_RAW_GAP" if minute in missing_cvd else None,
                 "canonical-test", "test", 1),
            )
            connection.execute(
                "INSERT INTO oi_1m VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (INSTRUMENT, bucket, "1m", None if minute in missing_oi else 1000.0 + minute,
                 None if minute in missing_oi else bucket + 59_000,
                 0 if minute in missing_oi else 1, "o" if minute not in missing_oi else None,
                 oi_status, "TRUE_RAW_GAP" if minute in missing_oi else None,
                 "canonical-test", 1),
            )
    return path


def test_ai_uses_canonical_1m_when_legacy_4h_is_empty(tmp_path: Path) -> None:
    path = canonical(tmp_path / "canonical.db")
    # This is the historical source that produced false FLOW_UNAVAILABLE.
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE cvd_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER)")
        connection.execute("CREATE TABLE oi_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER)")
    output = ReadOnlyOrderflowAdapter(path).read(INSTRUMENT, START, START + 240 * 60, "4H")
    assert len(output["cvd"]) == len(output["oi"]) == 1
    assert output["cvd"][0]["status"] == "VALID"
    assert output["cvd"][0]["signed_delta"] == 2400.0
    assert output["oi"][0]["confirmed_oi"] == 1239.0
    assert output["canonical_metadata"]["source_resolution"] == "1m"
    assert output["cvd"][0]["synthetic_data"] is False


def test_real_canonical_gap_is_preserved_not_filled(tmp_path: Path) -> None:
    path = canonical(tmp_path / "canonical.db", missing_cvd={45}, missing_oi={45})
    output = ReadOnlyOrderflowAdapter(path).read(INSTRUMENT, START, START + 240 * 60, "4H")
    assert output["cvd"][0]["status"] == "GAP"
    assert output["cvd"][0]["gap"] is True
    assert output["oi"][0]["status"] == "GAP"
    assert output["oi"][0]["confirmed_oi"] == 1239.0  # last observed, never interpolated
    assert output["oi"][0]["synthetic_data"] is False


def test_canonical_oi_chart_evidence_cannot_become_ai_zero(tmp_path: Path) -> None:
    path = canonical(tmp_path / "canonical.db")
    output = ReadOnlyOrderflowAdapter(path).read(INSTRUMENT, START, START + 240 * 60, "4H")
    row = output["oi"][0]
    assert row["source_bucket_count"] == 240
    assert row["observation_count"] == 240
    assert row["confirmed_oi"] == 1239.0
    assert row["status"] == "VALID"


def test_bounded_rebuild_restores_raw_complete_missing_aggregate(tmp_path: Path) -> None:
    micro = MicrostructureStore(tmp_path / "micro.db")
    micro.initialize()
    start_ms = START * 1000
    for minute in range(3):
        micro.insert_trade(INSTRUMENT, {
            "ts": start_ms + minute * 60_000 + 1, "px": 100, "sz": 1,
            "side": "buy", "tradeId": f"t{minute}",
        }, contract_value=1)
    repair = AggregateGapRepair(micro.path)
    diagnosis = repair.diagnose(INSTRUMENT, "cvd", start_ms, start_ms + 3 * 60_000)
    assert diagnosis["recoverable_bucket_count"] == 3
    rebuilt = repair.rebuild(INSTRUMENT, "cvd", start_ms, start_ms + 3 * 60_000, max_rows=20)
    assert rebuilt["status"] == "APPLIED"
    assert rebuilt["verified_recoverable_bucket_count"] == 0
