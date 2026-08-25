import { expect, test, type Page, type Route } from "@playwright/test";

const spec = {
  version: "thesis-spec-v1", instrument: "BTC", timeframe: "4H",
  required_conditions: [
    { feature: "VOLUME_RATIO", operator: "gte", value: 1.2 },
    { feature: "PRICE_ABOVE_MA200", operator: "eq", value: true },
  ], optional_conditions: [], forward_horizons: ["4H", "12H", "24H"], requested_as_of: 1_767_225_600,
};
const historicalId = "historical-frozen-9708";
const currentIdA = "current-live-candle-a";
const currentIdB = "current-live-candle-b";

const capabilities = {
  version: "thesis-capabilities-v1", thesis_spec_version: "thesis-spec-v1", feature_registry_version: "feature-registry-v1",
  instruments: ["BTC", "ETH", "SOL"], timeframes: ["1H", "4H"], horizons: ["4H", "12H", "24H"],
  unsupported_concepts: ["CONFIRMED_STRUCTURE_BREAKOUT", "HISTORICAL_OI", "HISTORICAL_CVD"],
  features: [
    { code: "VOLUME_RATIO", label: { en: "Volume ratio", zh: "成交量比率" }, value_type: "number", unit: "ratio", operators: ["gt", "gte", "lt", "lte"], bounds: { minimum: 0, maximum: null }, requires_threshold: true, fixed_value: null, input_scale: "identity", source_group: "OHLCV", availability: "AVAILABLE", supported_timeframes: ["1H", "4H"] },
    { code: "PRICE_ABOVE_MA200", label: { en: "Price above MA200", zh: "价格高于 MA200" }, value_type: "boolean", unit: "boolean", operators: ["eq"], bounds: { minimum: null, maximum: null }, requires_threshold: false, fixed_value: true, input_scale: "identity", source_group: "OHLCV", availability: "AVAILABLE", supported_timeframes: ["1H", "4H"] },
  ],
};

const aggregate = { eligible_n: 346, censored_n: 0, positive_n: 180, zero_n: 0, negative_n: 166,
  historical_positive_rate: .5202, mean_return_fraction: .004, median_return_fraction: .003,
  p25_return_fraction: -.012, p75_return_fraction: .019, min_return_fraction: -.1, max_return_fraction: .12,
  median_mfe_fraction: .024, median_mae_fraction: -.017, sample_quality: "ADEQUATE", sample_quality_policy_version: "sample-quality-v1" };

const historicalResult = {
  result_version: "thesis-test-result-v1", status: "COMPLETED", thesis_spec: spec,
  instrument: "BTC", canonical_instrument: "BTC-USDT", timeframe: "4H",
  tested_range: { start: 1_678_003_200, end: 1_767_225_600 },
  coverage: { version: "coverage-v1", qualification: "SUPPORTED", testable: true, common_start: 1_678_003_200,
    common_end: 1_767_225_600, reason: null, testable_subset: ["VOLUME_RATIO", "PRICE_ABOVE_MA200"],
    features: [{ feature: "VOLUME_RATIO", qualification: "SUPPORTED", usable_observations: 9708, coverage_ratio: 1, reason: "qualified", stale: false, partial: false },
      { feature: "PRICE_ABOVE_MA200", qualification: "SUPPORTED", usable_observations: 9508, coverage_ratio: .98, reason: "warmup", stale: false, partial: false }] },
  raw_candidate_count: 420, independent_event_count: 346, excluded_overlap_count: 74,
  aggregates: { "4H": aggregate, "12H": aggregate, "24H": aggregate }, event_records: [],
  limitations: ["Historical conditional evidence; not a prediction."], warnings: [],
  definition_hash: "definition-hash-phase4", result_hash: "historical-result-phase4", engine_version: "thesis-event-engine-v1",
  feature_versions: { VOLUME_RATIO: "feature-v1", PRICE_ABOVE_MA200: "feature-v1" },
  compiled_definition: { event_transition_semantics: "FALSE_TO_TRUE", independence_policy: { version: "event-independence-max-horizon-v1" } },
  data_identity: { version: "bounded-ohlcv-dataset-identity-v1", content_sha256: "historical-content", selected_dataset_id: historicalId, selection_policy_version: "historical-data-selection-policy-v1" },
  historical_data: { source_label: "Frozen canonical OKX history", source_type: "FROZEN_CANONICAL", source_version: "okx-v5",
    selection_policy_version: "historical-data-selection-policy-v1", dataset_id: historicalId, partition_content_sha256: "historical-content",
    immutable_store_sha256: "immutable-sha", immutable_store_verification: "VERIFIED", declared_dataset_id: historicalId,
    raw_range: { start: 1_677_052_800, end: 1_767_225_600 }, evaluable_range: { start: 1_678_003_200, end: 1_767_225_600 },
    reduction_reasons: ["FEATURE_WARMUP:PRICE_ABOVE_MA200:200_CANDLES"], warmup_candles: 200, continuity: "CONTINUOUS", gap_count: 0,
    raw_span_days: 1043, span_days: 1032, breadth_qualification: "SUFFICIENT_SPAN", minimum_research_span_days: 180, minimum_research_span_policy_version: "minimum-span-v1" },
};

