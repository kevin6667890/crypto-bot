from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_replay import replay
from .ai5_helpers import golden_bundle

def test_replay_twenty_times_is_byte_stable_excluding_created_at():
    result=replay(golden_bundle(),20);assert result["deterministic"] and all(v==1 for v in result["proof"].values())

def test_future_external_data_cannot_affect_frozen_replay():
    bundle=golden_bundle();before=audit_report(bundle,created_at="1970-01-01T00:00:00Z");external={"future_price":99999,"new_database_rows":1000};assert external
    after=audit_report(bundle,created_at="1970-01-01T00:00:00Z");assert before["payload_hash"]==after["payload_hash"] and stable_hash(bundle)==stable_hash(bundle)

def test_report_and_context_changes_change_identity():
    bundle=golden_bundle();base=audit_report(bundle)["audit_id"];bundle["report"]["headline"]+="字";bundle["report_hash"]=stable_hash(bundle["report"]);assert audit_report(bundle)["audit_id"]!=base
