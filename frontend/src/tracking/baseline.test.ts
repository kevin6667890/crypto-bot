import { describe, expect, it } from "vitest";
import { baselineEventLabel, baselineSampleLabel } from "./baseline";

describe("tracking historical baseline presentation", () => {
  it("renders a V1 independent-event count without fabrication", () => {
    expect(baselineEventLabel({ independent_event_count: 12 }, false)).toBe("12 independent events");
  });
  it("renders a V2 baseline with no V1-only count as an immutable baseline", () => {
    expect(baselineEventLabel({}, false)).toBe("Saved immutable historical baseline");
    expect(baselineEventLabel({ independent_event_count: null }, true)).toBe("已保存的不可变历史基线");
  });
  it("omits an empty optional sample label", () => {
    expect(baselineSampleLabel({ sample_quality: null }, false)).toBeNull();
  });
});
