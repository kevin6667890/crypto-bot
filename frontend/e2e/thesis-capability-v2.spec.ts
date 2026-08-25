import { expect, test, type Route } from "@playwright/test";

type Expression =
  | { node_type: "CONDITION"; feature: string; operator: string; value: number | boolean; parameters?: Record<string, number> }
  | { node_type: "ALL" | "ANY"; children: Expression[] }
  | { node_type: "NOT"; child: Expression };

const feature = (code: string, label: string, valueType: "number" | "boolean", sourceGroup = "OHLCV",
  historical = "AVAILABLE", current = "AVAILABLE", parameters: Record<string, unknown> = {}) => ({
  code, label: { en: label, zh: label }, value_type: valueType, unit: valueType === "boolean" ? "boolean" : "index",
  operators: valueType === "boolean" ? ["eq"] : ["gt", "gte", "lt", "lte"],
  bounds: { minimum: valueType === "number" ? 0 : null, maximum: valueType === "number" ? 100 : null },
  requires_threshold: valueType === "number", fixed_value: valueType === "boolean" ? true : null,
  input_scale: "identity", source_group: sourceGroup, availability: historical === "AVAILABLE" ? "AVAILABLE" : "NOT_CURRENTLY_TESTABLE",
  historical_availability: historical, current_availability: current, supported_timeframes: ["4H"], parameters,
});

const capabilities = {
  version: "thesis-capabilities-v2", thesis_spec_version: "thesis-spec-v2", feature_registry_version: "thesis-feature-contracts-v2",
  instruments: ["BTC", "ETH", "SOL"], timeframes: ["4H"], horizons: ["12H", "24H", "3D"], unsupported_concepts: [],
  features: [
    feature("ROLLING_HIGH_BREAKOUT_CONFIRMED", "Confirmed rolling-high breakout", "boolean", "OHLCV", "AVAILABLE", "AVAILABLE",
      { lookback_bars: { value_type: "integer", required: true, minimum: 5, maximum: 500, default: 20 } }),
    feature("FAILED_BREAKOUT_CONFIRMED", "Confirmed failed breakout", "boolean", "OHLCV", "AVAILABLE", "AVAILABLE",
      { lookback_bars: { value_type: "integer", required: true, minimum: 5, maximum: 500, default: 20 },
        failure_window_bars: { value_type: "integer", required: true, minimum: 1, maximum: 20, default: 3 } }),
    feature("VOLUME_PERCENTILE", "Volume percentile", "number"),
    feature("RSI", "RSI", "number"),
    { ...feature("OI_CHANGE_PERCENTILE", "OI change percentile", "number", "OI", "UNAVAILABLE", "UNAVAILABLE"),
      availability_reason: "DERIVATIVES_DATASET_UNAVAILABLE" },
  ],
  semantic_presets: { version: "semantic-preset-registry-v1", presets: [] },
};

const compoundExpression: Expression = { node_type: "ALL", children: [
  { node_type: "CONDITION", feature: "ROLLING_HIGH_BREAKOUT_CONFIRMED", operator: "eq", value: true, parameters: { lookback_bars: 20 } },
  { node_type: "ANY", children: [
    { node_type: "CONDITION", feature: "VOLUME_PERCENTILE", operator: "gte", value: 90 },
    { node_type: "CONDITION", feature: "RSI", operator: "gte", value: 70 },
  ] },
  { node_type: "NOT", child: { node_type: "CONDITION", feature: "RSI", operator: "gt", value: 80 } },
] };

function specFor(expression: Expression) {
  return { version: "thesis-spec-v2", instrument: "BTC", timeframe: "4H", expression,
    forward_horizons: ["12H", "24H"], requested_as_of: 1_767_225_600, assumptions: [], metadata: { parser_version: "thesis-natural-language-parser-v3" } };
}

const aggregate = { eligible_n: 12, censored_n: 0, positive_n: 7, zero_n: 0, negative_n: 5,
  historical_positive_rate: .5833, mean_return_fraction: .006, median_return_fraction: .004,
  p25_return_fraction: -.01, p75_return_fraction: .018, min_return_fraction: -.06, max_return_fraction: .08,
  median_mfe_fraction: .022, median_mae_fraction: -.014, sample_quality: "MODERATE", sample_quality_policy_version: "sample-quality-v1" };

