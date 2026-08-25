import { describe, expect, it } from "vitest";
import { marketProvenancePresentation } from "./marketProvenance";

describe("market provenance presentation", () => {
  it("keeps the initial canonical request neutral in English and Chinese", () => {
    expect(marketProvenancePresentation({ provenance: "DEMO_FALLBACK", asOf: "--", pendingCanonical: true })).toEqual({
      tone: "loading", label: "Loading canonical data", detail: "Fetching confirmed market facts",
    });
    expect(marketProvenancePresentation({ provenance: "BROWSER_FALLBACK", asOf: "--", pendingCanonical: true, language: "zh" })).toEqual({
      tone: "loading", label: "正在加载标准数据", detail: "正在获取已确认市场事实",
    });
  });

  it("keeps normal canonical provenance compact and quiet", () => {
    expect(marketProvenancePresentation({ provenance: "CANONICAL", asOf: "10:00" })).toEqual({
      tone: "canonical", label: "Canonical · Confirmed", detail: "As of 10:00",
    });
  });
  it("makes browser fallback explicit with source, freshness and reason", () => {
    const value = marketProvenancePresentation({ provenance: "BROWSER_FALLBACK", asOf: "10:01", fallbackReason: "Canonical backend unavailable" });
    expect(value.tone).toBe("degraded");
    expect(value.detail).toContain("Browser direct OKX · As of 10:01");
    expect(value.detail).toContain("Canonical backend unavailable");
  });
  it("does not mislabel demo data as canonical", () => {
    expect(marketProvenancePresentation({ provenance: "DEMO_FALLBACK", asOf: "10:02" }).label).toBe("Degraded data");
  });
  it("keeps the same canonical/degraded boundary in Chinese", () => {
    expect(marketProvenancePresentation({ provenance: "CANONICAL", asOf: "10:03", language: "zh" }).label).toBe("标准 · 已确认");
    expect(marketProvenancePresentation({ provenance: "BROWSER_FALLBACK", asOf: "10:03", language: "zh" }).label).toBe("降级数据");
  });
});
