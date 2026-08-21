// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./MarketStateResearch.tsx", import.meta.url), "utf8");

describe("market state research page", () => {
  it("is explicitly state recognition and contains no recommendation fields", () => {
    expect(source).toContain('t("state.disclaimer")');
    for (const forbidden of ["stop_loss", "take_profit", "win_rate", "profit_probability", "position_size"]) {
      expect(source.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("shows the required research evidence and quality surfaces", () => {
    for (const label of ["primary_state_code", "evidence_strength", "timeframes", "cross_timeframe", "level_interactions", "overlays", "transitions", "limitations", "quality"]) {
      expect(source).toContain(label);
    }
  });

  it("presents degraded data as an explained product state", () => {
    for (const key of ["state.waitingData", "state.unavailableFrame", "state.staleObservation", "state.partialCoverage"]) {
      expect(source).toContain(key);
    }
    expect(source).toContain("state-diagnostics");
    expect(source).toContain("reason_codes");
    expect(source).toContain("title={`${technical}");
  });

  it("localizes canonical Market status enums in Chinese rather than exposing raw English", () => {
    for (const value of ["BREAKOUT_DEVELOPING", "CONFLICTED", "TREND_UP", "TREND_DOWN", "AVAILABLE", "PARTIAL", "STALE", "MISSING"]) {
      expect(source).toContain(value);
    }
    expect(source).toContain('if (zh) return "未分类状态"');
  });
});
