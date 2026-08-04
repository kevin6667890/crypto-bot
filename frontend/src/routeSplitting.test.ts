// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const main = readFileSync(new URL("./main.tsx", import.meta.url), "utf8");
const vite = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");

describe("route splitting", () => {
  it("lazy loads the initial market application with a loading boundary", () => {
    expect(main).toContain('lazy(() => import("./App"))');
    expect(main).toContain("LOADING · Market Analysis");
  });

  it("creates independent lazy chunks for research, microstructure and operations", () => {
    expect(app).toContain('lazy(() => import("./routes/StrategyResearchRoute"))');
    expect(app).toContain('lazy(() => import("./MicrostructureResearch"))');
    expect(app).toContain('lazy(() => import("./Operations"))');
    expect(app).toContain('lazy(() => import("./MarketStateResearch"))');
  });

  it("retains visited route instances and isolates route errors", () => {
    expect(app).toContain("visitedPages.has");
    expect(app).toContain("RouteErrorBoundary");
    expect(app).toContain("UNAVAILABLE ·");
  });

  it("shares chart and React vendors without duplicate route bundles", () => {
    expect(vite).toContain('return "charts-vendor"');
    expect(vite).toContain('return "react-vendor"');
    expect(vite).toContain("chunkSizeWarningLimit: 300");
  });
});
