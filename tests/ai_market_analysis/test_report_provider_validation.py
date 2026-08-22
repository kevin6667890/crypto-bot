from __future__ import annotations
import copy
import sqlite3
import pytest
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_basic_validation import validate_report,ReportValidationError,expected_sections
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider,ProviderError
from dashboard.ai_market_analysis.provider_claim_pack import ground_provider_report
from dashboard.ai_market_analysis.report_response_parser import parse_report_response,ReportParseError
from dashboard.ai_market_analysis.report_level_audit import audit_report_levels
from dashboard.ai_market_analysis.report_scenario_audit import audit_report_scenarios
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_reference_audit import audit_references
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,freeze_report_bundle,migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.presentation import build_report_presentation
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION
from .ai4_helpers import base_context,macro_items

def setup(mode="FULL",macro=False):
    b=base_context();m=freeze_macro_evidence_set(macro_items() if macro else [],b["decision_time"]);e=build_enriched_context(b,none_position_context(b["instrument"]),m);r=build_fact_registry(e);c=compile_report_context(r,mode);q={"compiled_context":c,"mode":mode,"context_id":e["enriched_context_id"],"request_id":"request_test","language":"zh-CN","prompt_version":AI_REPORT_PROMPT_VERSION,"model":"fake-ai4","macro_items":m["items"],"position_source":"NONE"};return q,r

@pytest.mark.parametrize("mode",["QUICK","FULL"])
def test_valid_schema_modes(mode):
    q,r=setup(mode);report=parse_report_response(FakeAIReportProvider().generate(q).raw_text);assert validate_report(report,q,r)["status"]=="VALID"

def test_indicator_periods_are_not_treated_as_market_numeric_claims():
    """Indicator labels are not invented price/count claims in provider prose."""
    from dashboard.ai_market_analysis.report_numeric_normalizer import normalize_numbers
    assert normalize_numbers("价格位于20、30、60、200周期均线上方") == []
    assert normalize_numbers("价格位于60和200周期均线上方") == []
    assert normalize_numbers("价格位于20与60周期均线上方") == []

def test_full_structured_scenario_invalidations_satisfy_invalidation_contract():
    q,r=setup("FULL");report=parse_report_response(FakeAIReportProvider().generate(q).raw_text)
    limitations=next(section for section in report["sections"] if section["section_id"]=="LIMITATIONS")
    limitations["body"]="Data quality status only."
    assert report["scenarios"] and all(item["invalidation_text"] for item in report["scenarios"])
    assert validate_report(report,q,r)["status"]=="VALID"

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


def test_btc_quick_none_breakout_attempt_without_registry_scenarios_validates():
    """Regression for production request_61be20c9: an attempt is not a confirmed scenario."""
    b=base_context();b["instrument"]="BTC-USDT-SWAP"
    b["market_timeline"]["breakout_direction"]="UP"
    b["market_timeline"]["current_phase"]="BREAKOUT_ATTEMPT"
    b["scenario_tree"]={"status":"NOT_IMPLEMENTED","direction":"UP","scenarios":[]}
    m=freeze_macro_evidence_set([],b["decision_time"])
    e=build_enriched_context(b,none_position_context(b["instrument"]),m)
    r=build_fact_registry(e);c=compile_report_context(r,"QUICK")
    q={"compiled_context":c,"mode":"QUICK","context_id":e["enriched_context_id"],
       "request_id":"request_61be20c9_regression","language":"zh-CN",
       "prompt_version":AI_REPORT_PROMPT_VERSION,"model":"fake-ai4",
       "macro_items":[],"position_source":"NONE"}
    assert [f for f in r["facts"] if f["category"]=="SCENARIO"]==[]
    raw=FakeAIReportProvider().generate(q).raw_text
    report=parse_report_response(raw)
    assert report["scenarios"]==[]
    assert all(section["scenario_refs"]==[] for section in report["sections"])
    assert validate_report(report,q,r)["status"]=="VALID"
    level=audit_report_levels(report,r)
    assert level["field_coverage"]==1.0
    assert level["failure_codes"]==[]
    scenario=audit_report_scenarios(report,r)
    assert scenario["required_scenario_count"]==0
    assert scenario["field_coverage"]==scenario["invalidation_coverage"]==1.0
    assert scenario["failure_codes"]==[]


