// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const advancedNav = readFileSync(new URL("./AdvancedSecondaryNav.tsx", import.meta.url), "utf8");
const catalog = readFileSync(new URL("./i18n.tsx", import.meta.url), "utf8");
const researchRoute = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");

describe("portfolio product information architecture", () => {
  it("keeps five primary destinations with bilingual product names and no V2 nav labels", () => {
    for (const key of ["workspace", "market", "research", "microstructure", "operations"]) expect(advancedNav).toContain(`"${key}"`);
    expect(app).not.toContain("nav.router");
    expect(catalog).not.toContain('"nav.router"');
    expect(catalog).toContain('"nav.workspace": "Workspace"');
    expect(catalog).toContain('"nav.workspace":"工作台"');
  });

  it("integrates market state under Market and decision trace under Research", () => {
    expect(app).toContain('data-route="market"');
    expect(app).toContain("<MarketStateResearch");
    expect(researchRoute).toContain('data-research-view="overview"');
    expect(researchRoute).toContain("<StrategyRouterResearch");
  });

  it("keeps old deep links reachable without exposing them in primary navigation", () => {
    expect(app).toContain('route.includes("market-state-v2")');
    expect(app).toContain('route.includes("strategy-router-v2")');
    expect(app).toContain('"/advanced#research/router"');
  });
});
