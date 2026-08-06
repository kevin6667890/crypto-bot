"""Independent report provider abstraction and deterministic fake provider."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from .canonical import stable_hash
from .versions import AI_REPORT_PROVIDER_VERSION, AI_REPORT_RESPONSE_VERSION
from .report_identity import REPORT_PIPELINE_VERSIONS


@dataclass(frozen=True)
class ProviderResult:
    raw_text: str
    provider_request_id: str | None
    model: str
    usage: dict[str, int]
    finish_reason: str | None
    http_status: int | None
    latency_ms: int
    raw_response_hash: str


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, http_status: int | None = None):
        super().__init__(code); self.code=code; self.retryable=retryable; self.http_status=http_status


class AIReportProvider(Protocol):
    provider_name: str
    model: str
    supports_structured_output: bool
    timeout: int
    def generate(self, request: dict[str, Any]) -> ProviderResult: ...


FULL_SECTION_IDS = ("CONCLUSION","RECENT_PROCESS","MOVE_NATURE","TF_15M","TF_1H","TF_4H","TF_1D","TF_1W","ORDER_FLOW","KEY_LEVELS","SCENARIOS","LIMITATIONS")
TITLES = {"CONCLUSION":"综合结论","MACRO_BACKGROUND":"宏观背景","RECENT_PROCESS":"最近行情过程","MOVE_NATURE":"本轮上涨或下跌性质","TF_15M":"15分钟","TF_1H":"1小时","TF_4H":"4小时","TF_1D":"日线","TF_1W":"周线","ORDER_FLOW":"成交量、CVD、OI、Funding、Basis与Liquidation","KEY_LEVELS":"关键支撑压力","SCENARIOS":"后续三种路径","LIMITATIONS":"数据限制和判断失效","POSITION_PLAN":"当前持仓与原计划执行分析","QUICK_SUMMARY":"快速结论"}


class FakeAIReportProvider:
    provider_name="fake"; supports_structured_output=True; timeout=1
    def __init__(self, model: str="fake-ai4", behavior: str="success"):
        self.model=model; self.behavior=behavior; self.calls=0

    def generate(self, request: dict[str, Any]) -> ProviderResult:
        self.calls += 1
        if self.behavior == "timeout": raise ProviderError("TIMEOUT", retryable=True)
        if self.behavior == "429": raise ProviderError("RATE_LIMIT", retryable=True, http_status=429)
        if self.behavior == "500": raise ProviderError("SERVER_ERROR", retryable=True, http_status=500)
        if self.behavior == "401": raise ProviderError("AUTHENTICATION", retryable=False, http_status=401)
        if self.behavior in {"invalid_json","repair_failure"} or (self.behavior=="repair_success" and self.calls==1): raw="{not-json"
        else:
            report=self._report(request)
            if self.behavior == "missing_section": report["sections"].pop()
            elif self.behavior == "wrong_context": report["context_id"]="enriched_wrong"
            elif self.behavior == "unknown_fact": report["sections"][0]["fact_refs"]=["FACT_DOES_NOT_EXIST"]
            elif self.behavior == "hallucinated_number": report["sections"][0]["body"] += " 虚构价格 987654321。"
            elif self.behavior == "probability": report["sections"][0]["body"] += " 上涨概率为70%。"
            elif self.behavior == "order": report["sections"][0]["body"] += " 立即下单买入。"
            raw=json.dumps(report,ensure_ascii=False,separators=(",",":"))
        return ProviderResult(raw,"fake-request",self.model,{"prompt_tokens":request.get("token_estimate",100),"completion_tokens":min(4000,len(raw)//3),"total_tokens":request.get("token_estimate",100)+min(4000,len(raw)//3)},"stop",200,1,stable_hash(raw))

    def _report(self, request: dict[str, Any]) -> dict[str, Any]:
        registry=request["compiled_context"]; facts={f["fact_id"]:f for f in registry["facts"]}
        mode=request["mode"]
        all_ids=list(facts); level_ids=[x for x in all_ids if x.startswith("LEVEL_")]; scenario_ids=[x for x in all_ids if x.startswith("SCENARIO_")]
        macro_ids=[x for x in all_ids if x.startswith("MACRO_") and x!="MACRO_UNAVAILABLE"]
        position_ids=[x for x in all_ids if x.startswith("POSITION_")]
        flow_ids=[x for x in all_ids if x.startswith("FLOW_")]
        tfrefs={sid:[x for x in all_ids if x.startswith({"TF_15M":"TF15_","TF_1H":"TF1H_","TF_4H":"TF4H_","TF_1D":"TF1D_","TF_1W":"TF1W_"}.get(sid,"NO"))][:3] for sid in FULL_SECTION_IDS}
        if mode == "QUICK": ids=["QUICK_SUMMARY"]
        else:
            ids=list(FULL_SECTION_IDS)
            if macro_ids: ids.insert(1,"MACRO_BACKGROUND")
            if mode == "POSITION_AWARE": ids.append("POSITION_PLAN")
        bodies={
          "CONCLUSION":"突破已经发生，当前处于突破后回踩验证；短周期偏强，但周线更高周期压力仍限制长期结论。本报告未经完整事实审计。",
          "RECENT_PROCESS":"市场先经历压缩，随后向上突破、冲高并回踩；当前焦点是突破边界能否完成角色转换。",
          "MOVE_NATURE":"首段不是纯新增多头推动，主要包含空头回补，主动买盘同样存在；后续未平仓量恢复仍不足以确认新多全面接力。",
          "TF_15M":"15分钟保持偏强但处于回踩，结构限制是核心防守区失守。",
          "TF_1H":"1小时结构偏强，延续仍需突破确认位，而非仅凭方向推断。",
          "TF_4H":"4小时处于突破后消化，未确认第二段上涨。",
          "TF_1D":"日线是偏多修复，不等于长期趋势已经反转。",
          "TF_1W":"周线仍偏弱并存在更高周期压力，因此不能宣布长期牛市。",
          "ORDER_FLOW":"成交量扩张、CVD为正与主动买盘支持突破；未平仓量显著下降说明首段以空头回补为主，回踩阶段的小幅恢复尚不足以确认新多全面接力。Funding、Basis与Liquidation仅按已冻结数据披露。",
          "KEY_LEVELS":"核心防守取自已引用支撑 zone；延续确认取自已引用压力位，其他高周期压力不新增价格。",
          "SCENARIOS":"路径一是突破压力后延续；路径二是回踩支撑后确认；路径三是跌回核心 zone 且反抽失败，构成失败突破路径。触发前均不是已确认结果。",
          "LIMITATIONS":"数据 gap 与未知字段限制置信度；核心 zone 失守且反抽失败会使当前偏强判断失效。本次未加入已验证宏观证据。" if not macro_ids else "宏观证据仅作背景且不覆盖盘面；数据 gap 与未知字段限制置信度，核心结构失效条件必须继续观察。",
          "MACRO_BACKGROUND":"只依据冻结证据说明背景，不扩写政策结论，也不编造来源。",
          "POSITION_PLAN":"原计划主要任务已经完成，剩余持仓属于需要重新决策的部分；不能因行情继续上涨自动改变原计划，也不能把短线反弹计划自动升级为长期仓位。结构失效只引用既有关键位与情景，不虚构减仓比例或数量。",
          "QUICK_SUMMARY":"突破已经发生且处于回踩验证；首段含空头回补与主动买盘，后续接力尚未确认。支撑、压力与失效条件仅采用引用事实；数据限制约束置信度。"}
        sections=[]
        for sid in ids:
            refs=(tfrefs.get(sid) or [])
            if sid in {"CONCLUSION","RECENT_PROCESS","MOVE_NATURE","QUICK_SUMMARY"}: refs=(all_ids[:3]+flow_ids[:2]+level_ids[:2])[:7]
            if sid=="ORDER_FLOW": refs=flow_ids[:4]
            if sid=="KEY_LEVELS": refs=level_ids[:4]
            if sid=="SCENARIOS": refs=scenario_ids[:3]
            if sid=="LIMITATIONS": refs=[x for x in all_ids if x.startswith(("DATA_","UNSUPPORTED_","MACRO_UNAVAILABLE"))][:6]
            if sid=="MACRO_BACKGROUND": refs=macro_ids[:4]
            if sid=="POSITION_PLAN": refs=position_ids[:7]+scenario_ids[:1]+level_ids[:1]
            sections.append({"section_id":sid,"title":TITLES[sid],"body":bodies[sid],"fact_refs":refs,
                "level_refs":[facts[x]["value"].get("level_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("level_id")],
                "scenario_refs":[facts[x]["value"].get("scenario_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("scenario_id")],
                "macro_refs":[facts[x]["value"].get("evidence_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("evidence_id")],
                "position_refs":[x for x in refs if x.startswith("POSITION_")],"uncertainties":[]})
        return {"schema_version":AI_REPORT_RESPONSE_VERSION,"source_versions":request.get("source_versions",REPORT_PIPELINE_VERSIONS),"context_id":request["context_id"],"request_id":request["request_id"],
            "mode":mode,"language":request["language"],"headline":"突破后回踩验证，短强长压",
            "market_phase":registry["allowed_market_phases"][0],"directional_bias":"BULLISH","confidence":registry["max_confidence"],
            "sections":sections,"key_levels":[],"scenarios":[],"position_guidance":({"source":facts.get("POSITION_SOURCE",{}).get("value"),"fact_refs":position_ids} if mode=="POSITION_AWARE" else None),
            "unsupported_claims":[],"data_warnings":registry.get("context_warnings",[]),"citations":[{"evidence_id":facts[x]["value"]["evidence_id"]} for x in macro_ids],
            "model":self.model,"prompt_version":request["prompt_version"],"audit_status":"PENDING"}
