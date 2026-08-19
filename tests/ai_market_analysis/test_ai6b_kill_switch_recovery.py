from __future__ import annotations

import hashlib

import pytest

from dashboard.ai_market_analysis.live_provider_guard import recover, status, trip


def _active(path):
    trip("AUDIT_MISMATCH", path=path, evidence_id="TODAY_RELEASE_COVERAGE")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recovery_archives_switch_and_persists_authorization(tmp_path):
    path = tmp_path / "kill.json"; digest = _active(path)
    result = recover(path=path, expected_event="AUDIT_MISMATCH", expected_sha256=digest,
                     approval_id="TODAY_RELEASE_APPROVED", evidence_id="AI6B_PRESENTATION_TESTS_PASS")
    assert result["state"] == "RECOVERED" and not result["live_provider_disabled"]
    assert status(path)["live_provider_disabled"] is False
    assert len(list(tmp_path.glob("kill.recovered-*.json"))) == 1
    assert len(list(tmp_path.glob("kill.recovery-*.json"))) == 1


@pytest.mark.parametrize("field,value,error", [
    ("expected_sha256", "0" * 64, "KILL_SWITCH_HASH_MISMATCH"),
    ("expected_event", "WRONG_MODE", "KILL_SWITCH_EVENT_MISMATCH"),
    ("approval_id", "bad", "INVALID_RECOVERY_ID"),
])
def test_invalid_recovery_keeps_switch_active(tmp_path, field, value, error):
    path = tmp_path / "kill.json"; digest = _active(path)
    kwargs = {"path": path, "expected_event": "AUDIT_MISMATCH", "expected_sha256": digest,
              "approval_id": "TODAY_RELEASE_APPROVED", "evidence_id": "AI6B_PRESENTATION_TESTS_PASS"}
    kwargs[field] = value
    with pytest.raises(ValueError, match=error): recover(**kwargs)
    assert status(path)["live_provider_disabled"] is True
