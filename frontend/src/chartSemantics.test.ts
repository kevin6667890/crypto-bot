import { describe, expect, it } from "vitest";
import type { UTCTimestamp } from "lightweight-charts";
import {
  cvdDeltaHistogramData,
  NEGATIVE_FLOW_COLOR,
  NEGATIVE_VOLUME_COLOR,
  POSITIVE_FLOW_COLOR,
  POSITIVE_VOLUME_COLOR,
  volumeHistogramData,
} from "./chartSemantics";
import { splitFlowSegments, type FlowHistoryPoint } from "./flowHistory";

const time = (value: number) => value as UTCTimestamp;

describe("production chart semantics", () => {
  it("uses actual traded volume for height and candle direction only for Volume color", () => {
    const result = volumeHistogramData([
      { time: time(0), open: 100, high: 102, low: 99, close: 101, volume: 17 },
      { time: time(60), open: 101, high: 102, low: 98, close: 99, volume: 31 },
    ]);
    expect(result.map(item => item.value)).toEqual([17, 31]);
    expect(result.map(item => item.color)).toEqual([POSITIVE_VOLUME_COLOR, NEGATIVE_VOLUME_COLOR]);
  });

  it("colors CVD strictly by signed delta, independently of the candle", () => {
    const candles = [
      { time: time(0), open: 100, high: 102, low: 99, close: 101, volume: 20 }, // price up
      { time: time(60), open: 101, high: 102, low: 98, close: 99, volume: 20 }, // price down
    ];
    const points: FlowHistoryPoint[] = [
      { time: 0, value: -7, delta: -7, status: "VALID" },
      { time: 60, value: 5, delta: 12, status: "VALID" },
    ];
    const result = cvdDeltaHistogramData(candles, points, 60);
    expect(result[0]).toMatchObject({ value: -7, color: NEGATIVE_FLOW_COLOR });
    expect(result[1]).toMatchObject({ value: 12, color: POSITIVE_FLOW_COLOR });
    expect((result[0] as { value: number }).value).toBeLessThan(0);
    expect((result[1] as { value: number }).value).toBeGreaterThan(0);
  });

  it("starts a new cumulative CVD line segment at UTC 00:00", () => {
    const beforeMidnight = 86_340;
    const points: FlowHistoryPoint[] = [
      { time: beforeMidnight, value: 9, delta: 9, status: "VALID" },
      { time: 86_400, value: -3, delta: -3, status: "VALID", segment_start: true },
    ];
    expect(splitFlowSegments(points)).toEqual([[points[0]], [points[1]]]);
    expect(points[1].value).toBe(-3);
  });
});
