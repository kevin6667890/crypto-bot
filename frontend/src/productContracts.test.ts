// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const terms = readFileSync(new URL("./ResearchTerminology.tsx", import.meta.url), "utf8");
const trends = readFileSync(new URL("./OperationsTrends.tsx", import.meta.url), "utf8");
const operations = readFileSync(new URL("./Operations.tsx", import.meta.url), "utf8");

describe("research and operations product contracts", () => {
  it("explains all research measures bilingually without trading advice", () => {
    for (const term of ["Natural coverage days", "Gap-adjusted usable days", "Maximum contiguous interval", "Label overlap", "Native independent events", "Non-overlapping labels", "Calibration / validation events", "Cross-horizon cumulative count"]) {
      expect(terms).toContain(term);
    }
    expect(terms).toContain("EXPLORATORY_ONLY");
    expect(terms).toContain("VALIDATION_READY");
    expect(terms).toContain("FORMAL_RESEARCH_READY");
    expect(terms).toContain("not trading advice");
  });

  it("never calls cumulative cross-horizon rows independent samples", () => {
    expect(terms).toContain("never described as independent samples");
    expect(terms).toContain("绝不表述为独立样本");
  });

  it("shows disabled history as not enabled rather than a fault", () => {
    expect(trends).toContain("尚未启用历史采集");
    expect(trends).toContain('data-state="NOT_ENABLED"');
  });

  it("uses only read endpoints and no strategy, order, or admin write API", () => {
    const combined = operations + trends;
    expect(combined).toContain("/api/operations/summary");
    expect(combined).toContain("/api/operations/trends");
    expect(combined).not.toMatch(/\/api\/admin|\/api\/orders|\/api\/strateg/);
    expect(combined).not.toMatch(/method:\s*["'](?:POST|PUT|PATCH|DELETE)/);
  });
});
