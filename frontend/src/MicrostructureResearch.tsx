import React, { useEffect, useState, useRef } from "react";
import { ColorType, createChart, IChartApi, ISeriesApi, UTCTimestamp, LineSeries } from "lightweight-charts";
import { useLanguage } from "./i18n";

// Interfaces for API responses
interface HealthResponse {
  service_status: string;
  database_size_bytes: number;
  gap_count: number;
  raw_rows: number;
  aggregate_rows: number;
  sample_status: string;
  next_eligibility?: string;
  liquidation_events_count?: number;
  feature_statistics?: Record<string, any>;
}

interface CoverageItem {
  instrument: string;
  earliest_ms: number;
  latest_ms: number;
  rows: number;
}

interface CoverageResponse {
  trades?: CoverageItem[];
  oi?: CoverageItem[];
  funding_settled?: CoverageItem[];
  funding_predicted?: CoverageItem[];
  mark?: CoverageItem[];
  index?: CoverageItem[];
  liquidations?: CoverageItem[];
}

interface FeatureGroup {
  status: string;
  features: string[];
  usable_days: number;
  source_usable_days: number;
  overlap_usable_days: number;
  source_observation_count: number;
  event_count: number;
  source_data_status: string;
  event_study_status: string;
  blocking_reason?: string;
}

interface EligibilityResponse {
  feature_groups: Record<string, FeatureGroup>;
}

interface ChartPoint {
  time: number;
  value: number;
}

interface ChartResponse {
  instrument: string;
  points: ChartPoint[];
}

const chartTheme = {
  layout: { background: { type: ColorType.Solid as const, color: "transparent" }, textColor: "#6b7280", fontFamily: "Inter, ui-sans-serif, system-ui" },
  grid: { vertLines: { color: "rgba(17, 24, 39, 0.06)" }, horzLines: { color: "rgba(17, 24, 39, 0.06)" } },
  rightPriceScale: { borderColor: "rgba(17, 24, 39, 0.1)" },
  timeScale: { borderColor: "rgba(17, 24, 39, 0.1)", timeVisible: true, fixLeftEdge: true, fixRightEdge: true },
  crosshair: { vertLine: { color: "rgba(0, 179, 126, 0.28)" }, horzLine: { color: "rgba(0, 179, 126, 0.28)" } },
};

function SimpleLineChart({ data, title }: { data: ChartPoint[]; title: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      ...chartTheme,
      autoSize: true,
    });
    const lineSeries = chart.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
      crosshairMarkerVisible: true,
      lastValueVisible: true,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = lineSeries;

    return () => {
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (seriesRef.current && data.length > 0) {
      const sorted = [...data].sort((a, b) => a.time - b.time);
      const unique = sorted.filter((v, i, a) => i === 0 || v.time !== a[i - 1].time);
      seriesRef.current.setData(
        unique.map((p) => ({ time: (p.time / 1000) as UTCTimestamp, value: p.value }))
      );
      chartRef.current?.timeScale().fitContent();
    }
  }, [data]);

  return (
    <div className="chart-panel" style={{ display: "flex", flexDirection: "column", height: "300px" }}>
      <div className="section-title">
        <span className="eyebrow">{title}</span>
      </div>
      <div ref={containerRef} style={{ flexGrow: 1, position: "relative" }} />
    </div>
  );
}

