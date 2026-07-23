import copy
from dashboard.canonical_dataset import dataset_fingerprint, partition_fingerprint
from dashboard.discovery_identity import build_parameter_identity
from dashboard.discovery_v2_registry import FIXED_EXECUTION, TEMPLATES, plan, raw_definitions
from dashboard.strategy_v2 import normalize_parameters

def candle(ts=1): return {"ts":ts,"open":1.,"high":2.,"low":.5,"close":1.5,"volume":10.,"created_at":"ignored"}

def test_versioned_dataset_identity_is_deterministic_and_order_independent():
    a,b=candle(2),candle(1); fp=partition_fingerprint([a,b])
    parts=[{"instrument":"btc-usdt","timeframe":"15m","requested_start":1,"requested_end":3,"partition_fingerprint":fp},{"instrument":"ETH-USDT","timeframe":"1H","requested_start":1,"requested_end":3,"partition_fingerprint":"x"}]
    assert partition_fingerprint([a,b])==partition_fingerprint([copy.deepcopy(a),copy.deepcopy(b)])
    assert dataset_fingerprint(parts)==dataset_fingerprint(list(reversed(parts)))

def test_v2_plan_is_bounded_deterministic_and_v1_hashes_cannot_collide():
    first,rej,meta=plan(); second,_,_=plan()
    assert first==second and len(first)<=36 and all(meta['raw_combination_counts'][t]<=100 for t in TEMPLATES)
    assert {t for t,_ in first}==set(TEMPLATES) and not rej
    template,params=first[0]
    assert build_parameter_identity(template,dict(reversed(list(params.items()))))==build_parameter_identity(template,params)
    assert build_parameter_identity(template,params)!=build_parameter_identity("TREND_BREAKOUT",{"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":False})

def test_incoherent_v2_parameters_reject_before_execution():
    bad={k:FIXED_EXECUTION[k] for k in ("risk_per_trade","max_notional_fraction","trading_fee","slippage")}|{"rsi_lower":70,"rsi_upper":65,"stop_atr_multiple":1.0}
    try: normalize_parameters("RANGE_MEAN_REVERSION_V2",bad)
    except ValueError as error: assert "rsi" in str(error).lower()
    else: raise AssertionError("incoherent parameters accepted")