function resultFor(thesisSpec: Record<string, unknown>, event = true) {
  return {
    result_version: "thesis-test-result-v2", status: "COMPLETED", thesis_spec: thesisSpec,
    instrument: "BTC", canonical_instrument: "BTC-USDT", timeframe: "4H", tested_range: { start: 1_700_000_000, end: 1_767_225_600 },
    coverage: { version: "coverage-v2", qualification: "SUPPORTED", testable: true, common_start: 1_700_000_000, common_end: 1_767_225_600,
      reason: null, testable_subset: ["OHLCV"], features: [{ feature: "ROLLING_HIGH_BREAKOUT_CONFIRMED", qualification: "SUPPORTED",
        usable_observations: 1000, coverage_ratio: 1, reason: "QUALIFIED", stale: false, partial: false }] },
    raw_candidate_count: 14, independent_event_count: 12, excluded_overlap_count: 2,
    aggregates: { "12H": aggregate, "24H": aggregate }, event_records: event ? [{ event_id: "v2-event-1", timestamp: 1_750_060_800,
      reference_close: 68_500, exclusion_status: "INCLUDED", exclusion_reason: null, outcomes: {
        "12H": { available: true, censor_reason: null, forward_return_fraction: .012, mfe_fraction: .025, mae_fraction: -.008 },
        "24H": { available: true, censor_reason: null, forward_return_fraction: .018, mfe_fraction: .03, mae_fraction: -.011 },
      } }] : [], limitations: [], warnings: [], definition_hash: "v2-definition-hash", result_hash: "v2-result-hash",
    engine_version: "thesis-event-engine-v2", feature_versions: { ROLLING_HIGH_BREAKOUT_CONFIRMED: "rolling-structure-v1" },
    compiled_definition: { event_transition_semantics: "NOT_TRUE_TO_TRUE", independence_policy: { version: "event-independence-max-horizon-v1" } },
    data_identity: { version: "composite-historical-dataset-identity-v1", content_sha256: "historical-content", selected_dataset_id: "historical-v2" },
    historical_data: { source_label: "Immutable canonical OKX history", source_type: "FROZEN_CANONICAL", source_version: "okx-v5",
      selection_policy_version: "historical-data-selection-policy-v2", dataset_id: "historical-v2", partition_content_sha256: "historical-content",
      immutable_store_sha256: "immutable-sha", immutable_store_verification: "VERIFIED", declared_dataset_id: "historical-v2",
      raw_range: { start: 1_699_000_000, end: 1_767_225_600 }, evaluable_range: { start: 1_700_000_000, end: 1_767_225_600 },
      reduction_reasons: [], warmup_candles: 20, continuity: "CONTINUOUS", gap_count: 0, raw_span_days: 790, span_days: 778,
      breadth_qualification: "SUFFICIENT_SPAN", minimum_research_span_days: 180, minimum_research_span_policy_version: "minimum-span-v1" },
  };
}

async function fulfill(route: Route, json: unknown) {
  await route.fulfill({ contentType: "application/json", body: JSON.stringify(json) });
}

function explanation() {
  return { version: "thesis-evidence-explanation-v1", status: "FALLBACK", language: "en", result_hash: "v2-result-hash",
    definition_hash: "v2-definition-hash", dataset_id: "historical-v2", facts_version: "facts-v2", facts_hash: "facts-hash",
    plan_version: "plan-v1", renderer_version: "renderer-v1", blocks: [{ template_id: "FACT", text: "Twelve independent events were measured.", fact_refs: ["N"] }],
    provider: null, fallback_reason: "AI_OPTIONAL", cache_status: "MISS" };
}

