"""Small deterministic v2 Discovery registry; v1's registry is deliberately untouched."""
from __future__ import annotations
from itertools import product
from .strategy_v2 import TEMPLATES, TEMPLATE_VERSION, normalize_parameters, DISCOVERY_STRATEGY_VERSION, STRATEGY_RULES_VERSION, V2_FEATURE_VERSION

V2_DISCOVERY_REGISTRY_VERSION = "discovery-strategy-v2"
V2_SAMPLING_POLICY_VERSION = "discovery-v2-bounded-grid-v1"
V2_SEMANTIC_SEED = 20260723
FIXED_EXECUTION = {"risk_per_trade": .01, "max_notional_fraction": 1.0, "trading_fee": .0005, "slippage": .0003,
                   "initial_capital": 10000.0, "cooldown_bars": 16, "next_open_execution": True, "end_of_data_close": True,
                   "collision_policy": "STOP_WINS_TIE"}

# Regime policy is intentionally fixed.  Search only coherent setup/trigger/stop/exit behavior.
SPACES = {
 "TREND_PULLBACK_V2": {"pullback_max_atr": [.5, 1.0], "stop_mode": ["ATR", "RECENT_SWING"], "stop_atr_multiple": [1.0, 1.5], "target_r": [1.5, 2.0]},
 "TREND_BREAKOUT_V2": {"breakout_lookback": [20, 40], "stop_atr_multiple": [1.0, 1.5], "target_r": [1.5, 2.0]},
 "RANGE_MEAN_REVERSION_V2": {"rsi_lower": [30, 35], "rsi_upper": [65, 70], "stop_atr_multiple": [1.0, 1.5]},
}
PARAMETER_CLASSES = {
 "TREND_PULLBACK_V2": {"regime": [], "setup": ["pullback_max_atr"], "trigger": [], "stop": ["stop_mode", "stop_atr_multiple"], "exit": ["target_r"], "risk": [], "execution": []},
 "TREND_BREAKOUT_V2": {"regime": [], "setup": ["breakout_lookback"], "trigger": [], "stop": ["stop_atr_multiple"], "exit": ["target_r"], "risk": [], "execution": []},
 "RANGE_MEAN_REVERSION_V2": {"regime": [], "setup": ["rsi_lower", "rsi_upper"], "trigger": [], "stop": ["stop_atr_multiple"], "exit": ["BOLLINGER_MIDLINE (fixed policy)"], "risk": [], "execution": []},
}

def rejection_reason(template: str, p: dict) -> str | None:
    if float(p.get("stop_atr_multiple", 0)) <= 0: return "NONPOSITIVE_STOP_ATR_MULTIPLE"
    if template == "TREND_BREAKOUT_V2" and int(p.get("breakout_lookback", 0)) < 10: return "BREAKOUT_LOOKBACK_TOO_SHORT"
    if template == "RANGE_MEAN_REVERSION_V2" and int(p["rsi_lower"]) >= int(p["rsi_upper"]): return "RSI_EXTREMES_UNORDERED"
    return None

def raw_definitions(template: str):
    if template not in TEMPLATES: raise ValueError("Unknown v2 template")
    keys = sorted(SPACES[template]); rows=[]; rejected=[]
    for values in product(*(SPACES[template][k] for k in keys)):
        raw = {**{k: FIXED_EXECUTION[k] for k in ("risk_per_trade", "max_notional_fraction", "trading_fee", "slippage")}, **dict(zip(keys, values))}
        try: p=normalize_parameters(template, raw)
        except ValueError as exc: rejected.append((raw, "NORMALIZATION:" + str(exc))); continue
        reason=rejection_reason(template,p)
        (rejected if reason else rows).append((p,reason) if reason else p)
    return rows, rejected

def plan(maximum: int = 36):
    if not 1 <= maximum <= 36: raise ValueError("v2 pilot maximum is 1..36")
    rows=[]; rejected=[]
    for template in TEMPLATES:
        valid,bad=raw_definitions(template); rows += [(template,p) for p in valid]; rejected += [(template,p,r) for p,r in bad]
    # Round-robin is deterministic and preserves template coverage; raw grid is 26 <= 36.
    return rows[:maximum], rejected, {"policy_version": V2_SAMPLING_POLICY_VERSION, "semantic_seed": V2_SEMANTIC_SEED,
                                      "raw_combination_counts": {t: len(raw_definitions(t)[0])+len(raw_definitions(t)[1]) for t in TEMPLATES},
                                      "sampled_candidate_count": min(len(rows), maximum)}
