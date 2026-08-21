import { describe, expect, it } from "vitest";
import { intelligenceCenter, levelLabel, visibleFrame } from "./aiIntelligencePresentation";

describe("deterministic intelligence presentation", () => {
  const report = {
    audit: { overall_score: 100 },
    levels: [{ level_id: "near", representative_price: 2500, asserted_role: "TACTICAL_RESISTANCE" }],
    long_term_levels: [{ level_id: "far", representative_price: 3400, asserted_role: "LONG_TERM_REFERENCE", reference_tier: "LONG_TERM_REFERENCE" }],
    scenarios: [{ scenario_id: "bull", scenario_type: "BULLISH_CONTINUATION", trigger_text: "Close above 2500" }],
    evidence_quality: { flow_quality: "FLOW_UNAVAILABLE" },
    intelligence: {
      alignment: "TACTICAL_BULLISH",
      tactical: { state: "HIGH_LEVEL_CONSOLIDATION", trigger: "Close above 2500", invalidation: "Close below 2450" },
      flow_oi: { flow_quality: "FLOW_UNAVAILABLE", oi_state: "PRICE_UP_OI_DOWN" },
      timeframes: {
        "15m": { state: "HIGH_LEVEL_CONSOLIDATION", momentum: { state: "MOMENTUM_COOLING" } },
        "1H": { state: "SHALLOW_PULLBACK", extension_state: "NORMAL" },
        "4H": { state: "TREND_CONTINUATION", extension_state: "EXTENDED" },
        "1D": { state: "TREND_CONTINUATION", extension_state: "HIGHLY_EXTENDED" },
        "1W": { state: "TREND_CONTINUATION" },
      },
    },
  };

  it("renders all five registered timeframe facts without inventing a flow direction", () => {
    const center = intelligenceCenter(report, [], "en");
    expect(center.frames.filter(visibleFrame)).toHaveLength(5);
    expect(center.frames.find(frame => frame.timeframe === "15m")?.state).toBe("High-level consolidation");
    expect(center.flow).toBe("Flow unavailable");
    expect(center.priceOi).toBeUndefined();
  });

  it("keeps distant references out of the current price map", () => {
    const center = intelligenceCenter(report, [], "en");
    expect(center.priceMap.map(level => level.representative_price)).toEqual([2500]);
    expect(center.longTerm.map(level => level.representative_price)).toEqual([3400]);
    expect(levelLabel(center.priceMap[0], "en")).toBe("Tactical resistance");
  });

  it("uses report-section fallback for frozen historical reports", () => {
    const center = intelligenceCenter({ evidence_quality: { flow_quality: "FLOW_UNAVAILABLE" } }, [
      { section_id: "TF_15M", body: "Tactical observation" },
      { section_id: "TF_1H", body: "Setup observation" },
      { section_id: "TF_4H", body: "Primary observation" },
      { section_id: "TF_1D", body: "Daily observation" },
      { section_id: "TF_1W", body: "Weekly observation" },
    ], "en");
    expect(center.frames.filter(visibleFrame)).toHaveLength(5);
    expect(center.flow).toBe("Flow unavailable");
  });
});