function evaluation(status: "NOT_MATCHING" | "MATCHING", candle: number, id: string, changed = false) {
  const matching = status === "MATCHING";
  return { version: "current-thesis-evaluation-v1", evaluation_id: `evaluation-${id}`, evaluation_version: "current-thesis-evaluation-v1",
    evaluation_policy_version: "current-thesis-evaluation-policy-v1", track_id: "track-phase4", definition_hash: historicalResult.definition_hash,
    evaluated_at: new Date(candle * 1000).toISOString(), evaluated_at_epoch: candle, as_of: candle, source_candle_timestamp: candle,
    current_dataset_identity: { version: "current-canonical-dataset-v1", dataset_id: id, content_sha256: `content-${id}`, latest_confirmed_candle: candle, row_count: 320 },
    current_source_version: ["live-okx-v1"], overall_status: status, required_match_count: matching ? 2 : 1, required_condition_count: 2,
    conditions: [
      { feature: "VOLUME_RATIO", feature_version: "feature-v1", requirement: "REQUIRED", operator: "gte", value: 1.2,
        observed_value: matching ? 1.31 : .94, state: matching ? "TRUE" : "FALSE", source_timestamp: candle, quality: "AVAILABLE", limitation: null },
      { feature: "PRICE_ABOVE_MA200", feature_version: "feature-v1", requirement: "REQUIRED", operator: "eq", value: true,
        observed_value: true, state: "TRUE", source_timestamp: candle, quality: "AVAILABLE", limitation: null },
    ], freshness: { state: "FRESH", age_seconds: 120, threshold_seconds: 28_800 }, limitations: [],
    delta: { version: "thesis-evaluation-delta-v1", initial_evaluation: !changed, status_changed: changed,
      previous_status: changed ? "NOT_MATCHING" : null, current_status: status,
      condition_changes: changed ? [{ feature: "VOLUME_RATIO", requirement: "REQUIRED", from: "FALSE", to: "TRUE",
        previous_observed_value: .94, current_observed_value: 1.31, operator: "gte", configured_value: 1.2 }] : [],
      quality_changes: [], source_changes: changed ? [{ field: "current_dataset_identity", from: currentIdA, to: currentIdB }] : [], material_change: changed },
  };
}

const baseline = { version: "historical-thesis-baseline-v1", result_hash: historicalResult.result_hash,
  definition_hash: historicalResult.definition_hash, historical_dataset_identity: historicalId,
  historical_engine_version: historicalResult.engine_version, historical_tested_range: historicalResult.tested_range,
  historical_summary: { independent_event_count: 346, sample_quality: "ADEQUATE", horizon_aggregates: historicalResult.aggregates },
  captured_at: "2026-01-01T00:00:00Z" };
const track = { schema_version: "tracked-thesis-v1", track_id: "track-phase4", original_text: "BTC 4H volume ratio >= 1.2 and price above MA200",
  language: "en", thesis_spec: spec, compiled_definition: historicalResult.compiled_definition, definition_hash: historicalResult.definition_hash,
  historical_result_hash: historicalResult.result_hash, historical_dataset_identity: historicalId, historical_engine_version: historicalResult.engine_version,
  historical_tested_range: historicalResult.tested_range, historical_baseline: baseline,
  current_evaluation_policy_version: "current-thesis-evaluation-policy-v1", is_active: true, status: "NOT_MATCHING",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };

async function fulfill(route: Route, json: unknown, delay = 0) {
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  await route.fulfill({ contentType: "application/json", body: JSON.stringify(json) });
}

