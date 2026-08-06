import { ChartCacheKey, loadChartSnapshot, loadChartSnapshotMetadata, saveChartSnapshot } from "./chartState";
import type { components } from "./api/generated";

export type FlowSeriesName = components["schemas"]["FlowHistoryResponse"]["series"];
type ContractFlowHistoryPoint = components["schemas"]["FlowHistoryPoint"];
export type FlowHistoryPoint =
  Omit<ContractFlowHistoryPoint, "status" | "source_complete" | "partial_after_gap">
  & Partial<Pick<
    ContractFlowHistoryPoint,
    "status" | "source_complete" | "partial_after_gap"
  >> & { segment_start?: boolean };
type ContractFlowHistoryResponse = components["schemas"]["FlowHistoryResponse"];
export type FlowHistoryResponse =
  Omit<ContractFlowHistoryResponse, "points">
  & { points: FlowHistoryPoint[]; canonical_version?: string; canonical_generation?: string };
export type FlowCoverage = Omit<FlowHistoryResponse, "points">;
export type FlowRangeRequest = {
  instrument: string;
  series: FlowSeriesName;
  timeframe: string;
  start: number;
  end: number;
  maxPoints?: number;
  cursor?: string | null;
  cvdMode?: "UTC_DAILY_RESET";
};

const MEMORY_POINT_LIMIT = 50_000;
export const FLOW_HISTORY_MAX_POINTS = 500;
export const CANONICAL_FLOW_SCHEMA_VERSION = "canonical-microstructure-schema-v2";
export const CANONICAL_FLOW_HISTORY_VERSION = "canonical-microstructure-history-v2";
const memory = new Map<string, FlowHistoryPoint[]>();
const metadata = new Map<string, FlowCoverage>();
const inflight = new Map<string, Promise<FlowHistoryResponse>>();

const validPoint = (point: unknown): point is FlowHistoryPoint => {
  const row = point as FlowHistoryPoint;
  if (!row || !Number.isFinite(row.time)) return false;
  if (row.status === "WHITESPACE") return row.value === undefined || row.value === null;
  // Accept old cached points as VALID during the one-time contract upgrade.
  return Number.isFinite(row.value);
};

export function flowHistoryKey(instrument: string, timeframe: string, series: FlowSeriesName) {
  return `${CANONICAL_FLOW_HISTORY_VERSION}:${series}:${instrument}:${timeframe}`;
}

export function persistedFlowInstrument(instrument: string) {
  return instrument.endsWith("-SWAP") ? instrument.slice(0, -5) : instrument;
}

export function mergeHistoryPoints(
  retained: FlowHistoryPoint[],
  incoming: unknown,
  limit = MEMORY_POINT_LIMIT,
): FlowHistoryPoint[] {
  const byTime = new Map<number, FlowHistoryPoint>();
  for (const point of retained) if (validPoint(point)) byTime.set(point.time, point);
  if (Array.isArray(incoming)) {
    for (const point of incoming) if (validPoint(point)) byTime.set(point.time, point);
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time).slice(-limit);
}

