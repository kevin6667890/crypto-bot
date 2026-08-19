import json
from datetime import datetime, timedelta, timezone

import dashboard.paper_api as paper_api
from dashboard.paper_api import MAX_CONSECUTIVE_LOSSES, PaperService


def _loss(service: PaperService, instrument: str, closed_at: str) -> None:
    with service._connect() as connection:
        connection.execute(
            "INSERT INTO paper_trades(instrument,side,entry,stop_loss,take_profit,status,pnl_r,created_at,closed_at) "
            "VALUES(?,?,?,?,?,'LOSS',-1,?,?)",
            (instrument, "LONG", 100, 99, 102, closed_at, closed_at),
        )


def test_owner_reset_is_instrument_scoped_append_only_and_rule_retriggers(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_api, "ALERTS", None)
    service = PaperService(tmp_path / "paper.db")
    before = datetime.now(timezone.utc) - timedelta(days=1)
    for index in range(MAX_CONSECUTIVE_LOSSES):
        _loss(service, "ETH-USDT", (before + timedelta(seconds=index)).isoformat())
        _loss(service, "BTC-USDT", (before + timedelta(seconds=index)).isoformat())
    assert service.risk_state("ETH-USDT")["blockers"] == ["consecutive loss limit"]
    result = service.reset_loss_streak(
        "ETH-USDT", reason="OWNER_APPROVED_PAPER_ETH_LOSS_STREAK_RESET",
        approval_id="owner-production-20260819",
    )
    assert result["risk_scope"] == "INSTRUMENT"
    assert result["old_state"]["consecutive_losses"] == MAX_CONSECUTIVE_LOSSES
    assert result["new_state"] == {"consecutive_losses": 0, "cooldown_until": None}
    assert result["risk_rule_still_enabled"] is True
    assert service.risk_state("ETH-USDT")["consecutive_losses"] == 0
    assert service.risk_state("BTC-USDT")["consecutive_losses"] == MAX_CONSECUTIVE_LOSSES
    with service._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == MAX_CONSECUTIVE_LOSSES * 2
        event = connection.execute("SELECT payload FROM event_logs WHERE event_type='PAPER_RISK_LOSS_STREAK_RESET'").fetchone()
    payload = json.loads(event[0])
    assert len(payload["evidence_hash"]) == 64
    after = datetime.now(timezone.utc) + timedelta(seconds=1)
    for index in range(MAX_CONSECUTIVE_LOSSES):
        _loss(service, "ETH-USDT", (after + timedelta(seconds=index)).isoformat())
    retriggered = service.risk_state("ETH-USDT")
    assert retriggered["consecutive_losses"] == MAX_CONSECUTIVE_LOSSES
    assert "consecutive loss limit" in retriggered["blockers"]


def test_reset_rejects_unapproved_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_api, "ALERTS", None)
    service = PaperService(tmp_path / "paper.db")
    try:
        service.reset_loss_streak("ETH-USDT", reason="disable-rule", approval_id="owner-production-20260819")
    except ValueError as error:
        assert str(error) == "PAPER_RISK_RESET_REASON_NOT_APPROVED"
    else:
        raise AssertionError("unapproved reset must fail")
