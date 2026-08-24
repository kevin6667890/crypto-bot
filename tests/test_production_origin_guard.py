from pathlib import Path

import pytest

from scripts.production_origin_guard import assert_target, load_origin


def test_canonical_origin_is_the_only_allowed_target():
    assert assert_target("8.217.62.226")["PRODUCTION_ORIGIN_APP_DIR"] == "/opt/crypto-bot"


@pytest.mark.parametrize("target", [None, "", "43.167.197.67", "127.0.0.1"])
def test_unset_or_legacy_target_fails_closed(target):
    with pytest.raises(ValueError, match="PRODUCTION_(TARGET_UNSET|TARGET_BLOCKED)"):
        assert_target(target)


def test_config_rejects_noncanonical_host(tmp_path: Path):
    config = tmp_path / "origin.env"
    config.write_text("PRODUCTION_ORIGIN_HOST=43.167.197.67\nPRODUCTION_ORIGIN_USER=root\nPRODUCTION_ORIGIN_APP_DIR=/opt/crypto-bot\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PRODUCTION_ORIGIN_NOT_CANONICAL"):
        load_origin(config)


def test_production_candidate_is_paper_only_and_uses_4h_report_cadence():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "deploy/compose/ai6b-production-candidate.yml").read_text(
        encoding="utf-8"
    )
    assert 'LIVE_TRADING_ENABLED: "false"' in compose
    assert 'AI_REPORT_SCHEDULER_CADENCE_SECONDS: "14400"' in compose
    assert 'AI_REPORT_SCHEDULER_CONFIRMATION_GRACE_SECONDS: "120"' in compose
    assert "/app/logs:rw,noexec,nosuid,size=32m,uid=10001,gid=10001,mode=700" in compose
    assert "8501:80" not in compose