def test_grounded_unsupported_level_without_scenario_retains_invalidation_disclosure():
    """A fail-closed level suppression cannot erase the required QUICK disclosure."""
    q, registry = setup("QUICK")
    registry = {**registry, "facts": [fact for fact in registry["facts"] if fact["category"] != "SCENARIO"]}
    q = {**q, "compiled_context": compile_report_context(registry, "QUICK")}
    report = parse_report_response(FakeAIReportProvider().generate(q).raw_text)
    report["sections"][0]["body"] = "关键位置 999999 未获注册表支持。"

    grounded = ground_provider_report(report, q["compiled_context"]["provider_claim_pack"])

    assert "限制" in grounded["sections"][0]["body"]
    assert "失效" in grounded["sections"][0]["body"]
    assert validate_report(grounded, q, registry)["status"] == "VALID"


def test_empty_registry_does_not_allow_provider_to_invent_a_scenario():
    empty_q,empty_registry=setup("QUICK")
    empty_registry={**empty_registry,"facts":[
        f for f in empty_registry["facts"] if f["category"]!="SCENARIO"]}
    empty_q={**empty_q,"compiled_context":compile_report_context(empty_registry,"QUICK")}
    report=parse_report_response(FakeAIReportProvider().generate(empty_q).raw_text)
    full_report=parse_report_response(FakeAIReportProvider().generate(setup("QUICK")[0]).raw_text)
    report["scenarios"]=[full_report["scenarios"][0]]
    with pytest.raises(ReportValidationError) as error:
        validate_report(report,empty_q,empty_registry)
    assert error.value.code=="UNKNOWN_SCENARIO_REF"


def _run_btc_breakout_attempt(tmp_path,mode):
    base=base_context();base["instrument"]="BTC-USDT-SWAP"
    base["market_timeline"]["breakout_direction"]="UP"
    base["market_timeline"]["current_phase"]="BREAKOUT_ATTEMPT"
    base["scenario_tree"]={"status":"NOT_IMPLEMENTED","direction":"UP","scenarios":[]}
    database=tmp_path/f"btc-{mode.lower()}.db"
    migrate_database(database);migrate_audit_database(database)
    reports=ReportRepository(database);audits=AuditRepository(database)
    submitted=ReportService(reports).submit(base,mode=mode,position_source="NONE")
    assert ReportWorker(reports,lambda request:FakeAIReportProvider(request["model"])).run_once()
    report=reports.get_report(request_id=submitted["request_id"])
    with reports.connect() as connection:
        attempt=dict(connection.execute(
            "SELECT * FROM ai_report_attempts WHERE request_id=?",(submitted["request_id"],)
        ).fetchone())
    bundle=freeze_report_bundle(reports,report["report_id"])
    audit=audit_report(bundle,created_at="2026-08-14T02:30:00Z")
    audits.save_audit(audit)
    presentation=build_report_presentation(
        reports,report["report_id"],instrument="BTC-USDT-SWAP",mode=mode)
    return report,attempt,bundle,audit,presentation


@pytest.mark.parametrize("mode",["QUICK","FULL"])
def test_btc_breakout_attempt_empty_scenario_registry_audits_and_presents(tmp_path,mode):
    report,attempt,_,audit,presentation=_run_btc_breakout_attempt(tmp_path,mode)
    assert attempt["parse_status"]==attempt["validation_status"]=="VALID"
    assert report["response"]["scenarios"]==[]
    assert audit["status"]=="PASSED"
    assert audit["hard_failures"]==[]
    assert audit["scorecard"]["ratios"]["reference_semantic_support"]==1.0
    assert presentation["eligibility"]=="AUDIT_PASSED_SHADOW_ONLY"
    assert presentation["report"] is not None
    if mode=="FULL":
        section=next(item for item in report["response"]["sections"] if item["section_id"]=="SCENARIOS")
        assert section["body"]=="证据不足，当前没有可审计的情景路径。"
        assert section["fact_refs"]==section["level_refs"]==section["scenario_refs"]==[]


