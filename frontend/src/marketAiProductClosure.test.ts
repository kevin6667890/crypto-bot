// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const ai = readFileSync(new URL("./AiReportPresentation.tsx", import.meta.url), "utf8");
const market = readFileSync(new URL("./MarketStateResearch.tsx", import.meta.url), "utf8");

describe("Market + AI product closure", () => {
  it("places chart and audited AI in the primary grid and names the rule score honestly", () => {
    expect(app).toContain('className="workspace-primary"');
    expect(app.indexOf('<section className="chart-workspace">')).toBeLessThan(app.indexOf("<WorkspaceAiBrief"));
    expect(app).toContain("规则条件匹配度");
    expect(app).toContain("不代表成功率或收益概率");
  });
  it("removes the low-value workspace intro component", () => {
    expect(app).not.toContain('aria-labelledby="workspace-title"');
    expect(app).not.toContain('t("workspace.description")');
  });
  it("prioritizes risk block before score", () => {
    expect(app.indexOf("risk-block-banner")).toBeLessThan(app.indexOf("score-summary"));
    expect(app).toContain("风险阻断");
  });
  it("uses structured audited presentation without raw warning punctuation", () => {
    expect(ai).toContain("timeframe_quality");
    expect(ai).not.toContain('data_warnings.join');
    expect(ai).not.toContain("。；");
  });
  it("separates structural regimes from Workspace rule trend signals", () => {
    expect(market).toContain("结构状态与数据可用性分别呈现");
    expect(market).toContain("规则趋势信号请在 Workspace 查看");
  });
});
