from pathlib import Path

from dashboard.canonical_microstructure_history import (
    BuildIdentity,
    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
    CanonicalHistoryBuilder,
    CanonicalHistoryStore,
    aggregate_quality,
    fingerprint,
)


def test_schema_has_explicit_quality_and_version(tmp_path: Path) -> None:
    store = CanonicalHistoryStore(tmp_path / "canonical.db")
    store.initialise(BuildIdentity("a" * 64, "commit", 120_000, 123))
    with store.connect() as connection:
        version = connection.execute(
            "SELECT value_json FROM canonical_metadata WHERE key='history_version'"
        ).fetchone()[0]
        assert CANONICAL_MICROSTRUCTURE_HISTORY_VERSION in version
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='cvd_1m'"
        ).fetchone()[0]
        assert "UNRECOVERABLE_RAW_GAP" in sql
        assert "source_fingerprint" in sql


def test_fingerprint_excludes_generation_time_when_caller_excludes_it() -> None:
    fact = {"bucket_ms": 60_000, "delta": "1.25", "status": "VALID"}
    assert fingerprint(fact) == fingerprint(dict(reversed(list(fact.items()))))


def test_higher_quality_inheritance_is_conservative() -> None:
    assert aggregate_quality(["VALID"] * 5) == ("VALID", None)
    assert aggregate_quality(["VALID", "BACKFILLED_OFFICIAL"])[0] == (
        "BACKFILLED_OFFICIAL"
    )
    assert aggregate_quality(["VALID", "MISSING"])[0] == "PARTIAL"
    assert aggregate_quality(["VALID", "UNRECOVERABLE_RAW_GAP"])[0] == (
        "UNRECOVERABLE_RAW_GAP"
    )
    assert aggregate_quality(["VALID", "CONFLICT"])[0] == "CONFLICT"
