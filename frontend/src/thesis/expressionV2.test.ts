import { describe, expect, it } from "vitest";
import { expressionDepth, expressionIsTrackable, expressionIsValid, expressionLabel, expressionLeaves, featureAvailable, friendlyReason, friendlyStatus } from "./expressionV2";
import type { ThesisCapabilities, ThesisExpressionV2 } from "./types";

const capabilities: ThesisCapabilities = {
  version: "thesis-capabilities-v2", thesis_spec_version: "thesis-spec-v2", feature_registry_version: "v2",
  instruments: ["BTC"], timeframes: ["4H"], horizons: ["12H", "24H"], unsupported_concepts: [],
  features: [{ code: "RSI", label: { en: "RSI", zh: "RSI" }, value_type: "number", unit: "index", operators: ["gte", "lte"],
    bounds: { minimum: 0, maximum: 100 }, requires_threshold: true, fixed_value: null, input_scale: "identity", source_group: "OHLCV",
    availability: "AVAILABLE", historical_availability: "AVAILABLE", current_availability: "AVAILABLE", supported_timeframes: ["4H"] },
  { code: "ROLLING_HIGH_BREAKOUT_CONFIRMED", label: { en: "Confirmed rolling-high breakout", zh: "滚动前高确认突破" }, value_type: "boolean", unit: "boolean", operators: ["eq"],
    bounds: { minimum: null, maximum: null }, requires_threshold: false, fixed_value: true, input_scale: "identity", source_group: "OHLCV",
    historical_availability: "AVAILABLE", current_availability: "AVAILABLE", supported_timeframes: ["4H"],
    parameters: { lookback_bars: { value_type: "integer", required: true, minimum: 5, maximum: 500, default: 20 } } }],
};

const expression: ThesisExpressionV2 = { node_type: "ALL", children: [
  { node_type: "CONDITION", feature: "ROLLING_HIGH_BREAKOUT_CONFIRMED", operator: "eq", value: true, parameters: { lookback_bars: 20 } },
  { node_type: "ANY", children: [
    { node_type: "CONDITION", feature: "RSI", operator: "gte", value: 70 },
    { node_type: "NOT", child: { node_type: "CONDITION", feature: "RSI", operator: "gte", value: 80 } },
  ] },
] };

describe("Thesis Expression V2 product semantics", () => {
  it("renders nested ALL, ANY and NOT without flattening OR into AND", () => {
    expect(expressionLabel(expression, capabilities, "en")).toContain("AND");
    expect(expressionLabel(expression, capabilities, "en")).toContain("OR");
    expect(expressionLabel(expression, capabilities, "en")).toContain("NOT");
  });

  it("enforces the reviewed depth, leaf and feature parameter contracts", () => {
    expect(expressionDepth(expression)).toBe(4);
    expect(expressionLeaves(expression)).toBe(3);
    expect(expressionIsValid(expression, capabilities, "4H")).toBe(false);
    const valid: ThesisExpressionV2 = { node_type: "ALL", children: [expression.children[0], { node_type: "CONDITION", feature: "RSI", operator: "lte", value: 80 }] };
    expect(expressionIsValid(valid, capabilities, "4H")).toBe(true);
    expect(expressionIsValid({ ...expression.children[0], parameters: { lookback_bars: 501 } } as ThesisExpressionV2, capabilities, "4H")).toBe(false);
    expect(expressionIsValid({ ...expression.children[0], parameters: { lookback_bars: 20, sql: 1 } } as ThesisExpressionV2, capabilities, "4H")).toBe(false);
  });

  it("keeps historical and current availability separate", () => {
    expect(featureAvailable({ ...capabilities.features[0], current_availability: "UNAVAILABLE" }, "historical")).toBe(true);
    expect(featureAvailable({ ...capabilities.features[0], current_availability: "UNAVAILABLE" }, "current")).toBe(false);
  });

  it("does not advertise tracking when any required leaf is historical-only", () => {
    expect(expressionIsTrackable(expression, capabilities)).toBe(true);
    const limited = { ...capabilities, features: capabilities.features.map((feature) =>
      feature.code === "ROLLING_HIGH_BREAKOUT_CONFIRMED"
        ? { ...feature, current_availability: "UNAVAILABLE" }
        : feature) } as ThesisCapabilities;
    expect(expressionIsTrackable(expression, limited)).toBe(false);
  });

  it("localizes unsupported causes and never exposes a machine code", () => {
    const text = friendlyReason("CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE", "DATASET_UNAVAILABLE", "zh");
    expect(text).toContain("不会用 K 线伪造 CVD");
    expect(text).not.toContain("CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE");
    expect(friendlyReason("UNKNOWN_CODE", "NEEDS_PARAMETER", "en")).toContain("parameter");
    expect(friendlyStatus("QUALIFIED", "zh")).toBe("数据合格");
    expect(friendlyReason("OVERLAPPING_FORWARD_WINDOW", undefined, "zh")).not.toContain("OVERLAPPING");
  });
});
