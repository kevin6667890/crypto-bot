# rules_blueprint.py
# Strategy core logic V5.5 (performance-optimized: more precise structure scoring + no lag)

import numpy as np
import pandas as pd
import pandas_ta_classic as ta
from datetime import datetime
import pytz
import math  # used for safe NaN checks instead of numpy's isnan


# ==========================================
# 1. Market session engine
# ==========================================

def get_market_session(timezone_str: str, segments_cfg: dict) -> dict:
    tz = pytz.timezone(timezone_str)
    now_local = datetime.now(tz).time()

    current_state = {"status": "WATCH_ONLY", "segment": "global_watch", "modifier": 0.0, "min_score": 82}

    for seg_name, seg_cfg in segments_cfg.items():
        try:
            start_t = datetime.strptime(seg_cfg["start"], "%H:%M").time()
            end_t = datetime.strptime(seg_cfg["end"], "%H:%M").time()

            in_range = start_t <= now_local < end_t if start_t <= end_t else not (end_t <= now_local < start_t)

            if in_range:
                risk_mod = seg_cfg.get("risk_modifier", 1.0)
                status = "OPEN" if risk_mod > 0 else "WATCH_ONLY"
                return {
                    "status": status,
                    "segment": seg_name,
                    "modifier": risk_mod,
                    "min_score": seg_cfg.get("score_threshold", 70)
                }
        except Exception:
            continue
    return current_state


# ==========================================
# 2. Indicator calculations
# ==========================================

def _zlema(series, length):
    lag = (length - 1) // 2
    shifted = series + (series - series.shift(lag))
    return ta.ema(shifted, length=length)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "volume" not in d.columns and "vol" in d.columns:
        d["volume"] = d["vol"]

    d["ema20"] = ta.ema(d["close"], length=20)
    d["ema50"] = ta.ema(d["close"], length=50)
    d["ema100"] = ta.ema(d["close"], length=100)
    d["ema200"] = ta.ema(d["close"], length=200)
    d["zlema20"] = _zlema(d["close"], 20)

    d["rsi"] = ta.rsi(d["close"], length=14)
    d["atr"] = ta.atr(d["high"], d["low"], d["close"], length=14)

    adx = ta.adx(d["high"], d["low"], d["close"], length=14)
    if adx is not None and "ADX_14" in adx.columns:
        d["adx"] = adx["ADX_14"]
    else:
        d["adx"] = 0.0

    d["vol_ma"] = ta.sma(d["volume"], length=20)
    d["vol_ratio"] = d["volume"] / (d["vol_ma"] + 1e-9)

    win = 5
    d["high_5"] = d["high"].rolling(win, center=True).max()
    d["low_5"] = d["low"].rolling(win, center=True).min()

    return d.dropna().reset_index(drop=True)


# ==========================================
# 3. Trend & structure recognition (V5.5 core upgrade)
# ==========================================

def _extract_swings(series_high, series_low, lookback=60):
    """Extract recent swing highs/lows (used for structure analysis)"""
    h = series_high.tail(lookback)
    lows_series = series_low.tail(lookback)
    highs = h[(h.shift(1) < h) & (h.shift(-1) < h)]
    lows = lows_series[(lows_series.shift(1) > lows_series) & (lows_series.shift(-1) > lows_series)]
    return highs.dropna().tail(2).values, lows.dropna().tail(2).values


def analyze_trend_structure(df_4h, df_1h, df_15m):
    """
    V5.5 trend logic: structure analysis based on 4H/1H/15m
    """
    if df_4h.empty or df_1h.empty or df_15m.empty:
        return {"direction": "NEUTRAL", "structure_score": 0, "swing_level": np.nan, "key_levels": {}}

    row_4h = df_4h.iloc[-1]
    row_1h = df_1h.iloc[-1]

    # Macro trend determination (4H)
    up_trend = (row_4h["close"] > row_4h["ema50"] and row_4h["adx"] > 18)
    down_trend = (row_4h["close"] < row_4h["ema50"] and row_4h["adx"] > 18)

    main_dir = "NEUTRAL"
    if up_trend and row_1h["close"] > row_1h["ema50"]:
        main_dir = "LONG"
    elif down_trend and row_1h["close"] < row_1h["ema50"]:
        main_dir = "SHORT"

    # --- 15m micro-structure analysis (dynamic structure score) ---
    swing_highs, swing_lows = _extract_swings(df_15m["high_5"], df_15m["low_5"], lookback=80)

    structure_score = 0
    swing_level = np.nan

    if len(swing_highs) == 2 and len(swing_lows) == 2 and main_dir != "NEUTRAL":
        last_high1, last_high2 = swing_highs[-2], swing_highs[-1]
        last_low1, last_low2 = swing_lows[-2], swing_lows[-1]

        # Check whether structure is intact (HH/HL or LH/LL)
        if main_dir == "LONG":
            is_structure_good = (last_high2 > last_high1 and last_low2 > last_low1)
        else:  # SHORT
            is_structure_good = (last_high2 < last_high1 and last_low2 < last_low1)

        # Assign dynamic score
        if is_structure_good:
            try:
                adx_1h = float(row_1h.get("adx", 0.0))
            except Exception:
                adx_1h = 0.0
            structure_score = min(30, max(10, int(adx_1h * 1.2)))
            swing_level = float(last_low2 if main_dir == "LONG" else last_high2)

    return {
        "direction": main_dir,
        "structure_score": structure_score,
        "swing_level": swing_level,
        "key_levels": {}
    }


