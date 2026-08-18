import { normalizePresentation, parsePresentation, type Instrument, type Presentation, type ReportMode } from "./types";

const base = (window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || "").replace(/\/$/, "");
export class PresentationApiError extends Error { constructor(public code: string, public status: number) { super(code); } }
export async function fetchPresentation(input: { instrument: Instrument; mode: ReportMode; language: string; token: string; signal?: AbortSignal; reportId?: string }): Promise<Presentation> {
  const path = input.reportId ? `/api/ai-market-analysis/v1/presentations/${encodeURIComponent(input.reportId)}` : "/api/ai-market-analysis/v1/presentations/latest";
  const query = new URLSearchParams({ instrument: input.instrument, mode: input.mode, language: input.language });
  const response = await fetch(`${base}${path}?${query}`, { signal: input.signal, cache: "no-store", headers: { Authorization: `Bearer ${input.token}`, Accept: "application/json" } });
  const payload = await response.json().catch(() => ({})) as { error?: { code?: string } };
  if (!response.ok) throw new PresentationApiError(payload.error?.code || `HTTP_${response.status}`, response.status);
  let normalized:Presentation;
  try{normalized=normalizePresentation(parsePresentation(payload));}catch{throw new PresentationApiError("PRESENTATION_CONTRACT_ERROR",200);}
  if (typeof performance !== "undefined") performance.mark("ama-presentation-normalized");
  return normalized;
}
export async function tripKillSwitch(token:string,event:string,evidenceId:string):Promise<void>{
  await fetch(`${base}/api/ai-market-analysis/v1/kill-switch`,{method:"POST",cache:"no-store",headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json",Accept:"application/json"},body:JSON.stringify({event,evidence_id:evidenceId})}).catch(()=>undefined);
}
export async function fetchPositionDetails(input: { reportId: string; instrument: Instrument; mode: ReportMode; token: string; signal?: AbortSignal }) {
  const query = new URLSearchParams({ instrument: input.instrument, mode: input.mode });
  const response = await fetch(`${base}/api/ai-market-analysis/v1/presentations/${encodeURIComponent(input.reportId)}/position?${query}`, { signal: input.signal, cache: "no-store", headers: { Authorization: `Bearer ${input.token}`, Accept: "application/json" } });
  if (!response.ok) throw new PresentationApiError(`HTTP_${response.status}`, response.status);
  return response.json() as Promise<Record<string, unknown>>;
}
