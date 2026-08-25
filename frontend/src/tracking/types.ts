import type { ThesisCondition, ThesisSpec } from "../thesis/types";

export type ConditionState = "TRUE" | "FALSE" | "UNKNOWN";
export type EvaluationStatus = "WATCHING" | "MATCHING" | "NOT_MATCHING" | "PARTIAL" | "STALE" | "BLOCKED" | "BLOCKED_VERSION_MISMATCH";

export type HistoricalBaseline = {
  version: string; result_hash: string; definition_hash: string;
  historical_dataset_identity: string; historical_engine_version: string;
  historical_tested_range: { start: number | null; end: number | null };
  historical_summary: { independent_event_count: number; sample_quality: string; horizon_aggregates: Record<string, unknown> };
  captured_at: string;
};

export type TrackedThesis = {
  schema_version: string; track_id: string; original_text: string | null; language: "en" | "zh";
  thesis_spec: ThesisSpec; compiled_definition: Record<string, unknown>; definition_hash: string;
  historical_result_hash: string; historical_dataset_identity: string; historical_engine_version: string;
  historical_tested_range: { start: number | null; end: number | null }; historical_baseline: HistoricalBaseline;
  current_evaluation_policy_version: string; is_active: boolean; status: EvaluationStatus;
  created_at: string; updated_at: string; archived_at?: string;
};

export type CurrentCondition = ThesisCondition & {
  node_id?: string; feature_version: string; requirement?: "REQUIRED" | "OPTIONAL"; observed_value: number | boolean | null;
  state: ConditionState; source_timestamp: number | null; quality: "AVAILABLE" | "STALE" | "PARTIAL" | string;
  limitation: string | null;
};

export type EvaluationDelta = {
  version: string; initial_evaluation: boolean; status_changed: boolean;
  previous_status: EvaluationStatus | null; current_status: EvaluationStatus;
  condition_changes: Array<{ feature: string; requirement: string; from: ConditionState; to: ConditionState;
    previous_observed_value: number | boolean | null; current_observed_value: number | boolean | null;
    operator: string; configured_value: number | boolean }>;
  quality_changes: Array<{ feature: string; from: string; to: string }>;
  source_changes: Array<{ field: string; from: unknown; to: unknown }>;
  overall_change?: { from: EvaluationStatus; to: EvaluationStatus } | null;
  leaf_changes?: Array<{ node_id: string; node_type: "CONDITION"; feature: string; from: ConditionState; to: ConditionState }>;
  group_changes?: Array<{ node_id: string; node_type: "ALL" | "ANY" | "NOT"; feature?: null; from: ConditionState; to: ConditionState }>;
  material_change: boolean;
};

export type EvaluationTreeNode = ({ node_id: string; state: ConditionState } & (
  | { node_type: "CONDITION"; feature: string; operator: string; value: number | boolean; parameters?: Record<string, unknown>;
      observed_value: number | boolean | null; quality: string; limitation: string | null }
  | { node_type: "ALL" | "ANY" | "NOT"; children: EvaluationTreeNode[] }
));

export type CurrentEvaluation = {
  version: string; evaluation_id: string; evaluation_version: string; evaluation_policy_version: string;
  track_id: string; definition_hash: string; evaluated_at: string; evaluated_at_epoch: number;
  as_of: number | null; source_candle_timestamp: number | null;
  current_dataset_identity: { version: string; dataset_id: string; content_sha256: string; latest_confirmed_candle: number; row_count: number } | null;
  current_source_version: string[] | null; overall_status: EvaluationStatus;
  required_match_count: number; required_condition_count: number; conditions: CurrentCondition[];
  expression_state?: ConditionState; tree_result?: EvaluationTreeNode | null; leaf_results?: CurrentCondition[];
  freshness: { state: string; age_seconds: number | null; threshold_seconds?: number }; limitations: string[];
  delta?: EvaluationDelta;
};

export type TrackBundle = { track: TrackedThesis; latest_evaluation: CurrentEvaluation | null };
export type TrackDetail = TrackBundle & { evaluation_history: CurrentEvaluation[] };
export type TrackMutation = TrackBundle & { evaluation_created: boolean; outcome: "EVALUATED" | "NO_CHANGE"; created?: boolean };
export type ChangeBundle = { track: TrackedThesis; evaluation: CurrentEvaluation };
export type MarketStateChange = {
  source: "MARKET_STATE_V2"; instrument: string; timeframe: string;
  previous_as_of: number; current_as_of: number;
  transition: { from_state: string; to_state: string; transition_timestamp: number;
    trigger_evidence: string[]; source_candle_timestamps: number[];
    confirmation_status: string; invalidation_reason: string | null };
};
