import type { HistogramData, UTCTimestamp, WhitespaceData } from "lightweight-charts";
import type { Candle } from "./data";
import type { FlowHistoryPoint } from "./flowHistory";

export const POSITIVE_FLOW_COLOR = "rgba(0,179,126,.72)";
export const NEGATIVE_FLOW_COLOR = "rgba(246,70,93,.72)";
export const NEUTRAL_FLOW_COLOR = "rgba(102,112,133,.48)";
export const POSITIVE_VOLUME_COLOR = "rgba(0,179,126,.42)";
export const NEGATIVE_VOLUME_COLOR = "rgba(246,70,93,.42)";

export function volumeHistogramData(candles: Candle[]): HistogramData<UTCTimestamp>[] {
  return candles.map(candle => ({
    time: candle.time,
    value: Number(candle.volume) || 0,
    color: candle.close >= candle.open ? POSITIVE_VOLUME_COLOR : NEGATIVE_VOLUME_COLOR,
  }));
}

/**
 * Projects each canonical per-bucket signed CVD delta onto the candle bucket.
 * Color is deliberately derived only from delta sign; candle direction is not
 * consulted anywhere in this function.
 */
export function cvdDeltaHistogramData(
  candles: Candle[],
  points: FlowHistoryPoint[],
  timeframeSeconds: number,
): Array<HistogramData<UTCTimestamp> | WhitespaceData<UTCTimestamp>> {
  const byTime = new Map(points.map(point => [point.time, point]));
  return candles.map(candle => {
    const start = Number(candle.time);
    const point = byTime.get(start) || points.find(item => item.time >= start && item.time < start + timeframeSeconds);
    if (!point || point.status === "WHITESPACE" || !Number.isFinite(point.delta)) return { time: candle.time };
    const value = Number(point.delta);
    return {
      time: candle.time,
      value,
      color: value > 0 ? POSITIVE_FLOW_COLOR : value < 0 ? NEGATIVE_FLOW_COLOR : NEUTRAL_FLOW_COLOR,
    };
  });
}