export default function MicrostructureResearch() {
  const { t } = useLanguage();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [eligibility, setEligibility] = useState<EligibilityResponse | null>(null);
  
  const [fundingData, setFundingData] = useState<ChartPoint[]>([]);
  const [basisData, setBasisData] = useState<ChartPoint[]>([]);
  const [cvdData, setCvdData] = useState<ChartPoint[]>([]);
  const [oiData, setOiData] = useState<ChartPoint[]>([]);

  const fetchAll = async () => {
    try {
      const hRes = await fetch("/api/research/microstructure/health");
      if (hRes.ok) setHealth(await hRes.json());

      const cRes = await fetch("/api/research/microstructure/coverage");
      if (cRes.ok) setCoverage(await cRes.json());

      const eRes = await fetch("/api/research/microstructure/eligibility");
      if (eRes.ok) setEligibility(await eRes.json());

      const instrument = "BTC-USDT-SWAP";
      
      const fRes = await fetch(`/api/research/microstructure/charts/funding?instrument=${instrument}&limit=500`);
      if (fRes.ok) { const d = await fRes.json(); setFundingData(d.points || []); }

      const bRes = await fetch(`/api/research/microstructure/charts/basis?instrument=${instrument}&limit=500`);
      if (bRes.ok) { const d = await bRes.json(); setBasisData(d.points || []); }

      const cvRes = await fetch(`/api/research/microstructure/charts/cvd?instrument=${instrument}&limit=500`);
      if (cvRes.ok) { const d = await cvRes.json(); setCvdData(d.points || []); }

      const oRes = await fetch(`/api/research/microstructure/charts/oi?instrument=${instrument}&limit=500`);
      if (oRes.ok) { const d = await oRes.json(); setOiData(d.points || []); }
    } catch (err) {
      console.error("Microstructure API error", err);
    }
  };

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 60000);
    return () => clearInterval(interval);
  }, []);

  const formatBytes = (bytes: number) => {
    if (!bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  const getStatusClass = (status: string) => {
    const s = status?.toUpperCase() || "";
    if (s === "EXPLORATORY_ONLY") return "warning"; // amber
    if (s === "MINIMUM_SAMPLE_REACHED") return "blue"; // blue? or we can use regular classes if they exist
    if (s === "VALIDATION_READY") return "pass"; // green
    if (s === "FORMAL_RESEARCH_READY") return "pass"; // bright green
    return "";
  };

  return (
    <div className="main-grid" id="microstructure">
      <div className="demo-note" style={{ gridColumn: "1 / -1", textAlign: "center", fontWeight: "bold", color: "var(--amber)" }}>
        {t("micro.disclaimer")}
      </div>

      <section className="panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">{t("micro.collectorStatus")}</span>
            <h2>{t("micro.title")}</h2>
          </div>
        </div>
        <div className="execution-summary">
          <div className="metric-card tone-neutral">
            <span>{t("micro.serviceStatus")}</span>
            <strong>{health?.service_status || "--"}</strong>
          </div>
          <div className="metric-card tone-neutral">
            <span>{t("micro.databaseSize")}</span>
            <strong>{health?.database_size_bytes ? formatBytes(health.database_size_bytes) : "--"}</strong>
          </div>
          <div className="metric-card tone-warning">
            <span>{t("micro.gapCount")}</span>
            <strong>{health?.gap_count ?? "--"}</strong>
          </div>
          <div className="metric-card tone-neutral">
            <span>{t("micro.rawRows")}</span>
            <strong>{health?.raw_rows?.toLocaleString() ?? "--"}</strong>
          </div>
          <div className="metric-card tone-neutral">
            <span>{t("micro.aggregateRows")}</span>
            <strong>{health?.aggregate_rows?.toLocaleString() ?? "--"}</strong>
          </div>
          <div className="metric-card tone-neutral">
            <span>{t("micro.sampleStatus")}</span>
            <strong className={getStatusClass(health?.sample_status || "")}>{health?.sample_status || "--"}</strong>
          </div>
        </div>
      </section>

      <section className="panel wide-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">{t("micro.coverage")}</span>
            <h2>{t("micro.coverage")}</h2>
          </div>
        </div>
        <div className="trade-table">
          <div className="trade-row table-head">
            <span>{t("micro.source")}</span>
            <span>{t("micro.instrument")}</span>
            <span>{t("micro.earliest")}</span>
            <span>{t("micro.latest")}</span>
            <span>{t("micro.rows")}</span>
            <span>{t("micro.lag")}</span>
          </div>
          {coverage && Object.entries(coverage).map(([source, items]) => 
            (items as CoverageItem[]).map((item, idx) => {
              const now = Date.now();
              const lagMs = now - item.latest_ms;
              const lagStr = lagMs > 0 ? `${(lagMs / 1000).toFixed(1)}s` : "--";
              return (
                <div className="trade-row" key={`${source}-${item.instrument}-${idx}`}>
                  <span>{source}</span>
                  <strong>{item.instrument}</strong>
                  <span>{new Date(item.earliest_ms).toLocaleString()}</span>
                  <span>{new Date(item.latest_ms).toLocaleString()}</span>
                  <span>{item.rows.toLocaleString()}</span>
                  <strong className={lagMs > 60000 ? "negative" : "positive"}>{lagStr}</strong>
                </div>
              );
            })
          )}
        </div>
      </section>

      <section className="panel wide-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">{t("micro.eligibility")}</span>
            <h2>{t("micro.eligibility")}</h2>
          </div>
        </div>
        <div className="trade-table">
          <div className="trade-row table-head eligibility-row">
            <span>{t("micro.featureGroup")}</span>
            <span>{t("micro.features")}</span>
            <span>Source days / rows</span>
            <span>Source status</span>
            <span>Label overlap / events</span>
            <span>Event-study status</span>
            <span>{t("micro.blockingReason")}</span>
          </div>
          {eligibility && Object.entries(eligibility.feature_groups).map(([group, data]) => (
            <div className="trade-row eligibility-row" key={group}>
              <strong>{group}</strong>
              <span style={{ fontSize: '0.85em', color: 'var(--muted)' }}>{data.features.join(", ")}</span>
              <span>{data.source_usable_days}d / {data.source_observation_count.toLocaleString()}</span>
              <strong className={getStatusClass(data.source_data_status)}>{data.source_data_status}</strong>
              <span>{data.overlap_usable_days}d / {data.event_count.toLocaleString()}</span>
              <strong className={getStatusClass(data.event_study_status)}>{data.event_study_status}</strong>
              <span className="negative">{data.blocking_reason || "--"}</span>
            </div>
          ))}
        </div>
      </section>

      {health?.liquidation_events_count !== undefined && (
        <section className="panel">
          <div className="panel-head">
            <div>
              <span className="eyebrow">{t("micro.liquidations")}</span>
              <h2>{t("micro.liquidations")}</h2>
            </div>
          </div>
          <div className="execution-summary">
            <div className="metric-card tone-neutral">
              <span>{t("micro.eventCount")}</span>
              <strong>{health.liquidation_events_count.toLocaleString()}</strong>
            </div>
          </div>
        </section>
      )}

      {health?.feature_statistics && Object.keys(health.feature_statistics).length > 0 && (
        <section className="panel wide-panel">
          <div className="panel-head">
            <div>
              <span className="eyebrow">{t("micro.statistics")}</span>
              <h2>{t("micro.statistics")}</h2>
            </div>
          </div>
          <pre style={{ padding: 12, background: 'var(--surface)', borderRadius: 4, overflowX: 'auto', fontSize: '0.85em' }}>
            {JSON.stringify(health.feature_statistics, null, 2)}
          </pre>
        </section>
      )}

      <div style={{ gridColumn: "1 / -1", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
        <SimpleLineChart data={fundingData} title={t("micro.funding")} />
        <SimpleLineChart data={basisData} title={t("micro.basis")} />
        <SimpleLineChart data={cvdData} title={t("micro.cvd")} />
        <SimpleLineChart data={oiData} title={t("micro.oi")} />
      </div>
    </div>
  );
}