test("A: Home to historical evidence to persistent tracking detail and no-op refresh", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  let createPosts = 0;
  const current = evaluation("NOT_MATCHING", 1_767_225_600, currentIdA);
  await page.route("**/api/research/thesis/**", async (route) => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    if (path.endsWith("/changes")) return fulfill(route, { changes: [], market_state_changes: [] });
    if (path.endsWith("/capabilities")) return fulfill(route, capabilities);
    if (path.endsWith("/parse")) return fulfill(route, { version: "thesis-parse-result-v1", status: "READY", original_text: "BTC thesis", detected_language: "en", draft_spec: spec, partial_spec: spec,
      recognized_clauses: spec.required_conditions.map((item) => ({ ...item, source_text: item.feature, required: true })), unsupported_clauses: [], missing_parameters: [], assumptions: [], warnings: [], parser_version: "parser-v1", assumption_policy_version: "assumption-v1" });
    if (path.endsWith("/test")) return fulfill(route, historicalResult);
    if (path.endsWith("/explain")) return fulfill(route, { version: "thesis-evidence-explanation-v1", status: "FALLBACK", language: "en",
      result_hash: historicalResult.result_hash, definition_hash: historicalResult.definition_hash, dataset_id: historicalId, facts_version: "facts-v1", facts_hash: "facts-hash",
      plan_version: "plan-v1", renderer_version: "renderer-v1", blocks: [{ template_id: "FACT", text: "Historical evidence measured 346 independent events.", fact_refs: ["N"] }], provider: null, fallback_reason: "AI_OPTIONAL", cache_status: "MISS" });
    if (path.endsWith("/tracks") && request.method() === "POST") { createPosts += 1; return fulfill(route, { track, latest_evaluation: current, evaluation_created: true, outcome: "EVALUATED", created: true }, 120); }
    if (path.endsWith("/tracks") && request.method() === "GET") return fulfill(route, { tracks: [{ track, latest_evaluation: current }] });
    if (path.endsWith("/track-phase4/evaluate")) return fulfill(route, { track, latest_evaluation: current, evaluation_created: false, outcome: "NO_CHANGE" });
    if (path.endsWith("/track-phase4")) return fulfill(route, { track, latest_evaluation: current, evaluation_history: [current] });
    return route.abort();
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Evidence, not predictions." })).toBeVisible();
  await page.getByRole("link", { name: /Test an idea/ }).first().click();
  await page.getByLabel("Test an idea").fill("BTC 4H volume ratio >= 1.2 and price above MA200");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  await page.getByRole("button", { name: "Run historical test" }).click();
  await expect(page.getByText("346", { exact: true }).first()).toBeVisible();
  const trackButton = page.getByRole("button", { name: "Track this thesis" });
  await trackButton.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(page.getByText("Tracking started")).toBeVisible();
  expect(createPosts).toBe(1);
  await page.getByRole("link", { name: "Tracking", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What I'm tracking" })).toBeVisible();
  await expect(page.getByText("346 independent events")).toBeVisible();
  await page.locator(".tracking-card").click();
  await expect(page.getByText("CURRENT EVIDENCE", { exact: true })).toBeVisible();
  await expect(page.getByText("HISTORICAL EVIDENCE · SAVED BASELINE", { exact: true })).toBeVisible();
  await page.getByText("Evidence identities and audit details").click();
  await expect(page.getByText(historicalId, { exact: true })).toBeVisible();
  await expect(page.getByText(currentIdA, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Latest confirmed evidence checked. No material change record was added.")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".track-evidence-columns")).toHaveCSS("grid-template-columns", /.+/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("B: a new confirmed candle creates deterministic NOT_MATCHING to MATCHING change", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "zh"));
  const first = evaluation("NOT_MATCHING", 1_767_225_600, currentIdA);
  const second = evaluation("MATCHING", 1_767_240_000, currentIdB, true);
  let advanced = false;
  await page.route("**/api/research/thesis/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path.endsWith("/track-phase4/evaluate")) { advanced = true; return fulfill(route, { track: { ...track, status: "MATCHING" }, latest_evaluation: second, evaluation_created: true, outcome: "EVALUATED" }); }
    if (path.endsWith("/track-phase4")) return fulfill(route, { track, latest_evaluation: first, evaluation_history: [first] });
    if (path.endsWith("/changes")) return fulfill(route, { changes: advanced ? [{ track: { ...track, status: "MATCHING" }, evaluation: second }] : [], market_state_changes: [] });
    return route.abort();
  });
  await page.goto("/tracking/track-phase4");
  await expect(page.getByText("NOT MATCHING", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "刷新" }).click();
  await expect(page.getByText("MATCHING", { exact: true }).first()).toBeVisible();
  await page.getByRole("link", { name: "发生了什么变化" }).click();
  await expect(page.getByRole("heading", { name: "发生了什么变化？" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "NOT MATCHING → MATCHING" })).toBeVisible();
  await expect(page.getByText("FALSE → TRUE")).toBeVisible();
  await expect(page.getByText("0.94 → 1.31")).toBeVisible();
});

test("C: Home enters Advanced and preserves legacy Market and Research routes", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.route("**/api/**", (route) => new URL(route.request().url()).pathname.endsWith("/changes")
    ? fulfill(route, { changes: [], market_state_changes: [] })
    : fulfill(route, { error: { code: "TEST_UNAVAILABLE" } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Evidence, not predictions." })).toBeVisible();
  await page.getByRole("link", { name: "Advanced" }).click();
  await expect(page.getByRole("button", { name: "Market", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Market", exact: true }).click();
  await expect(page).toHaveURL(/#market$/);
  await expect(page.locator('[data-route="market"]')).toBeAttached();
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await expect(page).toHaveURL(/#research$/);
  await expect(page.locator('[data-route="research"]')).toBeAttached();
  await page.goto("/market-state-v2");
  await expect(page.locator('[data-route="market"]')).toBeAttached();
  await page.goto("/strategy-router-v2");
  await expect(page.locator('[data-route="research"]')).toBeAttached();
});
