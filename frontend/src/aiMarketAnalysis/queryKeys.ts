import type { Instrument, ReportMode } from "./types";
export function presentationKey(input: { instrument: Instrument; mode: ReportMode; language: string; reportId?: string; adminScope: string }) {
  return ["ai-market-presentation-v1", input.instrument, input.mode, input.language, input.reportId || "latest", input.adminScope] as const;
}
