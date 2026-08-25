// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const component = readFileSync(new URL("./AiReportPresentation.tsx", import.meta.url), "utf8");
const data = readFileSync(new URL("./data.ts", import.meta.url), "utf8");
const research = readFileSync(new URL("./routes/StrategyResearchRoute.tsx", import.meta.url), "utf8");

describe("AI6B production presentation wiring", () => {
  it("does not render the legacy paper ai_brief", () => {
    expect(app).not.toContain("paper?.ai_brief");
    expect(app).toContain("WorkspaceAiBrief");
  });
  it("selects only the requested instrument and QUICK workspace mode", () => {
    expect(data).toContain("assertAiInstrument(instrument, value.instrument)");
    expect(component).toContain("fetchAuditedAiBrief(instrument)");
  });
  it("never renders a failed audit body", () => {
    expect(data).toContain("if (!value.summary.display_eligible) value.report = null");
    expect(component).toContain("detail?.report");
  });
  it("marks stale reports and hides their market conclusion", () => {
    expect(component).toContain("AI 分析已过期");
    expect(component).toContain("历史内容不会作为当前市场结论展示");
  });
  it("shows no-current state instead of a legacy fallback", () => {
    expect(component).toContain("暂无当前有效 AI 分析");
    expect(component).not.toContain("ai_brief");
  });
  it("renders generated, facts-as-of, and next-evaluation timestamps separately", () => {
    expect(component).toContain("<dt>{copy.updated}</dt><dd>{when(brief.generated_at)}</dd>");
    expect(component).toContain("<dt>{copy.dataTime}</dt><dd>{when(brief.market_snapshot_at)}</dd>");
    expect(component).toContain("<dt>{copy.next}</dt><dd>{when(brief.scheduler?.next_tick)}</dd>");
  });
  it("keeps the previous audited report visible after a no-material-change evaluation", () => {
    expect(component).toContain("SKIPPED_NO_MATERIAL_CHANGE");
    expect(component).toContain("No material facts changed at the latest evaluation");
    expect(component).toContain("displayAuditedReport");
  });
  it("adds audited AI6B history to Research", () => {
    expect(research).toContain("AiReportResearch instrument={instrument}");
    expect(component).toContain("copy.modes");
  });
  it("keeps historical reports visibly historical", () => {
    expect(component).toContain("copy.eyebrow");
    expect(component).toContain("copy.auditHidden");
  });
});
