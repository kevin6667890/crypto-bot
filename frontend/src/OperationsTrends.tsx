import { useMemo, useState } from "react";
import type { components } from "./api/generated";
import { useAsyncResource } from "./asyncResource";

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
}: {
  title: string;
  unit: string;
  field: keyof TrendPoint;
  points: TrendPoint[];
}) {
  const values = points.map((point) => point[field]).filter((value): value is number => typeof value === "number");
  const maximum = Math.max(...values, 1);
  const sampled = points.filter((_, index) => index % Math.max(1, Math.ceil(points.length / 48)) === 0);
  return (
    <section className="trend-card">
      <div><span>{title}</span><b>{latest(points, field)?.toLocaleString() ?? "NO_DATA"} {values.length ? unit : ""}</b></div>
      <div className="trend-bars" aria-label={`${title} trend`}>
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
        <div><span className="eyebrow">Local · Read only</span><h2>Operations Trends / 运维趋势</h2></div>
        <div className="trend-window" role="group" aria-label={"trend window"}>
          {(["1h", "6h", "24h"] as const).map((windowValue) => (
            <button key={windowValue} className={selectedWindow === windowValue ? "active" : ""} onClick={() => setSelectedWindow(windowValue)}>
              {windowValue.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {resource.phase === "LOADING" && <div className="research-alert" data-state="LOADING">LOADING · 正在加载本地趋势</div>}
      {resource.phase === "UNAVAILABLE" && <div className="research-alert error" data-state="UNAVAILABLE">UNAVAILABLE · 趋势查询暂不可用</div>}
      {resource.data && !resource.data.enabled && <div className="trend-disabled" data-state="NOT_ENABLED">尚未启用历史采集 · Historical capture is not enabled</div>}
      {resource.data?.enabled && !points.length && <div className="trend-disabled" data-state="NO_DATA">NO_DATA · 已启用采集，但所选时间段尚无样本</div>}
      {points.length > 0 && <>
        <div className="trend-summary">
          <span>API p50 <b>{resource.data?.latency?.p50_ms ?? "NO_DATA"} ms</b></span>
          <span>API p95 <b>{resource.data?.latency?.p95_ms ?? "NO_DATA"} ms</b></span>
          <span>Anomalies / 异常点 <b>{points.filter((point) => point.anomaly).length}</b></span>
          <span>Samples / 分钟样本 <b>{points.length}</b></span>
        </div>
        <div className="trends-grid">
          <MetricTrend title={"WAL"} unit="bytes" field="wal_size_bytes" points={points} />
          <MetricTrend title={"Maintenance"} unit="ms" field="maintenance_duration_ms" points={points} />
          <MetricTrend title={"Queue"} unit="" field="queue_depth" points={points} />
          <MetricTrend title={"Live lag"} unit="s" field="live_lag_seconds" points={points} />
          <MetricTrend title={"Critical gap"} unit="" field="critical_gap_count" points={points} />
          <MetricTrend title={"Checkpoint"} unit="ms" field="checkpoint_duration_ms" points={points} />
        </div>
      </>}
    </section>
  );
}
