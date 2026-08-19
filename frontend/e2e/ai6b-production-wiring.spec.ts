import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const stale = {
  instrument: "ETH-USDT-SWAP", mode: "QUICK", report_id: "report_eth", display_eligible: true,
  status: "STALE_AUDITED_REPORT", generated_at: "2026-08-18T06:47:53Z", market_snapshot_at: "2026-08-18T06:45:00Z",
  freshness: { status: "STALE", quality: "AVAILABLE" }, latest_generated: { report_id: "report_eth", eligibility: "AUDIT_PASSED_SHADOW_ONLY" },
  audit: { status: "PASSED", overall_score: 100, promotion_eligible: true }, provider: "deepseek", model: "deepseek-v4-flash",
  headline: "must not render while stale", executive_summary: "ETH-USDT 当前报 1877 legacy text", data_warnings: [],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "zh"));
  await page.route("**/api/**", (route) => route.abort());
  await page.route("**/api/ai-market-analysis/v1/workspace-brief/latest?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(stale) }));
  await page.route("**/api/ai-market-analysis/v1/research-reports?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ instrument: "ETH-USDT-SWAP", language: "zh-CN", items: [stale] }) }));
});

test("Workspace marks stale audited analysis and never renders its old conclusion", async ({ page }) => {
  await page.goto("/#workspace");
  const card = page.getByTestId("workspace-ai6b-brief");
  await expect(card).toContainText("AI 分析已过期");
  await expect(card).toContainText("最后有效分析");
  await expect(card).not.toContainText("ETH-USDT 当前报 1877");
});

test("Research reads the audited AI6B history source", async ({ page }) => {
  await page.goto("/#research");
  const history = page.getByTestId("research-ai6b-reports");
  await expect(history).toContainText("AI 深度中心");
  await expect(history).toContainText("QUICK");
  await expect(history).toContainText("Audit 100");
});

test("Workspace current audited Hero shows audit, freshness, partial coverage and Research link", async ({ page }) => {
  const current = { ...stale, status: "CURRENT_AUDITED_REPORT", freshness: { status: "CURRENT", quality: "PARTIAL", age_seconds: 120, threshold_seconds: 7200 }, headline: "失败突破后的混合阶段", executive_summary: "短期结构偏多但仍需确认。", decision_label: "观察", market_phase: "FAILED_BREAKOUT", confidence: "LOW", drivers: [{ label: "15m 结构", value: "偏多" }], risks: ["订单流确认不足"], timeframe_quality: [{ timeframe: "1W", availability: "PARTIAL", quality: "WARMUP_INCOMPLETE", bar_count: 42, required_bar_count: 200, reason_code: "INDICATOR_WARMUP_INCOMPLETE" }] };
  await page.unroute("**/api/ai-market-analysis/v1/workspace-brief/latest?**");
  await page.route("**/api/ai-market-analysis/v1/workspace-brief/latest?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(current) }));
  await page.goto("/#workspace");
  const hero = page.getByTestId("workspace-ai6b-brief");
  await expect(hero).toContainText("AI 市场研判");
  await expect(hero).toContainText("AI 审计通过 · 100 / 100");
  await expect(hero).toContainText("当前");
  await expect(hero).toContainText("1W · 历史不足");
  await expect(hero.getByRole("link", { name: /查看完整 AI 分析/ })).toHaveAttribute("href", /#research\/report\/report_eth/);
  expect((await new AxeBuilder({ page }).include('[data-testid="workspace-ai6b-brief"]').analyze()).violations).toEqual([]);
});

test("Audit failed state hides report body", async ({ page }) => {
  const failed = { ...stale, display_eligible: false, status: "NO_CURRENT_AUDITED_REPORT", latest_generated: { report_id: "failed", eligibility: "AUDIT_FAILED" }, audit: { status: "FAILED", overall_score: 0 }, headline: "must stay hidden", executive_summary: "must stay hidden" };
  await page.unroute("**/api/ai-market-analysis/v1/workspace-brief/latest?**");
  await page.route("**/api/ai-market-analysis/v1/workspace-brief/latest?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(failed) }));
  await page.goto("/#workspace");
  const hero = page.getByTestId("workspace-ai6b-brief");
  await expect(hero).toContainText("最新 AI 分析未通过审计");
  await expect(hero).not.toContainText("must stay hidden");
});
