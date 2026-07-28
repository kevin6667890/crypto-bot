// @ts-expect-error Vitest runs this contract test in Node; the app bundle does not need Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const component = readFileSync(new URL("./MicrostructureResearch.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

describe("responsive microstructure information architecture", () => {
  it("groups collection health and feature availability by BTC, ETH, and SOL tabs", () => {
    for (const instrument of ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]) {
      expect(component).toContain(`"${instrument}"`);
    }
    expect(component).toContain('role="tablist"');
    expect(component).toContain("data.instruments?.[instrument]");
  });

  it("keeps complete source coverage and validation horizons collapsed by default", () => {
    expect(component).toContain('data-testid="all-sources-details"');
    expect(component).toContain('data-testid="funding-validation-details"');
    expect(component).toContain('data-testid="basis-validation-details"');
    expect(component).not.toMatch(/<details[^>]*\sopen(?:=|>)/);
  });

  it("distinguishes natural, gap-adjusted usable, and label-overlap days", () => {
    expect(component).toContain("row.source_days.toFixed(2)");
    expect(component).toContain("row.gap_adjusted_usable_days.toFixed(2)");
    expect(component).toContain("row.overlap_usable_days.toFixed(2)");
    expect(component).toContain("row.event_count.toLocaleString()");
  });

  it("renders one selected research chart behind four chart tabs", () => {
    expect(component).toContain('type ResearchChart = "funding" | "basis" | "cvd" | "oi"');
    expect(component).toContain("chartData[activeChart]");
    expect(component.match(/<SimpleLineChart/g)).toHaveLength(1);
  });

  it("prevents page-level horizontal scrolling while keeping wide tables internally scrollable", () => {
    expect(styles).toContain(".microstructure-workspace");
    expect(styles).toContain("overflow-x:clip");
    expect(styles).toContain(".micro-table-scroll");
    expect(styles).toContain("overflow-x:auto");
    expect(styles).toContain("@media(max-width:1440px)");
    expect(styles).toContain("@media(max-width:1000px)");
  });
});
