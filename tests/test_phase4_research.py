import pytest

from dashboard.benchmarks import run_asset_benchmarks
from dashboard.robustness import run_robustness, stress_curve
from dashboard.sensitivity import parameter_combinations, stability_scores
from dashboard.strategy_rules import DEFAULT_PARAMETERS
from dashboard.validation_service import ValidationService


def candles(count=240, start=100):
    return [{"ts":i*900,"open":start+i*.1,"high":start+i*.1+1,"low":start+i*.1-1,"close":start+i*.1+.2} for i in range(count)]


def test_chart_series_are_deterministically_downsampled():
    points = [{"ts": index, "equity": index} for index in range(1000)]
    sampled = ValidationService._downsample(points, 100)
    assert len(sampled) == 100 and sampled[0] == points[0] and sampled[-1] == points[-1]
    assert sampled == ValidationService._downsample(points, 100)


def trades():
    return [{"pnl":p,"fees":2,"slippage_cost":1,"side":"LONG","entry_ts":i,"exit_ts":i+1} for i,p in enumerate([100,-50,80,-40,60,-30])]


def test_oat_and_grid_combination_limits():
    oat=parameter_combinations(DEFAULT_PARAMETERS,[{"parameter":"minimum_score","start":70,"stop":80,"step":5}],"OAT")
    grid=parameter_combinations(DEFAULT_PARAMETERS,[{"parameter":"rsi_min","start":30,"stop":40,"step":5},{"parameter":"rsi_max","start":60,"stop":70,"step":5}],"GRID_2D")
    assert len(oat)==3 and len(grid)==9
    with pytest.raises(ValueError,match="exceeds"):parameter_combinations(DEFAULT_PARAMETERS,[{"parameter":"rsi_min","start":0,"stop":99,"step":1},{"parameter":"rsi_max","start":1,"stop":100,"step":1}],"GRID_2D")


def test_sensitivity_deterministic_stability_and_oos():
    source=[{"parameters":{"minimum_score":x},"total_return":ret,"oos_return":ret-1,"maximum_drawdown":5+x/20,"trades":40,"oos_profit_factor":1.1} for x,ret in [(70,4),(75,5),(80,4)]]
    first=stability_scores([dict(x) for x in source],["minimum_score"]);second=stability_scores([dict(x) for x in source],["minimum_score"])
    assert first==second and all("stability_score" in x and "oos_return" in x for x in first)
    assert any("Most Stable Region" in x["labels"] for x in first)


def test_buy_hold_fees_cash_and_dates():
    result=run_asset_benchmarks(candles(),10000,.001,.001,900)
    cash=next(x for x in result if x["name"]=="Cash / No Position");hold=next(x for x in result if x["name"]=="Buy & Hold")
    assert cash["metrics"]["total_return"]==0 and cash["metrics"]["fees_paid"]==0
    assert hold["metrics"]["fees_paid"]>0 and hold["equity"][0]["ts"]==0 and hold["equity"][-1]["ts"]==239*900


def test_ma_crossover_and_negative_results_not_hidden():
    result=run_asset_benchmarks(list(reversed(candles(start=200))),10000,.001,.001,900)
    assert any(x["name"]=="MA60 / MA200 Crossover" for x in result)
    assert all("total_return" in x["metrics"] for x in result)


def test_monte_carlo_fixed_and_different_seed():
    a=run_robustness(trades(),10000,200,42,"BOOTSTRAP");b=run_robustness(trades(),10000,200,42,"BOOTSTRAP");c=run_robustness(trades(),10000,200,43,"BOOTSTRAP")
    assert a==b and a["returns"]!=c["returns"]


def test_trade_order_bootstrap_and_risk_of_ruin():
    order=run_robustness(trades(),10000,100,1,"TRADE_ORDER");boot=run_robustness(trades(),10000,100,1,"BOOTSTRAP")
    assert order["median_final_equity"]==10120 and boot["simulation_count"]==100
    ruin=run_robustness([{"pnl":-9000,"fees":0}],10000,10,1,"BOOTSTRAP")
    assert ruin["risk_of_ruin"]==1


def test_fee_slippage_and_missed_trade_stress():
    fee=stress_curve(trades(),10000,[1,2],"fee");slip=stress_curve(trades(),10000,[1,2],"slippage")
    missed=run_robustness(trades(),10000,100,2,"TRADE_ORDER",missed_trade_rate=.5)
    assert fee[1]["return"]<fee[0]["return"] and slip[1]["return"]<slip[0]["return"]
    assert missed["parameters"]["missed_trade_rate"]==.5


def test_simulation_limit_and_empty_trades():
    with pytest.raises(ValueError):run_robustness(trades(),10000,5001)
    assert run_robustness([],10000,10)["status"]=="INSUFFICIENT_DATA"