def test_unreferenced_generic_scenario_paths_remain_fail_closed(tmp_path):
    _,_,bundle,_,_=_run_btc_breakout_attempt(tmp_path,"FULL")
    section=next(item for item in bundle["report"]["sections"] if item["section_id"]=="SCENARIOS")
    section["body"]="路径一是突破压力后延续；路径二是回踩支撑后确认；路径三是跌回核心 zone 且反抽失败，构成失败突破路径。触发前均不是已确认结果。"
    section["fact_refs"]=[];section["level_refs"]=[];section["scenario_refs"]=[]
    bundle["report_hash"]=stable_hash(bundle["report"])
    audit=audit_report(bundle,created_at="2026-08-14T02:31:00Z")
    unsupported=[item for item in audit["reference_audits"] if item["code"]=="UNSUPPORTED_CLAIM"]
    assert audit["status"]=="FAILED"
    assert "UNSUPPORTED_CLAIM" in audit["hard_failures"]
    assert len(unsupported)==4


def test_scenario_section_uses_only_registry_scenarios_and_their_fact_refs():
    request,registry=setup("FULL")
    report=parse_report_response(FakeAIReportProvider().generate(request).raw_text)
    section=next(item for item in report["sections"] if item["section_id"]=="SCENARIOS")
    frozen_registry=request["compiled_context"]
    scenario_facts=[item for item in frozen_registry["facts"] if item["category"]=="SCENARIO"]
    referenced_levels=set()
    for item in scenario_facts:
        value=item["value"]
        referenced_levels.update(value["source_level_ids"])
        referenced_levels.update((value.get("trigger") or {}).get("level_ids",[]))
        referenced_levels.update(value.get("expected_path",[]))
        referenced_levels.update(value.get("targets",[]))
        invalidation_level=(value.get("invalidation") or {}).get("level_id")
        if invalidation_level:
            referenced_levels.add(invalidation_level)
    level_facts=[item for item in frozen_registry["facts"] if item["category"]=="LEVEL" and item["value"]["level_id"] in referenced_levels]
    assert section["fact_refs"]==[item["fact_id"] for item in scenario_facts+level_facts]
    assert section["scenario_refs"]==[item["value"]["scenario_id"] for item in scenario_facts]
    assert section["level_refs"]==[item["value"]["level_id"] for item in level_facts]
    assert "路径一是突破压力后延续" not in section["body"]
    assert all(item["value"]["type"] in section["body"] for item in scenario_facts)
    assert "失败突破路径" in section["body"]


def test_typed_scenario_ref_without_supporting_fact_ref_cannot_bypass_audit(tmp_path):
    _,_,bundle,_,_=_run_btc_breakout_attempt(tmp_path,"FULL")
    golden=setup("FULL")[1]
    known=next(item["value"]["scenario_id"] for item in golden["facts"] if item["category"]=="SCENARIO")
    section=next(item for item in bundle["report"]["sections"] if item["section_id"]=="SCENARIOS")
    section["body"]="路径一是突破压力后延续。"
    section["scenario_refs"]=[known]
    section["fact_refs"]=[]
    bundle["report_hash"]=stable_hash(bundle["report"])
    audit=audit_report(bundle,created_at="2026-08-14T02:32:00Z")
    assert "UNKNOWN_REFERENCE" in audit["hard_failures"] or "UNSUPPORTED_CLAIM" in audit["hard_failures"]


class _FakeWithoutCompiledFlow(FakeAIReportProvider):
    def __init__(self):
        super().__init__();self.compiled_flow_count=None

    def generate(self,request):
        request=copy.deepcopy(request);compiled=request["compiled_context"]
        compiled["facts"]=[fact for fact in compiled["facts"] if fact["category"]!="ORDER_FLOW"]
        kept={fact["fact_id"] for fact in compiled["facts"]}
        compiled["numeric_registry"]=[item for item in compiled["numeric_registry"] if item["source_fact_id"] in kept]
        self.compiled_flow_count=sum(fact["fact_id"].startswith("FLOW_") for fact in compiled["facts"])
        return super().generate(request)


