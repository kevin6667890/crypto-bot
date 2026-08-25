import type { ThesisSpec } from "../thesis/types";
import type { ChangeBundle, MarketStateChange, TrackBundle, TrackDetail, TrackMutation, TrackedThesis } from "./types";

const base = (window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || "").replace(/\/$/, "");

export class TrackingApiError extends Error {
  constructor(public code: string, public status: number) { super(code); }
}

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 8_000): Promise<T> {
  const controller = new AbortController();
  const upstream = options.signal;
  const abort = () => controller.abort(upstream?.reason);
  upstream?.addEventListener("abort", abort, { once: true });
  const timer = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(`${base}${path}`, { ...options, signal: controller.signal, cache: "no-store", headers: {
      Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers,
    } });
    const payload = await response.json().catch(() => null) as { error?: { code?: string } | string } | null;
    if (!response.ok) {
      const error = payload?.error;
      throw new TrackingApiError(typeof error === "object" && error?.code ? error.code : `HTTP_${response.status}`, response.status);
    }
    return payload as T;
  } finally {
    window.clearTimeout(timer); upstream?.removeEventListener("abort", abort);
  }
}

export function createTrackedThesis(input: { result_hash: string; thesis_spec: ThesisSpec; language: "en" | "zh"; original_text?: string }, signal?: AbortSignal) {
  const version = input.thesis_spec.version === "thesis-spec-v2" ? "track-thesis-request-v2" : "track-thesis-request-v1";
  return request<TrackMutation>("/api/research/thesis/tracks", { method: "POST", signal, body: JSON.stringify({ version, ...input }) }, 15_000);
}
export function fetchTrackedTheses(signal?: AbortSignal) {
  return request<{ tracks: TrackBundle[] }>("/api/research/thesis/tracks", { signal });
}
export function fetchTrackedThesis(trackId: string, signal?: AbortSignal) {
  return request<TrackDetail>(`/api/research/thesis/tracks/${encodeURIComponent(trackId)}`, { signal });
}
export function evaluateTrackedThesis(trackId: string, schemaVersion = "tracked-thesis-v1", signal?: AbortSignal) {
  const payload = schemaVersion === "tracked-thesis-v2"
    ? { version: "current-thesis-evaluate-request-v2" }
    : { version: "current-thesis-evaluate-request-v1" };
  return request<TrackMutation>(`/api/research/thesis/tracks/${encodeURIComponent(trackId)}/evaluate`, { method: "POST", signal, body: JSON.stringify(payload) }, 15_000);
}
export function archiveTrackedThesis(trackId: string, signal?: AbortSignal) {
  return request<{ track: TrackedThesis }>(`/api/research/thesis/tracks/${encodeURIComponent(trackId)}/archive`, { method: "POST", signal, body: JSON.stringify({ version: "track-thesis-archive-v1" }) });
}
export function fetchThesisChanges(signal?: AbortSignal, limit = 50) {
  const hours = limit <= 5 ? 24 : 72;
  return request<{ changes: ChangeBundle[]; market_state_changes: MarketStateChange[] }>(`/api/research/thesis/changes?hours=${hours}`, { signal });
}
