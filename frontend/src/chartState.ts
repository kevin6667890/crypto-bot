/** Bounded, versioned last-known-good chart snapshots (7 day / 500 points). */
export const CHART_CACHE_VERSION = 1;
export const CHART_POINT_LIMIT = 500;
export const CHART_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
const PREFIX = `crypto-bot.chart-cache.v${CHART_CACHE_VERSION}:`;

export type ChartSeriesType = "candles" | "cvd" | "oi";
export type ChartCacheKey = { instrument: string; timeframe: string; series: ChartSeriesType };
type Snapshot<T> = { version: number; savedAt: number; points: T[] };

export function chartCacheKey({ instrument, timeframe, series }: ChartCacheKey) {
  return `${PREFIX}${series}:${instrument}:${timeframe}`;
}

function storage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

export function normalizePoints<T extends { time: number }>(points: unknown, valid: (point: unknown) => point is T): T[] {
  if (!Array.isArray(points)) return [];
  const byTime = new Map<number, T>();
  for (const point of points) if (valid(point) && Number.isFinite(point.time)) byTime.set(point.time, point);
  return [...byTime.values()].sort((a, b) => a.time - b.time).slice(-CHART_POINT_LIMIT);
}

export function saveChartSnapshot<T extends { time: number }>(key: ChartCacheKey, points: T[], valid: (point: unknown) => point is T, now = Date.now()) {
  const normalized = normalizePoints(points, valid);
  if (!normalized.length) return false; // Empty refreshes are never destructive.
  try { storage()?.setItem(chartCacheKey(key), JSON.stringify({ version: CHART_CACHE_VERSION, savedAt: now, points: normalized } satisfies Snapshot<T>)); return true; } catch { return false; }
}

export function loadChartSnapshot<T extends { time: number }>(key: ChartCacheKey, valid: (point: unknown) => point is T, now = Date.now()): T[] {
  try {
    const raw = storage()?.getItem(chartCacheKey(key));
    if (!raw) return [];
    const snapshot = JSON.parse(raw) as Snapshot<unknown>;
    if (snapshot.version !== CHART_CACHE_VERSION || !Number.isFinite(snapshot.savedAt) || now - snapshot.savedAt > CHART_RETENTION_MS) return [];
    return normalizePoints(snapshot.points, valid);
  } catch { return []; }
}

export function formatMillions(value: unknown, decimals = 2, noData = "--"): string {
  return typeof value === "number" && Number.isFinite(value) ? `${(value / 1_000_000).toFixed(decimals)}M` : noData;
}