def _paper_db(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE paper_trades(id INTEGER,instrument TEXT,side TEXT,entry REAL,stop_loss REAL,take_profit REAL,status TEXT,position_size REAL,mark_price REAL,pnl_usdt REAL,net_pnl REAL,created_at TEXT,closed_at TEXT,execution_timeframe TEXT,trade_rationale TEXT,accounting_version TEXT,risk_amount REAL,actual_risk_amount REAL)")
        connection.execute("INSERT INTO paper_trades VALUES(1,'ETH-USDT','BUY',1835,1810,1890,'OPEN',2,1900,NULL,NULL,'2027-10-01T00:00:00Z',NULL,'15m','rebound','v2',10,10)")


def test_eth_full_paper_without_compiled_flow_is_limitation_only(tmp_path):
    database=tmp_path/"eth-full-paper.db";paper=tmp_path/"paper.db";_paper_db(paper)
    migrate_database(database);migrate_audit_database(database)
    reports=ReportRepository(database);audits=AuditRepository(database);provider=_FakeWithoutCompiledFlow()
    submitted=ReportService(reports,paper_db=paper).submit(
        base_context(),mode="FULL",position_source="PAPER",current_mark=1900)
    assert ReportWorker(reports,lambda _:provider).run_once()
    assert provider.compiled_flow_count==0
    report=reports.get_report(request_id=submitted["request_id"])
    bundle=freeze_report_bundle(reports,report["report_id"])
    audit=audit_report(bundle,created_at="2026-08-14T03:30:00Z")
    audits.save_audit(audit)
    presentation=build_report_presentation(
        reports,report["report_id"],instrument="ETH-USDT-SWAP",mode="FULL")
    forbidden=("空头回补","主动买盘","OI恢复","CVD为正","Funding","Basis","Liquidation")
    flow_sections=[section for section in report["response"]["sections"] if section["section_id"] in {"MOVE_NATURE","ORDER_FLOW"}]
    assert all("证据不足" in section["body"] and section["fact_refs"]==[] for section in flow_sections)
    assert not any(term in section["body"] for section in flow_sections for term in forbidden)
    assert audit["status"]=="PASSED"
    assert audit["scorecard"]["ratios"]["reference_semantic_support"]==1.0
    assert not [item for item in audit["reference_audits"] if item["code"]]
    assert presentation["eligibility"]=="AUDIT_PASSED_SHADOW_ONLY" and presentation["report"]


def test_positive_flow_projection_preserves_attribution_and_exact_refs(tmp_path):
    report,attempt,bundle,audit,presentation=_run_btc_breakout_attempt(tmp_path,"FULL")
    section=next(item for item in report["response"]["sections"] if item["section_id"]=="MOVE_NATURE")
    orderflow=next(item for item in report["response"]["sections"] if item["section_id"]=="ORDER_FLOW")
    flow_ids=[item["fact_id"] for item in bundle["fact_registry"]["facts"] if item["category"]=="ORDER_FLOW"]
    assert attempt["validation_status"]=="VALID"
    assert "空头回补" in section["body"] and "主动买盘同样存在" in section["body"]
    assert section["fact_refs"] and set(section["fact_refs"])<=set(flow_ids)
    assert orderflow["fact_refs"]==flow_ids
    assert audit["status"]=="PASSED"
    assert audit["scorecard"]["ratios"]["reference_semantic_support"]==1.0
    assert presentation["report"]


@pytest.mark.parametrize("removed",["TIMELINE","TIMEFRAME","ORDER_FLOW","LEVEL","SCENARIO","POSITION","WARNING","MACRO"])
def test_fake_provider_has_no_unconditional_market_claim_when_evidence_category_absent(removed):
    request,_=setup("FULL",macro=removed!="MACRO")
    request=copy.deepcopy(request);compiled=request["compiled_context"]
    compiled["facts"]=[fact for fact in compiled["facts"] if fact["category"]!=removed]
    kept={fact["fact_id"] for fact in compiled["facts"]}
    compiled["numeric_registry"]=[item for item in compiled["numeric_registry"] if item["source_fact_id"] in kept]
    if removed=="MACRO":request["macro_items"]=[]
    report=parse_report_response(FakeAIReportProvider().generate(request).raw_text)
    reference=audit_references(extract_claims("report_claim_sweep",report),compiled)
    assert reference["unsupported_claims"]==[]
    assert reference["reference_support_ratio"]==1.0
