from __future__ import annotations
import pytest
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_basic_validation import validate_report,ReportValidationError,expected_sections
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider,ProviderError
from dashboard.ai_market_analysis.report_response_parser import parse_report_response,ReportParseError
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION
from .ai4_helpers import base_context,macro_items

def setup(mode="FULL",macro=False):
    b=base_context();m=freeze_macro_evidence_set(macro_items() if macro else [],b["decision_time"]);e=build_enriched_context(b,none_position_context(b["instrument"]),m);r=build_fact_registry(e);c=compile_report_context(r,mode);q={"compiled_context":c,"mode":mode,"context_id":e["enriched_context_id"],"request_id":"request_test","language":"zh-CN","prompt_version":AI_REPORT_PROMPT_VERSION,"model":"fake-ai4","macro_items":m["items"],"position_source":"NONE"};return q,r

@pytest.mark.parametrize("mode",["QUICK","FULL"])
def test_valid_schema_modes(mode):
    q,r=setup(mode);report=parse_report_response(FakeAIReportProvider().generate(q).raw_text);assert validate_report(report,q,r)["status"]=="VALID"

@pytest.mark.parametrize("behavior,code",[("wrong_context","CONTEXT_ID_MISMATCH"),("unknown_fact","UNKNOWN_FACT_REF"),("hallucinated_number","NUMERIC_NOT_IN_REGISTRY"),("probability","EXACT_PROBABILITY_FORBIDDEN"),("order","ORDER_INSTRUCTION_FORBIDDEN"),("missing_section","SECTION_ORDER_OR_COMPLETENESS")])
def test_validation_failures(behavior,code):
    q,r=setup();report=parse_report_response(FakeAIReportProvider(behavior=behavior).generate(q).raw_text)
    with pytest.raises(ReportValidationError) as error:validate_report(report,q,r)
    assert error.value.code==code

def test_invalid_json_and_unknown_fields():
    q,_=setup()
    with pytest.raises(ReportParseError):parse_report_response(FakeAIReportProvider(behavior="invalid_json").generate(q).raw_text)
    good=FakeAIReportProvider().generate(q).raw_text
    with pytest.raises(ReportParseError):parse_report_response(good[:-1]+',"extra":1}')

@pytest.mark.parametrize("behavior,retryable",[("timeout",True),("429",True),("500",True),("401",False)])
def test_provider_error_retry_classification(behavior,retryable):
    q,_=setup()
    with pytest.raises(ProviderError) as error:FakeAIReportProvider(behavior=behavior).generate(q)
    assert error.value.retryable is retryable

def test_macro_section_and_refs():
    q,r=setup(macro=True);report=parse_report_response(FakeAIReportProvider().generate(q).raw_text);assert [s["section_id"] for s in report["sections"]]==expected_sections("FULL",True);validate_report(report,q,r)
