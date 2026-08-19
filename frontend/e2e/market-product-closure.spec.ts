import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const observation = (timeframe: string, availability = "AVAILABLE") => ({ timeframe, observed_at: 1787112000, source_at: 1787111100, oldest_at: 1780000000, bar_count: availability === "PARTIAL" ? 42 : 300, required_bar_count: 200, freshness_seconds: 90, freshness_limit_seconds: 1800, availability, quality: availability, structure_state: "TREND_UP", reason_codes: availability === "PARTIAL" ? ["INDICATOR_WARMUP_INCOMPLETE"] : [] });
const frame = (timeframe: string, availability = "AVAILABLE") => ({ primary_state: "TREND_UP", role: "SETUP", evidence_strength: 80, momentum_state: "NEUTRAL", overlays: [], quality: { status: availability }, limitations: [], observation: observation(timeframe, availability) });
const state = { version: "market-state-engine-v2", instrument: "ETH-USDT-SWAP", as_of: 1787112000, primary_state_code: "TREND_UP", evidence_strength: 80, timeframes: { "15m": frame("15m"), "1H": frame("1H"), "4H": frame("4H"), "1D": frame("1D"), "1W": frame("1W", "PARTIAL") }, cross_timeframe: { state: "ALIGNED_UP", supporting_timeframes: ["15m", "1H", "4H"], conflicting_timeframes: [], missing_timeframes: [] }, level_interactions: [{ level_type: "CONFLUENCE_ZONE", timeframe: "15m", interaction_type: "UNKNOWN", boundary: 1913.31, distance_pct: -0.03, quality: "AVAILABLE", current_stage: "OBSERVING" }], overlays: [], transitions: [], evidence: [], limitations: ["1W indicator warmup incomplete"], quality: { overall_status: "PARTIAL", stale_sources: [], partial_sources: ["1W"], missing_sources: [] } };

test("Market renders compact timeframe matrix and observing key level", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "zh"));
  await page.route("**/api/**", (route) => route.abort());
  await page.route("**/api/market/state?**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(state) }));
  await page.goto("/#market");
  const market = page.locator("[data-market-state-page]");
  await expect(market).toContainText("市场状态");
  for (const timeframe of ["15m", "1H", "4H", "1D", "1W"]) await expect(market.getByText(timeframe, { exact: true }).first()).toBeVisible();
  await expect(market).toContainText("1W");
  await expect(market).toContainText("部分");
  await expect(market).toContainText("观察中");
  await expect(market).not.toContainText("互动类型尚未分类");
  await market.getByRole("button", { name: "正在测试" }).click();
  await expect(market).not.toContainText("1913.31");
  expect((await new AxeBuilder({ page }).include("[data-market-state-page]").analyze()).violations).toEqual([]);
});
