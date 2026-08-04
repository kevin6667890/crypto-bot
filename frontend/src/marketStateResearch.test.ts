// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./MarketStateResearch.tsx", import.meta.url), "utf8");

describe("market state research page", () => {
  it("is explicitly state recognition and contains no recommendation fields", () => {
    expect(source).toContain("市场状态识别，不是交易信号。");
    for (const forbidden of ["stop_loss", "take_profit", "win_rate", "profit_probability", "position_size"]) {
      expect(source.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("shows the required research evidence and quality surfaces", () => {
    for (const label of ["primary_state_code", "evidence_strength", "timeframes", "cross_timeframe", "level_interactions", "overlays", "transitions", "limitations", "quality"]) {
      expect(source).toContain(label);
    }
  });
});
