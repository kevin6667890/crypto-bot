// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./ProductApp.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("./product/ProductShell.tsx", import.meta.url), "utf8");
const productI18n = readFileSync(new URL("./product/i18n.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("./PredictionMarkets.tsx", import.meta.url), "utf8");
const productApp = readFileSync(new URL("./ProductApp.tsx", import.meta.url), "utf8");

describe("prediction markets research vertical", () => {
  it("provides the four read-only research destinations and product navigation", () => {
    expect(app).toContain('kind: "prediction-markets"');
    expect(shell).toContain('href="/prediction-markets"');
    expect(productI18n).toContain('predictionMarkets: "Prediction Markets"');
    for (const route of ["/overview", "/markets", "/forecasts", "/scoreboard"]) expect(page).toContain(route);
    expect(page).toContain('className="pm-nav"');
    expect(page).toContain('aria-current={view === destination ? "page" : undefined}');
  });

  it("keeps unresolved performance honest and handles unavailable API data", () => {
    expect(page).toContain("Awaiting resolutions");
    expect(page).toContain("No forecasts have resolved yet.");
    expect(page).toContain("Prediction Markets API unavailable");
    expect(page).not.toContain("AI beats market");
    expect(page).toContain("Performance is unavailable until forecasts resolve.");
  });

  it("uses paged projections rather than shipping raw collection payloads", () => {
    expect(page).toContain("/markets?limit=50");
    expect(page).toContain("/forecasts?limit=50");
    expect(page).not.toContain("raw_payload");
  });

  it("loads immutable detail projections and avoids stale search responses", () => {
    expect(page).toContain("/markets/${encodeURIComponent(selected)}");
    expect(page).toContain("/forecasts/${encodeURIComponent(selected)}");
    expect(page).toContain("AbortController");
    expect(page).toContain("useDebounced(query)");
    expect(page).toContain('asRow(val(item, "frozen_market_snapshot"))');
    expect(page).toContain('asRow(val(item, "audit"))');
    expect(page).toContain('val(item, "evidence") ?? val(embedded, "evidence")');
    expect(page).toContain('text(item, "question", "market_question")');
  });

  it("uses the same-origin production API by default and contains no provider secret", () => {
    expect(page).toContain('/api/polymarket');
    expect(page).not.toContain("localhost");
    expect(page).not.toContain("127.0.0.1");
    expect(page).not.toContain("DEEPSEEK_API_KEY");
    expect(productApp).toContain('FRONTEND_BUILD_ID = "thesis-capability-v2-production-rc-20260825"');
    expect(productApp).toContain("document.documentElement.dataset.frontendBuild = FRONTEND_BUILD_ID");
  });
});
