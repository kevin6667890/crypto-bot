export type MarketDataProvenance = "CANONICAL" | "BROWSER_FALLBACK" | "DEMO_FALLBACK";

export type MarketProvenanceInput = {
  provenance: MarketDataProvenance;
  asOf: string;
  fallbackReason?: string;
  language?: "zh" | "en";
  pendingCanonical?: boolean;
};

export function marketProvenancePresentation(input: MarketProvenanceInput) {
  const zh = input.language === "zh";
  if (input.pendingCanonical) {
    return {
      tone: "loading" as const,
      label: zh ? "正在加载标准数据" : "Loading canonical data",
      detail: zh ? "正在获取已确认市场事实" : "Fetching confirmed market facts",
    };
  }
  if (input.provenance === "CANONICAL") {
    return {
      tone: "canonical" as const,
      label: zh ? "标准 · 已确认" : "Canonical · Confirmed",
      detail: zh ? `截至 ${input.asOf}` : `As of ${input.asOf}`,
    };
  }
  const source = input.provenance === "BROWSER_FALLBACK"
    ? (zh ? "浏览器直连 OKX" : "Browser direct OKX")
    : (zh ? "演示降级数据" : "Demo fallback");
  const reason = input.fallbackReason || (zh ? "Canonical 后端不可用" : "Canonical backend unavailable");
  return {
    tone: "degraded" as const,
    label: zh ? "降级数据" : "Degraded data",
    detail: zh ? `${source} · 截至 ${input.asOf} · ${reason}` : `${source} · As of ${input.asOf} · ${reason}`,
  };
}
