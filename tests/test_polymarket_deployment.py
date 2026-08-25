from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scripts.run_polymarket_backup import retention_set


ROOT = Path(__file__).resolve().parents[1]


def _backup(tmp_path: Path, stamp: str) -> Path:
    path = tmp_path / f"polymarket_research-{stamp}.sqlite"
    path.touch()
    return path


def test_backup_retention_is_bounded_and_calendar_based(tmp_path: Path) -> None:
    paths = [
        _backup(tmp_path, datetime(2026, 8, day, tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        for day in range(1, 26)
    ]
    kept = retention_set(paths, daily=3, weekly=4)
    assert len(kept) <= 7
    assert paths[-1] in kept
    assert len({_stamp(path).date() for path in kept if _stamp(path).day >= 23}) == 3


def _stamp(path: Path) -> datetime:
    return datetime.strptime(path.stem.removeprefix("polymarket_research-"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def test_compose_keeps_provider_secret_out_of_web_services() -> None:
    text = (ROOT / "deploy/compose/polymarket-production.override.yml").read_text(encoding="utf-8")
    paper, collector = text.split("  polymarket-collector:", 1)
    collector, backup = collector.split("  polymarket-backup:", 1)
    assert "polymarket_llm_api_key" not in paper
    assert "polymarket_llm_api_key" in collector
    assert "polymarket_llm_api_key" not in backup.split("volumes:", 1)[0]
    assert "POLYMARKET_DB_PATH: /var/lib/polymarket/polymarket_research.sqlite" in paper


def test_systemd_schedule_does_not_backfill_or_overlap() -> None:
    timer = (ROOT / "deploy/polymarket/polymarket-collector.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy/polymarket/polymarket-collector.service").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 00/3:15:00" in timer
    assert "Persistent=false" in timer
    assert "/usr/bin/flock --nonblock" in service
    assert "TimeoutStartSec=90min" in service
