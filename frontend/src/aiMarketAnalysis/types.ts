export const instruments = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"] as const;
export const modes = ["QUICK", "FULL", "POSITION_AWARE"] as const;
export const eligibilities = ["AUDIT_PENDING", "AUDIT_PASSED_SHADOW_ONLY", "AUDIT_FAILED", "AUDIT_ERROR", "AUDIT_NOT_FOUND", "AUDIT_SCHEMA_UPGRADE_REQUIRED"] as const;
export const freshnessStates = ["CURRENT", "AGING", "STALE", "SUPERSEDED", "UNKNOWN"] as const;
export type Instrument = typeof instruments[number];
export type ReportMode = typeof modes[number];
export type Eligibility = typeof eligibilities[number];
export type Freshness = typeof freshnessStates[number];

export type Fact = { fact_id: string; category?: string; label?: string; display_value?: unknown; value?: unknown; unit?: string | null; timestamp?: string | number; quality?: string; source?: string; context_pointer?: string };
export type Section = { section_id: string; title: string; body: string; fact_refs: string[]; level_refs: string[]; scenario_refs: string[]; macro_refs: string[]; position_refs: string[]; uncertainties: string[] };
export type Presentation = {
  presentation_schema_version: "ai-market-presentation-v1"; presentation_id: string;
  instrument: Instrument; mode: ReportMode; language: "zh-CN" | "en";
  report_id: string; request_id: string; context_id: string; registry_snapshot_id?: string | null; audit_id?: string | null;
  report_schema_version?: string; audit_schema_version?: string; decision_time?: string; latest_confirmed_market_time?: string | number;
  generated_at?: string; audited_at?: string; eligibility: Eligibility;
  freshness: { status: Freshness; policy_version: string; confirmed_15m_bars_behind?: number | null; current_market_time?: number | null; report_market_time?: number | null; quality?: string };
  latest_generated: { report_id: string; request_id?: string; eligibility: Eligibility; queue_status?: string | null; decision_time?: string | null };
  report: null | { schema_version: string; headline: string; market_phase: string; directional_bias: string; confidence: string; sections: Section[]; key_levels: Record<string, unknown>[]; scenarios: Record<string, unknown>[]; data_warnings: string[]; source_versions: Record<string, string>; model?: string; prompt_version?: string; language: string };
  audit_summary: null | { status: string; overall_score?: number; promotion_eligible: boolean; ratios: Record<string, number | boolean>; hard_failures: string[]; hard_failure_count: number; warnings: string[]; policy_version?: string };
  referenced_facts: Fact[]; referenced_levels: Record<string, unknown>[]; referenced_scenarios: Record<string, unknown>[]; referenced_macro: Record<string, unknown>[];
  position_summary: null | Record<string, unknown>; data_warnings: string[]; health_summary: Record<string, unknown>; source_versions: Record<string, string>; presentation_hash?: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const oneOf = <T extends readonly string[]>(value: unknown, values: T): value is T[number] => typeof value === "string" && values.includes(value);
export function parsePresentation(value: unknown): Presentation {
  if (!isRecord(value) || value.presentation_schema_version !== "ai-market-presentation-v1" || typeof value.presentation_id !== "string"
      || !oneOf(value.instrument, instruments) || !oneOf(value.mode, modes) || !oneOf(value.eligibility, eligibilities)
      || !isRecord(value.freshness) || !oneOf(value.freshness.status, freshnessStates)
      || !Array.isArray(value.referenced_facts) || !Array.isArray(value.data_warnings)
      || !(value.report === null || isRecord(value.report))) throw new Error("INVALID_PRESENTATION_CONTRACT");
  if (value.report && value.eligibility !== "AUDIT_PASSED_SHADOW_ONLY") throw new Error("UNAUDITED_REPORT_BODY_REJECTED");
  if (value.report && value.report.context_id !== value.context_id) throw new Error("PRESENTATION_CONTEXT_MISMATCH");
  return value as unknown as Presentation;
}

export function isSafeHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try { const url = new URL(value); return url.protocol === "http:" || url.protocol === "https:"; } catch { return false; }
}
