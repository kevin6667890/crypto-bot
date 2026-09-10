export type HistoricalSummary = {
  independent_event_count?: number | null;
  sample_quality?: string | null;
};

export function baselineEventLabel(summary: HistoricalSummary, zh: boolean): string {
  const count = summary.independent_event_count;
  if (typeof count === "number" && Number.isFinite(count)) {
    return `${count.toLocaleString()} ${zh ? "个独立事件" : "independent events"}`;
  }
  return zh ? "已保存的不可变历史基线" : "Saved immutable historical baseline";
}

export function baselineSampleLabel(summary: HistoricalSummary, zh: boolean): string | null {
  if (typeof summary.sample_quality === "string" && summary.sample_quality.trim()) {
    return `${summary.sample_quality} ${zh ? "样本" : "sample"}`;
  }
  return null;
}
