import type { Language } from "./i18n";
import { localizeWorkspaceNarrative, presentAiLevels, workspaceScenarioLabel } from "./aiWorkspaceSemantics";

export const intelligenceTimeframes = ["15m", "1H", "4H", "1D", "1W"] as const;
export type IntelligenceTimeframe = typeof intelligenceTimeframes[number];

type LooseRecord = Record<string, unknown>;
type Section = { section_id?: string; title?: string; body?: string; uncertainties?: string[] };
type BriefLike = {
  levels?: Array<LooseRecord>;
  long_term_levels?: Array<LooseRecord>;
  scenarios?: Array<LooseRecord>;
  evidence_quality?: LooseRecord;
  audit?: LooseRecord;
  intelligence?: LooseRecord;
};

export type IntelligenceFrame = {
  timeframe: IntelligenceTimeframe;
  role?: string;
  state?: string;
  momentum?: string;
  extension?: string;
  observation?: string;
  localHigh?: number;
  localLow?: number;
  maDistances?: LooseRecord;
  impulse?: LooseRecord;
  pullback?: LooseRecord;
  compression?: LooseRecord;
  volumeStates?: string[];
  providerNarrative?: string;
};

export type IntelligenceCenter = {
  frames: IntelligenceFrame[];
  tactical?: string;
  alignment?: string;
  flow?: string;
  oi?: string;
  oiQuality?: string;
  priceOi?: string;
  volume?: string;
  impulse?: string;
  priceMap: Array<LooseRecord>;
  longTerm: Array<LooseRecord>;
  scenarios: Array<LooseRecord>;
  trigger?: string;
  invalidation?: string;
  auditScore?: unknown;
  conflicts: string[];
  dominantContext?: string;
};

const role: Record<IntelligenceTimeframe, Record<Language, string>> = {
  "15m": { zh: "战术", en: "Tactical" },
  "1H": { zh: "建仓环境", en: "Setup context" },
  "4H": { zh: "主要环境", en: "Primary environment" },
  "1D": { zh: "中期方向", en: "Medium-term direction" },
  "1W": { zh: "长期结构", en: "Long-term structure" },
};

