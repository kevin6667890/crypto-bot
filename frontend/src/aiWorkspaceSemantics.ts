import type { ReactNode } from "react";

const scenarioLabel: Record<string, string> = {
  BEARISH_CONTINUATION: "偏空延续", BULLISH_CONTINUATION: "偏多延续",
  NORMAL_RETEST: "正常回踩", FAILED_BREAKOUT: "突破失败",
  NORMAL_BEARISH_RETEST: "正常反抽", FAILED_BREAKDOWN: "跌破失败",
  RANGE: "区间震荡", WAIT: "混合观察", MIXED: "混合观察",
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

export const workspaceScenarioLabel = (value: unknown) => scenarioLabel[String(value || "")] || "主要场景";
export const presentAiLevels = <T extends object>(levels: T[] = []) => levels.filter(item => isPresent((item as { representative_price?: unknown }).representative_price));

export function compactAiSummary(value: unknown, maxLength = 220): string {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) return "暂无核心摘要。";
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