test("V2 rolling breakout with OR and NOT preserves the exact definition through evidence and Track", async ({ page }) => {
  const parsedSpec = specFor(compoundExpression); let submittedSpec: Record<string, unknown> | null = null; let trackCalls = 0;
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.route("**/api/research/thesis/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path.endsWith("/capabilities")) return fulfill(route, capabilities);
    if (path.endsWith("/parse")) {
      expect(request.postDataJSON()).toMatchObject({ version: "thesis-parse-request-v2" });
      return fulfill(route, { version: "thesis-parse-result-v2", parser_version: "thesis-natural-language-parser-v3", status: "READY",
        detected_language: "en", expression: compoundExpression, thesis_spec: parsedSpec, recognized_clauses: ["breakout", "volume or RSI", "not RSI above 80"],
        assumptions: [], unsupported_clauses: [], missing_parameters: [], warnings: [] });
    }
    if (path.endsWith("/test")) {
      submittedSpec = request.postDataJSON();
      expect(submittedSpec).toMatchObject({ version: "thesis-spec-v2", instrument: "BTC", timeframe: "4H", expression: compoundExpression,
        forward_horizons: ["12H", "24H"], assumptions: [], metadata: parsedSpec.metadata });
      return fulfill(route, resultFor(submittedSpec!));
    }
    if (path.endsWith("/explain")) return fulfill(route, explanation());
    if (path.endsWith("/tracks") && request.method() === "POST") {
      trackCalls += 1; const body = request.postDataJSON();
      expect(body.version).toBe("track-thesis-request-v2"); expect(body.thesis_spec).toEqual(submittedSpec);
      return fulfill(route, { track: { track_id: "track-v2" }, latest_evaluation: null, evaluation_created: false, outcome: "NO_CHANGE", created: true });
    }
    return route.abort();
  });

  await page.goto("/test-an-idea");
  await page.getByLabel("Test an idea").fill("BTC 4H breaks the previous 20-bar high and (volume percentile is at least 90 or RSI is at least 70), and RSI is not above 80");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  await expect(page.locator('[data-node="ALL"]')).toBeVisible();
  await expect(page.locator('[data-node="ANY"]')).toBeVisible();
  await expect(page.locator('[data-node="NOT"]')).toBeVisible();
  await expect(page.locator('[data-node="CONDITION"]').first().getByLabel("Feature")).toHaveValue("ROLLING_HIGH_BREAKOUT_CONFIRMED");
  await page.getByRole("button", { name: "Run historical test" }).click();
  await expect(page.getByRole("heading", { name: "Historical evidence" }).first()).toBeVisible();
  await expect(page.getByText("12", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Track this thesis" }).click();
  await expect(page.getByText("Tracking started")).toBeVisible();
  expect(trackCalls).toBe(1);
});

test("V2 failed breakout evidence marks the original breakout and failure confirmation timestamps", async ({ page }) => {
  const failed: Expression = { node_type: "CONDITION", feature: "FAILED_BREAKOUT_CONFIRMED", operator: "eq", value: true,
    parameters: { lookback_bars: 20, failure_window_bars: 3 } };
  const parsedSpec = specFor(failed); const failureTimestamp = 1_750_060_800; const breakoutTimestamp = 1_750_017_600;
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.route("**/api/research/thesis/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path.endsWith("/capabilities")) return fulfill(route, capabilities);
    if (path.endsWith("/parse")) return fulfill(route, { version: "thesis-parse-result-v2", parser_version: "thesis-natural-language-parser-v3",
      status: "READY", detected_language: "en", expression: failed, thesis_spec: parsedSpec, recognized_clauses: ["failed breakout within 3 bars"],
      assumptions: [], unsupported_clauses: [], missing_parameters: [], warnings: [] });
    if (path.endsWith("/test")) return fulfill(route, resultFor(request.postDataJSON()));
    if (path.endsWith("/explain")) return fulfill(route, explanation());
    if (path.endsWith("/event-context")) {
      expect(request.postDataJSON().thesis_spec.expression).toEqual(failed);
      return fulfill(route, { version: "thesis-event-context-v2", context_policy_version: "window-v2", result_hash: "v2-result-hash",
        definition_hash: "v2-definition-hash", engine_version: "thesis-event-engine-v2", instrument: "BTC", canonical_instrument: "BTC-USDT", timeframe: "4H",
        dataset_identity: { version: "composite-historical-dataset-identity-v1", selected_dataset_id: "historical-v2" },
        event: { event_id: "v2-event-1", timestamp: failureTimestamp, candle_index: 3, reference_close: 68_500,
          expression_result: { node_type: "CONDITION", state: "TRUE", feature: "FAILED_BREAKOUT_CONFIRMED", operator: "eq", value: true, observed_value: true },
          structure_context: [{ feature: "FAILED_BREAKOUT_CONFIRMED", event_timestamp: failureTimestamp, original_breakout_timestamp: breakoutTimestamp,
            failure_confirmation_timestamp: failureTimestamp, reference_level: 68_200 }] },
        candles: [1_749_988_800, breakoutTimestamp, 1_750_032_000, failureTimestamp, 1_750_075_200].map((close_timestamp, index) => ({
          open_timestamp: close_timestamp - 14_400, close_timestamp, open: 68_000 + index * 100, high: 68_400 + index * 100,
          low: 67_800 + index * 80, close: 68_100 + index * 70, volume: 1_000 + index * 50 })),
        horizons: [{ horizon: "12H", target_timestamp: 1_750_104_000, candle_index: null, outcome_close: null, available: false,
          censor_reason: "FUTURE_OUTCOME_CENSORED", forward_return_fraction: null, mfe_fraction: null, mae_fraction: null }], row_limit: 96 });
    }
    return route.abort();
  });

  await page.goto("/test-an-idea");
  await page.getByLabel("Test an idea").fill("BTC 4H failed breakout: close back below the previous 20-bar high within 3 confirmed candles");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  await page.getByRole("button", { name: "Run historical test" }).click();
  await page.getByRole("button", { name: "View event" }).click();
  const chart = page.getByRole("img", { name: "Historical event candlestick evidence" });
  await expect(chart).toBeVisible();
  await expect(chart).toHaveAttribute("data-event-marker-timestamp", String(failureTimestamp));
  await expect(chart).toHaveAttribute("data-original-breakout-timestamp", String(breakoutTimestamp));
  await expect(chart).toHaveAttribute("data-reference-level", "68200");
  expect(failureTimestamp).toBeGreaterThan(breakoutTimestamp);
});