const stateLabels: Record<string, Record<Language, string>> = {
  IMPULSE_UP: { zh: "向上推进", en: "Upward impulse" },
  IMPULSE_DOWN: { zh: "向下推进", en: "Downward impulse" },
  PULLBACK: { zh: "回撤", en: "Pullback" },
  SHALLOW_PULLBACK: { zh: "浅回撤", en: "Shallow pullback" },
  DEEP_PULLBACK: { zh: "深回撤", en: "Deep pullback" },
  BREAKOUT_TEST: { zh: "突破测试", en: "Breakout test" },
  BREAKOUT_ACCEPTANCE: { zh: "突破确认", en: "Breakout acceptance" },
  FAILED_BREAKOUT: { zh: "突破失败", en: "Failed breakout" },
  RETEST: { zh: "回测", en: "Retest" },
  HIGH_LEVEL_CONSOLIDATION: { zh: "高位整理", en: "High-level consolidation" },
  HIGH_LEVEL_COMPRESSION: { zh: "高位收敛", en: "High-level compression" },
  LOW_LEVEL_COMPRESSION: { zh: "低位收敛", en: "Low-level compression" },
  TREND_CONTINUATION: { zh: "趋势延续", en: "Trend continuation" },
  STRUCTURE_WEAKENING: { zh: "结构走弱", en: "Structure weakening" },
  NO_CLEAR_TACTICAL_STATE: { zh: "暂无清晰战术状态", en: "No clear tactical state" },
  MOMENTUM_EXPANDING: { zh: "动量扩张", en: "Momentum expanding" },
  MOMENTUM_COOLING: { zh: "动量降温", en: "Momentum cooling" },
  MOMENTUM_RESET: { zh: "动量重置", en: "Momentum reset" },
  MOMENTUM_REACCELERATING: { zh: "动量再加速", en: "Momentum reaccelerating" },
  PRICE_RESILIENT_MOMENTUM_RESET: { zh: "价格韧性下的动量重置", en: "Price-resilient momentum reset" },
  NORMAL: { zh: "正常", en: "Normal" },
  EXTENDED: { zh: "伸展", en: "Extended" },
  HIGHLY_EXTENDED: { zh: "明显伸展", en: "Highly extended" },
  IMPULSE_VOLUME_EXPANSION: { zh: "推进量能放大", en: "Impulse-volume expansion" },
  POST_IMPULSE_VOLUME_CONTRACTION: { zh: "推进后量能收缩", en: "Post-impulse volume contraction" },
  HIGH_VOLUME_REJECTION: { zh: "高量拒绝", en: "High-volume rejection" },
  BREAKOUT_VOLUME_EXPANSION: { zh: "突破量能放大", en: "Breakout-volume expansion" },
  PRICE_UP_OI_UP: { zh: "价格上涨 · OI 增加", en: "Price up · OI up" },
  PRICE_UP_OI_DOWN: { zh: "价格上涨 · OI 下降", en: "Price up · OI down" },
  PRICE_DOWN_OI_UP: { zh: "价格下跌 · OI 增加", en: "Price down · OI up" },
  PRICE_DOWN_OI_DOWN: { zh: "价格下跌 · OI 下降", en: "Price down · OI down" },
  FLOW_COMPLETE: { zh: "订单流完整", en: "Flow complete" },
  FLOW_PARTIAL_USABLE: { zh: "订单流部分可用", en: "Flow partially usable" },
  FLOW_UNAVAILABLE: { zh: "订单流暂不可用", en: "Flow unavailable" },
  VALID: { zh: "可用", en: "Available" },
  AVAILABLE: { zh: "可用", en: "Available" },
  MISSING: { zh: "不可用", en: "Unavailable" },
  INSUFFICIENT_DATA: { zh: "数据不足", en: "Insufficient data" },
  ALIGNED: { zh: "多周期同向", en: "Aligned" },
  MIXED: { zh: "多周期混合", en: "Mixed" },
  CONFLICTED: { zh: "多周期分歧", en: "Conflicted" },
  HIGHER_TIMEFRAME_EXTENSION: { zh: "高周期仍伸展", en: "Higher-timeframe extension" },
  TACTICAL_WEAKNESS_INSIDE_HIGHER_TIMEFRAME_EXTENSION: { zh: "短线走弱，但高周期仍处于伸展", en: "Tactical weakness inside higher-timeframe extension" },
  SETUP_COOLING_WHILE_HIGHER_TIMEFRAMES_EXTENDED: { zh: "小时级动量降温，但高周期仍处于伸展", en: "Setup cooling while higher timeframes remain extended" },
  LONG_TERM_RECOVERY: { zh: "长期修复", en: "Long-term recovery" },
  LONG_TERM_PULLBACK: { zh: "长期回撤", en: "Long-term pullback" },
  LONG_TERM_TREND: { zh: "长期趋势", en: "Long-term trend" },
};

