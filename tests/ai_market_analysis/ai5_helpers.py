from __future__ import annotations
import copy,tempfile
from functools import lru_cache
from pathlib import Path
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_repository import freeze_report_bundle,migrate_audit_database
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_prompt_templates import compile_prompt
from dashboard.ai_market_analysis.report_registry_snapshot import build_registry_snapshot
from dashboard.ai_market_analysis.report_identity import report_request_identity,report_identity
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context,macro_items,position_plan

@lru_cache(maxsize=8)
def _bundle(mode="FULL",position=False,macro=False):
    path=Path(tempfile.mkdtemp())/"reports.db";migrate_database(path);migrate_audit_database(path);repo=ReportRepository(path)
    item=ReportService(repo).submit(base_context(),mode=mode,position_source="USER_DECLARED" if position else "NONE",
      position_plan=position_plan() if position else None,macro_evidence=macro_items() if macro else [],current_mark=1900)
    ReportWorker(repo,lambda request:FakeAIReportProvider(request["model"])).run_once();report=repo.get_report(request_id=item["request_id"])
    return freeze_report_bundle(repo,report["report_id"])

def golden_bundle(mode="FULL",position=False,macro=False):return copy.deepcopy(_bundle(mode,position,macro))

def _section(bundle,section_id):return next(s for s in bundle["report"]["sections"] if s["section_id"]==section_id)
def _append(bundle,section_id,text):_section(bundle,section_id)["body"]+="。"+text
def _finalize(bundle):
    bundle["report_hash"]=stable_hash(bundle["report"]);bundle["generated_text"]="\n".join(s["body"] for s in bundle["report"]["sections"]);return bundle
def _rebuild_context(bundle):
    bundle["context_hash"]=stable_hash(bundle["context"]);registry=build_fact_registry(bundle["context"]);compiled=compile_report_context(registry,bundle["report"]["mode"]);prompt=compile_prompt(compiled,bundle["report"]["mode"])
    snapshot=build_registry_snapshot(request_id=bundle["request_id"],report_context_id=compiled["compiled_hash"],enriched_context_id=bundle["context_id"],fact_registry=registry,prompt_hash=prompt["prompt_hash"],source_versions=bundle["source_versions"])
    bundle.update({"registry_snapshot_id":snapshot["registry_snapshot_id"],"fact_registry_hash":snapshot["fact_registry_hash"],"numeric_registry_hash":snapshot["numeric_registry_hash"],"registry_source_versions_hash":snapshot["source_versions_hash"],"prompt_hash":snapshot["prompt_hash"],"generation_prompt_hash":snapshot["prompt_hash"],"registry_snapshot":snapshot,"fact_registry":registry,"numeric_registry":registry["numeric_registry"]})
    bundle["request"]["registry_snapshot_id"]=snapshot["registry_snapshot_id"]

def refreeze_bundle(bundle,reidentify=False):
    registry=bundle["fact_registry"];compiled=compile_report_context(registry,bundle["report"]["mode"]);prompt=compile_prompt(compiled,bundle["report"]["mode"])
    if reidentify:
        request=bundle["request"];request_id=report_request_identity(bundle["context_id"],request["mode"],request["language"],request["prompt_version"],request["provider"],request["model"],fact_registry_hash=stable_hash(registry),numeric_registry_hash=stable_hash(registry["numeric_registry"]),prompt_hash=prompt["prompt_hash"],registry_source_versions_hash=stable_hash(bundle["source_versions"]))
        bundle["request_id"]=request_id;request["request_id"]=request_id;request["request_identity"]=request_id;bundle["report"]["request_id"]=request_id;bundle["report_hash"]=stable_hash(bundle["report"]);bundle["report_id"]=report_identity(bundle["report"])
    snapshot=build_registry_snapshot(request_id=bundle["request_id"],report_context_id=compiled["compiled_hash"],enriched_context_id=bundle["context_id"],fact_registry=registry,prompt_hash=prompt["prompt_hash"],source_versions=bundle["source_versions"])
    bundle.update({"registry_snapshot_id":snapshot["registry_snapshot_id"],"fact_registry_hash":snapshot["fact_registry_hash"],"numeric_registry_hash":snapshot["numeric_registry_hash"],"registry_source_versions_hash":snapshot["source_versions_hash"],"prompt_hash":snapshot["prompt_hash"],"generation_prompt_hash":snapshot["prompt_hash"],"registry_snapshot":snapshot,"numeric_registry":registry["numeric_registry"]});bundle["request"]["registry_snapshot_id"]=snapshot["registry_snapshot_id"];return bundle

