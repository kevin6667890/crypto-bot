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
        completion_tokens = min(int(request.get("max_output_tokens", 4000)), len(raw) // 3)
        prompt_tokens = request.get("token_estimate", 100)
        return ProviderResult(raw,"fake-request",self.model,{"prompt_tokens":prompt_tokens,"completion_tokens":completion_tokens,"total_tokens":prompt_tokens+completion_tokens},"stop",200,1,stable_hash(raw))

    def _report(self, request: dict[str, Any]) -> dict[str, Any]:
        registry=request["compiled_context"]; facts={f["fact_id"]:f for f in registry["facts"]}
        mode=request["mode"]
        all_ids=list(facts); level_ids=[x for x in all_ids if x.startswith("LEVEL_")]; scenario_ids=[x for x in all_ids if x.startswith("SCENARIO_")]
        macro_ids=[x for x in all_ids if x.startswith("MACRO_") and x!="MACRO_UNAVAILABLE"]
        position_ids=[x for x in all_ids if x.startswith("POSITION_")]
        flow_ids=[x for x in all_ids if x.startswith("FLOW_")]
        scenario_values=[facts[x]["value"] for x in scenario_ids]
        scenario_labels={
            "BULLISH_CONTINUATION":"上行延续路径",
            "BEARISH_CONTINUATION":"下行延续路径",
            "NORMAL_RETEST":"正常回踩路径",
            "FAILED_BREAKOUT":"失败突破路径",
            "RANGE_CONTINUATION":"区间延续路径",
        }
        scenario_level_ids=set()
        for value in scenario_values:
            trigger=value.get("trigger") or {};invalidation=value.get("invalidation") or {}
            scenario_level_ids.update(value.get("source_level_ids",[]))
            scenario_level_ids.update(trigger.get("level_ids",[]))
            scenario_level_ids.update(value.get("expected_path",[]))
            scenario_level_ids.update(value.get("targets",[]))
            if invalidation.get("level_id"):scenario_level_ids.add(invalidation["level_id"])
        scenario_level_fact_ids=[
            fact_id for fact_id in level_ids
            if facts[fact_id]["value"].get("level_id") in scenario_level_ids
        ]
        scenario_body=(
            "冻结注册表提供的条件情景为："+"、".join(
                f"{value['type']} {scenario_labels.get(value['type'],'条件路径')}"
                for value in scenario_values
            )+"；只有在各自注册表触发条件满足后才可审计。"
            if scenario_values else "证据不足，当前没有可审计的情景路径。"
        )
        timeline_ids=[x for x in all_ids if facts[x]["category"]=="TIMELINE"]
        warning_ids=[x for x in all_ids if facts[x]["category"]=="WARNING"]
        timeframe_prefixes={"TF_15M":"TF15_","TF_1H":"TF1H_","TF_4H":"TF4H_","TF_1D":"TF1D_","TF_1W":"TF1W_"}
        tfrefs={sid:[x for x in all_ids if x.startswith(prefix)] for sid,prefix in timeframe_prefixes.items()}
        timeframe_labels={"TF_15M":"15分钟","TF_1H":"1小时","TF_4H":"4小时","TF_1D":"日线","TF_1W":"周线"}
        ordering_labels={"BULLISH":"偏强","BEARISH":"偏弱","MIXED":"多空混合","FLAT":"横向","UNKNOWN":"未知"}
        attribution_labels={
            "SHORT_COVERING_DOMINANT":"空头回补主导","ACTIVE_BUYING_CONTRIBUTED":"主动买盘参与",
            "ALTERNATIVE_ACTIVE_BUYING":"主动买盘可能参与","NEW_LONGS_DOMINANT":"新增多头主导",
            "NEW_SHORTS_DOMINANT":"新增空头主导","LONG_UNWINDING_DOMINANT":"多头减仓主导",
            "LONG_LIQUIDATION_ASSISTED":"多头强平参与","SHORT_LIQUIDATION_ASSISTED":"空头强平参与",
            "LEVERAGED_LONG_BUILDUP":"杠杆多头增加","LEVERAGED_SHORT_BUILDUP":"杠杆空头增加",
            "SPOT_BUYING_LIKELY":"现货买盘可能参与","SPOT_SELLING_LIKELY":"现货卖盘可能参与",
            "TWO_SIDED_DELEVERAGING":"双向去杠杆","MIXED_POSITIONING":"多空混合",
            "POSSIBLE_NEW_POSITION_BUILDUP":"可能有新增持仓","INSUFFICIENT_EVIDENCE":"证据不足",
            "UNAVAILABLE":"不可用",
        }
        flow_values=[(fact_id,facts[fact_id]["value"]) for fact_id in flow_ids if isinstance(facts[fact_id]["value"],dict)]

        phase_fact=next((fact_id for fact_id in timeline_ids if fact_id=="TIMELINE_CURRENT_PHASE"),None)
        phase_value=facts[phase_fact]["value"] if phase_fact else None
        conclusion_parts=[];conclusion_refs=[]
        if phase_value=="POST_BREAKOUT_PULLBACK":
            conclusion_parts.append("突破已经发生，当前处于突破后回踩验证")
            conclusion_refs.append(phase_fact)
        elif phase_value:
            conclusion_parts.append(f"冻结时间线显示当前阶段为 {phase_value}")
            conclusion_refs.append(phase_fact)
        short_fact=next((x for x in tfrefs["TF_15M"] if x.endswith("_SUMMARY")),None)
        weekly_fact=next((x for x in tfrefs["TF_1W"] if x.endswith("_SUMMARY")),None)
        short_order=(facts[short_fact]["value"] or {}).get("moving_average_ordering") if short_fact else None
        weekly_order=(facts[weekly_fact]["value"] or {}).get("moving_average_ordering") if weekly_fact else None
        if short_order=="BULLISH" and weekly_order=="BEARISH":
            conclusion_parts.append("短周期偏强，但周线仍偏弱，不能宣布长期牛市")
            conclusion_refs.extend([short_fact,weekly_fact])
        elif short_fact:
            conclusion_parts.append(f"短周期结构为{ordering_labels.get(short_order,'未知')}")
            conclusion_refs.append(short_fact)
        conclusion_body="；".join(conclusion_parts)+"。本报告尚未经完整事实审计。" if conclusion_parts else "证据不足，当前无法形成可审计综合结论。"

        if phase_fact:
            recent_body=f"冻结时间线显示当前阶段为 {phase_value}，最近过程仅按该时间线事实披露。"
            recent_refs=[phase_fact]
        else:
            recent_body="证据不足，当前没有可审计的最近行情过程。";recent_refs=[]

        usable_attributions=[(fact_id,(value.get("attribution") or {}).get("primary")) for fact_id,value in flow_values]
        usable_attributions=[item for item in usable_attributions if item[1] not in {None,"INSUFFICIENT_EVIDENCE","UNAVAILABLE"}]
        positive_cvd=[fact_id for fact_id,value in flow_values if value.get("cvd_status") not in {None,"UNAVAILABLE","GAP_AFFECTED"} and isinstance(value.get("cvd_delta"),(int,float)) and value["cvd_delta"]>0]
        if usable_attributions:
            move_refs=list(dict.fromkeys([fact_id for fact_id,_ in usable_attributions]+positive_cvd))
            labels=list(dict.fromkeys(attribution_labels.get(value,value) for _,value in usable_attributions))
            move_parts=["冻结订单流归因为"+"、".join(labels)]
            if any(value=="SHORT_COVERING_DOMINANT" for _,value in usable_attributions):
                move_parts.insert(0,"首段不是纯新增多头推动，实际订单流归因包含空头回补")
                if positive_cvd:move_parts.append("CVD正向证据显示主动买盘同样存在")
                move_parts.append("现有证据不足以确认新多全面接力")
            move_body="；".join(move_parts)+"。"
        else:
            move_body="证据不足，当前无法从已冻结订单流证据判定驱动性质。";move_refs=[]

        flow_parts=[];missing_oi=False
        for fact_id,value in flow_values:
            phase=value.get("phase") or "UNKNOWN"
            attribution=(value.get("attribution") or {}).get("primary")
            volume=value.get("volume_regime")
            cvd=value.get("cvd_delta");cvd_status=value.get("cvd_status")
            oi=value.get("oi_change");oi_status=value.get("oi_status")
            details=[]
            if attribution not in {None,"UNAVAILABLE"}:details.append("归因为"+attribution_labels.get(attribution,attribution))
            if volume not in {None,"UNAVAILABLE"}:details.append("成交量状态为"+str(volume))
            if cvd_status not in {None,"UNAVAILABLE"}:
                details.append("CVD方向为"+("正" if isinstance(cvd,(int,float)) and cvd>0 else "负" if isinstance(cvd,(int,float)) and cvd<0 else "中性"))
            if oi_status not in {None,"UNAVAILABLE"} and isinstance(oi,(int,float)):
                details.append("OI变化为"+("上升" if oi>0 else "下降" if oi<0 else "持平"))
            elif oi_status not in {None,"UNAVAILABLE"}:missing_oi=True
            if details:flow_parts.append(f"{phase}阶段"+"，".join(details))
        orderflow_body="；".join(flow_parts)+"。" if flow_parts else "证据不足，当前没有可审计的订单流证据。"
        if flow_parts and missing_oi:orderflow_body+="证据不足，当前没有可审计的 OI 变化。"
        orderflow_refs=flow_ids if flow_parts else []

        timeframe_bodies={}
        for sid,label in timeframe_labels.items():
            summary=next((x for x in tfrefs[sid] if x.endswith("_SUMMARY")),None)
            if not summary:
                timeframe_bodies[sid]=f"证据不足，当前无法判断{label}结构。";continue
            value=facts[summary]["value"] if isinstance(facts[summary]["value"],dict) else {}
            ordering=value.get("moving_average_ordering")
            if sid=="TF_15M" and ordering=="BULLISH":timeframe_bodies[sid]="15分钟保持偏强，结构判断仅采用已引用事实。"
            elif sid=="TF_1H" and ordering=="BULLISH":timeframe_bodies[sid]="1小时结构偏强，延续仍需后续确认。"
            elif sid=="TF_1W" and ordering=="BEARISH":timeframe_bodies[sid]="周线仍偏弱，因此不能宣布长期牛市。"
            else:timeframe_bodies[sid]=f"{label}结构为{ordering_labels.get(ordering,'未知')}，仅按已引用事实披露。"

        level_roles=list(dict.fromkeys((facts[x]["value"] or {}).get("role") for x in level_ids if isinstance(facts[x]["value"],dict) and (facts[x]["value"] or {}).get("role")))
        if level_ids:
            role_labels={"SUPPORT":"支撑","RESISTANCE":"压力","PIVOT":"枢轴"}
            levels_body="冻结注册表提供的可审计关键位角色为"+"、".join(role_labels.get(x,x) for x in level_roles)+"；关键位状态仅按引用事实披露。"
        else:levels_body="证据不足，当前没有可引用关键位。"

        limitation_refs=warning_ids+scenario_ids+scenario_level_fact_ids
        limitation_parts=[]
        if warning_ids:limitation_parts.append("数据 gap 与遗漏事实限制置信度")
        elif registry.get("context_warnings"):limitation_parts.append("证据不足，编译上下文存在遗漏事实")
        if "MACRO_UNAVAILABLE" in facts:limitation_parts.append("本次未加入已验证宏观证据")
        elif macro_ids:limitation_parts.append("宏观证据仅作背景且不覆盖盘面")
        if scenario_ids:limitation_parts.append("情景失效条件仅采用已引用注册表事实")
        limitations_body="；".join(limitation_parts)+"。" if limitation_parts else "当前未识别额外数据限制。"

        macro_body="冻结注册表包含已验证宏观证据，宏观内容仅作背景且不覆盖盘面。" if macro_ids else "本次未加入已验证宏观证据。"
        if position_ids:
            position_body="该持仓计划仅按冻结持仓事实审计。"
            position_source=facts.get("POSITION_SOURCE",{}).get("value")
            average_cost=facts.get("POSITION_AVERAGE_COST",{}).get("value")
            if average_cost is not None:
                position_body+=f"该 {position_source} 计划的平均成本为{average_cost:g} USDT。"
            warnings=(facts.get("POSITION_WARNINGS",{}).get("value") or [])
            if "PLAN_MOSTLY_COMPLETED" in warnings:
                position_body+="原计划主要任务已经完成，剩余持仓属于需要重新决策的部分；不能因行情继续上涨自动改变原计划，也不能把短线反弹计划自动升级为长期仓位。"
            position_body+="结构失效只引用既有关键位与情景，不虚构减仓比例或数量。"
        else:position_body="证据不足，当前没有可审计持仓计划。"

        quick_parts=[];quick_refs=[]
        if phase_value=="POST_BREAKOUT_PULLBACK":quick_parts.append("突破已经发生且处于回踩验证");quick_refs.append(phase_fact)
        elif phase_fact:quick_parts.append(f"当前阶段为 {phase_value}");quick_refs.append(phase_fact)
        if usable_attributions:
            quick_parts.append("订单流归因为"+"、".join(dict.fromkeys(attribution_labels.get(value,value) for _,value in usable_attributions)))
            quick_refs.extend(fact_id for fact_id,_ in usable_attributions)
        if level_ids:quick_parts.append("关键位仅采用已引用事实");quick_refs.extend(level_ids)
        if scenario_ids:quick_parts.append("情景仅采用已引用注册表路径");quick_refs.extend(scenario_ids)
        if warning_ids:
            quick_parts.append("数据限制约束置信度")
            quick_refs.extend(warning_ids)
        quick_body="；".join(quick_parts)+"。" if quick_parts else "证据不足，当前限制条件下无法形成可审计快速结论。"

        if mode == "QUICK": ids=["QUICK_SUMMARY"]
        else:
            ids=list(FULL_SECTION_IDS)
            if macro_ids: ids.insert(1,"MACRO_BACKGROUND")
            if mode == "POSITION_AWARE": ids.append("POSITION_PLAN")
        bodies={
          "CONCLUSION":conclusion_body,
          "RECENT_PROCESS":recent_body,
          "MOVE_NATURE":move_body,
          **timeframe_bodies,
          "ORDER_FLOW":orderflow_body,
          "KEY_LEVELS":levels_body,
          "SCENARIOS":scenario_body,
          "LIMITATIONS":limitations_body,
          "MACRO_BACKGROUND":macro_body,
          "POSITION_PLAN":position_body,
          "QUICK_SUMMARY":quick_body}
        refs_by_section={
          "CONCLUSION":list(dict.fromkeys(conclusion_refs)),"RECENT_PROCESS":recent_refs,
          "MOVE_NATURE":move_refs,"ORDER_FLOW":orderflow_refs,"KEY_LEVELS":level_ids,
          "SCENARIOS":scenario_ids+scenario_level_fact_ids,"LIMITATIONS":list(dict.fromkeys(limitation_refs+macro_ids)),
          "MACRO_BACKGROUND":macro_ids,"POSITION_PLAN":position_ids+scenario_ids+scenario_level_fact_ids,
          "QUICK_SUMMARY":list(dict.fromkeys(quick_refs)),**tfrefs}
        sections=[]
        for sid in ids:
            refs=refs_by_section.get(sid,[])
            sections.append({"section_id":sid,"title":TITLES[sid],"body":bodies[sid],"fact_refs":refs,
                "level_refs":[facts[x]["value"].get("level_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("level_id")],
                "scenario_refs":[facts[x]["value"].get("scenario_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("scenario_id")],
                "macro_refs":[facts[x]["value"].get("evidence_id") for x in refs if x in facts and isinstance(facts[x]["value"],dict) and facts[x]["value"].get("evidence_id")],
                "position_refs":[x for x in refs if x.startswith("POSITION_")],"uncertainties":[]})
        referenced_levels={level_id for section in sections for level_id in section["level_refs"]}
        projected_level_facts=[
            facts[x] for x in level_ids
            if mode!="QUICK" or facts[x]["value"].get("level_id") in referenced_levels
        ]
        key_level_projections=[]
        for fact in projected_level_facts:
            level=fact["value"]
            key_level_projections.append({"level_id":level["level_id"],"analysis_text":f"{level['role']} {level['state']} {level['strength']} on {level.get('primary_timeframe')}",
              "asserted_role":level["role"],"asserted_state":level["state"],"asserted_strength":level["strength"],
              "asserted_timeframe":level.get("primary_timeframe"),"asserted_dynamic":level.get("dynamic",False),
              "valid_until":level.get("valid_until"),"fact_refs":[fact["fact_id"]],"level_refs":[level["level_id"]]})
        projected_scenario_facts=[facts[x] for x in scenario_ids[:1] if mode=="QUICK"] if mode=="QUICK" else [facts[x] for x in scenario_ids]
        scenario_projections=[]
        for fact in projected_scenario_facts:
            scenario=fact["value"];trigger=scenario.get("trigger") or {};confirmation=scenario.get("confirmation") or {};invalidation=scenario.get("invalidation") or {}
            scenario_projections.append({"scenario_id":scenario["scenario_id"],"scenario_type":scenario["type"],"direction":scenario["direction"],"likelihood":scenario["likelihood"],
              "summary":f"Conditional {scenario['type']} path", "trigger_text":trigger.get("rule"),"trigger_level_refs":trigger.get("level_ids",[]),
              "confirmation_text":confirmation.get("rule") if isinstance(confirmation,dict) else str(confirmation),
              "expected_path_text":" -> ".join(scenario.get("expected_path",[])),"expected_path_level_refs":scenario.get("expected_path",[]),
              "target_level_refs":scenario.get("targets",[]),"invalidation_text":invalidation.get("rule"),"invalidation_level_ref":invalidation.get("level_id"),
              "invalidation_timeframe":invalidation.get("timeframe"),"confirmed_close_required":"confirmed" in str(invalidation.get("rule","")).lower(),
              "volume_confirmation_text":scenario.get("volume_confirmation"),"cvd_confirmation_text":scenario.get("cvd_confirmation"),
              "oi_confirmation_text":scenario.get("oi_confirmation"),"funding_basis_confirmation_text":scenario.get("funding_basis_confirmation"),
              "contradicting_evidence_text":"; ".join(map(str,scenario.get("contradicting_evidence",[]))),
              "fact_refs":[fact["fact_id"]],"level_refs":scenario.get("source_level_ids",[]),
              "source_phase_ids":scenario.get("source_phase_ids",[]),"source_event_ids":scenario.get("source_event_ids",[]),"uncertainty_markers":[scenario.get("likelihood")]})
        return {"schema_version":AI_REPORT_RESPONSE_VERSION,"source_versions":request.get("source_versions",REPORT_PIPELINE_VERSIONS),"context_id":request["context_id"],"request_id":request["request_id"],
            "mode":mode,"language":request["language"],"headline":"突破后回踩验证，短强长压",
            "market_phase":registry["allowed_market_phases"][0],"directional_bias":"BULLISH","confidence":registry["max_confidence"],
            "sections":sections,"key_levels":key_level_projections,"scenarios":scenario_projections,"position_guidance":({"source":facts.get("POSITION_SOURCE",{}).get("value"),"fact_refs":position_ids,"original_invalidation":{"stop":facts.get("POSITION_ORIGINAL_STOP",{}).get("value"),"fact_ref":"POSITION_ORIGINAL_STOP" if "POSITION_ORIGINAL_STOP" in facts else None,"timeframe":facts.get("POSITION_ORIGINAL_TIMEFRAME",{}).get("value"),"thesis":facts.get("POSITION_ORIGINAL_THESIS",{}).get("value")}} if mode=="POSITION_AWARE" else None),
            "unsupported_claims":[],"data_warnings":registry.get("context_warnings",[]),"citations":[{"evidence_id":facts[x]["value"]["evidence_id"]} for x in macro_ids],
            "model":self.model,"prompt_version":request["prompt_version"],"audit_status":"PENDING"}
