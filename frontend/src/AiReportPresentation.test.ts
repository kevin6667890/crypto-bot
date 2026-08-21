import { describe, expect, it } from "vitest";
import { compactAiSummary, coverageMatrixRows, isPresent, localizeWorkspaceNarrative, presentAiLevels, workspaceScenarioLabel } from "./aiWorkspaceSemantics";
import { researchPresentationCopy, selectResearchReport } from "./aiResearchPresentation";
import { workspaceAiPrimaryState } from "./aiWorkspaceState";

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
