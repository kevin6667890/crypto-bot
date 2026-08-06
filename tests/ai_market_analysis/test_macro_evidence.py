from __future__ import annotations
import copy
import pytest
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set,normalize_macro_evidence
from dashboard.ai_market_analysis.macro_evidence_providers import AUTOMATIC_MACRO_RETRIEVAL,FixtureMacroEvidenceProvider
from .ai4_helpers import macro_items
DECISION="2027-11-01T00:00:00Z"
@pytest.mark.parametrize("source",["OFFICIAL_PRIMARY","OFFICIAL_DATA","REPUTABLE_NEWS","SECONDARY_RESEARCH","USER_SUPPLIED"])
def test_source_types(source):
 raw=macro_items()[0];raw["source_type"]=source;assert normalize_macro_evidence(raw,DECISION)["source_type"]==source
@pytest.mark.parametrize("field",["published_at","event_time"])
def test_future_cutoff(field):
 raw=macro_items()[0];raw[field]="2027-12-01T00:00:00Z"
 with pytest.raises(ValueError):normalize_macro_evidence(raw,DECISION)
def test_missing_timestamp_rejected():
 raw=macro_items()[0];raw.pop("published_at")
 with pytest.raises(ValueError):normalize_macro_evidence(raw,DECISION)
def test_dedup_and_order_independent_identity():
 items=macro_items();a=freeze_macro_evidence_set(items+[copy.deepcopy(items[0])],DECISION);b=freeze_macro_evidence_set(list(reversed(items)),DECISION);assert len(a["items"])==3 and a["evidence_set_id"]==b["evidence_set_id"]
def test_add_remove_changes_identity():
 items=macro_items();assert freeze_macro_evidence_set(items,DECISION)["evidence_set_id"]!=freeze_macro_evidence_set(items[:-1],DECISION)["evidence_set_id"]
def test_fixture_and_hash():
 x=freeze_macro_evidence_set(FixtureMacroEvidenceProvider(macro_items()).evidence("ETH-USDT-SWAP",DECISION),DECISION);assert all(i["fixture"] and i["content_hash"] for i in x["items"])
def test_no_macro_is_explicit():
 x=freeze_macro_evidence_set([],DECISION);assert x["quality"]=="UNAVAILABLE" and "未加入" in x["warnings"][0]
def test_automatic_retrieval_not_implemented():assert AUTOMATIC_MACRO_RETRIEVAL=="NOT_IMPLEMENTED"
def test_article_body_not_accepted_or_persisted():
 raw=macro_items()[0];raw["article_body"]="copyright body";out=normalize_macro_evidence(raw,DECISION);assert "article_body" not in out