function record(value: unknown): LooseRecord { return value && typeof value === "object" && !Array.isArray(value) ? value as LooseRecord : {}; }
function text(value: unknown, language: Language): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  return stateLabels[value]?.[language] || localizeWorkspaceNarrative(value, language);
}
function section(sections: Section[], id: string): string | undefined {
  return sections.find(item => item.section_id === id)?.body || undefined;
}
function field(value: LooseRecord, ...names: string[]): unknown {
  for (const name of names) if (value[name] != null && value[name] !== "") return value[name];
  return undefined;
}
function numeric(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
export function localizeAiRule(value: unknown, language: Language): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const known: Record<string, Record<Language, string>> = {
    "confirmed close above the nearest active resistance or impulse extreme": { zh: "确认收于最近有效压力位或当前脉冲高点上方", en: "Confirmed close above the nearest active resistance or impulse extreme" },
    "two confirmed 15m closes violate the referenced boundary": { zh: "连续两根已确认的15分钟K线收于该战术边界下方", en: "Two confirmed 15-minute closes violate the referenced tactical boundary" },
    "price enters the flipped breakout/retest zone": { zh: "价格进入已翻转的突破／回测区域", en: "Price enters the flipped breakout/retest zone" },
    "two confirmed closes return inside the prior range below the core breakout zone": { zh: "连续两根已确认K线重新收回核心突破区下方的原区间", en: "Two confirmed closes return inside the prior range below the core breakout zone" },
    "a confirmed retest fails to reclaim the referenced zone with weakening CVD": { zh: "确认回测未能重新站回该区域，且 CVD 走弱", en: "A confirmed retest fails to reclaim the referenced zone with weakening CVD" },
    "volume recovers and CVD turns positive on the confirming close": { zh: "确认收盘时成交量回升，且 CVD 转为正向", en: "Volume recovers and CVD turns positive on the confirming close" },
    "two confirmed closes hold the referenced zone with contracting sell volume": { zh: "连续两根已确认K线守住该区域，且卖出量能收缩", en: "Two confirmed closes hold the referenced zone with contracting sell volume" },
    "OI must not expand aggressively while price loses the zone": { zh: "价格失守该区域时，OI 不应激进扩张", en: "OI must not expand aggressively while price loses the zone" },
    "funding/basis must not show extreme leverage expansion opposing the path": { zh: "资金费率／基差不应出现与该路径相反的极端杠杆扩张", en: "Funding/basis must not show extreme leverage expansion opposing the path" },
    "volume regime must agree with the trigger, contraction on retest and expansion on continuation/failure": { zh: "量能状态需与触发一致：回测时收缩，延续或失败时扩张", en: "Volume regime must agree with the trigger, contracting on retest and expanding on continuation or failure" },
  };
  if (known[value]) return known[value][language];
  const fallback = localizeWorkspaceNarrative(value, language);
  return language === "zh" && /[A-Za-z]{4,}/.test(fallback) ? "依据已注册的审计条件" : fallback;
}
function weeklyPresentationState(frame: LooseRecord): string | undefined {
  const state = String(field(frame, "state", "structure_state", "tactical_state") ?? field(record(frame.tactical), "state") ?? "");
  if (!state) return undefined;
  if (["DEEP_PULLBACK", "PULLBACK", "SHALLOW_PULLBACK"].includes(state)) {
    const distances = record(frame.ma_distances_pct);
    const major = [numeric(distances.ma60), numeric(distances.ma200)].filter((value): value is number => value != null);
    return major.length >= 1 && major.every(value => value >= 0) ? "LONG_TERM_RECOVERY" : "LONG_TERM_PULLBACK";
  }
  if (["TREND_CONTINUATION", "IMPULSE_UP"].includes(state)) return "LONG_TERM_TREND";
  return state;
}

/**
 * A display-only projection of registered deterministic facts.  It never
 * derives market claims; absent fields simply remain absent for old reports.
 */
export function intelligenceCenter(input: unknown, sections: Section[] = [], language: Language): IntelligenceCenter {
  const brief = record(input) as BriefLike;
  const intelligence = record(brief.intelligence);
  const frameSource = record(intelligence.timeframes);
  const frames = intelligenceTimeframes.map(timeframe => {
    const frame = record(frameSource[timeframe]);
    const momentum = record(frame.momentum);
    const tactical = record(frame.tactical);
    const volume = record(frame.volume);
    const fallback = section(sections, `TF_${timeframe.toUpperCase()}`);
    // Keep the summary card deterministic and compact. The audited provider
    // prose is rendered below as the explanatory narrative, not repeated here.
    const observation = text(field(frame, "observation", "summary"), language)
      || text(field(volume, "state"), language);
    return {
      timeframe,
      role: text(field(frame, "role"), language) || role[timeframe][language],
      state: text(timeframe === "1W" ? weeklyPresentationState(frame) : field(frame, "state", "structure_state", "tactical_state") ?? field(tactical, "state"), language),
      momentum: text(field(momentum, "state") ?? field(frame, "momentum_state"), language),
      extension: text(field(frame, "extension_state"), language),
      // Volume is folded into the observation only when the API supplied no
      // narrative, keeping individual cards compact and non-duplicative.
      observation,
      localHigh: numeric(frame.local_high),
      localLow: numeric(frame.local_low),
      maDistances: record(frame.ma_distances_pct),
      impulse: record(tactical.impulse),
      pullback: record(tactical.pullback),
      compression: record(tactical.compression),
      volumeStates: Array.isArray(volume.states) ? volume.states.map(String) : [],
      providerNarrative: language === "zh" ? fallback : undefined,
    };
  });
  const tactical = record(intelligence.tactical);
  const tacticalImpulse = record(tactical.impulse);
  const volume = record(intelligence.volume);
  const flowOi = record(intelligence.flow_oi);
  const priceMap = Array.isArray(intelligence.price_map) ? intelligence.price_map.map(record) : presentAiLevels(brief.levels || []);
  const longTerm = Array.isArray(intelligence.long_term_levels) ? intelligence.long_term_levels.map(record) : presentAiLevels(brief.long_term_levels || []);
  const scenarios = Array.isArray(intelligence.scenarios) ? intelligence.scenarios.map(record) : (brief.scenarios || []);
  return {
    frames,
    tactical: text(field(tactical, "state") ?? intelligence.tactical_state, language),
    alignment: text(field(intelligence, "alignment", "dominant_context", "multi_timeframe_state"), language),
    flow: text(field(flowOi, "flow_state", "flow_quality", "flow_confirmation") ?? field(record(brief.evidence_quality), "flow_quality"), language),
    oi: text(field(flowOi, "oi_state"), language),
    oiQuality: text(field(flowOi, "oi_quality"), language),
    priceOi: text(field(flowOi, "price_oi_state", "price_oi_relation"), language),
    volume: text(field(volume, "state") ?? intelligence.volume_state, language),
    impulse: text(field(tacticalImpulse, "state") ?? intelligence.impulse_state, language),
    priceMap,
    longTerm,
    scenarios,
    trigger: localizeAiRule(field(tactical, "trigger") ?? intelligence.trigger, language),
    invalidation: localizeAiRule(field(tactical, "invalidation") ?? intelligence.invalidation, language),
    auditScore: record(brief.audit).overall_score,
    conflicts: Array.isArray(intelligence.conflicts) ? intelligence.conflicts.map(String) : [],
    dominantContext: text(field(intelligence, "dominant_context"), language),
  };
}

