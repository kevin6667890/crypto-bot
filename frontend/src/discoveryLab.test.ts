// @ts-expect-error Source-contract test needs Node's fs types only in Vitest.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./DiscoveryLab.tsx", import.meta.url), "utf8");
const route = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");

describe("research UI simplification", () => {
  it("prioritizes automatic research evidence and explicit empty states", () => {
    for (const token of ["automatic-research-summary", "Validation / Registry", "Approved / Active Strategy", "Recent Research", "No candidate passed Development eligibility", "dataset fingerprint"]) expect(source).toContain(token);
  });

  it("keeps the manual workspace available but collapsed by default", () => {
    expect(route).toContain("advanced-manual-research");
    expect(route).toContain("<details");
    expect(route).toContain("<StrategyResearch />");
  });
});
