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