export function visibleFrame(frame: IntelligenceFrame): boolean {
  return Boolean(frame.state || frame.momentum || frame.extension || frame.observation);
}

function price(value: number | undefined): string | undefined {
  return value == null ? undefined : Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2);
}
function signedRelation(distances: LooseRecord | undefined, language: Language): string | undefined {
  if (!distances) return undefined;
  const ema = numeric(distances.ema20 ?? distances.ma20);
  const ma60 = numeric(distances.ma60);
  const above = [ema, ma60].filter((value): value is number => value != null).filter(value => value >= 0).length;
  const below = [ema, ma60].filter((value): value is number => value != null).filter(value => value < 0).length;
  if (above >= 2) return language === "zh" ? "价格仍位于 EMA20 与 MA60 上方。" : "Price remains above EMA20 and MA60.";
  if (below >= 2) return language === "zh" ? "价格仍位于 EMA20 与 MA60 下方。" : "Price remains below EMA20 and MA60.";
  if (above || below) return language === "zh" ? "价格与短中期均线关系存在分歧。" : "Price has a mixed relationship with short- and medium-term averages.";
  return undefined;
}

export function frameNarrative(frame: IntelligenceFrame, language: Language): string {
  const sentences: string[] = [];
  const subject = language === "zh" ? `${frame.timeframe} ${frame.role || ""}` : `${frame.timeframe} ${frame.role || ""}`;
  if (frame.state) sentences.push(language === "zh" ? `${subject}当前为${frame.state}。` : `${subject} is currently in ${frame.state.toLowerCase()}.`);
  const relation = signedRelation(frame.maDistances, language);
  if (relation) sentences.push(relation);
  if (frame.timeframe === "15m" && (frame.localLow != null || frame.localHigh != null)) {
    const parts = [
      frame.localLow != null && (language === "zh" ? `局部支撑参考 ${price(frame.localLow)}` : `local support reference ${price(frame.localLow)}`),
      frame.localHigh != null && (language === "zh" ? `局部压力参考 ${price(frame.localHigh)}` : `local resistance reference ${price(frame.localHigh)}`),
    ].filter(Boolean);
    if (parts.length) sentences.push(language === "zh" ? `${parts.join("；")}。` : `${parts.join("; ")}.`);
  }
  if (frame.momentum) sentences.push(language === "zh" ? `动量状态为${frame.momentum}，用于判断该周期的推进是否延续。` : `Momentum is ${frame.momentum.toLowerCase()}, which frames whether this timeframe's move is still developing.`);
  if (frame.extension && frame.extension !== (language === "zh" ? "正常" : "Normal")) sentences.push(language === "zh" ? `相对均线的伸展状态为${frame.extension}，需要与更低周期的触发条件一起观察。` : `Its moving-average extension is ${frame.extension.toLowerCase()}, so it should be read alongside lower-timeframe triggers.`);
  const volume = frame.volumeStates?.map(value => text(value, language)).filter(Boolean) || [];
  if (volume.length) sentences.push(language === "zh" ? `量能观察为${volume.join("、")}。` : `Volume observations are ${volume.join(" and ").toLowerCase()}.`);
  // Provider prose is already audited. It enriches the deterministic account
  // in Chinese; English remains deterministic so a Chinese source report never
  // leaks fragments into the English interface.
  if (frame.providerNarrative) sentences.push(frame.providerNarrative);
  return sentences.join(" ");
}

