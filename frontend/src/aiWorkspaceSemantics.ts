import type { ReactNode } from "react";
import { translateKnownEnum, type UiLanguage } from "./aiMarketAnalysis/enumTranslations";

const scenarioLabel: Record<string, Record<UiLanguage, string>> = {
  BEARISH_CONTINUATION: { zh: "偏空延续", en: "Bearish continuation" },
  BULLISH_CONTINUATION: { zh: "偏多延续", en: "Bullish continuation" },
  NORMAL_RETEST: { zh: "正常回踩", en: "Normal retest" },
  FAILED_BREAKOUT: { zh: "突破失败", en: "Failed breakout" },
  NORMAL_BEARISH_RETEST: { zh: "正常反抽", en: "Normal bearish retest" },
  FAILED_BREAKDOWN: { zh: "跌破失败", en: "Failed breakdown" },
  RANGE: { zh: "区间震荡", en: "Range" },
  WAIT: { zh: "混合观察", en: "Mixed watch" },
  MIXED: { zh: "混合观察", en: "Mixed watch" },
};
const absentText = new Set(["", "-", "—", "unknown", "unavailable", "not available", "not applicable", "n/a", "null", "none"]);

export function isPresent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return !absentText.has(value.trim().toLowerCase());
  if (Array.isArray(value)) return value.some(isPresent);
  return true;
}

export function renderIfPresent<T>(value: T, render: (present: T) => ReactNode) {
  return isPresent(value) ? render(value) : null;
}

export const workspaceScenarioLabel = (value: unknown, language: UiLanguage = "zh") =>
  scenarioLabel[String(value || "")]?.[language]
  || (language === "zh" ? "主要场景" : "Primary scenario");
export const presentAiLevels = <T extends object>(levels: T[] = []) => levels.filter(item => isPresent((item as { representative_price?: unknown }).representative_price));

export function coverageMatrixRows(quality: any, language: UiLanguage = "zh") {
  const flow = quality?.flow_quality || "FLOW_UNAVAILABLE";
  const labels = language === "zh" ? {
    core: "\u6838\u5fc3\u5e02\u573a\u6570\u636e", flow: "\u8ba2\u5355\u6d41", long: "\u957f\u671f\u7ed3\u6784", macro: "\u5b8f\u89c2\u80cc\u666f",
    complete: "\u5b8c\u6574", usable: "\u53ef\u7528", partial: "\u90e8\u5206", unavailable: "\u4e0d\u53ef\u7528", limited: "\u6709\u9650", notIncluded: "\u672c\u8f6e\u672a\u7eb3\u5165",
  } : { core: "Core market data", flow: "Order flow", long: "Long-term structure", macro: "Macro context", complete: "Complete", usable: "Usable", partial: "Partial", unavailable: "Unavailable", limited: "Limited", notIncluded: "Not included" };
  return [
    { key: "core", label: labels.core, state: quality?.core_quality === "COMPLETE" ? "complete" : quality?.core_quality === "USABLE" ? "partial" : "warning", text: quality?.core_quality === "COMPLETE" ? labels.complete : quality?.core_quality === "USABLE" ? labels.usable : labels.unavailable },
    { key: "flow", label: labels.flow, state: flow === "FLOW_COMPLETE" ? "complete" : flow === "FLOW_PARTIAL_USABLE" ? "partial" : "muted", text: flow === "FLOW_COMPLETE" ? labels.complete : flow === "FLOW_PARTIAL_USABLE" ? labels.partial : labels.unavailable },
    { key: "long", label: labels.long, state: quality?.long_term_quality === "COMPLETE" ? "complete" : "partial", text: quality?.long_term_quality === "COMPLETE" ? labels.complete : labels.limited },
    { key: "macro", label: labels.macro, state: quality?.macro_quality === "AVAILABLE" ? "complete" : quality?.macro_quality === "STALE" ? "partial" : "neutral", text: quality?.macro_quality === "AVAILABLE" ? labels.complete : quality?.macro_quality === "STALE" ? labels.limited : labels.notIncluded },
  ];
}

export function localizeWorkspaceNarrative(value: unknown, language: UiLanguage): string {
  const text = typeof value === "string" ? value : "";
  return text.replace(/\b[A-Z]+(?:_[A-Z]+)+\b/g, enumValue => {
    const translated = translateKnownEnum(enumValue, language);
    if (translated !== enumValue) return translated;
    return enumValue.toLowerCase().split("_").map((word, index) => index ? word : word[0].toUpperCase() + word.slice(1)).join(" ");
  });
}

export function compactAiSummary(value: unknown, maxLength = 220, language: UiLanguage = "zh"): string {
  const text = localizeWorkspaceNarrative(value, language).replace(/\s+/g, " ").trim();
  if (!text) return language === "zh" ? "暂无核心摘要。" : "No concise summary is available.";
  const sentences = text.match(/[^。！？.!?]+[。！？.!?]?/g) || [text];
  let summary = "";
  for (const sentence of sentences.slice(0, 2)) {
    if (summary && summary.length + sentence.length > maxLength) break;
    summary += sentence;
  }
  if (!summary) summary = text.slice(0, maxLength);
  if (summary.length > maxLength) summary = summary.slice(0, maxLength);
  return summary.length < text.length ? `${summary.replace(/[，,;；\s]+$/, "")}…` : summary;
}
