from dashboard.portfolio_backtest import PortfolioParameters, run_portfolio_backtest
from dashboard.strategy_rules import StrategyParameters

def candles(offset=0):
    rows=[]; price=100+offset
    for i in range(50):
        open_price=price; price*=1.001
        rows.append({"ts":i*900,"open":open_price,"high":price+0.3,"low":open_price-0.2,"close":price,"volume":100})
    return rows

def test_portfolio_is_deterministic_and_uses_shared_resources():
    strategy=StrategyParameters(fast_ma=2,slow_ma=3,ema_pullback_period=2,rsi_period=2,atr_period=2,rsi_min=1,rsi_max=100,minimum_score=70,cooldown_bars=0,trading_fee=.001,slippage=.001)
    config=PortfolioParameters(initial_capital=10000,max_positions=2,max_asset_weight=.4,max_asset_risk=.02,max_portfolio_risk=.03,max_long_exposure=.8,max_short_exposure=.8,asset_weights={"BTC-USDT":.5,"ETH-USDT":.5})
    one=run_portfolio_backtest({"BTC-USDT":candles(),"ETH-USDT":candles(10)},strategy,config,0,49*900)
    two=run_portfolio_backtest({"ETH-USDT":candles(10),"BTC-USDT":candles()},strategy,config,0,49*900)
    assert one["trades"]==two["trades"]
    assert one["metrics"]["concurrent_positions"]<=2
    assert max(point["gross"] for point in one["exposure_timeline"])<=80.01
    assert min(point["cash"] for point in one["equity"])>=-1e-6
    assert one["metrics"]["fees_paid"]>0
    assert set(one["metrics"]["per_asset_contribution"])=={"BTC-USDT","ETH-USDT"}
    assert one["metrics"]["maximum_drawdown"]>=0

