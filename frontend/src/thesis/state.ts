import type { PartialThesisCondition, ThesisCapabilities, ThesisSpecV1 } from "./types";

export type EditableDefinition = {
  instrument: string; timeframe: string; required: PartialThesisCondition[];
  optional: PartialThesisCondition[]; horizons: string[];
};

export function canRunDefinition(value: EditableDefinition, capabilities: ThesisCapabilities | null): boolean {
  if (!capabilities || !capabilities.instruments.includes(value.instrument) || !capabilities.timeframes.includes(value.timeframe)) return false;
  if (!value.required.length || !value.horizons.length || value.required.length + value.optional.length > 5) return false;
  return [...value.required, ...value.optional].every((condition) => {
    const feature = capabilities.features.find((item) => item.code === condition.feature);
    if (!feature || feature.availability !== "AVAILABLE" || !feature.operators.includes(condition.operator || "")) return false;
    if (feature.value_type === "boolean") return typeof condition.value === "boolean";
    return typeof condition.value === "number" && Number.isFinite(condition.value)
      && (feature.bounds.minimum === null || condition.value >= feature.bounds.minimum)
      && (feature.bounds.maximum === null || condition.value <= feature.bounds.maximum);
  });
}

export function executableSpec(value: EditableDefinition, capabilities: ThesisCapabilities, nowSeconds = Math.floor(Date.now() / 1000)): ThesisSpecV1 {
  if (!canRunDefinition(value, capabilities)) throw new Error("INVALID_EDITABLE_THESIS");
  return {
    version: "thesis-spec-v1", instrument: value.instrument, timeframe: value.timeframe,
    required_conditions: value.required as ThesisSpecV1["required_conditions"],
    optional_conditions: value.optional as ThesisSpecV1["optional_conditions"],
    forward_horizons: value.horizons, requested_as_of: nowSeconds,
    event_independence: { version: "event-independence-max-horizon-v1", exclude_overlapping_forward_windows: true },
    metadata: { source: "test-an-idea-v1" },
  };
}

export function formatFraction(value: number | null, language: "en" | "zh"): string {
  return value === null ? "—" : new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", { style: "percent", maximumFractionDigits: 2 }).format(value);
}

export function formatTimestamp(value: number | null, language: "en" | "zh"): string {
  return value === null ? "—" : new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", { year: "numeric", month: "short", day: "2-digit", timeZone: "UTC" }).format(new Date(value * 1000));
}
