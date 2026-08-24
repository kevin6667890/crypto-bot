export type ThesisCondition = { feature: string; operator: string; value: number | boolean };
export type PartialThesisCondition = { feature: string; operator: string | null; value: number | boolean | null };

export type ThesisSpecV1 = {
  version: "thesis-spec-v1";
  instrument: string;
  timeframe: string;
  required_conditions: ThesisCondition[];
  optional_conditions: ThesisCondition[];
  forward_horizons: string[];
  requested_as_of: number;
  event_independence?: { version: string; exclude_overlapping_forward_windows: boolean };
  metadata?: Record<string, unknown>;
};

export type ThesisFeatureCapability = {
  code: string;
  label: { en: string; zh: string };
  value_type: "number" | "boolean";
  unit: string;
  operators: string[];
  bounds: { minimum: number | null; maximum: number | null };
  requires_threshold: boolean;
  fixed_value: true | null;
  input_scale: "percentage_points" | "identity";
  source_group: "OHLCV" | "OI" | "CVD";
  availability: "AVAILABLE" | "NOT_CURRENTLY_TESTABLE";
  supported_timeframes: string[];
};

export type ThesisCapabilities = {
  version: string;
  thesis_spec_version: "thesis-spec-v1";
  feature_registry_version: string;
  instruments: string[];
  timeframes: string[];
  horizons: string[];
  features: ThesisFeatureCapability[];
  unsupported_concepts: string[];
};

export type ThesisParseResult = {
  version: "thesis-parse-result-v1";
  status: "READY" | "NEEDS_INPUT" | "UNSUPPORTED" | "ERROR";
  original_text: string;
  detected_language: "en" | "zh";
  draft_spec: ThesisSpecV1 | null;
  partial_spec: (Omit<ThesisSpecV1, "required_conditions" | "optional_conditions"> & {
    instrument: string | null; timeframe: string | null;
    required_conditions: PartialThesisCondition[]; optional_conditions: PartialThesisCondition[];
  }) | null;
  recognized_clauses: Array<PartialThesisCondition & { source_text: string; required: boolean }>;
  unsupported_clauses: Array<{ source_text: string; reason_code: string }>;
  missing_parameters: Array<{ clause_index: string; field: string; feature: string }>;
  assumptions: string[];
  warnings: string[];
  parser_version: string;
  assumption_policy_version: string;
};

export type HorizonAggregate = {
  eligible_n: number; censored_n: number; positive_n: number; zero_n: number; negative_n: number;
  historical_positive_rate: number | null; mean_return_fraction: number | null;
  median_return_fraction: number | null; p25_return_fraction: number | null; p75_return_fraction: number | null;
  min_return_fraction: number | null; max_return_fraction: number | null;
  median_mfe_fraction: number | null; median_mae_fraction: number | null;
  sample_quality: "INSUFFICIENT" | "LOW" | "MODERATE" | "ADEQUATE";
  sample_quality_policy_version: string;
};

export type ThesisEventRecord = {
  event_id: string; timestamp: number; reference_close: number;
  matched_conditions?: Record<string, number | boolean | null>;
  exclusion_status: "INCLUDED" | "EXCLUDED"; exclusion_reason: string | null;
  outcomes: Record<string, { available: boolean; censor_reason: string | null;
    forward_return_fraction: number | null; mfe_fraction: number | null; mae_fraction: number | null }>;
};

export type ThesisTestResult = {
  result_version: "thesis-test-result-v1"; status: string; thesis_spec: ThesisSpecV1;
  instrument: string; canonical_instrument: string; timeframe: string;
  tested_range: { start: number | null; end: number | null };
  coverage: { version: string; qualification: string; testable: boolean;
    common_start: number | null; common_end: number | null; reason: string | null;
    testable_subset: string[]; features: Array<{ feature: string; qualification: string;
      usable_observations: number; coverage_ratio: number; reason: string; stale: boolean; partial: boolean }> };
  raw_candidate_count: number; independent_event_count: number; excluded_overlap_count: number;
  event_records: ThesisEventRecord[]; aggregates: Record<string, HorizonAggregate>;
  limitations: string[]; warnings: string[]; definition_hash: string; result_hash: string;
  engine_version: string; feature_versions: Record<string, string>;
  compiled_definition: { event_transition_semantics: string; independence_policy: { version: string } };
  data_identity: { version: string; content_sha256: string; selected_dataset_id?: string; selection_policy_version?: string };
  historical_data: { source_label: string; source_type: string; source_version: string | null;
    selection_policy_version: string | null; dataset_id: string;
    partition_content_sha256: string; immutable_store_sha256: string | null;
    immutable_store_verification: string | null; declared_dataset_id: string | null;
    raw_range: { start: number | null; end: number | null };
    evaluable_range: { start: number | null; end: number | null };
    reduction_reasons: string[]; warmup_candles: number; continuity: string; gap_count: number;
    raw_span_days: number | null; span_days: number | null; breadth_qualification: "SUFFICIENT_SPAN" | "LIMITED_HISTORICAL_SPAN" | "UNKNOWN";
    minimum_research_span_days: number; minimum_research_span_policy_version: string | null };
};

export type ThesisEventContext = {
  version: "thesis-event-context-v1"; context_policy_version: string; result_hash: string;
  definition_hash: string; engine_version: string; instrument: string; canonical_instrument: string; timeframe: string;
  dataset_identity: { version: string; content_sha256: string; selected_dataset_id: string | null; source_version: string | null };
  event: { event_id: string; timestamp: number; candle_index: number; reference_close: number;
    conditions: Array<{ feature: string; operator: string; expected: number | boolean; actual: number | boolean | null; matched: boolean }> };
  candles: Array<{ open_timestamp: number; close_timestamp: number; open: number; high: number; low: number; close: number; volume: number }>;
  horizons: Array<{ horizon: string; target_timestamp: number; candle_index: number | null; outcome_close: number | null;
    available: boolean; censor_reason: string | null; forward_return_fraction: number | null; mfe_fraction: number | null; mae_fraction: number | null }>;
  row_limit: number;
};

export type EvidenceExplanation = {
  version: "thesis-evidence-explanation-v1"; status: "GENERATED" | "FALLBACK"; language: "en" | "zh";
  result_hash: string; definition_hash: string; dataset_id: string; facts_version: string; facts_hash: string;
  plan_version: string; renderer_version: string;
  blocks: Array<{ template_id: string; text: string; fact_refs: string[] }>;
  provider: { model: string; latency_ms: number | null } | null; fallback_reason: string | null; cache_status: "HIT" | "MISS";
};
