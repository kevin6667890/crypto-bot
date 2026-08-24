import { describe, expect, it } from "vitest";
// @ts-expect-error Vitest contract tests run in Node; the browser bundle has no Node types.
import { readFileSync } from "node:fs";
import { canRunDefinition, executableSpec, formatFraction } from "./state";
import type { EditableDefinition } from "./state";
import type { ThesisCapabilities } from "./types";

const capabilities: ThesisCapabilities = {
  version: "thesis-capabilities-v1", thesis_spec_version: "thesis-spec-v1",
  feature_registry_version: "registry", instruments: ["BTC", "ETH", "SOL"], timeframes: ["1H", "4H"],
  horizons: ["4H", "12H", "24H"], unsupported_concepts: ["CONFIRMED_STRUCTURE_BREAKOUT"],
  features: [
    { code: "VOLUME_RATIO", label: { en: "Volume ratio", zh: "成交量比率" }, value_type: "number", unit: "ratio",
      operators: ["gt", "gte", "lt", "lte"], bounds: { minimum: 0, maximum: null }, requires_threshold: true,
      fixed_value: null, input_scale: "identity", source_group: "OHLCV", availability: "AVAILABLE", supported_timeframes: ["1H", "4H"] },
    { code: "PRICE_ABOVE_MA200", label: { en: "Price above MA200", zh: "价格高于 MA200" }, value_type: "boolean", unit: "boolean",
      operators: ["eq"], bounds: { minimum: null, maximum: null }, requires_threshold: false,
      fixed_value: true, input_scale: "identity", source_group: "OHLCV", availability: "AVAILABLE", supported_timeframes: ["1H", "4H"] },
    { code: "OI_CHANGE", label: { en: "OI", zh: "持仓量" }, value_type: "number", unit: "percent",
      operators: ["gt"], bounds: { minimum: null, maximum: null }, requires_threshold: true,
      fixed_value: null, input_scale: "percentage_points", source_group: "OI", availability: "NOT_CURRENTLY_TESTABLE", supported_timeframes: ["1H", "4H"] },
  ],
};

const valid: EditableDefinition = { instrument: "BTC", timeframe: "4H", optional: [], horizons: ["4H", "12H", "24H"],
  required: [{ feature: "VOLUME_RATIO", operator: "gte", value: 1.2 }, { feature: "PRICE_ABOVE_MA200", operator: "eq", value: true }] };

