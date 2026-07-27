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
  collector_warnings?: Array<{
    code: string; severity: string; component: string; instrument?: string | null; message: string;
  }>;
  gap_summary?: {
    recorded_gap_count: number;
    synthetic_live_gap_count: number;
    by_classification: Record<string, { count: number; total_duration_ms: number }>;
    critical_live_gaps: Array<{ lane: string; instrument: string; duration_ms: number }>;
  };
  funding_schedule?: Record<string, {
    latest_settlement_ms?: number; next_expected_settlement_ms?: number; overdue: boolean;
  }>;
  liquidation_health?: {
    stream_connected: boolean; connection_status: string; last_heartbeat_ms?: number;
    last_message_ms?: number; last_genuine_event_ms?: number; event_count: number;
    reconnect_count: number; completeness_limitation: string;
  };
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
  instruments: Record<string, {
    source_days: number;
    source_rows: number;
    gap_adjusted_usable_days: number;
    label_earliest_ms?: number;
    label_latest_ms?: number;
    overlap_usable_days: number;
    event_count: number;
    source_data_status: string;
    event_study_status: string;
    next_eligibility_date?: string;
    blocking_reason?: string;
  }>;
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

interface ValidationResponse {
  available: boolean;
  disclaimer: string;
  completed_oot_claim: boolean;
  chronological_partition_policy?: string;
  studies?: {
    funding?: { instruments?: Record<string, ValidationInstrument> };
    basis?: { instruments?: Record<string, ValidationInstrument> };
  };
}

interface ValidationInstrument {
  feature_classifications?: Record<string, string>;
  features?: Record<string, Record<string, {
    segments?: Record<string, { event_count?: number; sign_consistency?: number }>;
  }>>;
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
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [eligibilityInstrument, setEligibilityInstrument] = useState("BTC-USDT-SWAP");
  
  const [fundingData, setFundingData] = useState<ChartPoint[]>([]);
  const [basisData, setBasisData] = useState<ChartPoint[]>([]);
  const [cvdData, setCvdData] = useState<ChartPoint[]>([]);
  const [oiData, setOiData] = useState<ChartPoint[]>([]);

