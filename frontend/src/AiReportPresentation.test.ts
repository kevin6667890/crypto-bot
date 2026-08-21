import { describe, expect, it } from "vitest";
import { compactAiSummary, coverageMatrixRows, isPresent, localizeWorkspaceNarrative, presentAiLevels, workspaceScenarioLabel } from "./aiWorkspaceSemantics";
import { researchPresentationCopy, selectResearchReport } from "./aiResearchPresentation";
import { workspaceAiPrimaryState } from "./aiWorkspaceState";
import { crossTimeframeNarrative, frameNarrative, intelligenceCenter, localizeAiRule } from "./aiIntelligencePresentation";
// @ts-expect-error Node source inspection is a product-layout regression test.
import { readFileSync } from "node:fs";

describe("Workspace AI presentation", () => {
  it("makes a latest audit failure primary over an old valid report", () => {
    expect(workspaceAiPrimaryState({ display_eligible: true, status: "STALE_AUDITED_REPORT", latest_generated: { eligibility: "AUDIT_FAILED" } })).toBe("LATEST_FAILED");
    expect(workspaceAiPrimaryState({ display_eligible: true, status: "CURRENT_AUDITED_REPORT" })).toBe("CURRENT_VALID");
    expect(workspaceAiPrimaryState({ display_eligible: true, status: "STALE_AUDITED_REPORT" })).toBe("STALE_VALID");
  });
  it.each([null, undefined, "", "—", "unknown", "not applicable", "N/A"])("filters absent value %s", value => {
    expect(isPresent(value)).toBe(false);
  });

  it("removes repeated empty level cards generically", () => {
    const levels = Array.from({ length: 3 }, (_, index) => ({
      level_id: `level-${index}`, asserted_role: "RESISTANCE", primary_timeframe: "MULTI",
    }));
    expect(presentAiLevels(levels)).toEqual([]);
    expect(presentAiLevels([...levels, { level_id: "real", representative_price: 1888 }])).toEqual([
      { level_id: "real", representative_price: 1888 },
    ]);
  });

  it("localizes Workspace scenario enums without exposing internal codes", () => {
    expect(workspaceScenarioLabel("BEARISH_CONTINUATION")).toBe("偏空延续");
    expect(workspaceScenarioLabel("BULLISH_CONTINUATION")).toBe("偏多延续");
    expect(workspaceScenarioLabel("RANGE")).toBe("区间震荡");
    expect(workspaceScenarioLabel("WAIT")).toBe("混合观察");
    expect(workspaceScenarioLabel("MIXED")).toBe("混合观察");
    expect(workspaceScenarioLabel("BEARISH_CONTINUATION", "en")).toBe("Bearish continuation");
    expect(workspaceScenarioLabel("RANGE", "en")).toBe("Range");
    expect(workspaceScenarioLabel("WAIT", "en")).toBe("Mixed watch");
  });

  it("removes raw internal enums from Workspace narrative text", () => {
    expect(localizeWorkspaceNarrative("阶段 BEARISH_CONTINUATION", "zh")).toBe("阶段 偏空延续");
    expect(localizeWorkspaceNarrative("State BEARISH_CONTINUATION", "en")).toBe("State Bearish continuation");
    expect(localizeWorkspaceNarrative("CUSTOM_INTERNAL_STATE", "en")).not.toMatch(/[A-Z]+_[A-Z_]+/);
  });

  it("keeps only a compact two-sentence homepage summary", () => {
    const value = "第一句是核心结论。第二句说明最重要的确认条件。第三句属于完整报告，不应进入 Workspace。";
    expect(compactAiSummary(value)).toBe("第一句是核心结论。第二句说明最重要的确认条件。…");
    expect(compactAiSummary(value)).not.toContain("第三句");
  });

  it("renders the four-dimension coverage matrix without a global PARTIAL badge", () => {
    const rows = coverageMatrixRows({ core_quality: "COMPLETE", flow_quality: "FLOW_PARTIAL_USABLE", long_term_quality: "PARTIAL", macro_quality: "NOT_INCLUDED" }, "en");
    expect(rows.map(item => item.label)).toEqual(["Core market data", "Order flow", "Long-term structure", "Macro context"]);
    expect(rows.map(item => item.text)).toEqual(["Complete", "Partial", "Limited", "Not included"]);
    expect(JSON.stringify(rows)).not.toContain("Data quality: Partial");
  });
});

