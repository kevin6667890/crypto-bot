from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_bounds_logs_without_changing_database_mount() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'driver: json-file' in compose
    assert 'max-size: "20m"' in compose
    assert 'max-file: "5"' in compose
    assert "./data_cache:/app/data_cache" in compose
    assert "volume prune" not in compose


def test_operations_page_exposes_storage_without_delete_controls() -> None:
    source = (ROOT / "frontend" / "src" / "Operations.tsx").read_text(
        encoding="utf-8"
    )
    for value in (
        "存储生命周期",
        "Root usage",
        "Root free",
        "Paper DB",
        "Microstructure DB",
        "Snapshot mode",
        "Raw hot retention",
        "Archive backlog",
        "Last off-host ACK",
        "Projected days to 85%",
        "Projected days to 90%",
        "INSUFFICIENT_HISTORY",
    ):
        assert value in source
    assert "delete archive" not in source.lower()
    assert "prune now" not in source.lower()


def test_archived_data_does_not_drive_service_stopped_state() -> None:
    source = (ROOT / "frontend" / "src" / "Operations.tsx").read_text(
        encoding="utf-8"
    )
    assert "raw_retention_status" in source
    assert "summary?.collector.status" in source
    assert "raw_retention_status === \"STOPPED\"" not in source


def test_protection_level_semantics_are_in_public_contract() -> None:
    contract = (
        ROOT / "frontend" / "openapi" / "openapi.json"
    ).read_text(encoding="utf-8")
    for level in ("NORMAL", "WARNING", "CRITICAL", "EMERGENCY"):
        assert f'"{level}"' in contract
    assert '"core_ledger_allowed"' in contract
    assert '"optional_artifacts_allowed"' in contract
