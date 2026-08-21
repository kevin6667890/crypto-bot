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
};

export type IntelligenceCenter = {
  frames: IntelligenceFrame[];
  tactical?: string;
  alignment?: string;
  flow?: string;
  oi?: string;
  priceOi?: string;
  volume?: string;
  impulse?: string;
  priceMap: Array<LooseRecord>;
  longTerm: Array<LooseRecord>;
  scenarios: Array<LooseRecord>;
  trigger?: string;
  invalidation?: string;
  auditScore?: unknown;
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
    const observation = text(field(frame, "observation", "summary") ?? fallback, language)
      || text(field(volume, "state"), language);
    return {
      timeframe,
      role: text(field(frame, "role"), language) || role[timeframe][language],
      state: text(field(frame, "state", "structure_state", "tactical_state") ?? field(tactical, "state"), language),
      momentum: text(field(momentum, "state") ?? field(frame, "momentum_state"), language),
      extension: text(field(frame, "extension_state"), language),
      // Volume is folded into the observation only when the API supplied no
      // narrative, keeping individual cards compact and non-duplicative.
      observation,
    };
  });
  const tactical = record(intelligence.tactical);
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
    priceOi: text(field(flowOi, "price_oi_state", "price_oi_relation"), language),
    volume: text(field(volume, "state") ?? intelligence.volume_state, language),
    impulse: text(field(tactical, "impulse") ?? intelligence.impulse_state, language),
    priceMap,
    longTerm,
    scenarios,
    trigger: text(field(tactical, "trigger") ?? intelligence.trigger, language),
    invalidation: text(field(tactical, "invalidation") ?? intelligence.invalidation, language),
    auditScore: record(brief.audit).overall_score,
  };
}

export function visibleFrame(frame: IntelligenceFrame): boolean {
  return Boolean(frame.state || frame.momentum || frame.extension || frame.observation);
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
