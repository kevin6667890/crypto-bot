from __future__ import annotations
import copy
import pytest
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import golden_bundle,refreeze_bundle

def _report_mutation(mutator):
    bundle=golden_bundle();mutator(bundle["report"]);bundle["report_hash"]=stable_hash(bundle["report"]);return audit_report(bundle)

SCENARIO_CASES=(
 ("confirmation_text",None,"SCENARIO_CONFIRMATION_MISSING"),
 ("target_level_refs",["not_a_target"],"SCENARIO_TARGET_MISMATCH"),
 ("invalidation_level_ref","wrong_level","SCENARIO_INVALIDATION_LEVEL_MISMATCH"),
 ("invalidation_timeframe","1W","SCENARIO_INVALIDATION_TIMEFRAME_MISMATCH"),
 ("confirmed_close_required",False,"SCENARIO_CONFIRMED_CLOSE_MISSING"),
 ("volume_confirmation_text","opposite volume direction","SCENARIO_VOLUME_CONDITION_MISMATCH"),
 ("cvd_confirmation_text","opposite CVD direction","SCENARIO_CVD_CONDITION_MISMATCH"),
 ("oi_confirmation_text","opposite OI condition","SCENARIO_OI_CONDITION_MISMATCH"),
 ("funding_basis_confirmation_text","predicted funding is settled","SCENARIO_FUNDING_BASIS_MISMATCH"),
 ("contradicting_evidence_text","","SCENARIO_COUNTEREVIDENCE_OMITTED"),
 ("trigger_text","trigger already confirmed","SCENARIO_TRIGGER_MISMATCH"),
 ("expected_path_level_refs",[],"SCENARIO_EXPECTED_PATH_MISMATCH"),
 ("source_phase_ids",[],"SCENARIO_SOURCE_REFERENCE_MISMATCH"),
 ("scenario_type","OTHER_PATH","SCENARIO_TYPE_MISMATCH"),
 ("direction","DOWN_SIDEWAYS","SCENARIO_DIRECTION_MISMATCH"),
)

@pytest.mark.parametrize("field,value,code",SCENARIO_CASES,ids=[x[2] for x in SCENARIO_CASES])
def test_scenario_field_counterexamples(field,value,code):
    audit=_report_mutation(lambda report:report["scenarios"][0].__setitem__(field,value));assert code in audit["hard_failures"] and audit["status"]=="FAILED"

def test_scenario_target_from_other_path_fails():
    def mutate(report):report["scenarios"][0]["target_level_refs"]=report["scenarios"][1]["target_level_refs"]
    assert "SCENARIO_TARGET_MISMATCH" in _report_mutation(mutate)["hard_failures"]

def test_duplicate_scenario_identity_fails():
    def mutate(report):report["scenarios"][1]["scenario_id"]=report["scenarios"][0]["scenario_id"]
    assert "SCENARIO_PROJECTION_MISSING" in _report_mutation(mutate)["hard_failures"]

LEVEL_CASES=(
 ("asserted_role","RESISTANCE","LEVEL_ROLE_MISMATCH"),
 ("asserted_state","BROKEN","LEVEL_STATE_MISMATCH"),
 ("asserted_timeframe","1W","LEVEL_TIMEFRAME_MISMATCH"),
 ("asserted_dynamic",False,"LEVEL_DYNAMIC_STATIC_MISMATCH"),
 ("asserted_zone_low",0.0,"LEVEL_ZONE_MISMATCH"),
 ("asserted_zone_high",999999.0,"LEVEL_ZONE_MISMATCH"),
 ("valid_until",0,"LEVEL_VALIDITY_MISMATCH"),
)

@pytest.mark.parametrize("field,value,code",LEVEL_CASES,ids=[x[2]+str(i) for i,x in enumerate(LEVEL_CASES)])
def test_level_field_counterexamples(field,value,code):
    audit=_report_mutation(lambda report:report["key_levels"][0].__setitem__(field,value));assert code in audit["hard_failures"] and audit["status"]=="FAILED"

def test_level_strength_cannot_be_exaggerated():
    audit=_report_mutation(lambda report:report["key_levels"][1].__setitem__("asserted_strength","MAJOR"));assert "LEVEL_STRENGTH_EXAGGERATED" in audit["hard_failures"]

def test_flipped_level_cannot_be_described_as_unbroken_resistance():
    audit=_report_mutation(lambda report:report["key_levels"][0].__setitem__("analysis_text","UNBROKEN RESISTANCE"));assert "LEVEL_PROJECTION_TEXT_CONTRADICTION" in audit["hard_failures"]

def test_level_projection_cannot_be_omitted():
    audit=_report_mutation(lambda report:report["key_levels"].pop(0));assert "LEVEL_PROJECTION_MISSING" in audit["hard_failures"]

def test_registry_fact_payload_tamper_is_detected():
    bundle=golden_bundle();bundle["fact_registry"]["facts"][0]["value"]="tampered";assert "FACT_REGISTRY_HASH_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_registry_numeric_payload_tamper_is_detected():
    bundle=golden_bundle();bundle["numeric_registry"][0]["canonical_value"]=-999;assert "NUMERIC_REGISTRY_HASH_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_numeric_registry_divergence_is_detected():
    bundle=golden_bundle();bundle["numeric_registry"]=copy.deepcopy(bundle["numeric_registry"]);bundle["numeric_registry"].pop();bundle["numeric_registry_hash"]=stable_hash(bundle["numeric_registry"]);assert "NUMERIC_REGISTRY_DIVERGENCE" in audit_report(bundle)["hard_failures"]

def test_registry_context_mismatch_is_detected():
    bundle=golden_bundle();bundle["fact_registry"]["context_id"]="wrong";bundle["fact_registry_hash"]=stable_hash(bundle["fact_registry"]);assert "REGISTRY_CONTEXT_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_registry_prompt_hash_mismatch_is_detected():
    bundle=golden_bundle();bundle["prompt_hash"]="wrong";assert "REGISTRY_PROMPT_HASH_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_registry_source_versions_mismatch_is_detected():
    bundle=golden_bundle();bundle["registry_source_versions_hash"]="wrong";assert "REGISTRY_SOURCE_VERSION_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_registry_snapshot_missing_is_error():
    bundle=golden_bundle();bundle.pop("registry_snapshot");audit=audit_report(bundle);assert audit["status"]=="ERROR" and audit["hard_failures"]==["REGISTRY_SNAPSHOT_NOT_FOUND"]

def test_same_snapshot_id_with_changed_identity_payload_fails():
    bundle=golden_bundle();bundle["registry_snapshot"]["identity_input"]["prompt_hash"]="wrong";assert "REGISTRY_IDENTITY_CONFLICT" in audit_report(bundle)["hard_failures"]

def test_registry_json_key_order_does_not_change_audit_identity():
    bundle=golden_bundle();before=audit_report(bundle)["audit_id"];bundle["fact_registry"]={k:bundle["fact_registry"][k] for k in reversed(list(bundle["fact_registry"]))};assert audit_report(bundle)["audit_id"]==before

def test_single_registry_field_changes_audit_identity_even_when_refrozen():
    bundle=golden_bundle();before=audit_report(bundle)["audit_id"];bundle["fact_registry"]["max_confidence"]="LOW";refreeze_bundle(bundle);audit=audit_report(bundle);assert audit["audit_id"]!=before and "REGISTRY_IDENTITY_CONFLICT" in audit["hard_failures"]
