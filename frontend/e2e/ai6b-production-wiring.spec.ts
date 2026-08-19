import { expect, test } from "@playwright/test";

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
  await expect(card).toContainText("AI 简报已过期");
  await expect(card).toContainText("最后有效分析");
  await expect(card).not.toContainText("ETH-USDT 当前报 1877");
});

test("Research reads the audited AI6B history source", async ({ page }) => {
  await page.goto("/#research");
  const history = page.getByTestId("research-ai6b-reports");
  await expect(history).toContainText("AI6B");
  await expect(history).toContainText("QUICK");
  await expect(history).toContainText("Audit 100");
});
