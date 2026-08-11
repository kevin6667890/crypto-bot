// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const source = readFileSync(new URL("./StrategyRouterResearch.tsx", import.meta.url), "utf8");
const route = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");
describe("decision trace research view", () => {
  it("is lazy-loaded inside the research workflow and explicitly research-only", () => { expect(route).toContain('lazy(() => import("../StrategyRouterResearch"))'); expect(source).toContain('t("router.disclaimer")'); });
  it("shows route evidence and identity", () => { for (const token of ["primary_route", "alternatives", "no_trade", "score_breakdown", "supporting_evidence", "conflicting_evidence", "blockers", "next_confirmation", "geometry", "identity", "source_timestamps"]) expect(source).toContain(token); });
  it("contains no execution controls", () => { expect(source).not.toContain("<button"); for (const token of ["position_size", "create_order", "take_profit"]) expect(source.toLowerCase()).not.toContain(token); });
});
