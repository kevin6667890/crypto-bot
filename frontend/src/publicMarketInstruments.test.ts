// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PUBLIC_MARKET_INSTRUMENTS } from "./marketInstruments";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

describe("public market chart instruments", () => {
  it("offers every persisted realtime aggregation instrument", () => {
    expect(PUBLIC_MARKET_INSTRUMENTS).toEqual(["BTC-USDT", "ETH-USDT", "SOL-USDT"]);
  });

  it("exposes SOL in the global instrument selector", async () => {
    expect(app).toContain("<option>SOL-USDT</option>");
  });
});