export function crossTimeframeNarrative(center: IntelligenceCenter, language: Language): string {
  const state = center.alignment || (language === "zh" ? "多周期混合" : "Mixed");
  const parts = language === "zh"
    ? [`多周期当前呈现${state}。`, center.dominantContext ? `主导背景为${center.dominantContext}。` : ""]
    : [`The multi-timeframe view is ${state.toLowerCase()}.`, center.dominantContext ? `The dominant context is ${center.dominantContext.toLowerCase()}.` : ""];
  for (const conflict of center.conflicts) {
    const label = text(conflict, language);
    if (label) parts.push(language === "zh" ? `核心矛盾：${label}。` : `Key tension: ${label}.`);
  }
  return parts.filter(Boolean).join(" ");
}

export function derivativesNarrative(center: IntelligenceCenter, language: Language): string {
  const cvd = center.flow || (language === "zh" ? "CVD 状态未提供" : "CVD status was not provided");
  const oi = [center.oiQuality, center.oi].filter(Boolean).join(language === "zh" ? " · " : " · ") || (language === "zh" ? "OI 状态未提供" : "OI status was not provided");
  const relation = center.priceOi || (language === "zh" ? "价格 × OI 关系未提供" : "Price × OI relation was not provided");
  return language === "zh"
    ? `CVD：${cvd}，其不可用或覆盖不足时不参与方向确认。OI：${oi}。价格 × OI：${relation}；这仅反映仓位变化的代理关系，不能单独证明多头或空头主导。`
    : `CVD: ${cvd}; unavailable or insufficient coverage is excluded from directional confirmation. OI: ${oi}. Price × OI: ${relation}; this is a positioning proxy and cannot alone prove long or short control.`;
}

export function volumeImpulseNarrative(center: IntelligenceCenter, language: Language): string {
  const impulse = center.impulse || (language === "zh" ? "未形成可审计脉冲结论" : "No auditable impulse conclusion");
  const volume = center.volume || (language === "zh" ? "量能状态未提供" : "Volume state was not provided");
  return language === "zh"
    ? `最新推进：${impulse}。量能：${volume}。该组合用于判断推进后的跟随或消化，不单独推断参与者身份。`
    : `Latest impulse: ${impulse}. Volume: ${volume}. Together they describe follow-through or digestion after the move, not participant identity.`;
}

export function levelLabel(level: LooseRecord, language: Language): string {
  const value = String(level.asserted_role || level.role || level.level_type || "");
  const labels: Record<string, Record<Language, string>> = {
    SUPPORT: { zh: "支撑", en: "Support" }, RESISTANCE: { zh: "压力", en: "Resistance" },
    TACTICAL_SUPPORT: { zh: "短线支撑", en: "Tactical support" }, TACTICAL_RESISTANCE: { zh: "短线压力", en: "Tactical resistance" },
    MEDIUM_SUPPORT: { zh: "中期支撑", en: "Medium support" }, MEDIUM_RESISTANCE: { zh: "中期压力", en: "Medium resistance" },
    LONG_TERM_REFERENCE: { zh: "长期参考", en: "Long-term reference" }, ROUND_LEVEL: { zh: "整数关口", en: "Round level" },
  };
  return labels[value]?.[language] || (language === "zh" ? "关键位置" : "Key level");
}

export function scenarioLabel(value: LooseRecord, language: Language): string {
  return workspaceScenarioLabel(value.scenario_type || value.type || value.direction || "", language);
}
