import math

import pytest

from dashboard.polymarket.scoring import brier_score, log_loss, market_baseline_score


def test_brier_and_log_loss_math():
    assert brier_score(.7, 1) == pytest.approx(.09)
    assert log_loss(.7, 1) == pytest.approx(-math.log(.7))
    baseline = market_baseline_score(.4, 0)
    assert baseline["brier"] == pytest.approx(.16)
    assert baseline["log_loss"] == pytest.approx(-math.log(.6))
