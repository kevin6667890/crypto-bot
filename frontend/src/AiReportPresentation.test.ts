import { describe, expect, it } from "vitest";
import { compactAiSummary, isPresent, presentAiLevels, workspaceScenarioLabel } from "./aiWorkspaceSemantics";

describe("Workspace AI presentation", () => {
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
  });

  it("keeps only a compact two-sentence homepage summary", () => {
    const value = "第一句是核心结论。第二句说明最重要的确认条件。第三句属于完整报告，不应进入 Workspace。";
    expect(compactAiSummary(value)).toBe("第一句是核心结论。第二句说明最重要的确认条件。…");
    expect(compactAiSummary(value)).not.toContain("第三句");
  });
});
