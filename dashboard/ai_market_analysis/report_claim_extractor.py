"""Deterministic sentence and claim extraction without models or I/O."""
from __future__ import annotations
import re
from typing import Any
from .canonical import identity
from .report_audit_models import ClaimType, Modality
from .report_numeric_normalizer import normalize_numbers,suspicious_unparsed
from .versions import AI_REPORT_CLAIM_EXTRACTOR_VERSION

URL_RE=re.compile(r"https?://[^\s，。！？；]+",re.I)
DECIMAL_RE=re.compile(r"\d+\.\d+")
TIMEFRAME_RE=re.compile(r"(?i)(15m|1H|4H|1D|1W|15分钟|1小时|4小时|日线|周线)")
INSTRUMENT_RE=re.compile(r"\b(?:BTC|ETH|SOL)(?:-USDT(?:-SWAP)?)?\b",re.I)
MODALITY_TERMS=[(Modality.NOT_AVAILABLE,("不可用","未提供","未加入")),(Modality.UNKNOWN,("无法判断","数据不足","证据不足","未知")),
 (Modality.CONDITIONAL,("如果","只有在","前提是","则")),(Modality.CONFIRMED,("已确认","已经确认","已发生","明确","证明")),
 (Modality.LIKELY,("倾向于","大概率","可能有")),(Modality.POSSIBLE,("可能","不能排除")),(Modality.UNCERTAIN,("尚未确认","不确定"))]

SECTION_TYPES={"CONCLUSION":ClaimType.INFERENCE,"RECENT_PROCESS":ClaimType.PRICE_STRUCTURE,"MOVE_NATURE":ClaimType.ORDER_FLOW_ATTRIBUTION,
 "TF_15M":ClaimType.TIMEFRAME_TREND,"TF_1H":ClaimType.TIMEFRAME_TREND,"TF_4H":ClaimType.TIMEFRAME_TREND,
 "TF_1D":ClaimType.TIMEFRAME_TREND,"TF_1W":ClaimType.TIMEFRAME_TREND,"ORDER_FLOW":ClaimType.ORDER_FLOW_ATTRIBUTION,
 "KEY_LEVELS":ClaimType.KEY_LEVEL,"SCENARIOS":ClaimType.SCENARIO,"LIMITATIONS":ClaimType.LIMITATION,
 "POSITION_PLAN":ClaimType.POSITION_DISCIPLINE,"MACRO_BACKGROUND":ClaimType.MACRO,"QUICK_SUMMARY":ClaimType.INFERENCE}
KEYWORD_TYPES=((ClaimType.INVALIDATION,("失效","无效","止损")),(ClaimType.CVD,("CVD",)),(ClaimType.OPEN_INTEREST,("OI","持仓量","未平仓量")),
 (ClaimType.VOLUME,("成交量","放量","缩量")),(ClaimType.FUNDING,("Funding","资金费率")),(ClaimType.BASIS,("Basis","基差")),
 (ClaimType.LIQUIDATION,("Liquidation","强平","爆仓")),(ClaimType.MACRO,("美联储","Fed","CPI","ETF","宏观")),
 (ClaimType.POSITION,("持仓","仓位","成本","减仓","止损")),(ClaimType.DATA_QUALITY,("gap","缺口","数据不足","不可用","forward-only")),
 (ClaimType.KEY_LEVEL,("支撑","压力","阻力","关键位")),(ClaimType.SCENARIO,("路径","情景","触发")),(ClaimType.MARKET_PHASE,("阶段","突破后回踩")))

def split_sentences(text:str)->list[str]:
    protected={}
    def protect(m):key=f"\ue000{len(protected)}\ue001";protected[key]=m.group();return key
    work=URL_RE.sub(protect,text);work=DECIMAL_RE.sub(protect,work)
    parts=[x.strip(" \t\r\n。！？!?；;") for x in re.split(r"[。！？!?；;\n]+",work) if x.strip(" \t\r\n。！？!?；;")]
    return [next((p.replace(k,v) for k,v in protected.items() if k in p),p) if sum(k in p for k in protected)<=1 else _restore(p,protected) for p in parts]

def _restore(text:str,protected:dict[str,str])->str:
    for key,value in protected.items():text=text.replace(key,value)
    return text

