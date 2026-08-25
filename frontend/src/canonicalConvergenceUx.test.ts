// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const research = readFileSync(new URL("./DiscoveryLab.tsx", import.meta.url), "utf8");
const route = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");
const microstructure = readFileSync(new URL("./MicrostructureResearch.tsx", import.meta.url), "utf8");
const operations = readFileSync(new URL("./Operations.tsx", import.meta.url), "utf8");

describe("canonical convergence UX contracts", () => {
  it("preserves the five primary navigation destinations", () => {
    for (const page of ["workspace", "market", "research", "microstructure", "operations"]) {
      expect(app).toContain(`${page}: "/advanced#${page}"`);
    }
  });

  it("shows canonical provenance normally and browser data only as an explicit fallback", () => {
    expect(app).toContain("fetchEthSnapshot(instrument)");
    expect(app).toContain("fetchBrowserOkxSnapshot(instrument)");
    expect(app).toContain("marketProvenancePresentation");
    expect(app).toContain("const [pendingCanonical, setPendingCanonical] = useState(true)");
    expect(app).toContain("setPendingCanonical(false)");
    expect(app).toContain('data-market-provenance={pendingCanonical ? "CANONICAL_LOADING" : snapshot.provenance}');
    expect(app).toContain('current.provenance !== "BROWSER_FALLBACK" ? current');
    expect(app).toContain("RAW OBSERVATION · BROWSER DIRECT OKX");
    expect(app).not.toContain("Browser ${snapshot.source} observation — not canonical production truth");
    expect(app).toContain('const action = runtimeAnalysis?.action || "WAIT"');
    expect(app).toContain("ACTIVE NONE · WAIT");
    expect(app).not.toContain('runtimeAnalysis?.action || (signal.score >= 70 ? "WATCH" : "WAIT")');
  });

  it("labels product-specific flow evidence without silently substituting SPOT for SWAP", () => {
    expect(app).toContain("DERIVED EVIDENCE · SWAP MICROSTRUCTURE");
    expect(app).toContain("CVD is SWAP-only: no SPOT substitution");
    expect(app).toContain("exact trade-price profile");
    expect(app).toContain("OHLCV approximate profile");
  });

  it("keeps AI, automatic research, registry evidence, and advanced tools distinct", () => {
    expect(route).toContain("AI INTERPRETATION · AI DEEP CENTER");
    expect(route).toContain("Advanced manual research");
    expect(route).toContain("Advanced Strategy Router");
    for (const label of ["autoResearch.title", "autoResearch.latestCycle", "autoResearch.noApprovedCandidate", "local.recent"]) {
      expect(research).toContain(label);
    }
    expect(research).toContain("advanced-rejection-diagnostics");
    expect(research).toContain("diagnostics_available");
  });

  it("keeps microstructure and operations visibly advanced evidence surfaces", () => {
    expect(microstructure).toContain("DERIVED EVIDENCE · ADVANCED MICROSTRUCTURE");
    expect(operations).toContain("OPERATIONS · ADVANCED RUNTIME EVIDENCE");
  });
});
