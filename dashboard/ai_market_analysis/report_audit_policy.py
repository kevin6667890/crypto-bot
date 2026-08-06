"""Single versioned source of all AI-5 thresholds and weights."""
from __future__ import annotations
from .versions import AI_REPORT_AUDIT_POLICY_VERSION

POLICY = {
    "version": AI_REPORT_AUDIT_POLICY_VERSION,
    "pass_score": 90.0,
    "max_payload_bytes": 131072,
    "target_payload_bytes": 32768,
    "repeated_claim_ratio": {"QUICK": .12, "FULL": .18, "POSITION_AWARE": .18},
    "vague_sentence_ratio": .10,
    "near_duplicate_jaccard": .82,
    "weights": {"numeric_grounding":25,"reference_semantic_support":20,"contradiction_freedom":15,
                "scenario_invalidation":15,"data_quality_disclosure":10,"repetition_specificity":10,
                "position_macro_safety":5},
    "required_ratios": {"numeric_grounding":1.0,"reference_support":1.0,"warning_coverage":1.0,
                         "invalidation_coverage":1.0,"scenario_completeness":1.0},
}
