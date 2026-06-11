# backtest_g.py
# Wall Street Day Trading Hunter V5 - backtest version (with data caching + multi-param comparison)

import sys
import ccxt
import pandas as pd
import numpy as np
import time
import os
import pickle
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

# The project root (where rules_blueprint.py lives) must be added to sys.path,
# since this file lives in the backtest/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules_blueprint import (
    compute_indicators,
    analyze_trend_structure,
    calculate_signal_score,
    generate_trade_plan,
)

# ==========================================
# ⚙️ Backtest configuration
# ==========================================
SYMBOLS = ["BTC/USDT", "SOL/USDT","ETH/USDT", ]
BASE_TF = "1m"
DAYS_BACK = 730
INITIAL_BALANCE = 7500.0
COMMISSION = 0.001

# Data cache directory
CACHE_DIR = "data_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# 📊 Single strategy configuration (EMA20 pullback + 3R TP + 1R breakeven, original stop-loss)
PARAM_SWEEP = [
    {"name": "EMA20_3R_BE1", "regime_mode": "trend", "adx_threshold": 25, "sl_mode": "orig"},
]

# Fixed parameters per regime
REGIME_PARAMS = {
    "trend":   {"pullback_target": "ema20",  "tp1_r": 3.0, "breakeven_r": 1.0, "expire_bars": 8},
    "ranging": {"pullback_target": "zlema20","tp1_r": 2.0, "breakeven_r": 1.0, "expire_bars": 4},
}


def make_cfg(min_score: int = 70):
    return {
        "session": {"timezone": "America/New_York", "segments": {}},
        "risk": {"equity_usd": INITIAL_BALANCE, "base_risk_pct": 1.0},
        "score_weights": {
            "trend_alignment": 35,
            "structure_quality": 20,
            "trigger_quality": 25,
            "volume_analysis": 10,
            "volatility_atr": 10,
        },
    }


def get_session(min_score: int = 70):
    return {"status": "OPEN", "segment": "BACKTEST", "modifier": 1.0, "min_score": min_score}


def resample_data(df_5m, target_tf):
    rule = target_tf.replace("m", "min").lower()
    agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    df_res = df_5m.resample(rule, on="ts").agg(agg_dict).dropna()
    df_res["ts"] = df_res.index
    df_res = df_res.reset_index(drop=True)
    return compute_indicators(df_res)