  const fetchAll = async () => {
    const load = async <T,>(
      url: string,
      apply: (payload: T) => void,
    ) => {
      try {
        const response = await fetch(url);
        if (response.ok) apply(await response.json());
      } catch (err) {
        console.error(`Microstructure API error: ${url}`, err);
      }
    };
    const instrument = "BTC-USDT-SWAP";
    const chart = (apply: (points: ChartPoint[]) => void) =>
      (payload: ChartResponse & { data?: ChartPoint[] }) => {
        if (payload.points) {
          apply(payload.points);
          return;
        }
        // Compatibility with the original seconds-based API response.
        apply((payload.data || []).map((point) => ({
          ...point,
          time: point.time * 1000,
        })));
      };
    await Promise.all([
      load<HealthResponse>("/api/research/microstructure/health", setHealth),
      load<CoverageResponse>("/api/research/microstructure/coverage", setCoverage),
      load<EligibilityResponse>("/api/research/microstructure/eligibility", setEligibility),
      load<ValidationResponse>("/api/research/microstructure/validation", setValidation),
      load<ChartResponse>(`/api/research/microstructure/charts/funding?instrument=${instrument}&limit=500`, chart(setFundingData)),
      load<ChartResponse>(`/api/research/microstructure/charts/basis?instrument=${instrument}&limit=500`, chart(setBasisData)),
      load<ChartResponse>(`/api/research/microstructure/charts/cvd?instrument=${instrument}&limit=500`, chart(setCvdData)),
      load<ChartResponse>(`/api/research/microstructure/charts/oi?instrument=${instrument}&limit=500`, chart(setOiData)),
    ]);
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

  const validationRows = (item?: ValidationInstrument) => Object.entries(item?.features || {}).map(
    ([feature, horizons]) => {
      const preferred = horizons["1H"] || Object.values(horizons)[0] || {};
      const research = preferred.segments?.RESEARCH_CALIBRATION;
      const later = preferred.segments?.LATER_VALIDATION;
      return {
        feature,
        classification: item?.feature_classifications?.[feature] || "INSUFFICIENT_SAMPLE",
        researchCount: research?.event_count || 0,
        validationCount: later?.event_count || 0,
        stability: later?.sign_consistency,
      };
    }
  );

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
        <div className="alert-list" style={{ marginTop: 12 }}>
          {(health?.collector_warnings || []).map((warning, index) => (
            <div className={`demo-note ${warning.severity === "critical" ? "negative" : ""}`} key={`${warning.code}-${warning.instrument}-${index}`}>
              <strong>{warning.code}</strong>
              {warning.instrument ? ` · ${warning.instrument}` : ""} — {warning.message}
            </div>
          ))}
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
              const schedule = health?.funding_schedule?.[item.instrument];
              const eventBased = source === "liquidations";
              const settled = source === "funding_settled";
              const lagStr = eventBased
                ? "event-based"
                : settled
                  ? (schedule?.overdue ? "OVERDUE" : "on schedule")
                  : lagMs > 0 ? `${(lagMs / 1000).toFixed(1)}s` : "--";
              return (
                <div className="trade-row" key={`${source}-${item.instrument}-${idx}`}>
                  <span>{source}</span>
                  <strong>{item.instrument}</strong>
                  <span>{new Date(item.earliest_ms).toLocaleString()}</span>
                  <span>{new Date(item.latest_ms).toLocaleString()}</span>
                  <span>{item.rows.toLocaleString()}</span>
                  <strong className={(settled && schedule?.overdue) || (!eventBased && !settled && lagMs > 60000) ? "negative" : "positive"}>{lagStr}</strong>
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
          <div className="instrument-tabs" style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            {["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"].map((value) => (
              <button className={eligibilityInstrument === value ? "active" : ""} key={value} onClick={() => setEligibilityInstrument(value)}>
                {value.split("-")[0]}
              </button>
            ))}
          </div>
          <div className="trade-row table-head eligibility-row">
            <span>{t("micro.featureGroup")}</span>
            <span>{t("micro.features")}</span>
            <span>Source days / rows</span>
            <span>Source status</span>
            <span>Label overlap / events</span>
            <span>Event-study status</span>
            <span>{t("micro.blockingReason")}</span>
          </div>
          {eligibility && Object.entries(eligibility.feature_groups).map(([group, data]) => {
            const row = data.instruments?.[eligibilityInstrument];
            if (!row) return null;
            return (
            <div className="trade-row eligibility-row" key={`${group}-${eligibilityInstrument}`}>
              <strong>{group}</strong>
              <span style={{ fontSize: '0.85em', color: 'var(--muted)' }}>{data.features.join(", ")}</span>
              <span>{row.gap_adjusted_usable_days}d / {row.source_rows.toLocaleString()}</span>
              <strong className={getStatusClass(row.source_data_status)}>{row.source_data_status}</strong>
              <span>{row.overlap_usable_days}d / {row.event_count.toLocaleString()}</span>
              <strong className={getStatusClass(row.event_study_status)}>{row.event_study_status}</strong>
              <span className={row.blocking_reason ? "negative" : ""}>{row.blocking_reason || row.next_eligibility_date || "--"}</span>
            </div>
          )})}
        </div>
      </section>

      <section className="panel wide-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">Gap classification / 缺口分类</span>
            <h2>Recorded and live gaps</h2>
          </div>
        </div>
        <div className="execution-summary">
          {Object.entries(health?.gap_summary?.by_classification || {}).map(([classification, data]) => (
            <div className={`metric-card ${classification === "CRITICAL_LIVE_GAP" ? "tone-warning" : "tone-neutral"}`} key={classification}>
              <span>{classification}</span>
              <strong>{data.count.toLocaleString()}</strong>
              <small>{(data.total_duration_ms / 3_600_000).toFixed(2)}h</small>
            </div>
          ))}
        </div>
      </section>

      {health?.liquidation_health && (
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
              <strong>{health.liquidation_health.event_count.toLocaleString()}</strong>
            </div>
            <div className="metric-card tone-neutral">
              <span>Stream</span>
              <strong className={health.liquidation_health.stream_connected ? "positive" : "negative"}>
                {health.liquidation_health.connection_status}
              </strong>
            </div>
            <div className="metric-card tone-neutral">
              <span>Last genuine event</span>
              <strong>{health.liquidation_health.last_genuine_event_ms ? new Date(health.liquidation_health.last_genuine_event_ms).toLocaleString() : "--"}</strong>
            </div>
            <div className="metric-card tone-neutral">
              <span>Reconnects</span>
              <strong>{health.liquidation_health.reconnect_count}</strong>
            </div>
          </div>
          <p className="muted">{health.liquidation_health.completeness_limitation}</p>
        </section>
      )}

      <section className="panel wide-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">Chronological validation / 时序验证</span>
            <h2>Funding and BTC basis</h2>
          </div>
        </div>
        <div className="demo-note">{validation?.disclaimer || "VALIDATION RESEARCH ONLY — NOT A TRADING SIGNAL"}</div>
        <p className="muted">{validation?.chronological_partition_policy || "No persisted validation report is available yet."}</p>
        {validation?.available && ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"].map((value) => {
          const funding = validation.studies?.funding?.instruments?.[value];
          if (!funding) return null;
          return (
            <div className="trade-table" key={`funding-${value}`} style={{ marginTop: 12 }}>
              <strong>Settled funding · {value}</strong>
              {validationRows(funding).map((row) => (
                <div className="trade-row" key={row.feature}>
                  <span>{row.feature}</span><span>{row.classification}</span>
                  <span>{row.researchCount} calibration</span><span>{row.validationCount} later validation</span>
                  <span>sign stability {row.stability === undefined ? "--" : row.stability.toFixed(2)}</span><span>1H view</span>
                </div>
              ))}
            </div>
          );
        })}
        {validation?.available && (() => {
          const basis = validation.studies?.basis?.instruments?.["BTC-USDT-SWAP"];
          if (!basis) return null;
          return (
            <div className="trade-table" style={{ marginTop: 12 }}>
              <strong>BTC basis only</strong>
              {validationRows(basis).map((row) => (
                <div className="trade-row" key={row.feature}>
                  <span>{row.feature}</span><span>{row.classification}</span>
                  <span>{row.researchCount} calibration</span><span>{row.validationCount} later validation</span>
                  <span>sign stability {row.stability === undefined ? "--" : row.stability.toFixed(2)}</span><span>1H view</span>
                </div>
              ))}
            </div>
          );
        })()}
      </section>

      <div style={{ gridColumn: "1 / -1", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
        <SimpleLineChart data={fundingData} title={t("micro.funding")} />
        <SimpleLineChart data={basisData} title={t("micro.basis")} />
        <SimpleLineChart data={cvdData} title={t("micro.cvd")} />
        <SimpleLineChart data={oiData} title={t("micro.oi")} />
      </div>
    </div>
  );
}
