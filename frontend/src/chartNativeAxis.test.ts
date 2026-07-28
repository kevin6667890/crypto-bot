// @ts-expect-error Vitest runs this contract test in Node; the app bundle does not need Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const charts = readFileSync(new URL("./charts.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

describe("market chart native price-axis UI", () => {
  it("does not render custom floating price-label DOM", () => {
    expect(charts).not.toContain("price-label-layer");
    expect(charts).not.toContain("multi-price-label");
    expect(charts).not.toContain("priceToCoordinate");
    expect(styles).not.toContain(".price-label-layer");
    expect(styles).not.toContain(".multi-price-label");
  });

  it("does not render return-to-latest or new-data overlays", () => {
    expect(charts).not.toContain("回到最新");
    expect(charts).not.toContain("有新数据");
    expect(charts).not.toContain("chart-follow-control");
    expect(styles).not.toContain(".chart-follow-control");
  });

  it("creates official axis-only PriceLines and keeps the right scale wide enough", () => {
    expect(charts).toContain("createPriceLine");
    expect(charts).toContain("lineVisible: false");
    expect(charts).toContain("axisLabelVisible: false");
    expect(charts).toContain("minimumWidth: PRICE_AXIS_MINIMUM_WIDTH");
    for (const title of ["K线", "EMA20", "MA60", "MA200"]) expect(charts).toContain(`name: "${title}"`);
  });
});
