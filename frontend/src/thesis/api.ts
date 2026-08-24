import type { EvidenceExplanation, ThesisCapabilities, ThesisEventContext, ThesisEventRecord, ThesisParseResult, ThesisSpecV1, ThesisTestResult } from "./types";

const base = (window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || "").replace(/\/$/, "");

export class ThesisApiError extends Error {
  constructor(public code: string, public status: number) { super(code); }
}

async function request<T>(path: string, options: RequestInit, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const upstream = options.signal;
  const abort = () => controller.abort(upstream?.reason);
  upstream?.addEventListener("abort", abort, { once: true });
  const timer = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(`${base}${path}`, { ...options, signal: controller.signal, cache: "no-store",
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
    let payload: unknown;
    try { payload = await response.json(); } catch { throw new ThesisApiError("INVALID_API_RESPONSE", response.status); }
    if (!response.ok) {
      const error = (payload as { error?: { code?: string } | string }).error;
      throw new ThesisApiError(typeof error === "object" && error?.code ? error.code : `HTTP_${response.status}`, response.status);
    }
    return payload as T;
  } finally {
    window.clearTimeout(timer);
    upstream?.removeEventListener("abort", abort);
  }
}

export function fetchThesisCapabilities(signal?: AbortSignal) {
  return request<ThesisCapabilities>("/api/research/thesis/capabilities", { signal }, 8_000);
}

export function parseThesis(input: { text: string; language?: "en" | "zh"; requested_instrument?: string; requested_timeframe?: string }, signal?: AbortSignal) {
  return request<ThesisParseResult>("/api/research/thesis/parse", { method: "POST", signal,
    body: JSON.stringify({ version: "thesis-parse-request-v1", ...input }) }, 15_000);
}

export function testThesis(spec: ThesisSpecV1, signal?: AbortSignal) {
  return request<ThesisTestResult>("/api/research/thesis/test", { method: "POST", signal, body: JSON.stringify(spec) }, 12_000);
}

export function fetchThesisEventContext(result: ThesisTestResult, event: ThesisEventRecord, signal?: AbortSignal) {
  return request<ThesisEventContext>("/api/research/thesis/event-context", { method: "POST", signal, body: JSON.stringify({
    version: "thesis-event-context-request-v1", result_hash: result.result_hash, thesis_spec: result.thesis_spec,
    instrument: result.instrument, timeframe: result.timeframe, event_id: event.event_id, event_timestamp: event.timestamp,
  }) }, 8_000);
}

export function explainThesis(result: ThesisTestResult, language: "en" | "zh", signal?: AbortSignal) {
  return request<EvidenceExplanation>("/api/research/thesis/explain", { method: "POST", signal, body: JSON.stringify({
    version: "thesis-evidence-explain-request-v1", thesis_spec: result.thesis_spec,
    result_hash: result.result_hash, language,
  }) }, 12_000);
}
