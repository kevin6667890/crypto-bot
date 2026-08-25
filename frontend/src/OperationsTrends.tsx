import { useMemo, useState } from "react";
import type { components } from "./api/generated";
import { useAsyncResource } from "./asyncResource";
import { useLanguage } from "./i18n";

type Trends = components["schemas"]["OperationsTrends"] & {
  latency?: { p50_ms: number | null; p95_ms: number | null };
};
type TrendPoint = components["schemas"]["TrendPoint"];
type Window = "1h" | "6h" | "24h";

const paperApiBase = (
  window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || ""
).replace(/\/$/, "");

function latest(points: TrendPoint[], key: keyof TrendPoint) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const value = points[index][key];
    if (typeof value === "number") return value;
  }
  return null;
}

function MetricTrend({
  title,
  unit,
  field,
  points,
  noData,
  trendLabel,
}: {
  title: string;
  unit: string;
  field: keyof TrendPoint;
  points: TrendPoint[];
  noData: string;
  trendLabel: string;
}) {
  const values = points.map((point) => point[field]).filter((value): value is number => typeof value === "number");
  const maximum = Math.max(...values, 1);
  const sampled = points.filter((_, index) => index % Math.max(1, Math.ceil(points.length / 48)) === 0);
  return (
    <section className="trend-card">
      <div><span>{title}</span><b>{latest(points, field)?.toLocaleString() ?? noData} {values.length ? unit : ""}</b></div>
      <div className="trend-bars" aria-label={`${title} ${trendLabel}`}>
        {sampled.map((point) => {
          const value = point[field];
          return <i
            key={`${point.timestamp}-${String(field)}`}
            className={point.anomaly ? "anomaly" : ""}
            title={`${new Date(point.timestamp * 1000).toLocaleString()} · ${String(value ?? "NO_DATA")} ${unit}`}
            style={{ height: `${typeof value === "number" ? Math.max(4, value / maximum * 100) : 2}%` }}
          />;
        })}
      </div>
    </section>
  );
}

export default function OperationsTrends() {
  const { language } = useLanguage();
  const zh = language === "zh";
  const copy = zh ? {
    eyebrow: "本地 · 只读", title: "运维趋势", window: "趋势时间窗", loading: "正在加载本地趋势",
    unavailable: "趋势查询暂不可用", disabled: "尚未启用历史采集", empty: "已启用采集，但所选时间段尚无样本",
    noData: "暂无数据", anomalies: "异常点", samples: "分钟样本", trend: "趋势", bytes: "字节", wal: "WAL 日志",
    maintenance: "维护耗时", queue: "队列深度", lag: "实时延迟", gap: "关键缺口", checkpoint: "检查点耗时",
  } : {
    eyebrow: "Local · Read only", title: "Operations trends", window: "Trend window", loading: "Loading local trends",
    unavailable: "Trend query is unavailable", disabled: "Historical capture is not enabled", empty: "Capture is enabled, but the selected window has no samples",
    noData: "No data", anomalies: "Anomalies", samples: "Minute samples", trend: "trend", bytes: "bytes", wal: "WAL",
    maintenance: "Maintenance", queue: "Queue", lag: "Live lag", gap: "Critical gap", checkpoint: "Checkpoint",
  };
  const [selectedWindow, setSelectedWindow] = useState<Window>("24h");
  const resource = useAsyncResource<Trends>(
    `operations-trends-${selectedWindow}`,
    `${paperApiBase}/api/operations/trends?window=${selectedWindow}`,
    { timeoutMs: 5_000 },
  );
  const points = useMemo(() => resource.data?.points ?? [], [resource.data]);

  return (
    <section className="operations-trends">
      <div className="section-title">
        <div><span className="eyebrow">{copy.eyebrow}</span><h2>{copy.title}</h2></div>
        <div className="trend-window" role="group" aria-label={copy.window}>
          {(["1h", "6h", "24h"] as const).map((windowValue) => (
            <button key={windowValue} className={selectedWindow === windowValue ? "active" : ""} onClick={() => setSelectedWindow(windowValue)}>
              {windowValue.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {resource.phase === "LOADING" && <div className="research-alert" data-state="LOADING">{copy.loading}</div>}
      {resource.phase === "UNAVAILABLE" && <div className="research-alert error" data-state="UNAVAILABLE">{copy.unavailable}</div>}
      {resource.data && !resource.data.enabled && <div className="trend-disabled" data-state="NOT_ENABLED">{copy.disabled}</div>}
      {resource.data?.enabled && !points.length && <div className="trend-disabled" data-state="NO_DATA">{copy.empty}</div>}
      {points.length > 0 && <>
        <div className="trend-summary">
          <span>API p50 <b>{resource.data?.latency?.p50_ms ?? copy.noData} ms</b></span>
          <span>API p95 <b>{resource.data?.latency?.p95_ms ?? copy.noData} ms</b></span>
          <span>{copy.anomalies} <b>{points.filter((point) => point.anomaly).length}</b></span>
          <span>{copy.samples} <b>{points.length}</b></span>
        </div>
        <div className="trends-grid">
          <MetricTrend title={copy.wal} unit={copy.bytes} field="wal_size_bytes" points={points} noData={copy.noData} trendLabel={copy.trend} />
          <MetricTrend title={copy.maintenance} unit="ms" field="maintenance_duration_ms" points={points} noData={copy.noData} trendLabel={copy.trend} />
          <MetricTrend title={copy.queue} unit="" field="queue_depth" points={points} noData={copy.noData} trendLabel={copy.trend} />
          <MetricTrend title={copy.lag} unit="s" field="live_lag_seconds" points={points} noData={copy.noData} trendLabel={copy.trend} />
          <MetricTrend title={copy.gap} unit="" field="critical_gap_count" points={points} noData={copy.noData} trendLabel={copy.trend} />
          <MetricTrend title={copy.checkpoint} unit="ms" field="checkpoint_duration_ms" points={points} noData={copy.noData} trendLabel={copy.trend} />
        </div>
      </>}
    </section>
  );
}
