import { expect, test } from "@playwright/test";

async function fixtureApi(page: import("@playwright/test").Page, mode: "ready" | "empty" | "error") {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/polymarket/")) {
      if (mode === "error") return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      const payload = path.endsWith("/overview")
        ? { active_universe_count: 2, eligible_count: 1, forecast_count: 1, unresolved: 1, resolved: 0 }
        : { items: mode === "ready" ? [{ forecast_id: "fixture-1", market_question: "Fixture market?", ai_probability: .6, market_probability: .5, resolution_status: "OPEN" }] : [] };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
    }
    if (path.endsWith("/changes")) return route.fulfill({ contentType: "application/json", body: JSON.stringify({ changes: [], market_state_changes: [] }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tracks: [] }) });
  });
}

test("core navigation mounts without a blank screen", async ({ page }) => {
  await fixtureApi(page, "ready");
  for (const route of ["/", "/test-an-idea", "/tracking", "/what-changed", "/advanced"]) {
    await page.goto(route);
    await expect(page.locator("#root")).not.toBeEmpty();
    await expect(page.getByRole("alert")).toHaveCount(0);
  }
});

for (const mode of ["ready", "empty", "error"] as const) test(`Prediction Markets ${mode} state mounts`, async ({ page }) => {
  await fixtureApi(page, mode);
  await page.goto("/prediction-markets");
  if (mode === "error") await expect(page.getByRole("alert")).toContainText("Prediction Markets API unavailable");
  else if (mode === "empty") await expect(page.getByText("No committed forecasts yet.")).toBeVisible();
  else await expect(page.getByText("Fixture market?")).toBeVisible();
});
