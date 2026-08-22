// @ts-expect-error Source-contract test needs Node's fs types only in Vitest.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./DiscoveryLab.tsx", import.meta.url), "utf8");
const route = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");

describe("research UI simplification", () => {
  it("prioritizes automatic research evidence and explicit empty states", () => {
    for (const token of ["automatic-research-summary", "Validation / Registry", "Approved / Active Strategy", "Recent Research", "No candidate passed Development eligibility", "dataset fingerprint"]) expect(source).toContain(token);
  });

  it("renders completed-cycle diagnostics without a hidden entry point", () => {
    for (const token of ["Development Rejection Analysis", "Candidate Diagnostics", "diagnostics_available", "eligibility:\"REJECTED\"", "Observed:", "Program AST / parameters", "Factor Program", "Template Discovery"]) expect(source).toContain(token);
  });

  it("keeps empty and historical diagnostics states explicit", () => {
    expect(source).toContain("Detailed rejection diagnostics unavailable for this historical cycle.");
    expect(source).toContain("setDiagnostics(null)");
  });

  it("keeps the manual workspace available but collapsed by default", () => {
    expect(route).toContain("advanced-manual-research");
    expect(route).toContain("<details");
    expect(route).toContain("<StrategyResearch />");
  });
});
