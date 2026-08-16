"""Central, versioned Chinese semantic vocabulary for deterministic checks."""
from __future__ import annotations
from .versions import AI_REPORT_SEMANTIC_REGISTRY_VERSION

SEMANTIC_REGISTRY = {
 "version": AI_REPORT_SEMANTIC_REGISTRY_VERSION,
 "POST_BREAKOUT_PULLBACK":{"allowed":("突破后回踩","突破后的回撤验证","回踩验证","突破后消化"),
   "weaker":("已经突破","突破已经发生"),"forbidden":("尚未突破","仍未突破","突破失败已经确认","假突破已成立","第二段上涨已经确认")},
 "SHORT_COVERING_DOMINANT":{"allowed":("空头回补为主","空头回补为主要推动","主要包含空头回补","空头平仓主导"),
   "weaker":("空头回补","主动买盘同样存在","主动买盘共同参与"),"forbidden":("纯新增多头主导","新多主导","完全由现货买盘推动","纯空头回补")},
 "STRONG_BEAR":{"allowed":("周线仍偏空","周线仍受下降结构压制","高周期压力尚未解除","周线更高周期压力"),
   "weaker":("周线压力","长期趋势未反转"),"forbidden":("周线强多","周线已进入强多头","长期牛市已经确认")},
 "LIKELY":{"required_uncertainty":("可能","倾向","大概率","不能排除","尚未确认"),
   "forbidden_certainty":("已确认","已经确认","明确","证明","必然","基本确定")},
 "UNKNOWN":{"required_uncertainty":("未知","无法确认","证据不足","数据不足","不可用","不纳入判断","部分观察"),
   "forbidden_certainty":("已确认","明确","证明","一定","主要由","数据显示","高置信")},
 "UNAVAILABLE":{"required_uncertainty":("不可用","未提供","未加入","无法判断","不纳入判断"),
   "forbidden_certainty":("已结算","完整确认","没有发生","明确")},
}

REFERENCE_COMPATIBILITY = {
 "MARKET_PHASE":{"TIMELINE"},"PRICE_STRUCTURE":{"TIMELINE","TIMEFRAME"},"TIMEFRAME_TREND":{"TIMEFRAME"},
 "MOMENTUM":{"TIMEFRAME"},"VOLUME":{"ORDER_FLOW","TIMEFRAME"},"CVD":{"ORDER_FLOW"},
 "OPEN_INTEREST":{"ORDER_FLOW"},"FUNDING":{"ORDER_FLOW"},"BASIS":{"ORDER_FLOW"},"LIQUIDATION":{"ORDER_FLOW"},
 "ORDER_FLOW_ATTRIBUTION":{"ORDER_FLOW"},"KEY_LEVEL":{"LEVEL"},"SCENARIO":{"SCENARIO","LEVEL"},
 "INVALIDATION":{"SCENARIO","LEVEL","TIMEFRAME","TIMELINE","WARNING"},"POSITION":{"POSITION"},"POSITION_DISCIPLINE":{"POSITION","SCENARIO","LEVEL"},
 "MACRO":{"MACRO"},"DATA_QUALITY":{"WARNING"},"LIMITATION":{"WARNING","ORDER_FLOW","TIMEFRAME"},
 "INFERENCE":{"TIMELINE","ORDER_FLOW","TIMEFRAME","LEVEL","SCENARIO"},"UNCERTAINTY":{"WARNING","ORDER_FLOW","TIMEFRAME"},
 "SAFETY":set(),"OTHER":{"WARNING","TIMELINE","ORDER_FLOW","TIMEFRAME","LEVEL","SCENARIO","POSITION","MACRO"},
}
