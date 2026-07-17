"""Canonical deterministic decision engine shared by paper and backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from signal_identity import config_hash as make_config_hash, signal_id as make_signal_id
    from market_regime import classify_regime
except ImportError:
    from .signal_identity import config_hash as make_config_hash, signal_id as make_signal_id
    from .market_regime import classify_regime

LIVE_STRATEGY_VERSION = "live-mtf-flow-v1"
HISTORICAL_STRATEGY_VERSION = "historical-mtf-no-flow-v1"
SINGLE_TIMEFRAME_VERSION = "historical-single-timeframe-no-flow-v1"


@dataclass(frozen=True)
class MarketContext:
    instrument: str
    execution_timeframe: str
    candle_close_ts: int
    close: float
    indicators: dict[str, float | None]
    data_source: str = "OKX"
    data_version: str = "confirmed-candles-v1"


@dataclass(frozen=True)
class TimeframeContext:
    frames: dict[str, dict[str, Any]] = field(default_factory=dict)
    required_frames: tuple[str, ...] = ()
    daily_enabled: bool = False
    mode: str = "single-timeframe"


@dataclass(frozen=True)
class FlowContext:
    available: bool = False
    cvd_delta: float | None = None
    oi_change_pct: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class RiskContext:
    allowed: bool = True
    blockers: tuple[str, ...] = ()
    open_positions: int = 0
    risk_utilization: float = 0.0
    cooldown_clear: bool = True
    existing_position_clear: bool = True


@dataclass(frozen=True)
class StrategyDecision:
    signal_id: str
    instrument: str
    execution_timeframe: str
    candle_close_ts: int
    strategy_version: str
    config_hash: str
    action: str
    bias: str
    score: float
    warmed: bool
    contributions: list[dict[str, Any]]
    failed_gates: list[str]
    indicator_values: dict[str, float | None]
    timeframe_context: dict[str, Any]
    flow_context: dict[str, Any]
    risk_context: dict[str, Any]
    entry_allowed: bool
    rejection_reason: str | None
    data_source: str
    data_version: str
    decision_input_summary: dict[str, Any]
    gate_results: list[dict[str, Any]]
    regime: str
    regime_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _frame_bias(frame: dict[str, Any]) -> str:
    trend = str(frame.get("trend", "")).upper()
    if trend in {"BULLISH", "LONG"}: return "LONG"
    if trend in {"BEARISH", "SHORT"}: return "SHORT"
    close, fast, slow = frame.get("close"), frame.get("fast_ma", frame.get("ma60")), frame.get("slow_ma", frame.get("ma200"))
    if None not in (close, fast, slow):
        if float(close) > float(fast) > float(slow): return "LONG"
        if float(close) < float(fast) < float(slow): return "SHORT"
    return "WAIT"


def evaluate_decision(parameters: Any, market: MarketContext, timeframes: TimeframeContext | None = None,
                      flow: FlowContext | None = None, risk: RiskContext | None = None,
                      strategy_version: str | None = None) -> StrategyDecision:
    """Evaluate all deterministic gates. No execution or mutable state lives here."""
    timeframes, flow, risk = timeframes or TimeframeContext(), flow or FlowContext(), risk or RiskContext()
    params = asdict(parameters) if hasattr(parameters, "__dataclass_fields__") else dict(parameters)
    version = strategy_version or (LIVE_STRATEGY_VERSION if flow.available else HISTORICAL_STRATEGY_VERSION if timeframes.mode == "multi-timeframe" else SINGLE_TIMEFRAME_VERSION)
    cfg_hash = make_config_hash(params)
    ind = dict(market.indicators)
    fast, slow, ema = ind.get("fast_ma"), ind.get("slow_ma"), ind.get("ema")
    rsi, atr, volume_ratio = ind.get("rsi"), ind.get("atr"), ind.get("volume_ratio")
    warmed = all(value is not None for value in (fast, slow, ema, rsi, atr, volume_ratio))
    bias = "WAIT"
    failed: list[str] = []
    if warmed:
        assert fast is not None and slow is not None and ema is not None and rsi is not None and volume_ratio is not None
        if market.close > fast > slow and params.get("enable_long", True): bias = "LONG"
        elif market.close < fast < slow and params.get("enable_short", True): bias = "SHORT"
    frame_biases = {name: _frame_bias(frame) for name, frame in timeframes.frames.items()}
    frames_ready = all(name in timeframes.frames for name in timeframes.required_frames)
    frames_aligned = frames_ready and all(frame_biases.get(name) == bias for name in timeframes.required_frames)
    if timeframes.required_frames and not frames_ready: warmed = False
    if timeframes.required_frames and not frames_aligned: failed.append("higher_timeframe_alignment")
    distance = abs(market.close - float(ema)) / float(ema) if warmed and ema else None
    pullback = distance is not None and distance <= float(params["ema_pullback_distance"])
    momentum = warmed and float(params["rsi_min"]) <= float(rsi) <= float(params["rsi_max"]) and float(volume_ratio) >= float(params["minimum_volume_ratio"])
    flow_aligned = not flow.available or (bias == "LONG" and float(flow.cvd_delta or 0) > 0) or (bias == "SHORT" and float(flow.cvd_delta or 0) < 0)
    oi_aligned = not flow.available or flow.oi_change_pct is None or (bias == "LONG" and float(flow.oi_change_pct) >= 0) or (bias == "SHORT" and float(flow.oi_change_pct) <= 0)
    raw = [("trend", 30 if bias != "WAIT" and frames_aligned else 8, 30), ("structure", 20 if bias != "WAIT" and frames_aligned else 6, 20),
           ("pullback", 20 if pullback else 5, 20), ("momentum", 15 if momentum else 6, 15)]
    if flow.available: raw.append(("flow", 15 if flow_aligned else 5, 15))
    base_score = sum(points for _, points, _ in raw)
    score = round(base_score if flow.available else base_score / 85 * 100, 2)
    contributions = [{"key": key, "label": key.replace("_", " ").title(), "points": points, "max": maximum,
                      "status": "pass" if points == maximum else "watch"} for key, points, maximum in raw]
    contributions.append({"key": "flow", "label": "Flow unavailable" if not flow.available else "Flow", "points": 0, "max": 0, "status": "unavailable"}) if not flow.available else None
    for gate, passed in (("warmup", warmed), ("trend", bias != "WAIT"), ("pullback", pullback), ("momentum", momentum), ("flow_alignment", flow_aligned), ("risk", risk.allowed)):
        if not passed and gate not in failed: failed.append(gate)
    if not risk.cooldown_clear: failed.append("cooldown")
    if not risk.existing_position_clear: failed.append("existing_position")
    entry_allowed = not failed and score >= float(params["minimum_score"])
    if score < float(params["minimum_score"]): failed.append("minimum_score")
    action = bias if entry_allowed else "WAIT"
    rejection = None if entry_allowed else ("indicator warm-up incomplete" if not warmed else ", ".join(failed))
    tf_payload = {"mode": timeframes.mode, "required_frames": list(timeframes.required_frames), "daily_enabled": timeframes.daily_enabled,
                  "frames": timeframes.frames, "frame_biases": frame_biases}
    flow_payload = {**asdict(flow), "score_mode": "live-flow-100" if flow.available else "historical-no-flow-normalized-100"}
    risk_payload = asdict(risk); risk_payload["blockers"] = list(risk.blockers)
    sid = make_signal_id(version, cfg_hash, market.instrument, market.execution_timeframe, market.candle_close_ts)
    gates = [
        ("indicator_warmup", "Indicator Warm-up", warmed, True, True),
        ("directional_bias", "Directional Bias", bias != "WAIT", True, True),
        ("higher_timeframe_alignment", "Higher-Timeframe Alignment", not timeframes.required_frames or frames_aligned, True, True),
        ("ma_structure", "MA60 / MA200 Structure", bias != "WAIT", True, True),
        ("ema_pullback", "EMA20 Pullback", pullback, True, True),
        ("rsi_range", "RSI Range", warmed and float(params["rsi_min"]) <= float(rsi or -1) <= float(params["rsi_max"]), True, True),
        ("volume_ratio", "Volume Ratio", warmed and float(volume_ratio or -1) >= float(params["minimum_volume_ratio"]), True, True),
        ("momentum_combined", "Momentum Combined", momentum, True, True),
        ("cvd_alignment", "CVD Alignment", flow_aligned, flow.available, True),
        ("oi_context", "OI Context", oi_aligned, flow.available and flow.oi_change_pct is not None, False),
        ("flow_combined", "Flow Combined", flow_aligned, flow.available, True),
        ("minimum_score", "Minimum Score", score >= float(params["minimum_score"]), True, True),
        ("risk_permission", "Risk Permission", risk.allowed, True, True),
        ("cooldown", "Cooldown", risk.cooldown_clear, True, True),
        ("existing_position", "Existing Position", risk.existing_position_clear, True, True),
        ("final_entry_allowed", "Final Entry Allowed", entry_allowed, True, True),
    ]
    gate_results = [{"key": key, "label": label, "passed": bool(passed) if applicable else True, "applicable": applicable, "blocking": blocking} for key, label, passed, applicable, blocking in gates]
    regime = classify_regime(market.close, ind)
    return StrategyDecision(sid, market.instrument, market.execution_timeframe, int(market.candle_close_ts), version, cfg_hash,
                            action, bias, score if warmed else 0.0, warmed, contributions, failed, ind, tf_payload, flow_payload,
                            risk_payload, entry_allowed, rejection, market.data_source, market.data_version,
                            {"close": market.close, "indicator_keys": sorted(ind), "frame_close_timestamps": {k: v.get("candle_close_ts") for k, v in timeframes.frames.items()}, "parameters": params},
                            gate_results, regime["name"], regime["version"])


def asof_timeframe_context(execution_close_ts: int, datasets: dict[str, list[dict[str, Any]]], required_frames: tuple[str, ...], daily_enabled: bool = False) -> TimeframeContext:
    """Select only higher-timeframe candles whose explicit close timestamp is causal."""
    selected: dict[str, dict[str, Any]] = {}
    for frame, rows in datasets.items():
        eligible = [row for row in rows if int(row.get("candle_close_ts", row.get("ts", 0))) <= int(execution_close_ts) and bool(row.get("confirmed", True))]
        if eligible: selected[frame] = dict(max(eligible, key=lambda row: int(row.get("candle_close_ts", row.get("ts", 0)))))
    return TimeframeContext(selected, required_frames + (("1D",) if daily_enabled and "1D" not in required_frames else ()), daily_enabled, "multi-timeframe")