def adversarial_bundle(case_id):
    position=case_id in {"undeclared_half","plan_not_started"};bundle=golden_bundle("POSITION_AWARE" if position else "FULL",position)
    if case_id=="price_digit_swap":_append(bundle,"CONCLUSION","冲高点为1982 USDT")
    elif case_id in {"oi_direction","chinese_percent_direction"}:
        bundle["numeric_registry"].append({"canonical_value":-6.8,"unit":"percent","absolute_tolerance":.01,"source_fact_id":"FLOW_PHASE_01"})
        _append(bundle,"ORDER_FLOW","持仓量增加6.8%" if case_id=="oi_direction" else "持仓量增加百分之六点八")
    elif case_id=="cvd_direction":_append(bundle,"ORDER_FLOW","CVD为负")
    elif case_id=="new_longs_primary":_append(bundle,"ORDER_FLOW","本轮由新增多头主导")
    elif case_id=="alternative_unique":_append(bundle,"MOVE_NATURE","空头回补是唯一机制")
    elif case_id=="likely_confirmed":_append(bundle,"MOVE_NATURE","现货买盘已确认")
    elif case_id=="weekly_bull":_append(bundle,"TF_1W","周线强多")
    elif case_id=="phase_unbroken":_append(bundle,"CONCLUSION","当前尚未突破")
    elif case_id=="continuation_confirmed":_append(bundle,"TF_4H","第二段上涨已经确认")
    elif case_id in {"support_as_resistance","internal_level_conflict"}:_append(bundle,"KEY_LEVELS","该关键位是当前压力")
    elif case_id=="invent_level":_append(bundle,"KEY_LEVELS","新增关键位1945 USDT")
    elif case_id=="invent_probability":_append(bundle,"CONCLUSION","上涨概率70%")
    elif case_id=="scenario_no_trigger":_section(bundle,"SCENARIOS")["body"]=_section(bundle,"SCENARIOS")["body"].replace("触发", "条件");bundle["report"]["scenarios"][0]["trigger_text"]=None
    elif case_id=="scenario_no_invalidation":_section(bundle,"LIMITATIONS")["body"]=_section(bundle,"LIMITATIONS")["body"].replace("失效","受损");bundle["report"]["scenarios"][0]["confirmed_close_required"]=False
    elif case_id=="scenario_unknown_target":_section(bundle,"SCENARIOS")["level_refs"].append("level_missing")
    elif case_id=="scenario_missing_failed":_section(bundle,"SCENARIOS")["body"]="路径一是突破压力后延续；路径二是回踩支撑后确认；触发前均不是已确认结果";bundle["report"]["scenarios"].pop()
    elif case_id=="cvd_gap_confirmed":
        for fact in bundle["fact_registry"]["facts"]:
            if fact["category"]=="ORDER_FLOW" and isinstance(fact["value"],dict):fact["value"]["cvd_status"]="GAP_AFFECTED"
        _append(bundle,"ORDER_FLOW","CVD完整确认")
    elif case_id=="warning_omitted":_section(bundle,"LIMITATIONS")["fact_refs"]=[]
    elif case_id=="macro_without_evidence":_append(bundle,"CONCLUSION","Fed降息已经确认")
    elif case_id=="macro_fake_url":_append(bundle,"CONCLUSION","Fed来源为https://invalid.example/fake")
    elif case_id=="paper_as_real":
        bundle["position_context"]={"source":"PAPER"};bundle["context"]["position_context"]={"source":"PAPER"};bundle["context_hash"]=stable_hash(bundle["context"]);_append(bundle,"CONCLUSION","这是你的真实持仓")
    elif case_id=="none_reduce_half":_append(bundle,"CONCLUSION","建议减仓一半")
    elif case_id=="undeclared_half":_append(bundle,"POSITION_PLAN","建议卖一半")
    elif case_id=="plan_not_started":_append(bundle,"POSITION_PLAN","原计划尚未开始")
    elif case_id=="timeframe_repetition":
        for sid in ("TF_15M","TF_1H","TF_4H","TF_1D","TF_1W"):_section(bundle,sid)["body"]="偏多但注意风险"
    elif case_id=="vague_standalone":bundle["report"]["sections"].append({"section_id":"OTHER","title":"其他","body":"多空博弈激烈","fact_refs":[],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[],"uncertainties":[]})
    elif case_id=="internal_breakout_conflict":_append(bundle,"CONCLUSION","前述突破已确认，但此处尚未突破")
    elif case_id=="predicted_as_settled":_append(bundle,"ORDER_FLOW","predicted funding已结算")
    elif case_id=="no_liquidation_claim":_append(bundle,"ORDER_FLOW","没有发生强平")
    elif case_id in {"unknown_as_fact","insufficient_high_confidence"}:
        for fact in bundle["fact_registry"]["facts"]:
            if fact["fact_id"]=="TIMELINE_CURRENT_PHASE":fact["value"]="UNKNOWN"
        _append(bundle,"CONCLUSION","UNKNOWN字段已经明确" if case_id=="unknown_as_fact" else "数据不足但属于HIGH confidence明确结论")
    elif case_id=="tf_reference_mismatch":_section(bundle,"TF_1W")["fact_refs"]=["TF15_SUMMARY"]
    elif case_id=="price_ref_for_oi":_section(bundle,"ORDER_FLOW")["fact_refs"]=["TIMELINE_CURRENT_PHASE"]
    elif case_id=="wrong_unit":_append(bundle,"KEY_LEVELS","冲高点为1928张合约")
    elif case_id=="chinese_hallucination":_append(bundle,"KEY_LEVELS","自创关键位一千九百四十五美元")
    elif case_id=="local_path":_append(bundle,"CONCLUSION",r"内部文件C:\\Users\\admin\\secret.txt")
    elif case_id=="order_instruction":_append(bundle,"CONCLUSION","立即下单买入")
    elif case_id=="guaranteed_return":_append(bundle,"CONCLUSION","保证收益")
    else:raise KeyError(case_id)
    return _finalize(bundle)