export function splitFlowSegments(points: FlowHistoryPoint[]): FlowHistoryPoint[][] {
  const segments: FlowHistoryPoint[][] = [];
  let current: FlowHistoryPoint[] = [];
  for (const point of points) {
    if (point.segment_start && current.length) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}

export function flowStatusAtCandle(
  candleTime: number,
  timeframeSeconds: number,
  points: FlowHistoryPoint[],
) {
  const matches = points.filter(point =>
    point.time >= candleTime && point.time < candleTime + timeframeSeconds
  );
  const point = matches[matches.length - 1];
  if (!point) {
    return { status: "WHITESPACE" as const, value: null, partial: false };
  }
  if (point.status === "WHITESPACE" || !Number.isFinite(point.value)) {
    const partial = point.quality_status === "PARTIAL_AFTER_GAP" || point.partial_after_gap === true;
    return {
      status: partial ? "PARTIAL_AFTER_GAP" as const : "WHITESPACE" as const,
      value: null,
      partial,
    };
  }
  return {
    status: point.status || "VALID",
    value: Number(point.value),
    partial: point.status === "PARTIAL_AFTER_GAP" || point.partial_after_gap,
  };
}

function localKey(instrument: string, timeframe: string, series: FlowSeriesName): ChartCacheKey {
  return { instrument, timeframe, series };
}

export function hydrateFlowHistory(instrument: string, timeframe: string, series: FlowSeriesName) {
  const key = flowHistoryKey(instrument, timeframe, series);
  const retained = memory.get(key);
  if (retained?.length) return retained;
  const local = loadChartSnapshot(localKey(instrument, timeframe, series), validPoint);
  const retainedMetadata = loadChartSnapshotMetadata(localKey(instrument, timeframe, series)) as FlowCoverage | undefined;
  if (retainedMetadata?.schema_version === CANONICAL_FLOW_SCHEMA_VERSION
      && retainedMetadata?.history_version === CANONICAL_FLOW_HISTORY_VERSION) {
    metadata.set(key, retainedMetadata);
  }
  if (local.length) memory.set(key, local);
  return local;
}

export function retainedCoverage(instrument: string, timeframe: string, series: FlowSeriesName) {
  return metadata.get(flowHistoryKey(instrument, timeframe, series));
}

export function retainServerHistory(
  timeframe: string,
  response: FlowHistoryResponse,
): FlowHistoryPoint[] {
  const key = flowHistoryKey(response.instrument, timeframe, response.series);
  const previousCoverage = metadata.get(key);
  const versionChanged = Boolean(
    previousCoverage?.canonical_version && response.canonical_version
    && previousCoverage.canonical_version !== response.canonical_version
  );
  const current = versionChanged
    ? []
    : memory.get(key) || hydrateFlowHistory(response.instrument, timeframe, response.series);
  if (response.actual_resolution !== timeframe || response.timeframe !== timeframe) return current;
  const { points: _points, ...coverage } = response;
  metadata.set(key, coverage);
  if (!response.points.length) {
    saveChartSnapshot(localKey(response.instrument, timeframe, response.series), current, validPoint, Date.now(), coverage);
    return current;
  }
  // One authoritative response uses one deterministic resolution. Replace
  // only its requested range so cached pages outside the range survive while
  // overlapping points from a different resolution cannot mix semantics.
  const retainedOutsideRange = response.fallback
    ? current
    : current.filter(point => point.time < response.requested_start || point.time > response.requested_end);
  const merged = mergeHistoryPoints(retainedOutsideRange, response.points);
  memory.set(key, merged);
  saveChartSnapshot(localKey(response.instrument, timeframe, response.series), merged, validPoint, Date.now(), coverage);
  return merged;
}

export function retainFallbackHistory(
  instrument: string,
  timeframe: string,
  series: FlowSeriesName,
  incoming: unknown,
) {
  const key = flowHistoryKey(instrument, timeframe, series);
  const current = memory.get(key) || hydrateFlowHistory(instrument, timeframe, series);
  if (!Array.isArray(incoming) || !incoming.some(validPoint)) return current;
  const merged = mergeHistoryPoints(current, incoming);
  memory.set(key, merged);
  saveChartSnapshot(localKey(instrument, timeframe, series), merged, validPoint);
  return merged;
}

function apiBase() {
  const configured = typeof window === "undefined"
    ? ""
    : window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || "";
  return configured.replace(/\/$/, "");
}

export function historyRequestUrl(request: FlowRangeRequest) {
  const query = new URLSearchParams({
    instrument: request.instrument,
    series: request.series,
    start: String(Math.floor(request.start)),
    end: String(Math.floor(request.end)),
    max_points: String(Math.min(request.maxPoints || FLOW_HISTORY_MAX_POINTS, FLOW_HISTORY_MAX_POINTS)),
    timeframe: request.timeframe,
    schema_version: CANONICAL_FLOW_SCHEMA_VERSION,
    history_version: CANONICAL_FLOW_HISTORY_VERSION,
  });
  if (request.cursor) query.set("cursor", request.cursor);
  if (request.series === "cvd") query.set("cvd_mode", request.cvdMode || "UTC_DAILY_RESET");
  return `${apiBase()}/api/paper/flow/history/v1?${query}`;
}

export async function requestFlowHistory(request: FlowRangeRequest): Promise<FlowHistoryResponse> {
  const url = historyRequestUrl(request);
  const existing = inflight.get(url);
  if (existing) return existing;
  const pending = fetch(url, { headers: { Accept: "application/json" } }).then(async response => {
    if (!response.ok) throw new Error(`Flow history request failed: ${response.status}`);
    const payload = await response.json() as FlowHistoryResponse;
    if (
      payload.actual_resolution !== request.timeframe
      || payload.timeframe !== request.timeframe
      || payload.schema_version !== CANONICAL_FLOW_SCHEMA_VERSION
      || payload.history_version !== CANONICAL_FLOW_HISTORY_VERSION
    ) throw new Error("Flow history contract mismatch");
    return payload;
  }).finally(() => inflight.delete(url));
  inflight.set(url, pending);
  return pending;
}

export class FlowSelectionGuard {
  private generation = 0;
  private selection = "";

  select(instrument: string, timeframe: string) {
    const next = `${instrument}:${timeframe}`;
    if (next !== this.selection) {
      this.selection = next;
      this.generation += 1;
    }
    return this.generation;
  }

  token() {
    return { selection: this.selection, generation: this.generation };
  }

  accepts(token: { selection: string; generation: number }) {
    return token.selection === this.selection && token.generation === this.generation;
  }
}

export function visibleRangeFromCandles(
  candles: Array<{ time: number }>,
  visibleCount = 260,
): { start: number; end: number } | null {
  if (!candles.length) return null;
  const visible = candles.slice(-visibleCount);
  return { start: Number(visible[0].time), end: Number(visible[visible.length - 1].time) };
}

export function olderPageRequest(
  coverage: FlowCoverage | undefined,
  points: FlowHistoryPoint[],
  visibleStart: number,
  thresholdSeconds: number,
): Pick<FlowRangeRequest, "start" | "end" | "maxPoints" | "cursor"> | null {
  if (
    !coverage?.next_before_cursor
    || !points.length
    || visibleStart > points[0].time + thresholdSeconds
  ) return null;
  return {
    start: coverage.available_start ?? visibleStart,
    end: visibleStart,
    maxPoints: FLOW_HISTORY_MAX_POINTS,
    cursor: coverage.next_before_cursor,
  };
}

export function formatFlowCoverage(coverage?: FlowCoverage) {
  if (!coverage) return "Loading persisted coverage…";
  if (!coverage.has_history || coverage.available_start === null || coverage.available_end === null) return "No persisted coverage";
  const start = new Date(coverage.available_start * 1000).toISOString().slice(0, 16).replace("T", " ");
  const end = new Date(coverage.available_end * 1000).toISOString().slice(0, 16).replace("T", " ");
  return `${start} – ${end} UTC · ${coverage.resolution || "native"}${coverage.stale ? " · stale" : ""}${coverage.has_gaps ? ` · ${coverage.gap_count} gap${coverage.gap_count === 1 ? "" : "s"}` : ""}`;
}

export function withPreservedLogicalRange<T>(
  timeScale: {
    getVisibleLogicalRange(): T | null;
    setVisibleLogicalRange(range: T): void;
  } | undefined,
  update: () => void,
) {
  const range = timeScale?.getVisibleLogicalRange();
  update();
  if (range) timeScale?.setVisibleLogicalRange(range);
}

export function __resetFlowHistoryForTests() {
  memory.clear();
  metadata.clear();
  inflight.clear();
}
