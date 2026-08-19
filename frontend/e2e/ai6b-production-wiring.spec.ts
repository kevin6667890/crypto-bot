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
  await expect(card.getByRole("link", { name: /查看历史完整 AI 分析/ })).toHaveAttribute("href", /#research\/report\/report_eth/);
  expect((await new AxeBuilder({ page }).include('[data-testid="workspace-ai6b-brief"]').analyze()).violations).toEqual([]);
});

test("Workspace makes the full chart primary and switches instruments, timeframes and overlays", async ({ page }) => {
  await page.goto("/#workspace");
  await expect(page.getByText("当前可执行视图", { exact: true })).toHaveCount(0);
  await expect(page.getByText("集中查看实时市场背景、当前模拟决策、规则证据与风险控制。", { exact: true })).toHaveCount(0);
  const primary = page.locator(".workspace-primary");
  await expect(primary.locator(".chart-workspace canvas").first()).toBeVisible();
  await expect(primary.getByTestId("workspace-ai6b-brief")).toBeVisible();
  await expect(primary).toContainText("Volume");
  const timeframe = primary.locator(".timeframe-toggles");
  for (const value of ["1m", "5m", "15m", "1h", "4h", "1D"]) {
    await timeframe.getByRole("button", { name: value, exact: true }).click();
    await expect(timeframe.getByRole("button", { name: value, exact: true })).toHaveClass(/active/);
  }
  const overlays = primary.locator(".overlay-toggles");
  await overlays.getByRole("button", { name: "EMA20" }).click();
  await expect(overlays.getByRole("button", { name: "EMA20" })).not.toHaveClass(/active/);
  const instrument = page.locator("header select").first();
  for (const value of ["BTC-USDT", "ETH-USDT", "SOL-USDT"]) {
    await instrument.selectOption(value);
    await expect(instrument).toHaveValue(value);
  }
  expect((await new AxeBuilder({ page }).analyze()).violations.filter(item => item.impact === "critical" || item.impact === "serious")).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(100);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations.filter(item => item.impact === "critical" || item.impact === "serious")).toEqual([]);
});

test("Research reads the audited AI6B history source", async ({ page }) => {
  await page.goto("/#research");
  const history = page.getByTestId("research-ai6b-reports");
  await expect(history).toContainText("AI 深度中心");
  await expect(history).toContainText("QUICK");
  await expect(history).toContainText("Audit 100");
});

test("Workspace current audited Hero filters empty levels, localizes scenarios and keeps the desktop Hero balanced", async ({ page }) => {
  const current = { ...stale, status: "CURRENT_AUDITED_REPORT", freshness: { status: "CURRENT", quality: "PARTIAL", age_seconds: 120, threshold_seconds: 7200 }, headline: "失败突破后的混合阶段", executive_summary: "短期结构偏多但仍需确认。", decision_label: "观察", market_phase: "MIXED", confidence: "LOW", drivers: [{ label: "15m 结构", value: "偏多" }], risks: ["订单流确认不足"], levels: [0, 1, 2].map(index => ({ level_id: `empty_${index}`, asserted_role: "RESISTANCE", primary_timeframe: "MULTI" })), scenarios: [{ scenario_id: "bear", scenario_type: "BEARISH_CONTINUATION", trigger_text: "跌破区间下沿", confirmation_text: "15m 收盘确认", invalidation_text: "重新站回区间" }], timeframe_quality: [{ timeframe: "1W", availability: "PARTIAL", quality: "WARMUP_INCOMPLETE", bar_count: 42, required_bar_count: 200, reason_code: "INDICATOR_WARMUP_INCOMPLETE" }] };
  await page.unroute("**/api/ai-market-analysis/v1/workspace-brief/latest?**");
  await page.route("**/api/ai-market-analysis/v1/workspace-brief/latest?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(current) }));
  await page.goto("/#workspace");
  const hero = page.getByTestId("workspace-ai6b-brief");
  await expect(hero).toContainText("AI 市场研判");
  await expect(hero).toContainText("AI 审计通过 · 100 / 100");
  await expect(hero).toContainText("当前");
  await expect(hero).toContainText("1W · 历史不足");
  await expect(hero.getByText("当前无可靠关键压力位", { exact: true })).toHaveCount(1);
  await expect(hero).toContainText("偏空延续");
  await expect(hero).toContainText("混合观察");
  await expect(hero).not.toContainText("BEARISH_CONTINUATION");
  const chart = page.locator(".workspace-primary > .chart-workspace");
  const [heroBox, chartBox] = await Promise.all([hero.boundingBox(), chart.boundingBox()]);
  expect(Math.abs((heroBox?.height || 0) - (chartBox?.height || 0))).toBeLessThanOrEqual(2);
  expect((chartBox?.width || 0) / ((chartBox?.width || 0) + (heroBox?.width || 0))).toBeGreaterThan(.60);
  expect(await hero.locator(".ai-hero-content").evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
  const researchLink = hero.getByRole("link", { name: /查看完整 AI 分析/ });
  await expect(researchLink).toHaveAttribute("href", /#research\/report\/report_eth/);
  expect((await new AxeBuilder({ page }).include('[data-testid="workspace-ai6b-brief"]').analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => {
    const [mobileHero, mobileChart] = await Promise.all([hero.boundingBox(), chart.boundingBox()]);
    return (mobileHero?.y || 0) < (mobileChart?.y || 0);
  }).toBe(true);
  await researchLink.click();
  await expect(page.getByTestId("research-ai6b-reports")).toBeVisible();
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