describe("Research report selection and localization", () => {
  const current = { report_id: "eth-current", instrument: "ETH-USDT-SWAP", display_eligible: true, status: "CURRENT_AUDITED_REPORT" } as any;
  const stale = { report_id: "eth-stale", instrument: "ETH-USDT-SWAP", display_eligible: true, status: "STALE_AUDITED_REPORT" } as any;
  const failed = { report_id: "eth-failed", instrument: "ETH-USDT-SWAP", display_eligible: false, status: "AUDIT_FAILED" } as any;
  const btc = { report_id: "btc-current", instrument: "BTC-USDT-SWAP", display_eligible: true, status: "CURRENT_AUDITED_REPORT" } as any;
  it("gives an eligible direct report priority over latest selection", () => {
    expect(selectResearchReport([current, stale], "ETH-USDT-SWAP", "eth-stale")?.report_id).toBe("eth-stale");
  });
  it("uses the current eligible report and isolates instruments", () => {
    expect(selectResearchReport([stale, btc, current], "ETH-USDT-SWAP", "")?.report_id).toBe("eth-current");
    expect(selectResearchReport([current, btc], "BTC-USDT-SWAP", "")?.report_id).toBe("btc-current");
  });
  it("does not select an audit-failed report body", () => {
    expect(selectResearchReport([failed], "ETH-USDT-SWAP", "eth-failed")).toBeNull();
  });
  it("provides complete Chinese and English Research chrome", () => {
    expect(researchPresentationCopy.zh.latest).toBe("\u6700\u65b0\u5206\u6790");
    expect(researchPresentationCopy.zh.history).toBe("\u5386\u53f2\u62a5\u544a");
    expect(researchPresentationCopy.en.latest).toBe("Latest Analysis");
    expect(researchPresentationCopy.en.history).toBe("History");
  });
});

describe("AI product presentation closure", () => {
  const source = readFileSync(new URL("./AiReportPresentation.tsx", import.meta.url), "utf8");
  it("keeps the homepage compressed and sends detail into the Research deep centre", () => {
    expect(source).toContain('data-testid="workspace-ai-conditions"');
    expect(source).toContain('data-testid="research-ai-deep-center"');
    expect(source).toContain('data-testid="research-ai-timeframes"');
    expect(source).toContain('data-testid="research-ai-price-map"');
    expect(source).toContain('data-testid="research-ai-scenarios"');
    expect(source).toContain('data-testid="research-ai-synthesis"');
    expect(source).toContain('data-testid="research-ai-timeframe-narratives"');
  });

  it("builds explainable five-timeframe research prose without raw rule fragments", () => {
    const center = intelligenceCenter({
      audit: { overall_score: 100 },
      intelligence: {
        alignment: "CONFLICTED",
        conflicts: ["SETUP_COOLING_WHILE_HIGHER_TIMEFRAMES_EXTENDED"],
        dominant_context: "HIGHER_TIMEFRAME_EXTENSION",
        tactical: { trigger: "confirmed close above the nearest active resistance or impulse extreme", invalidation: "two confirmed 15m closes violate the referenced boundary" },
        timeframes: {
          "15m": { role: "TACTICAL", state: "IMPULSE_UP", local_low: 2300, local_high: 2400, ma_distances_pct: { ema20: 1, ma60: 2 }, momentum: { state: "MOMENTUM_REACCELERATING" } },
          "1H": { role: "SETUP_CONTEXT", state: "TREND_CONTINUATION", momentum: { state: "MOMENTUM_COOLING" } },
          "4H": { role: "PRIMARY_ENVIRONMENT", state: "HIGH_LEVEL_COMPRESSION", extension_state: "HIGHLY_EXTENDED" },
          "1D": { role: "MEDIUM_TERM_DIRECTION", state: "TREND_CONTINUATION", extension_state: "HIGHLY_EXTENDED" },
          "1W": { role: "LONG_TERM_STRUCTURE", state: "DEEP_PULLBACK", ma_distances_pct: { ma60: 3, ma200: 5 } },
        },
      },
    }, [], "zh");
    expect(center.frames).toHaveLength(5);
    expect(center.frames.find(item => item.timeframe === "1W")?.state).toBe("长期修复");
    expect(frameNarrative(center.frames[0], "zh")).toContain("局部支撑参考 2300");
    expect(crossTimeframeNarrative(center, "zh")).toContain("核心矛盾");
    expect(localizeAiRule("two confirmed 15m closes violate the referenced boundary", "zh")).toContain("连续两根");
  });
});
