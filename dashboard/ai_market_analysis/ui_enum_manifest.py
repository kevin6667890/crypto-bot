"""Versioned domain-enum contract consumed by the Shadow presentation UI.

Every value is sourced from an AI market-analysis schema or a backend constant.  The
generator validates those sources so this list cannot silently drift from the API.
"""
from __future__ import annotations

from .key_level_candidates import LEVEL_SOURCES
from .macro_evidence import MACRO_CATEGORIES, MACRO_SOURCE_TYPES
from .orderflow_attribution import ALTERNATIVE_ACTIVE_BUYING, ATTRIBUTIONS, METRIC_DIRECTIONS
from .position_context import DISCIPLINE_WARNINGS
from .position_plan_models import PLAN_STATUSES
from .presentation import ELIGIBILITIES, FRESHNESS, LANGUAGES, MODES
from .report_audit_models import HARD_FAILURE_CODES
from .scenario_builder import SCENARIO_TYPES
from .structure_timeline import EVENT_TYPES
from .versions import SUPPORTED_INSTRUMENTS

UI_ENUM_MANIFEST_VERSION = "ai-market-ui-enum-manifest-v1"

UI_ENUM_GROUPS: dict[str, tuple[str, ...]] = {
    "instrument": tuple(SUPPORTED_INSTRUMENTS),
    "report_mode": tuple(MODES),
    "language": tuple(LANGUAGES),
    "eligibility": tuple(ELIGIBILITIES),
    "freshness": tuple(FRESHNESS),
    "audit_status": ("PENDING", "PASSED", "FAILED", "ERROR", "NOT_FOUND", "SCHEMA_UPGRADE_REQUIRED"),
    "trend": ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR", "INSUFFICIENT_DATA", "UP", "DOWN", "RANGE", "TRANSITION_UP", "TRANSITION_DOWN", "MIXED", "UNKNOWN"),
    "structure": ("HH_HL", "LH_LL", "MIXED", "RANGE", "UNKNOWN"),
    "market_phase": ("RANGE_BUILDING", "COMPRESSION", "BREAKOUT_ATTEMPT", "BREAKOUT_CONFIRMED", "IMPULSE", "POST_BREAKOUT_PULLBACK", "RETEST", "CONTINUATION", "FAILED_BREAKOUT", "REVERSAL", "UNCLASSIFIED"),
    "timeline_event": tuple(EVENT_TYPES),
    "directional_bias": ("BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN"),
    "confidence": ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"),
    "data_quality": ("VALID", "PARTIAL", "PARTIAL_AFTER_GAP", "STALE", "MISSING", "UNAVAILABLE", "UNKNOWN"),
    "volume_regime": ("VERY_LOW", "CONTRACTING", "NORMAL", "EXPANDING", "CLIMACTIC", "NOT_AVAILABLE", "EXPANSION", "CONTRACTION", "UNKNOWN"),
    "order_flow_phase": ("BEFORE_BREAKOUT", "BREAKOUT_IMPULSE", "POST_BREAKOUT_HIGH", "CURRENT_PULLBACK", "PRE_STRUCTURE", "BREAKOUT_ATTEMPT", "BREAKOUT_CONFIRMATION", "IMPULSE", "POST_IMPULSE", "PULLBACK", "RETEST", "CURRENT", "FAILED_BREAKOUT", "REVERSAL"),
    "price_oi_quadrant": ("PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN", "PRICE_FLAT_OI_UP", "PRICE_FLAT_OI_DOWN", "PRICE_UP_OI_FLAT", "PRICE_DOWN_OI_FLAT", "PRICE_FLAT_OI_FLAT", "UNKNOWN"),
    "metric_direction": tuple(METRIC_DIRECTIONS),
    "order_flow_attribution": tuple(ATTRIBUTIONS) + (ALTERNATIVE_ACTIVE_BUYING,),
    "alternative_attribution": tuple(ATTRIBUTIONS) + (ALTERNATIVE_ACTIVE_BUYING,),
    "funding_status": ("POSITIVE", "NEGATIVE", "NEUTRAL", "EXTREME_POSITIVE", "EXTREME_NEGATIVE", "UNAVAILABLE", "UNKNOWN"),
    "basis_status": ("CONTANGO", "BACKWARDATION", "NEUTRAL", "WIDENING", "NARROWING", "UNAVAILABLE", "UNKNOWN"),
    "liquidation_status": ("LONG_DOMINANT", "SHORT_DOMINANT", "BALANCED", "ELEVATED", "NONE", "UNAVAILABLE", "UNKNOWN"),
    "level_role": ("SUPPORT", "RESISTANCE", "PIVOT"),
    "level_state": ("ACTIVE", "BROKEN", "FLIPPED", "UNCONFIRMED", "INVALIDATED"),
    "level_strength": ("WEAK", "MODERATE", "STRONG", "MAJOR"),
    "level_source_type": tuple(LEVEL_SOURCES),
    "scenario_type": tuple(SCENARIO_TYPES),
    "scenario_direction": ("UP", "DOWN", "NONE", "UNKNOWN"),
    "scenario_likelihood": ("LOW", "MEDIUM", "HIGH"),
    "scenario_status": ("NOT_TRIGGERED", "APPROACHING_TRIGGER", "TRIGGERED_UNCONFIRMED", "CONFIRMED", "INVALIDATED", "UNKNOWN"),
    "position_source": ("NONE", "PAPER", "USER_DECLARED"),
    "position_side": ("LONG", "SHORT", "NONE"),
    "position_status": tuple(PLAN_STATUSES) + ("NONE",),
    "discipline_warning": tuple(DISCIPLINE_WARNINGS),
    "macro_category": tuple(MACRO_CATEGORIES),
    "macro_source_type": tuple(MACRO_SOURCE_TYPES),
    "macro_quality": ("AVAILABLE", "PARTIAL", "MISSING", "NOT_REQUESTED", "VALID", "UNKNOWN"),
    "audit_ratio": ("numeric_grounding", "reference_support", "level_field_coverage", "scenario_field_coverage", "invalidation_coverage", "warning_coverage", "contradiction_freedom", "scenario_completeness", "repetition_specificity", "position_macro_safety"),
    "audit_failure_code": tuple(HARD_FAILURE_CODES),
    "data_warning": ("CVD_GAP", "OI_GAP", "DATA_GAP", "STALE", "PARTIAL", "MISSING", "WATERMARK_MISMATCH", "FORWARD_ONLY", "WARMUP_INCOMPLETE", "NO_MACRO", "NO_POSITION", "CONFIDENCE_CEILING", "SCHEMA_UPGRADE", "UNSUPPORTED_SOURCE", "CRITICAL", "MAJOR"),
    "health_field": ("reports_enabled", "shadow_only", "worker_enabled", "audit_enabled", "provider_configured", "live_provider_allowed", "queue_depth", "active_requests", "oldest_queued_age", "last_report_success", "last_audit_success", "failed_count", "budget_blocked", "daily_tokens", "db_size", "schema_versions"),
    "api_error_code": ("PRESENTATION_DISABLED", "UNAUTHORIZED", "RATE_LIMITED", "INVALID_INSTRUMENT", "INVALID_MODE", "INVALID_LANGUAGE", "PRESENTATION_NOT_FOUND", "PRESENTATION_PAYLOAD_TOO_LARGE", "PRESENTATION_CONSISTENCY_ERROR", "INVALID_PRESENTATION_CONTRACT", "CONTRACT_OR_NETWORK_ERROR", "POSITION_DETAILS_UNAVAILABLE"),
    "scenario_field": ("scenario_type", "direction", "likelihood", "status", "trigger_text", "confirmation_text", "expected_path_text", "target_level_refs", "invalidation_text", "invalidation_timeframe", "confirmed_close_required", "volume_confirmation_text", "cvd_confirmation_text", "oi_confirmation_text", "funding_basis_confirmation_text", "contradicting_evidence_text", "data_quality", "source_references"),
    "level_field": ("level_id", "zone", "representative_price", "role", "state", "strength", "timeframes", "primary_timeframe", "source_types", "dynamic_static", "valid_until", "slope", "first_detected", "last_tested", "broken_at", "flipped_at", "invalidation", "quality"),
    "provenance_field": ("report_version", "prompt_version", "provider_model", "context_id", "registry_snapshot_id", "audit_version", "policy_version", "source_versions", "presentation_hash"),
    "position_field": ("source", "side", "average_cost", "original_quantity", "remaining_quantity", "original_timeframe", "original_stop", "original_targets", "targets_completed", "discipline_warnings", "plan_completed", "plan_completion_ratio", "original_thesis", "invalidation"),
    "evidence_source": ("FROZEN_CONTEXT", "REGISTRY_SNAPSHOT", "MACRO_EVIDENCE", "POSITION_CONTEXT"),
}