# ==========================================
# 4. Signal scoring system (V5.5 performance-optimized)
# ==========================================

def calculate_signal_score(df_15m, trend_info, weights):
    direction = trend_info["direction"]
    if direction == "NEUTRAL":
        return 0, {}

    row = df_15m.iloc[-1]
    details = {}
    score = 0

    # 1. Trend score (ADX already filtered)
    score += weights.get("trend_alignment", 35)

    # 2. Structure score (dynamic)
    if trend_info["structure_score"] > 0:
        score += trend_info["structure_score"]
        details["Struct"] = f"Structure aligned (+{trend_info['structure_score']}pts)✅"
    else:
        details["Struct"] = "Structure unclear ⚠️"

    # 3. Trigger quality: ZLEMA + extreme breakout
    trigger_good = False
    lookback = 40
    prev = df_15m.iloc[:-1].tail(lookback)

    if direction == "LONG":
        if row["close"] > prev["high"].max() and row["close"] > row["zlema20"] > row["ema20"]:
            trigger_good = True
    else:
        if row["close"] < prev["low"].min() and row["close"] < row["zlema20"] < row["ema20"]:
            trigger_good = True

    if trigger_good:
        score += weights.get("trigger_quality", 25)
        details["Trigger"] = "Extreme breakout+ZLEMA✅"
    else:
        details["Trigger"] = "No breakout"
        score -= 15  # heavy penalty if no breakout

    # 4. Volume (confirmation)
    vr = row["vol_ratio"]
    if vr > 1.5:
        score += weights.get("volume_analysis", 10)
    elif vr < 0.7:
        score -= 6

    # 5. Volatility (adaptive)
    atr_pct = (row["atr"] / row["close"]) * 100
    if atr_pct > 0.25:
        score += weights.get("volatility_atr", 10)

    # 6. RSI light filter
    if direction == "LONG" and row["rsi"] > 78:
        score -= 8
    elif direction == "SHORT" and row["rsi"] < 22:
        score -= 8

    score = int(min(100, max(0, score)))
    return score, details


# ==========================================
# 5. Trade plan (V5.5) -- tightened version (avoid zero trades: degrade instead of rejecting when structure is insufficient)
# ==========================================

