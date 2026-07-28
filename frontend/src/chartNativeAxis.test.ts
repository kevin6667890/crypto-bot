// @ts-expect-error Vitest runs this contract test in Node; the app bundle does not need Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const charts = readFileSync(new URL("./charts.tsx", import.meta.url), "utf8");
const primitive = readFileSync(new URL("./priceAxisLabelPrimitive.ts", import.meta.url), "utf8");
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

  it("attaches numeric-only price-axis primitives to price, CVD, and OI series", () => {
    expect(charts).toContain("PriceAxisLabelPrimitive");
    expect(charts).toContain("attachPrimitive");
    expect(charts).toContain("minimumWidth: PRICE_AXIS_MINIMUM_WIDTH");
    expect(primitive).toContain("priceFormatter().format(this.price)");
    expect(primitive).not.toContain("this.title");
    expect(charts).toContain("FLOW_SERIES_CONFIG");
    expect(charts).toContain("lastValueVisible: false");
  });
});
