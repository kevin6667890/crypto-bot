import type { Language } from "../i18n";
import type { ThesisCondition } from "../thesis/types";
import type { CurrentCondition, CurrentEvaluation, EvaluationStatus } from "./types";

const operators: Record<string, string> = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=" };
export function conditionExpression(condition: ThesisCondition) {
  return `${condition.feature.replace(/_/g, " ")} ${operators[condition.operator] || condition.operator} ${String(condition.value)}`;
}
export function formatObserved(value: number | boolean | null) {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}
export function formatStatus(status: EvaluationStatus | null | undefined) { return status ? status.replace(/_/g, " ") : "WATCHING"; }
export function formatSemanticState(state: string | null | undefined) { return state ? state.replace(/_/g, " ") : "—"; }
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
