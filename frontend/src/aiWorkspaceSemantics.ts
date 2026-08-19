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
