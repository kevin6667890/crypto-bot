import { describe, expect, it } from "vitest";
import { PUBLIC_MARKET_INSTRUMENTS } from "./marketInstruments";

describe("public market chart instruments", () => {
  it("offers every persisted realtime aggregation instrument", () => {
    expect(PUBLIC_MARKET_INSTRUMENTS).toEqual(["BTC-USDT", "ETH-USDT", "SOL-USDT"]);
  });
});