test("V2 unavailable OI is human-readable and cannot run or Track", async ({ page }) => {
  let testCalls = 0; let trackCalls = 0;
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.route("**/api/research/thesis/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path.endsWith("/capabilities")) return fulfill(route, capabilities);
    if (path.endsWith("/parse")) return fulfill(route, { version: "thesis-parse-result-v2", parser_version: "thesis-natural-language-parser-v3",
      status: "UNSUPPORTED", detected_language: "en", expression: null, thesis_spec: null, recognized_clauses: [], assumptions: [],
      unsupported_clauses: [{ source_text: "OI surge", reason_code: "DERIVATIVES_DATASET_UNAVAILABLE", category: "DATASET_UNAVAILABLE", suggestions: ["VOLUME_PERCENTILE"] }],
      missing_parameters: [], warnings: [] });
    if (path.endsWith("/test")) { testCalls += 1; return route.abort(); }
    if (path.endsWith("/tracks")) { trackCalls += 1; return route.abort(); }
    return route.abort();
  });

  await page.goto("/test-an-idea");
  await page.getByText("What can I test?").click();
  const oi = page.getByRole("listitem").filter({ hasText: "OI change percentile" });
  await expect(oi).toBeVisible();
  await expect(oi).toContainText("Historical: — · Current: —");
  await page.getByLabel("Test an idea").fill("BTC 4H OI surge");
  await page.getByRole("button", { name: "Interpret idea" }).click();
  const blocked = page.locator(".thesis-unsupported");
  await expect(blocked.getByText("No qualified historical dataset is currently available for this condition.", { exact: true })).toBeVisible();
  await expect(blocked.getByText("Related testable conditions: VOLUME_PERCENTILE", { exact: true })).toBeVisible();
  await expect(page.getByText("DERIVATIVES_DATASET_UNAVAILABLE")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run historical test" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Track this thesis" })).toHaveCount(0);
  expect(testCalls).toBe(0); expect(trackCalls).toBe(0);
});