describe("test-an-idea deterministic UI state", () => {
  it("keeps Run disabled for an empty or incomplete interpretation", () => {
    expect(canRunDefinition({ instrument: "", timeframe: "", horizons: [], required: [], optional: [] }, capabilities)).toBe(false);
    expect(canRunDefinition({ ...valid, required: [{ feature: "VOLUME_RATIO", operator: "gte", value: null }] }, capabilities)).toBe(false);
  });

  it("accepts an explicit edited threshold and emits the exact deterministic spec", () => {
    const edited = { ...valid, required: [{ feature: "VOLUME_RATIO", operator: "gt", value: 1.37 }] };
    expect(canRunDefinition(edited, capabilities)).toBe(true);
    expect(executableSpec(edited, capabilities, 1_700_000_000)).toMatchObject({ instrument: "BTC", timeframe: "4H",
      requested_as_of: 1_700_000_000, required_conditions: [{ feature: "VOLUME_RATIO", operator: "gt", value: 1.37 }] });
  });

  it("preserves explicit optional conditions instead of weakening the parsed idea", () => {
    const withOptional = { ...valid, optional: [{ feature: "PRICE_ABOVE_MA200", operator: "eq", value: true }] };
    expect(executableSpec(withOptional, capabilities, 1_700_000_000).optional_conditions).toEqual([
      { feature: "PRICE_ABOVE_MA200", operator: "eq", value: true },
    ]);
  });

  it("rejects unavailable features, invalid operators, bounds and empty horizons", () => {
    expect(canRunDefinition({ ...valid, required: [{ feature: "OI_CHANGE", operator: "gt", value: 5 }] }, capabilities)).toBe(false);
    expect(canRunDefinition({ ...valid, required: [{ feature: "VOLUME_RATIO", operator: "eq", value: 1.2 }] }, capabilities)).toBe(false);
    expect(canRunDefinition({ ...valid, required: [{ feature: "VOLUME_RATIO", operator: "gte", value: -1 }] }, capabilities)).toBe(false);
    expect(canRunDefinition({ ...valid, horizons: [] }, capabilities)).toBe(false);
  });

  it("formats API fractions for display without deriving historical statistics", () => {
    expect(formatFraction(0.4217, "en")).toBe("42.17%");
    expect(formatFraction(null, "zh")).toBe("—");
  });

  it("keeps parse and test in explicit click handlers and never an effect", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    expect(source).toContain("async function interpret()");
    expect(source).toContain("async function runTest()");
    expect(source).not.toMatch(/useEffect\([\s\S]{0,300}parseThesis\(/);
    expect(source).not.toMatch(/useEffect\([\s\S]{0,300}testThesis\(/);
  });

  it("renders all result fields directly from the API contract and exposes failure semantics", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    for (const field of ["independent_event_count", "historical_positive_rate", "median_return_fraction",
      "p25_return_fraction", "p75_return_fraction", "median_mfe_fraction", "median_mae_fraction",
      "sample_quality", "censored_n", "limitations", "coverage", "event_records"]) expect(source).toContain(field);
    expect(source).toContain('data-state="unsupported"');
    expect(readFileSync(new URL("./i18n.ts", import.meta.url), "utf8")).toContain("No historical result has been produced.");
  });

  it("does not implement quantiles, sample-quality thresholds, or positive-rate calculation", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    for (const forbidden of ["quantile(", "positive_n /", "eligible_n <", ".sort((a, b) => a - b)"]) expect(source).not.toContain(forbidden);
  });

  it("provides bilingual core labels and a manual AI-unavailable path", () => {
    const copy = readFileSync(new URL("./i18n.ts", import.meta.url), "utf8");
    for (const value of ["Test an idea", "测试一个想法", "Historical evidence", "历史证据", "AI interpretation is unavailable", "AI 解析暂不可用"]) expect(copy).toContain(value);
    expect(readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8")).toContain("startManual");
  });

  it("renders raw and evaluable lineage, limited-span warning and audit identities", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    for (const field of ["historical_data.raw_range", "historical_data.evaluable_range", "breadth_qualification",
      "result_hash.slice", "definition_hash.slice", "feature_versions", "independence_policy.version"]) expect(source).toContain(field);
    const copy = readFileSync(new URL("./i18n.ts", import.meta.url), "utf8");
    for (const label of ["Limited historical span", "历史跨度有限", "Evidence details", "证据详情"]) expect(copy).toContain(label);
  });

  it("loads only the selected event and cancels an older event-context request", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    expect(source).toContain("async function viewEvent(event");
    expect(source).toContain("eventContextController.current?.abort()");
    expect(source).toContain("eventContextSequence.current");
    expect(source).not.toMatch(/useEffect\([\s\S]{0,300}fetchThesisEventContext\(/);
  });

  it("uses backend marker timestamps and never derives horizon timestamps or returns", () => {
    const chart = readFileSync(new URL("./EvidenceCandlestickChart.tsx", import.meta.url), "utf8");
    expect(chart).toContain("context.event.timestamp");
    expect(chart).toContain("horizon.target_timestamp");
    expect(chart).toContain("candle.close_timestamp");
    expect(chart).toContain("createSeriesMarkers");
    expect(chart).not.toMatch(/event\.timestamp\s*\+/);
    const page = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    expect(page).not.toContain("outcome_close / eventContext.event.reference_close");
  });

  it("requests explanation after a result without rerunning the historical test", () => {
    const source = readFileSync(new URL("./TestAnIdeaPage.tsx", import.meta.url), "utf8");
    expect(source).toContain("explainThesis(result, language");
    expect(source).toContain("explanation.status === \"FALLBACK\"");
    expect(source).not.toMatch(/useEffect\([\s\S]{0,400}testThesis\(/);
  });
});
