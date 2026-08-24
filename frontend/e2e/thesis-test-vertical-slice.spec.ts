import { expect, test } from "@playwright/test";

const feature = (code: string, valueType: "number" | "boolean", operators: string[], unit = "ratio") => ({
  code, label: { en: code === "VOLUME_RATIO" ? "Volume ratio" : code === "PRICE_ABOVE_MA200" ? "Price above MA200" : code, zh: code },
  value_type: valueType, unit, operators, bounds: { minimum: valueType === "number" ? 0 : null, maximum: null },
  requires_threshold: valueType === "number", fixed_value: valueType === "boolean" ? true : null,
  input_scale: unit === "percent" ? "percentage_points" : "identity", source_group: "OHLCV", availability: "AVAILABLE", supported_timeframes: ["1H", "4H"],
});

const capabilities = {
  version: "thesis-capabilities-v1", thesis_spec_version: "thesis-spec-v1", feature_registry_version: "thesis-feature-registry-v1",
  instruments: ["BTC", "ETH", "SOL"], timeframes: ["1H", "4H"], horizons: ["4H", "12H", "24H"],
  features: [feature("VOLUME_RATIO", "number", ["gt", "gte", "lt", "lte"]), feature("PRICE_ABOVE_MA200", "boolean", ["eq"], "boolean"), feature("RSI", "number", ["gt", "gte", "lt", "lte"], "index"), feature("ATR_PCT", "number", ["gt", "gte", "lt", "lte"], "percent")],
  unsupported_concepts: ["CONFIRMED_STRUCTURE_BREAKOUT", "FAILED_BREAKOUT"],
};

const conditions = [
  { feature: "VOLUME_RATIO", operator: "gte", value: 1.2 },
  { feature: "PRICE_ABOVE_MA200", operator: "eq", value: true },
];

