import type { Language } from "../i18n";
import type { ThesisCondition } from "../thesis/types";
import type { CurrentCondition, CurrentEvaluation, EvaluationStatus } from "./types";

const operators: Record<string, string> = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=" };
const zhLabels: Record<string, string> = {
  VOLUME_RATIO: "成交量比率", PRICE_ABOVE_MA200: "价格高于 MA200", PRICE_BELOW_MA200: "价格低于 MA200",
  WATCHING: "观察中", MATCHING: "匹配", NOT_MATCHING: "不匹配", PARTIAL: "部分可用", STALE: "已过期",
  BLOCKED: "暂不可评估", BLOCKED_VERSION_MISMATCH: "版本不匹配，无法评估",
  TRUE: "满足", FALSE: "不满足", UNKNOWN: "未知", REQUIRED: "必要", OPTIONAL: "可选",
  AVAILABLE: "可用", MISSING: "缺失", VOLATILITY_COMPRESSION: "波动压缩", VOLATILITY_EXPANSION: "波动扩张",
  TREND_UP: "上升趋势", TREND_DOWN: "下降趋势", RANGE: "区间震荡", CONFLICTED: "证据分歧",
  RANGE_LOW_VOLATILITY: "低波动区间", RANGE_HIGH_VOLATILITY: "高波动区间",
  TRANSITION_UP: "向上过渡", TRANSITION_DOWN: "向下过渡", TRANSITION_MIXED: "混合过渡",
  HTF_UPTREND_CONTINUATION: "高周期上涨延续", HTF_DOWNTREND_CONTINUATION: "高周期下跌延续",
  HTF_UPTREND_PULLBACK: "高周期上涨回撤", HTF_DOWNTREND_BOUNCE: "高周期下跌反弹",
  MAJOR_SUPPORT_TEST: "重要支撑测试", MAJOR_RESISTANCE_TEST: "重要压力测试",
  RANGE_ROTATION: "区间轮动", BREAKOUT_DEVELOPING: "突破正在形成", BREAKDOWN_DEVELOPING: "跌破正在形成",
  FAILED_BREAKOUT_DEVELOPING: "突破失败正在形成", VOLATILITY_TRANSITION: "波动状态过渡",
  NO_CLEAR_STATE: "暂无明确状态", INSUFFICIENT_DATA: "数据不足",
};
export function formatCode(value: string | null | undefined, language: Language = "en") {
  if (!value) return "—";
  return language === "zh" ? zhLabels[value.toUpperCase()] || value.replace(/_/g, " ") : value.replace(/_/g, " ");
}
export function conditionExpression(condition: ThesisCondition, language: Language = "en") {
  return `${formatCode(condition.feature, language)} ${operators[condition.operator] || condition.operator} ${formatObserved(condition.value, language)}`;
}
export function formatObserved(value: number | boolean | null, language: Language = "en") {
  if (value === null) return "—";
  if (typeof value === "boolean") return language === "zh" ? (value ? "是" : "否") : (value ? "true" : "false");
  return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}
export function formatStatus(status: EvaluationStatus | null | undefined, language: Language = "en") { return formatCode(status || "WATCHING", language); }
export function formatSemanticState(state: string | null | undefined, language: Language = "en") { return formatCode(state, language); }
export function statusTone(status: EvaluationStatus | null | undefined) {
  if (status === "MATCHING") return "matching";
  if (status === "NOT_MATCHING") return "not-matching";
  if (status === "STALE" || status === "PARTIAL") return "warning";
  if (status?.startsWith("BLOCKED")) return "blocked";
  return "neutral";
}
export function conditionTone(state: CurrentCondition["state"]) {
  return state === "TRUE" ? "matching" : state === "FALSE" ? "not-matching" : "unknown";
}
export function requiredConditionSummary(evaluation: CurrentEvaluation | null | undefined, language: Language) {
  if (!evaluation) return language === "zh" ? "等待首次合格评估" : "Awaiting the first qualified evaluation";
  if (evaluation.overall_status?.startsWith("BLOCKED")) {
    return language === "zh"
      ? `${evaluation.required_condition_count} 项必要条件当前无法评估`
      : `${evaluation.required_condition_count} required conditions currently unavailable`;
  }
  if (evaluation.tree_result) {
    const leaves = evaluation.leaf_results || evaluation.conditions;
    const trueCount = leaves.filter((condition) => condition.state === "TRUE").length;
    const unknown = leaves.filter((condition) => condition.state === "UNKNOWN").length;
    return language === "zh"
      ? `表达式为 ${evaluation.expression_state || "UNKNOWN"}；${trueCount} 项为真，${unknown} 项未知（共 ${leaves.length} 项）`
      : `Expression is ${evaluation.expression_state || "UNKNOWN"}; ${trueCount} true, ${unknown} unknown of ${leaves.length} leaves`;
  }
  const required = evaluation.conditions.filter((condition) => condition.requirement === "REQUIRED");
  const unknown = required.filter((condition) => condition.state === "UNKNOWN").length;
  if (unknown) {
    return language === "zh"
      ? `${evaluation.required_match_count} 项为真，${unknown} 项未知（共 ${evaluation.required_condition_count} 项必要条件）`
      : `${evaluation.required_match_count} true, ${unknown} unknown of ${evaluation.required_condition_count} required conditions`;
  }
  return language === "zh"
    ? `${evaluation.required_match_count} / ${evaluation.required_condition_count} 项必要条件匹配`
    : `${evaluation.required_match_count} / ${evaluation.required_condition_count} required conditions match`;
}
export function formatUtc(value: string | number | null | undefined, language: Language) {
  if (value === null || value === undefined || value === "") return "—";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(date) + " UTC";
}