def _claim_type(section_id:str,text:str)->ClaimType:
    if "当前无可审计订单流证据" in text:
        return ClaimType.LIMITATION
    if "当前没有可审计的情景失效路径" in text:
        return ClaimType.LIMITATION
    if any(term in text for term in ("本次未加入已验证宏观证据", "无已验证宏观证据", "宏观证据未纳入")):
        return ClaimType.LIMITATION
    # A qualified "order-flow phase" describes order-flow evidence, not the
    # market timeline. Resolve it before the generic phase keyword below.
    if section_id in {"ORDER_FLOW","MOVE_NATURE"} and any(term in text for term in (
        "\u8ba2\u5355\u6d41\u9636\u6bb5", "\u8ba2\u5355\u6d41\u7a97\u53e3", "\u8ba2\u5355\u6d41\u8bc1\u636e", "\u8ba2\u5355\u6d41\u8f6c\u53d8",
        "\u8ba2\u5355\u6d41\u90e8\u5206\u53ef\u7528", "\u6df7\u5408\u6301\u4ed3",
    )):
        return ClaimType.ORDER_FLOW_ATTRIBUTION
    if section_id in {"TF_15M","TF_1H","TF_4H","TF_1D","TF_1W","SCENARIOS","KEY_LEVELS","POSITION_PLAN","MACRO_BACKGROUND"}:
        return SECTION_TYPES[section_id]
    if section_id=="MOVE_NATURE" and any(token in text for token in ("均线","趋势","周期级别结构")):
        return ClaimType.TIMEFRAME_TREND
    for kind,terms in KEYWORD_TYPES:
        if any(t.lower() in text.lower() for t in terms):return kind
    return SECTION_TYPES.get(section_id,ClaimType.OTHER)

def _modality(text:str)->Modality:
    # These phrases explicitly defer a conclusion. The embedded word
    # "clear" must not promote the sentence to CONFIRMED modality.
    if any(term in text for term in (
        "\u7b49\u5f85\u66f4\u660e\u786e", "\u7b49\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4",
        "\u5c1a\u5f85\u786e\u8ba4", "\u9700\u7b49\u5f85\u786e\u8ba4", "\u5c1a\u672a\u660e\u786e", "\u90e8\u5206\u53ef\u7528",
    )):
        return Modality.UNCERTAIN
    for modality,terms in MODALITY_TERMS:
        if any(t in text for t in terms):return modality
    return Modality.FACT

def extract_claims(report_id:str,report:dict[str,Any])->list[dict[str,Any]]:
    claims=[]
    for section in report.get("sections",[]):
        sid=section.get("section_id","UNKNOWN")
        for index,text in enumerate(split_sentences(section.get("body", ""))):
            normalized=re.sub(r"\s+"," ",text).strip()
            core={"report_id":report_id,"section_id":sid,"sentence_index":index,"normalized_text":normalized,
                  "extractor_version":AI_REPORT_CLAIM_EXTRACTOR_VERSION}
            lower=normalized.lower()
            quantities=normalize_numbers(text)+[{"version":"ai-report-numeric-normalizer-v1","original":x,"parsed":False,"kind":"CHINESE"} for x in suspicious_unparsed(text)]
            claim={**core,"claim_id":identity("claim",core),"original_text":text,"claim_type":_claim_type(sid,text).value,
              "modality":_modality(text).value,"polarity":"NEGATIVE" if any(x in text for x in ("不","未","无","下降","减少","偏空","负")) else "POSITIVE",
              "subjects":[x for x in ("price","CVD","OI","volume","funding","basis","liquidation","position","macro","level","scenario") if x.lower() in lower or {"price":"价格","volume":"成交量","position":"持仓","macro":"宏观","level":"支撑","scenario":"路径"}.get(x,"") in text],
              "predicates":[],"quantities":quantities,"timeframe_mentions":TIMEFRAME_RE.findall(text),
              "instrument_mentions":INSTRUMENT_RE.findall(text),"fact_refs":sorted(set(section.get("fact_refs",[]))),
              "level_refs":sorted(set(section.get("level_refs",[]))),"scenario_refs":sorted(set(section.get("scenario_refs",[]))),
              "macro_refs":sorted(set(section.get("macro_refs",[]))),"position_refs":sorted(set(section.get("position_refs",[]))),
              "uncertainty_markers":[x for x in ("可能","尚未确认","数据不足","无法判断","不能排除") if x in text],
              "assertion_markers":[x for x in ("已确认","明确","证明","必然","数据显示") if x in text],"version":AI_REPORT_CLAIM_EXTRACTOR_VERSION}
            claims.append(claim)
    return claims