test("natural language definition runs one historical evidence request and renders API statistics", async ({ page }) => {
  let testCalls = 0, contextCalls = 0, explanationCalls = 0;
  await page.route("**/api/research/thesis/capabilities", (route) => route.fulfill({ json: capabilities }));
  await page.route("**/api/research/thesis/parse", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.text).toContain("volume ratio >= 1.2");
    await route.fulfill({ json: {
      version: "thesis-parse-result-v1", status: "READY", original_text: body.text, detected_language: "en",
      draft_spec: { version: "thesis-spec-v1", instrument: "BTC", timeframe: "4H", required_conditions: conditions, optional_conditions: [], forward_horizons: ["4H", "12H", "24H"], requested_as_of: 1_700_000_000 },
      partial_spec: { version: "thesis-spec-v1", instrument: "BTC", timeframe: "4H", required_conditions: conditions, optional_conditions: [], forward_horizons: ["4H", "12H", "24H"], requested_as_of: 1_700_000_000 },
      recognized_clauses: [{ source_text: "volume ratio >= 1.2", ...conditions[0], required: true }, { source_text: "price above MA200", ...conditions[1], required: true }],
      unsupported_clauses: [], missing_parameters: [], assumptions: [], warnings: [], parser_version: "parser-v1", assumption_policy_version: "policy-v1",
    } });
  });
  await page.route("**/api/research/thesis/test", async (route) => {
    testCalls += 1;
    const spec = route.request().postDataJSON();
    expect(spec.required_conditions).toEqual(conditions);
    expect(spec.forward_horizons).toEqual(["4H", "12H", "24H"]);
    const aggregate = (rate: number, quality: string, censored = 1) => ({ eligible_n: 7, censored_n: censored, positive_n: 3, zero_n: 0, negative_n: 4,
      historical_positive_rate: rate, mean_return_fraction: .0099, median_return_fraction: .01234, p25_return_fraction: -.00456, p75_return_fraction: .02567,
      min_return_fraction: -.1, max_return_fraction: .12, median_mfe_fraction: .03456, median_mae_fraction: -.02345, sample_quality: quality,
      sample_quality_policy_version: "sample-quality-v1" });
    await route.fulfill({ json: {
      result_version: "thesis-test-result-v1", status: "COMPLETED", thesis_spec: spec, instrument: "BTC", canonical_instrument: "BTC-USDT", timeframe: "4H",
      tested_range: { start: 1_680_000_000, end: 1_700_000_000 }, coverage: { version: "coverage-v1", qualification: "SUPPORTED", testable: true,
        common_start: 1_680_000_000, common_end: 1_700_000_000, reason: null, testable_subset: ["VOLUME_RATIO", "PRICE_ABOVE_MA200"],
        features: [{ feature: "VOLUME_RATIO", qualification: "SUPPORTED", usable_observations: 1000, coverage_ratio: 1, reason: "qualified", stale: false, partial: false }] },
      raw_candidate_count: 9, independent_event_count: 7, excluded_overlap_count: 2,
      aggregates: { "4H": aggregate(.4217, "INSUFFICIENT", 0), "12H": aggregate(.4381, "INSUFFICIENT"), "24H": aggregate(.4519, "INSUFFICIENT") },
      event_records: [{ event_id: "event-1", timestamp: 1_690_000_000, reference_close: 27123.45, exclusion_status: "INCLUDED", exclusion_reason: null,
        outcomes: { "4H": { available: true, censor_reason: null, forward_return_fraction: .01234, mfe_fraction: .03, mae_fraction: -.02 },
          "12H": { available: true, censor_reason: null, forward_return_fraction: -.004, mfe_fraction: .04, mae_fraction: -.03 },
          "24H": { available: false, censor_reason: "TERMINAL_HISTORY", forward_return_fraction: null, mfe_fraction: null, mae_fraction: null } } }],
      limitations: ["Historical conditional evidence; not causal proof, a trading signal, or a forward probability guarantee."], warnings: [],
      definition_hash: "d".repeat(64), result_hash: "r".repeat(64), engine_version: "thesis-event-engine-v1",
      feature_versions: { VOLUME_RATIO: "feature-v1", PRICE_ABOVE_MA200: "feature-v1" },
      compiled_definition: { event_transition_semantics: "FALSE_TO_TRUE", independence_policy: { version: "event-independence-max-horizon-v1" } },
      data_identity: { version: "bounded-ohlcv-dataset-identity-v1", content_sha256: "c".repeat(64), selected_dataset_id: "s".repeat(64), selection_policy_version: "historical-data-selection-policy-v1" },
      historical_data: { source_label: "Canonical OKX OHLCV", source_type: "FROZEN_CANONICAL", source_version: "okx-v5",
        selection_policy_version: "historical-data-selection-policy-v1", dataset_id: "s".repeat(64),
        raw_range: { start: 1_670_000_000, end: 1_700_000_000 }, evaluable_range: { start: 1_680_000_000, end: 1_700_000_000 },
        reduction_reasons: ["FEATURE_WARMUP:PRICE_ABOVE_MA200:200_CANDLES"], warmup_candles: 200, continuity: "CONTINUOUS", gap_count: 0,
        span_days: 17, breadth_qualification: "LIMITED_HISTORICAL_SPAN", minimum_research_span_days: 180, minimum_research_span_policy_version: "thesis-minimum-research-span-v1" },
    } });
  });
  await page.route("**/api/research/thesis/explain", async (route) => {
    explanationCalls += 1;
    await route.fulfill({ json: { version: "thesis-evidence-explanation-v1", status: "FALLBACK", language: "en",
      result_hash: "r".repeat(64), definition_hash: "d".repeat(64), dataset_id: "s".repeat(64), facts_version: "facts-v1", facts_hash: "f".repeat(64),
      plan_version: "plan-v1", renderer_version: "renderer-v1", blocks: [{ template_id: "OUTCOME", text: "Seven eligible historical events were measured from canonical facts.", fact_refs: ["N"] },
        { template_id: "LIMIT", text: "This is historical conditional evidence, not a forecast or trading recommendation.", fact_refs: ["QUALITY"] }],
      provider: null, fallback_reason: "AI_UNAVAILABLE", cache_status: "MISS" } });
  });
  await page.route("**/api/research/thesis/event-context", async (route) => {
    contextCalls += 1;
    await route.fulfill({ json: { version: "thesis-event-context-v1", context_policy_version: "window-v1", result_hash: "r".repeat(64),
      definition_hash: "d".repeat(64), engine_version: "thesis-event-engine-v1", instrument: "BTC", canonical_instrument: "BTC-USDT", timeframe: "4H",
      dataset_identity: { version: "dataset-v1", content_sha256: "c".repeat(64), selected_dataset_id: "s".repeat(64), source_version: "okx-v5" },
      event: { event_id: "event-1", timestamp: 1_690_000_000, candle_index: 1, reference_close: 27123.45,
        conditions: conditions.map(item => ({ feature: item.feature, operator: item.operator, expected: item.value, actual: item.value, matched: true })) },
      candles: [1_689_985_600, 1_690_000_000, 1_690_014_400, 1_690_043_200, 1_690_086_400].map((close_timestamp, index) => ({
        open_timestamp: close_timestamp - 14_400, close_timestamp, open: 100 + index, high: 102 + index, low: 99 + index, close: 101 + index, volume: 1000 })),
      horizons: [{ horizon: "4H", target_timestamp: 1_690_014_400, candle_index: 2, outcome_close: 103, available: true, censor_reason: null, forward_return_fraction: .01234, mfe_fraction: .03, mae_fraction: -.02 },
        { horizon: "12H", target_timestamp: 1_690_043_200, candle_index: 3, outcome_close: 104, available: true, censor_reason: null, forward_return_fraction: -.004, mfe_fraction: .04, mae_fraction: -.03 },
        { horizon: "24H", target_timestamp: 1_690_086_400, candle_index: 4, outcome_close: 105, available: true, censor_reason: null, forward_return_fraction: .01, mfe_fraction: .05, mae_fraction: -.04 }], row_limit: 96 } });
  });

  await page.goto("/test-an-idea");
  await expect(page.getByRole("heading", { name: "Test an idea" })).toBeVisible();
  await page.getByLabel("Test an idea").fill("BTC 4H volume ratio >= 1.2 and price above MA200. What happened over the next 4H, 12H and 24H historically?");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  await expect(page.getByRole("heading", { name: "I understood your idea as" })).toBeVisible();
  await expect(page.getByText(/VOLUME_RATIO · REQUIRED/)).toBeVisible();
  await expect(page.getByText(/PRICE_ABOVE_MA200 · REQUIRED/)).toBeVisible();
  await page.getByRole("button", { name: "Run historical test" }).click();
  await expect(page.getByRole("heading", { name: "Historical evidence" }).first()).toBeVisible();
  await expect(page.getByText("7", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("42.17%", { exact: true })).toBeVisible();
  await expect(page.getByText(/7 usable · 1 censored/).first()).toBeVisible();
  await expect(page.getByText("INSUFFICIENT", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Coverage" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Limitations" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What this evidence says" })).toBeVisible();
  await expect(page.getByText("Canonical OKX OHLCV")).toBeVisible();
  await expect(page.getByText("Limited historical span")).toBeVisible();
  await page.getByRole("button", { name: "View event" }).click();
  const chart = page.getByRole("img", { name: "Historical event candlestick evidence" });
  await expect(chart).toBeVisible();
  await expect(chart).toHaveAttribute("data-event-marker-timestamp", "1690000000");
  await expect(chart).toHaveAttribute("data-horizon-marker-timestamps", "4H:1690014400,12H:1690043200,24H:1690086400");
  await expect(page.getByLabel("Event evidence").getByText("Price above MA200")).toBeVisible();
  await page.getByText("Evidence details", { exact: true }).click();
  await expect(page.getByText("Result ID", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".thesis-event-evidence-grid")).toBeVisible();
  expect(await page.locator(".thesis-event-evidence-grid").evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(1);
  expect(testCalls).toBe(1);
  expect(contextCalls).toBe(1);
  expect(explanationCalls).toBe(1);
});

test("unsupported breakout and OI are shown without any historical test call", async ({ page }) => {
  let testCalls = 0;
  await page.route("**/api/research/thesis/capabilities", (route) => route.fulfill({ json: capabilities }));
  await page.route("**/api/research/thesis/parse", async (route) => route.fulfill({ json: {
    version: "thesis-parse-result-v1", status: "UNSUPPORTED", original_text: "BTC 4H breakout with rising OI", detected_language: "en",
    draft_spec: null, partial_spec: { version: "thesis-spec-v1", instrument: "BTC", timeframe: "4H", required_conditions: [], optional_conditions: [], forward_horizons: ["4H", "12H", "24H"], requested_as_of: 1_700_000_000 },
    recognized_clauses: [], unsupported_clauses: [{ source_text: "breakout", reason_code: "CONFIRMED_OR_FAILED_BREAKOUT_NOT_CURRENTLY_TESTABLE" }, { source_text: "OI", reason_code: "HISTORICAL_OI_NOT_CURRENTLY_TESTABLE" }],
    missing_parameters: [], assumptions: ["DEFAULT_FORWARD_HORIZONS_4H_12H_24H"], warnings: [], parser_version: "parser-v1", assumption_policy_version: "policy-v1",
  } }));
  await page.route("**/api/research/thesis/test", (route) => { testCalls += 1; return route.abort(); });
  await page.goto("/test-an-idea");
  await page.getByRole("button", { name: "Build manually" }).click();
  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByLabel("Test an idea").fill("BTC 4H breakout with rising OI");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  await expect(page.getByRole("heading", { name: "This idea cannot be tested exactly yet." })).toBeVisible();
  await expect(page.getByText("breakout", { exact: true })).toBeVisible();
  await expect(page.getByText("OI", { exact: true })).toBeVisible();
  await expect(page.getByText("No historical result has been produced.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run historical test" })).toHaveCount(0);
  expect(testCalls).toBe(0);
});