# ==========================================
# Data fetching (with caching, stored per symbol)
# ==========================================
def fetch_or_load_data(symbol: str):
    sym_safe = symbol.replace("/", "_")
    cache_file = os.path.join(CACHE_DIR, f"{sym_safe}_{BASE_TF}_{DAYS_BACK}d.pkl")

    # Cache validity: 720 hours (30 days)
    if os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_hours < 720:
            print(f"   📦 {symbol} using cache (from {age_hours:.1f}h ago)")
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        else:
            print(f"   ⏰ {symbol} cache expired ({age_hours:.1f}h), re-downloading")

    print(f"   📥 Downloading {symbol} ...")
    ex = ccxt.binance()
    start_time = datetime.now() - timedelta(days=DAYS_BACK)
    since = int(start_time.timestamp() * 1000)
    all_ohlcv = []

    while True:
        try:
            ohlcv = ex.fetch_ohlcv(symbol, BASE_TF, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            print(f"      Fetched: {len(all_ohlcv)} candles", end="\r")
            if len(ohlcv) < 1000:
                break
            time.sleep(0.05)
        except Exception as e:
            print(f"\n   ❌ {symbol} download interrupted: {e}")
            break

    if not all_ohlcv:
        return None

    df = pd.DataFrame(all_ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

    with open(cache_file, "wb") as f:
        pickle.dump(df, f)
    print(f"\n   💾 Cached to {cache_file}")

    return df


# ==========================================
# Single backtest run
# ==========================================
def run_single_backtest(df_5m_raw, df_15m_full, df_1h_full, df_4h_full, params):
    name = params["name"]
    regime_mode = params["regime_mode"]
    adx_threshold = params["adx_threshold"]
    sl_mode = params.get("sl_mode", "orig")
    min_score = 70

    df_5m = df_5m_raw
    balance = INITIAL_BALANCE
    position = None
    pending_signal = None
    history = []
    trade_log = []
    equity_curve = [{"ts": df_5m["ts"].iloc[0], "bal": balance}]
    max_balance = balance
    max_drawdown = 0.0
    breakeven_count = 0
    signal_expired_count = 0
    regime_counts = {"trend": 0, "ranging": 0}

    session = get_session(min_score)

    for i in range(500, len(df_5m)):
        row = df_5m.iloc[i]
        curr_ts = row["ts"]

        # Position management
        if position:
            bp = position["regime_params"]
            breakeven_r = bp["breakeven_r"]
            trailing_r = bp.get("trailing_r")

            # Breakeven stop-loss
            if breakeven_r is not None and not position.get("be_triggered", False):
                if position["action"] == "LONG":
                    if row["high"] >= position["entry"] + position["init_dist"] * breakeven_r:
                        position["sl"] = position["entry"]
                        position["be_triggered"] = True
                        breakeven_count += 1
                else:
                    if row["low"] <= position["entry"] - position["init_dist"] * breakeven_r:
                        position["sl"] = position["entry"]
                        position["be_triggered"] = True
                        breakeven_count += 1

            # Trailing stop
            if trailing_r:
                dist = position["init_dist"]
                if position["action"] == "LONG":
                    new_trail = row["high"] - dist * trailing_r
                    if new_trail > position["sl"]:
                        position["sl"] = new_trail
                else:
                    new_trail = row["low"] + dist * trailing_r
                    if new_trail < position["sl"]:
                        position["sl"] = new_trail

            close_reason = None
            exit_price = row["close"]

            if position["action"] == "LONG":
                if row["low"] <= position["sl"]:
                    close_reason = "BE" if position.get("be_triggered") else "SL"
                    exit_price = position["sl"]
                elif row["high"] >= position["tp1"]:
                    close_reason, exit_price = "TP", position["tp1"]
            else:
                if row["high"] >= position["sl"]:
                    close_reason = "BE" if position.get("be_triggered") else "SL"
                    exit_price = position["sl"]
                elif row["low"] <= position["tp1"]:
                    close_reason, exit_price = "TP", position["tp1"]

            if close_reason:
                pnl_pct = (exit_price - position["entry"]) / position["entry"]
                if position["action"] == "SHORT":
                    pnl_pct *= -1
                trade_val = position["qty"] * position["entry"]
                net_pnl = trade_val * pnl_pct - trade_val * COMMISSION * 2
                balance += net_pnl

                trade_log.append({
                    "entry_ts": position.get("entry_ts", curr_ts),
                    "exit_ts": curr_ts,
                    "action": position["action"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "res": close_reason,
                    "pnl_pct": pnl_pct * 100,
                    "net_pnl": net_pnl,
                    "bal_after": balance,
                    "regime": position.get("regime", "?"),
                })

                if balance <= INITIAL_BALANCE * 0.1:
                    history.append({"ts": curr_ts, "res": close_reason, "pnl": net_pnl, "bal": balance})
                    equity_curve.append({"ts": curr_ts, "bal": balance})
                    position = None
                    break

                if balance > max_balance:
                    max_balance = balance
                dd = (max_balance - balance) / max_balance
                if dd > max_drawdown:
                    max_drawdown = dd

                history.append({"ts": curr_ts, "res": close_reason, "pnl": net_pnl, "bal": balance})
                equity_curve.append({"ts": curr_ts, "bal": balance})
                position = None
                continue

        # Pending signal (pullback detection)
        if position is None and pending_signal is not None:
            sig = pending_signal
            bp = sig["regime_params"]
            expire_bars = bp.get("expire_bars", 8)

            if i - sig["bar_idx"] > expire_bars:
                pending_signal = None
                signal_expired_count += 1
            else:
                triggered = False
                final_entry = None
                df15_now = df_15m_full[df_15m_full["ts"] < curr_ts]

                if len(df15_now) > 0:
                    last15 = df15_now.iloc[-1]
                    target_col = bp["pullback_target"]
                    pb_level = last15.get(target_col, None)

                    if pb_level is not None and not pd.isna(pb_level):
                        lower, upper = pb_level * 0.997, pb_level * 1.003
                        if sig["action"] == "LONG":
                            if row["low"] <= upper and row["high"] >= lower:
                                triggered, final_entry = True, pb_level
                        else:
                            if row["high"] >= lower and row["low"] <= upper:
                                triggered, final_entry = True, pb_level

                if triggered:
                    orig_sl_dist = abs(sig["entry"] - sig["sl"])

                    # ---- Stop-loss method selection ----
                    if sl_mode == "fixed_wide":
                        sl_dist = orig_sl_dist * 1.5
                    elif sl_mode == "atr_protect":
                        # last15 is guaranteed to be defined here (triggered is only
                        # True within the len(df15_now) > 0 branch)
                        atr_now = float(last15.get("atr", orig_sl_dist) or orig_sl_dist)
                        sl_dist = max(orig_sl_dist, atr_now * 1.5)
                    else:  # "orig"
                        sl_dist = orig_sl_dist

                    tp1_r = bp["tp1_r"]
                    if sig["action"] == "LONG":
                        new_sl = final_entry - sl_dist
                        new_tp = final_entry + sl_dist * tp1_r
                    else:
                        new_sl = final_entry + sl_dist
                        new_tp = final_entry - sl_dist * tp1_r

                    risk_amt = balance * 0.01
                    qty = risk_amt / sl_dist  # position size based on actual SL distance, keeps risk constant
                    notional = qty * final_entry
                    if notional > balance * 10:
                        qty = (balance * 10) / final_entry
                        notional = balance * 10

                    if balance > notional / 7 and qty > 0:
                        position = {
                            "action": sig["action"],
                            "entry": final_entry,
                            "qty": qty,
                            "sl": new_sl,
                            "tp1": new_tp,
                            "init_dist": sl_dist,  # breakeven/trailing both based on actual sl_dist
                            "be_triggered": False,
                            "entry_ts": curr_ts,
                            "regime": sig["regime"],
                            "regime_params": bp,
                        }
                    pending_signal = None

        # Signal generation
        if position is None and pending_signal is None and curr_ts.minute % 15 == 0:
            mask_15 = df_15m_full["ts"] < curr_ts
            mask_1h = df_1h_full["ts"] < curr_ts
            mask_4h = df_4h_full["ts"] < curr_ts

            if not (mask_15.any() and mask_1h.any() and mask_4h.any()):
                continue

            df_core = df_15m_full.loc[mask_15].tail(100)
            df_mid = df_1h_full.loc[mask_1h].tail(100)
            df_macro = df_4h_full.loc[mask_4h].tail(100)

            trend_info = analyze_trend_structure(df_macro, df_mid, df_core)

            if trend_info["direction"] != "NEUTRAL":
                cfg = make_cfg(min_score)
                score, _ = calculate_signal_score(df_core, trend_info, cfg["score_weights"])

                if score >= min_score:
                    cfg["risk"]["equity_usd"] = balance
                    plan = generate_trade_plan(df_core, trend_info, score, session, cfg)
                    if plan is None:
                        continue

                    # 🔧 Core: decide which regime to use based on the current ADX
                    current_adx = float(df_core.iloc[-1].get("adx", 0) or 0)

                    if regime_mode == "trend":
                        current_regime = "trend"
                    elif regime_mode == "ranging":
                        current_regime = "ranging"
                    else:  # adaptive
                        current_regime = "trend" if current_adx >= adx_threshold else "ranging"

                    regime_counts[current_regime] += 1
                    bp = REGIME_PARAMS[current_regime]

                    # Adjust TP (using the original SL distance first)
                    sl_dist = abs(plan["entry"] - plan["sl"])
                    if plan["action"] == "LONG":
                        plan["tp1"] = plan["entry"] + sl_dist * bp["tp1_r"]
                    else:
                        plan["tp1"] = plan["entry"] - sl_dist * bp["tp1_r"]

                    pending_signal = {
                        "action": plan["action"],
                        "entry": plan["entry"],
                        "sl": plan["sl"],
                        "tp1": plan["tp1"],
                        "mode": "pullback",
                        "bar_idx": i,
                        "signal_ts": curr_ts,
                        "regime": current_regime,
                        "regime_params": bp,
                    }

    # Statistics
    wins = [x for x in history if x["pnl"] > 0]
    total = len(history)
    win_rate = len(wins) / total * 100 if total > 0 else 0
    tp_c = len([x for x in history if x["res"] == "TP"])
    sl_c = len([x for x in history if x["res"] == "SL"])
    be_c = len([x for x in history if x["res"] == "BE"])

    win_sum = sum(x["pnl"] for x in history if x["pnl"] > 0)
    loss_sum = abs(sum(x["pnl"] for x in history if x["pnl"] < 0))
    pf = win_sum / loss_sum if loss_sum > 0 else 999

    total_return = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return {
        "name": name,
        "sl_mode": sl_mode,
        "final_balance": balance,
        "total_return_pct": total_return,
        "trades": total,
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown * 100,
        "profit_factor": pf,
        "equity_curve": equity_curve,
        "tp_count": tp_c,
        "sl_count": sl_c,
        "be_count": be_c,
        "be_triggered": breakeven_count,
        "signal_expired": signal_expired_count,
        "regime_counts": regime_counts,
        "trade_log": trade_log,
    }

    cfg = make_cfg(min_score)
    cfg["plan"] = {"tp1_r_fallback": tp1_r, "tp2_r": tp1_r + 1.0}

    df_5m = df_5m_raw

    balance = INITIAL_BALANCE
    position = None
    pending_signal = None  # used for pullback / confirm modes
    history = []
    trade_log = []  # detailed trade log
    equity_curve = [{"ts": df_5m["ts"].iloc[0], "bal": balance}]
    max_balance = balance
    max_drawdown = 0.0
    breakeven_count = 0
    signal_expired_count = 0  # number of signals that expired while pending

    session = get_session(min_score)

    for i in range(500, len(df_5m)):
        row = df_5m.iloc[i]
        curr_ts = row["ts"]

        # Position management
        if position:
            # 🔧 Breakeven stop-loss
            if breakeven_r is not None and not position.get("be_triggered", False):
                if position["action"] == "LONG":
                    if row["high"] >= position["entry"] + position["init_dist"] * breakeven_r:
                        position["sl"] = position["entry"]
                        position["be_triggered"] = True
                        breakeven_count += 1
                else:
                    if row["low"] <= position["entry"] - position["init_dist"] * breakeven_r:
                        position["sl"] = position["entry"]
                        position["be_triggered"] = True
                        breakeven_count += 1

            # 🔧 Trailing stop
            if trailing_r is not None:
                dist = position["init_dist"]
                if position["action"] == "LONG":
                    # for every trailing_r of R gained, trail the stop by one trailing_r
                    new_trail_sl = row["high"] - dist * trailing_r
                    if new_trail_sl > position["sl"]:
                        position["sl"] = new_trail_sl
                else:
                    new_trail_sl = row["low"] + dist * trailing_r
                    if new_trail_sl < position["sl"]:
                        position["sl"] = new_trail_sl

            close_reason = None
            exit_price = row["close"]

            if position["action"] == "LONG":
                if row["low"] <= position["sl"]:
                    close_reason, exit_price = ("BE" if position.get("be_triggered") else "SL"), position["sl"]
                elif row["high"] >= position["tp1"]:
                    close_reason, exit_price = "TP", position["tp1"]
            else:
                if row["high"] >= position["sl"]:
                    close_reason, exit_price = ("BE" if position.get("be_triggered") else "SL"), position["sl"]
                elif row["low"] <= position["tp1"]:
                    close_reason, exit_price = "TP", position["tp1"]

            if close_reason:
                pnl_pct = (exit_price - position["entry"]) / position["entry"]
                if position["action"] == "SHORT":
                    pnl_pct *= -1

                trade_val = position["qty"] * position["entry"]
                raw_pnl = trade_val * pnl_pct
                fee = trade_val * COMMISSION * 2
                net_pnl = raw_pnl - fee

                balance += net_pnl

                # Detailed trade log
                trade_log.append({
                    "entry_ts": position.get("entry_ts", curr_ts),
                    "exit_ts": curr_ts,
                    "action": position["action"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "sl": position["sl"],
                    "tp1": position["tp1"],
                    "res": close_reason,
                    "pnl_pct": pnl_pct * 100,
                    "net_pnl": net_pnl,
                    "bal_after": balance,
                    "be_triggered": position.get("be_triggered", False),
                })

                if balance <= INITIAL_BALANCE * 0.1:
                    history.append({"ts": curr_ts, "type": position["action"], "res": close_reason, "pnl": net_pnl, "bal": balance})
                    equity_curve.append({"ts": curr_ts, "bal": balance})
                    position = None
                    break

                if balance > max_balance:
                    max_balance = balance
                dd = (max_balance - balance) / max_balance
                if dd > max_drawdown:
                    max_drawdown = dd

                history.append({"ts": curr_ts, "type": position["action"], "res": close_reason, "pnl": net_pnl, "bal": balance})
                equity_curve.append({"ts": curr_ts, "bal": balance})
                position = None
                continue

        # === Pending signal (pullback/confirm modes only) ===
        if position is None and pending_signal is not None:
            sig = pending_signal

            # Timeout cancellation (more than 8 x 5m candles, i.e. 40 minutes)
            if i - sig["bar_idx"] > 8:
                pending_signal = None
                signal_expired_count += 1
            else:
                # Check trigger condition
                triggered = False
                final_entry = None

                if sig["mode"] == "pullback":
                    # Get the pullback target price
                    df15_now = df_15m_full[df_15m_full["ts"] < curr_ts]
                    if len(df15_now) > 0:
                        last15 = df15_now.iloc[-1]
                        target_col = {
                            "ema20": "ema20",
                            "zlema": "zlema20",
                            "ema50": "ema50",
                        }.get(pullback_target, "ema20")

                        pb_level = last15.get(target_col, None)
                        if pb_level is not None and not pd.isna(pb_level):
                            lower = pb_level * 0.997
                            upper = pb_level * 1.003
                            if sig["action"] == "LONG":
                                if row["low"] <= upper and row["high"] >= lower:
                                    triggered = True
                                    final_entry = pb_level
                            else:
                                if row["high"] >= lower and row["low"] <= upper:
                                    triggered = True
                                    final_entry = pb_level

                elif sig["mode"] == "confirm":
                    # Wait for the next 15m close to confirm (still in breakout direction)
                    if curr_ts.minute % 15 == 0 and i > sig["bar_idx"] + 2:
                        if sig["action"] == "LONG" and row["close"] > sig["entry"]:
                            triggered = True
                            final_entry = row["close"]
                        elif sig["action"] == "SHORT" and row["close"] < sig["entry"]:
                            triggered = True
                            final_entry = row["close"]
                        else:
                            # Confirmation failed
                            pending_signal = None
                            signal_expired_count += 1

                if triggered:
                    # Recompute SL/TP with the new entry (keeping the original sl_dist ratio)
                    orig_sl_dist = abs(sig["entry"] - sig["sl"])
                    if sig["action"] == "LONG":
                        new_sl = final_entry - orig_sl_dist
                        new_tp = final_entry + orig_sl_dist * tp1_r
                    else:
                        new_sl = final_entry + orig_sl_dist
                        new_tp = final_entry - orig_sl_dist * tp1_r

                    # Recalculate qty
                    risk_amt = balance * 0.01  # 1%
                    qty = risk_amt / orig_sl_dist

                    MAX_LEVERAGE = 10
                    max_notional = balance * MAX_LEVERAGE
                    notional = qty * final_entry
                    if notional > max_notional:
                        qty = max_notional / final_entry
                        notional = max_notional

                    cost = notional / 7
                    if balance > cost and qty > 0:
                        position = {
                            "action": sig["action"],
                            "entry": final_entry,
                            "qty": qty,
                            "sl": new_sl,
                            "tp1": new_tp,
                            "init_dist": orig_sl_dist,
                            "be_triggered": False,
                            "entry_ts": curr_ts,
                        }
                    pending_signal = None

        # === Entry signal generation ===
        if position is None and pending_signal is None and curr_ts.minute % 15 == 0:
            mask_15 = df_15m_full["ts"] < curr_ts
            mask_1h = df_1h_full["ts"] < curr_ts
            mask_4h = df_4h_full["ts"] < curr_ts

            if not (mask_15.any() and mask_1h.any() and mask_4h.any()):
                continue

            df_core = df_15m_full.loc[mask_15].tail(100)
            df_mid = df_1h_full.loc[mask_1h].tail(100)
            df_macro = df_4h_full.loc[mask_4h].tail(100)

            trend_info = analyze_trend_structure(df_macro, df_mid, df_core)

            if trend_info["direction"] != "NEUTRAL":
                score, _ = calculate_signal_score(df_core, trend_info, cfg["score_weights"])

                if score >= min_score:
                    cfg["risk"]["equity_usd"] = balance
                    plan = generate_trade_plan(df_core, trend_info, score, session, cfg)
                    if plan is None:
                        continue

                    if not use_swing_tp:
                        sl_dist = abs(plan["entry"] - plan["sl"])
                        if plan["action"] == "LONG":
                            plan["tp1"] = plan["entry"] + sl_dist * tp1_r
                        else:
                            plan["tp1"] = plan["entry"] - sl_dist * tp1_r

                    if entry_mode == "breakout":
                        # Original logic: enter immediately
                        MAX_LEVERAGE = 10
                        max_notional = balance * MAX_LEVERAGE
                        notional = plan["qty"] * plan["entry"]
                        if notional > max_notional:
                            plan["qty"] = max_notional / plan["entry"]
                            notional = max_notional

                        cost = notional / 7
                        if balance > cost and plan["qty"] > 0:
                            position = {
                                "action": plan["action"],
                                "entry": plan["entry"],
                                "qty": plan["qty"],
                                "sl": plan["sl"],
                                "tp1": plan["tp1"],
                                "init_dist": abs(plan["entry"] - plan["sl"]),
                                "be_triggered": False,
                                "entry_ts": curr_ts,
                            }
                    else:
                        # pullback / confirm: record the signal first and wait for the trigger
                        pending_signal = {
                            "action": plan["action"],
                            "entry": plan["entry"],
                            "sl": plan["sl"],
                            "tp1": plan["tp1"],
                            "mode": entry_mode,
                            "bar_idx": i,
                            "signal_ts": curr_ts,
                        }

    # Statistics
    wins = len([x for x in history if x["pnl"] > 0])
    total = len(history)
    win_rate = (wins / total * 100) if total > 0 else 0

    # Categorize exit reasons
    tp_count = len([x for x in history if x["res"] == "TP"])
    sl_count = len([x for x in history if x["res"] == "SL"])
    be_count = len([x for x in history if x["res"] == "BE"])

    pf = 0
    if total > 0:
        win_sum = sum([x["pnl"] for x in history if x["pnl"] > 0])
        loss_sum = abs(sum([x["pnl"] for x in history if x["pnl"] < 0]))
        pf = win_sum / loss_sum if loss_sum > 0 else 999

    total_return = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return {
        "name": name,
        "final_balance": balance,
        "total_return_pct": total_return,
        "trades": total,
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown * 100,
        "profit_factor": pf,
        "equity_curve": equity_curve,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "be_count": be_count,
        "be_triggered": breakeven_count,
        "signal_expired": signal_expired_count,
        "trade_log": trade_log,
    }


# ==========================================
# Main flow
# ==========================================
def main():
    print(f"🚀 Multi-symbol backtest | Days: {DAYS_BACK} | Initial balance: ${INITIAL_BALANCE}")
    print(f"💡 Strategy: EMA20 pullback + 3R TP + 1R breakeven | Symbols: {', '.join(SYMBOLS)}\n")

    # symbol -> list[result]
    all_symbol_results: dict = {}

    for sym in SYMBOLS:
        sym_safe = sym.replace("/", "_")
        print(f"\n{'#'*65}")
        print(f"# Symbol: {sym}")
        print(f"{'#'*65}")

        df_raw = fetch_or_load_data(sym)
        if df_raw is None or df_raw.empty:
            print(f"   ❌ {sym} data load failed, skipping")
            continue

        print(f"   📊 {len(df_raw)} candles  "
              f"{df_raw['ts'].iloc[0]} → {df_raw['ts'].iloc[-1]}")

        print("   🧮 Computing indicators and multi-timeframe data...")
        df_5m = compute_indicators(df_raw.copy())
        df_5m["high_5"] = df_5m["high_5"].shift(2)
        df_5m["low_5"]  = df_5m["low_5"].shift(2)

        df_15m = resample_data(df_5m, "15m")
        df_1h  = resample_data(df_5m, "1h")
        df_4h  = resample_data(df_5m, "4h")

        df_15m["high_5"] = df_15m["high_5"].shift(2)
        df_15m["low_5"]  = df_15m["low_5"].shift(2)

        sym_results = []
        for idx, params in enumerate(PARAM_SWEEP):
            print(f"\n   {'='*56}")
            print(f"   ⚡ [{idx+1}/{len(PARAM_SWEEP)}] {sym} | {params['name']}")
            print(f"   {'='*56}")

            result = run_single_backtest(df_5m, df_15m, df_1h, df_4h, params)
            result["symbol"] = sym
            sym_results.append(result)

            print(f"   ✅ Balance ${result['final_balance']:.2f} ({result['total_return_pct']:+.2f}%) | "
                  f"PF {result['profit_factor']:.2f} | Win rate {result['win_rate']:.1f}% | "
                  f"Trades {result['trades']} "
                  f"(TP:{result['tp_count']}/SL:{result['sl_count']}/BE:{result['be_count']}) | "
                  f"Drawdown {result['max_drawdown_pct']:.2f}%")

            if result["trade_log"]:
                log_df = pd.DataFrame(result["trade_log"])
                log_fname = f"trades_{sym_safe}_{params['name']}.csv"
                log_df.to_csv(log_fname, index=False)
                print(f"   📝 Trade log: {log_fname}")

        # ── Per-symbol results table ──
        print(f"\n   {'='*90}")
        print(f"   📊 {sym} backtest results")
        print(f"   {'='*90}")
        print(f"   {'Config':<18} {'Final Balance':>12} {'Return':>10} {'PF':>8} "
              f"{'Win Rate':>8} {'Trades':>8} {'Drawdown':>10}")
        print(f"   {'-'*88}")
        for r in sym_results:
            print(f"   {r['name']:<18} ${r['final_balance']:>10.2f} "
                  f"{r['total_return_pct']:>+8.2f}% {r['profit_factor']:>8.2f} "
                  f"{r['win_rate']:>6.1f}% {r['trades']:>8} {r['max_drawdown_pct']:>8.2f}%")
        print(f"   {'='*90}")

        all_symbol_results[sym] = sym_results

    if not all_symbol_results:
        print("❌ No valid backtest results")
        return

    # ── Multi-symbol best-config comparison table ──
    print(f"\n\n{'='*100}")
    print("🌐 Multi-symbol best config comparison (selected by highest PF)")
    print(f"{'='*100}")
    print(f"{'Symbol':<14} {'Config':<18} {'Final Balance':>12} {'Return':>10} "
          f"{'PF':>8} {'Win Rate':>8} {'Trades':>8} {'Drawdown':>10}")
    print(f"{'-'*100}")

    best_per_sym: dict = {}
    for sym, sym_results in all_symbol_results.items():
        best = max(sym_results, key=lambda r: r["profit_factor"])
        best_per_sym[sym] = best
        print(f"{sym:<14} {best['name']:<18} ${best['final_balance']:>10.2f} "
              f"{best['total_return_pct']:>+8.2f}% {best['profit_factor']:>8.2f} "
              f"{best['win_rate']:>6.1f}% {best['trades']:>8} {best['max_drawdown_pct']:>8.2f}%")
    print(f"{'='*100}")

    overall_best = max(best_per_sym.values(), key=lambda r: r["profit_factor"])
    print(f"\n🏆 Overall best PF: {overall_best['symbol']} | {overall_best['name']} "
          f"(PF={overall_best['profit_factor']:.2f}, Return {overall_best['total_return_pct']:+.2f}%)")

    # ── Per-symbol equity curve comparison chart ──
    sym_colors = {
        "ETH/USDT": "#2962ff",
        "BTC/USDT": "#ff9800",
        "SOL/USDT": "#089981",
    }

    fig, axes = plt.subplots(2, 1, figsize=(14, 10),
                             gridspec_kw={"height_ratios": [3, 1]})

    # Top chart: equity curves (best config per symbol)
    ax1 = axes[0]
    for sym, best in best_per_sym.items():
        df_eq = pd.DataFrame(best["equity_curve"])
        df_eq["ts"] = pd.to_datetime(df_eq["ts"])
        color = sym_colors.get(sym, "#aaaaaa")
        sym_short = sym.split("/")[0]
        label = (f"{sym_short}  Return {best['total_return_pct']:+.1f}%  "
                 f"PF {best['profit_factor']:.2f}  "
                 f"Win Rate {best['win_rate']:.1f}%  "
                 f"Drawdown {best['max_drawdown_pct']:.1f}%")
        ax1.plot(df_eq["ts"], df_eq["bal"],
                 label=label, color=color, linewidth=1.5, alpha=0.9)

    ax1.axhline(y=INITIAL_BALANCE, color="gray", linestyle="--", alpha=0.5, label="Initial Balance")
    ax1.set_title(
        f"Multi-Symbol Equity Curve Comparison | EMA20 Pullback + 3R TP + 1R Breakeven | {DAYS_BACK} Days",
        fontsize=13,
    )
    ax1.set_ylabel("USDT")
    ax1.grid(True, alpha=0.25)
    ax1.legend(loc="upper left", fontsize=9)

    # Bottom chart: per-symbol TP/SL/BE distribution bar chart
    ax2 = axes[1]
    syms = list(best_per_sym.keys())
    sym_shorts = [s.split("/")[0] for s in syms]
    x = np.arange(len(syms))
    width = 0.25
    tp_vals = [best_per_sym[s]["tp_count"] for s in syms]
    sl_vals = [best_per_sym[s]["sl_count"] for s in syms]
    be_vals = [best_per_sym[s]["be_count"] for s in syms]
    ax2.bar(x - width, tp_vals, width, label="TP", color="#089981", alpha=0.8)
    ax2.bar(x,         sl_vals, width, label="SL", color="#f23645", alpha=0.8)
    ax2.bar(x + width, be_vals, width, label="BE", color="#ff9800", alpha=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(sym_shorts, fontsize=11)
    ax2.set_ylabel("Exit Count")
    ax2.set_title("Per-Symbol TP / SL / BE Distribution")
    ax2.legend()
    ax2.grid(True, axis="y", alpha=0.25)

    plt.tight_layout()
    fname = f"multi_symbol_{int(time.time())}.png"
    plt.savefig(fname, dpi=110, bbox_inches="tight")
    print(f"\n📸 Comparison chart: {fname}")


if __name__ == "__main__":
    main()