def generate_trade_plan(df_15m, trend_info, score, session_state, cfg):
    """
    Tightened SL/TP (15m intraday):
    - max_dist: 1.8% trend / 1.2% range
    - ATR buffer: 0.9 ATR trend / 0.7 ATR range
    - swing NaN fallback: extremes over the last 32 bars
    - TP1: structure anchor preferred, otherwise fall back to R-multiple
    - TP2: None if insufficient headroom
    - Insufficient structure no longer returns None; instead enters degraded
      (0.25R) mode to avoid zero trades in backtests
    """
    if df_15m is None or df_15m.empty:
        return None

    row = df_15m.iloc[-1]
    direction = trend_info.get("direction", "NEUTRAL")
    if direction not in ("LONG", "SHORT"):
        return None

    # ATR is required
    try:
        atr = float(row["atr"])
        entry = float(row["close"])
    except Exception:
        return None
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None

    # -------- Regime determination (structure score proxy) --------
    try:
        structure_score = int(trend_info.get("structure_score", 0))
    except Exception:
        structure_score = 0

    is_trend = structure_score >= 20
    degraded = structure_score < 12  # key change: degrade instead of rejecting

    # -------- swing_level (structure-based stop anchor) --------
    raw_swing = trend_info.get("swing_level", None)
    try:
        swing_level = float(raw_swing)
    except (TypeError, ValueError):
        swing_level = float("nan")

    # swing NaN fallback: last 32 bars
    if math.isnan(swing_level):
        window = df_15m.tail(32)
        if window.empty:
            return None
        if direction == "LONG":
            swing_level = float(window["low"].min())
        else:
            swing_level = float(window["high"].max())

    # -------- Parameters (overridable via cfg.plan) --------
    plan_cfg = cfg.get("plan", {}) if isinstance(cfg, dict) else {}

    min_stop_pct = float(plan_cfg.get("min_stop_pct", 0.003))
    max_stop_pct_trend = float(plan_cfg.get("max_stop_pct_trend", 0.018))
    max_stop_pct_range = float(plan_cfg.get("max_stop_pct_range", 0.012))

    atr_mult_trend = float(plan_cfg.get("atr_mult_trend", 0.9))
    atr_mult_range = float(plan_cfg.get("atr_mult_range", 0.7))

    swing_pad_atr = float(plan_cfg.get("swing_pad_atr", 0.20))
    tp1_r_fallback = float(plan_cfg.get("tp1_r_fallback", 1.4))
    tp2_r = float(plan_cfg.get("tp2_r", 2.4))
    min_tp2_headroom_atr = float(plan_cfg.get("min_tp2_headroom_atr", 0.6))

    max_stop_pct = max_stop_pct_trend if is_trend else max_stop_pct_range
    atr_mult = atr_mult_trend if is_trend else atr_mult_range

    # -------- Structure + ATR combined stop-loss --------
    swing_pad = swing_pad_atr * atr

    if direction == "LONG":
        structural_sl = swing_level - swing_pad
        atr_sl = entry - atr_mult * atr
        raw_sl = min(structural_sl, atr_sl)
        raw_dist = entry - raw_sl
    else:
        structural_sl = swing_level + swing_pad
        atr_sl = entry + atr_mult * atr
        raw_sl = max(structural_sl, atr_sl)
        raw_dist = raw_sl - entry

    # clamp
    min_dist = entry * min_stop_pct
    max_dist = entry * max_stop_pct
    dist = max(min_dist, min(max_dist, raw_dist))
    if not np.isfinite(dist) or dist <= 0:
        return None

    sl = entry - dist if direction == "LONG" else entry + dist

    # -------- TP1/TP2 --------
    tp1 = None
    tp2 = None

    try:
        swing_highs, swing_lows = _extract_swings(df_15m["high_5"], df_15m["low_5"], lookback=120)
    except Exception:
        swing_highs, swing_lows = [], []

    if direction == "LONG":
        if len(swing_highs) >= 1:
            anchor = float(swing_highs[-1])
            if np.isfinite(anchor) and anchor > entry + 0.2 * atr:
                tp1 = anchor
        if tp1 is None:
            tp1 = entry + dist * tp1_r_fallback

        candidate_tp2 = entry + dist * tp2_r
        if candidate_tp2 - tp1 >= min_tp2_headroom_atr * atr:
            tp2 = candidate_tp2
        else:
            tp2 = None
    else:
        if len(swing_lows) >= 1:
            anchor = float(swing_lows[-1])
            if np.isfinite(anchor) and anchor < entry - 0.2 * atr:
                tp1 = anchor
        if tp1 is None:
            tp1 = entry - dist * tp1_r_fallback

        candidate_tp2 = entry - dist * tp2_r
        if tp1 - candidate_tp2 >= min_tp2_headroom_atr * atr:
            tp2 = candidate_tp2
        else:
            tp2 = None

    # -------- Risk calculation (same as original logic, plus degrade multiplier) --------
    base_equity = float(cfg["risk"]["equity_usd"])
    base_risk_pct = float(cfg["risk"]["base_risk_pct"])
    score_factor = 1.0
    session_mod = float(session_state.get("modifier", 1.0))

    risk_amt = base_equity * (base_risk_pct / 100.0) * session_mod * score_factor
    sl_dist = abs(entry - sl)
    qty = risk_amt / sl_dist if sl_dist > 0 else 0.0

    return {
        "action": direction,
        "score": score,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,  # None means no TP2
        "qty": qty,
        "risk_usd": risk_amt,
        "reason": f"{session_state['segment']} | {score}pts | {'TREND' if is_trend else 'RANGE'}{' | DEGRADED' if degraded else ''}"
    }
