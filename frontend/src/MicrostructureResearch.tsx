import { useCallback, useEffect, useRef, useState } from "react";
import { ColorType, createChart, IChartApi, ISeriesApi, LineSeries, UTCTimestamp } from "lightweight-charts";
import { useLanguage } from "./i18n";

interface HealthResponse {
  service_status: string;
  database_size_bytes: number;
  gap_count: number;
  raw_rows: number;
  aggregate_rows: number;
  sample_status: string;
  next_eligibility?: string;
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
  data?: ChartPoint[];
}

interface ValidationSegment {
  event_count?: number;
  sign_consistency?: number;
}

interface ValidationInstrument {
  feature_classifications?: Record<string, string>;
  features?: Record<string, Record<string, {
    segments?: Record<string, ValidationSegment>;
  }>>;
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

type Instrument = "BTC-USDT-SWAP" | "ETH-USDT-SWAP" | "SOL-USDT-SWAP";
type ResearchChart = "funding" | "basis" | "cvd" | "oi";

const INSTRUMENTS: Instrument[] = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];
const SOURCE_ORDER: Array<keyof CoverageResponse> = ["trades", "oi", "funding_predicted", "mark", "index", "liquidations"];
const CHARTS: ResearchChart[] = ["funding", "basis", "cvd", "oi"];

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
    const chart = createChart(containerRef.current, { ...chartTheme, autoSize: true });
    seriesRef.current = chart.addSeries(LineSeries, {
      color: "#3b82f6", lineWidth: 2, crosshairMarkerVisible: true,
      lastValueVisible: true, priceLineVisible: false,
    });
    chartRef.current = chart;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !data.length) return;
    const sorted = [...data].sort((a, b) => a.time - b.time);
    const unique = sorted.filter((value, index) => index === 0 || value.time !== sorted[index - 1].time);
    seriesRef.current.setData(unique.map(point => ({
      time: (point.time / 1000) as UTCTimestamp,
      value: point.value,
    })));
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div className="micro-chart-panel">
      <div className="micro-chart-title">{title}</div>
      <div className="micro-chart-canvas" ref={containerRef} />
    </div>
  );
}

function StatusPill({ value, className = "" }: { value: string; className?: string }) {
  return <span className={`micro-status ${className}`}>{value || "--"}</span>;
}

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${parseFloat((bytes / 1024 ** index).toFixed(2))} ${sizes[index]}`;
}

function formatTime(value?: number) {
  return value ? new Date(value).toLocaleString() : "--";
}

function validationRows(item?: ValidationInstrument) {
  return Object.entries(item?.features || {}).map(([feature, horizons]) => {
    const preferred = horizons["1H"] || Object.values(horizons)[0] || {};
    const calibration = preferred.segments?.RESEARCH_CALIBRATION;
    const later = preferred.segments?.LATER_VALIDATION;
    return {
      feature,
      classification: item?.feature_classifications?.[feature] || "INSUFFICIENT_SAMPLE",
      calibration: calibration?.event_count || 0,
      later: later?.event_count || 0,
      stability: later?.sign_consistency,
    };
  });
}

function validationHeadline(item?: ValidationInstrument) {
  const rows = validationRows(item);
  if (!rows.length) return { classification: "--", calibration: 0, later: 0, stability: "--" };
  const classification = [...new Set(rows.map(row => row.classification))].join(" / ");
  const calibration = rows.reduce((total, row) => total + row.calibration, 0);
  const later = rows.reduce((total, row) => total + row.later, 0);
  const stableRows = rows.filter(row => row.stability !== undefined);
  const stability = stableRows.length
    ? (stableRows.reduce((total, row) => total + (row.stability || 0), 0) / stableRows.length).toFixed(2)
    : "--";
  return { classification, calibration, later, stability };
}

function ValidationDetails({ item }: { item?: ValidationInstrument }) {
  return (
    <div className="micro-table-scroll">
      <div className="micro-validation-table">
        <div className="micro-validation-row head">
          <span>Feature</span><span>Horizon</span><span>Classification</span>
          <span>Calibration</span><span>Later validation</span><span>Sign stability</span>
        </div>
        {Object.entries(item?.features || {}).flatMap(([feature, horizons]) =>
          Object.entries(horizons).map(([horizon, result]) => {
            const calibration = result.segments?.RESEARCH_CALIBRATION;
            const later = result.segments?.LATER_VALIDATION;
            return (
              <div className="micro-validation-row" key={`${feature}-${horizon}`}>
                <strong>{feature}</strong><span>{horizon}</span>
                <span>{item?.feature_classifications?.[feature] || "INSUFFICIENT_SAMPLE"}</span>
                <span>{calibration?.event_count || 0}</span><span>{later?.event_count || 0}</span>
                <span>{later?.sign_consistency === undefined ? "--" : later.sign_consistency.toFixed(2)}</span>
              </div>
            );
          }),
        )}
      </div>
    </div>
  );
}

export default function MicrostructureResearch() {
  const { language, t } = useLanguage();
  const zh = language === "zh";
  const copy = {
    overview: zh ? "顶部概览" : "Overview",
    critical: zh ? "关键实时缺口" : "Critical live gaps",
    recoverable: zh ? "可回填缺口" : "Recoverable gaps",
    historical: zh ? "历史来源限制" : "Historical source limits",
    legacy: "Legacy boundary",
    collection: zh ? "采集健康" : "Collection health",
    allSources: zh ? "全部来源" : "All sources",
    source: zh ? "来源" : "Source",
    earliest: zh ? "最早" : "Earliest",
    latest: zh ? "最新" : "Latest",
    rows: zh ? "行数" : "Rows",
    state: zh ? "状态" : "State",
    settled: zh ? "Settled Funding 事件调度" : "Settled funding schedule",
    onSchedule: zh ? "按计划" : "On schedule",
    latestSettlement: zh ? "最近结算" : "Latest settlement",
    nextSettlement: zh ? "下次预计结算" : "Next expected settlement",
    eligibility: zh ? "特征可用性" : "Feature availability",
    group: zh ? "特征组" : "Feature group",
    naturalDays: zh ? "自然覆盖天数" : "Natural source days",
    usableDays: zh ? "Gap-adjusted usable days" : "Gap-adjusted usable days",
    sourceStatus: "Source status",
    eventStatus: "Event-study status",
    overlap: zh ? "Label overlap / events" : "Label overlap / events",
    next: zh ? "Next eligibility / blocker" : "Next eligibility / blocker",
    validation: zh ? "研究验证" : "Research validation",
    summary: zh ? "摘要" : "Summary",
    details: zh ? "展开全部 horizon 结果" : "Show all horizon results",
    charts: zh ? "研究图表" : "Research charts",
    conclusion: zh ? "主要结论" : "Conclusion",
    warnings: zh ? "采集提示与来源限制" : "Collector notes and source limits",
  };

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [eligibility, setEligibility] = useState<EligibilityResponse | null>(null);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [instrument, setInstrument] = useState<Instrument>("BTC-USDT-SWAP");
  const [activeChart, setActiveChart] = useState<ResearchChart>("funding");
  const [chartData, setChartData] = useState<Record<ResearchChart, ChartPoint[]>>({
    funding: [], basis: [], cvd: [], oi: [],
  });

  const fetchAll = useCallback(async () => {
    const load = async <T,>(url: string, apply: (payload: T) => void) => {
      try {
        const response = await fetch(url);
        if (response.ok) apply(await response.json());
      } catch (error) {
        console.error(`Microstructure API error: ${url}`, error);
      }
    };
    const applyChart = (key: ResearchChart) => (payload: ChartResponse) => {
      const points = payload.points || (payload.data || []).map(point => ({ ...point, time: point.time * 1000 }));
      setChartData(current => ({ ...current, [key]: points }));
    };
    await Promise.all([
      load<HealthResponse>("/api/research/microstructure/health", setHealth),
      load<CoverageResponse>("/api/research/microstructure/coverage", setCoverage),
      load<EligibilityResponse>("/api/research/microstructure/eligibility", setEligibility),
      load<ValidationResponse>("/api/research/microstructure/validation", setValidation),
      ...CHARTS.map(chart => load<ChartResponse>(
        `/api/research/microstructure/charts/${chart}?instrument=${instrument}&limit=500`,
        applyChart(chart),
      )),
    ]);
  }, [instrument]);

  useEffect(() => {
    void fetchAll();
    const timer = window.setInterval(fetchAll, 60_000);
    return () => window.clearInterval(timer);
  }, [fetchAll]);

  const statusClass = (status = "") => {
    const value = status.toUpperCase();
    if (value.includes("READY") || value.includes("LIVE") || value.includes("HEALTHY")) return "pass";
    if (value.includes("EXPLORATORY") || value.includes("MINIMUM") || value.includes("PARTIAL")) return "warning";
    if (value.includes("ERROR") || value.includes("OVERDUE") || value.includes("CRITICAL")) return "negative";
    return "";
  };
  const classification = health?.gap_summary?.by_classification || {};
  const classificationCount = (matcher: RegExp) => Object.entries(classification)
    .filter(([name]) => matcher.test(name))
    .reduce((total, [, value]) => total + value.count, 0);
  const gapSummary = [
    [copy.critical, health?.gap_summary?.critical_live_gaps?.length ?? classificationCount(/CRITICAL|LIVE/i), "critical"],
    [copy.recoverable, classificationCount(/RECOVER|BACKFILL|TRANSIENT/i), "recoverable"],
    [copy.historical, classificationCount(/HISTOR|SOURCE|LIMIT/i), "historical"],
    [copy.legacy, classificationCount(/LEGACY|BOUNDARY/i), "legacy"],
  ] as const;
  const selectedCoverage = (source: keyof CoverageResponse) =>
    coverage?.[source]?.find(item => item.instrument === instrument);
  const fundingSchedule = health?.funding_schedule?.[instrument];
  const fundingValidation = validation?.studies?.funding?.instruments?.[instrument];
  const basisValidation = validation?.studies?.basis?.instruments?.["BTC-USDT-SWAP"];
  const fundingHeadline = validationHeadline(fundingValidation);
  const basisHeadline = validationHeadline(basisValidation);

  return (
    <div className="microstructure-workspace" id="microstructure">
      <div className="micro-disclaimer">{t("micro.disclaimer")}</div>

      <section className="micro-section micro-overview">
        <div className="micro-section-head">
          <div><span className="eyebrow">{copy.overview}</span><h1>{t("micro.title")}</h1></div>
        </div>
        <div className="micro-metrics">
          <article><span>{t("micro.serviceStatus")}</span><strong>{health?.service_status || "--"}</strong></article>
          <article><span>{t("micro.databaseSize")}</span><strong>{health ? formatBytes(health.database_size_bytes) : "--"}</strong></article>
          <article><span>{t("micro.rawRows")}</span><strong>{health?.raw_rows?.toLocaleString() ?? "--"}</strong></article>
          <article><span>{t("micro.aggregateRows")}</span><strong>{health?.aggregate_rows?.toLocaleString() ?? "--"}</strong></article>
          <article className={(health?.gap_summary?.critical_live_gaps?.length || 0) > 0 ? "critical" : ""}>
            <span>{copy.critical}</span><strong>{health?.gap_summary?.critical_live_gaps?.length ?? "--"}</strong>
          </article>
          <article><span>{t("micro.sampleStatus")}</span><strong>{health?.sample_status || "--"}</strong></article>
        </div>
        <div className="micro-gap-categories">
          {gapSummary.map(([label, count, kind]) => (
            <div className={kind} key={kind}><span>{label}</span><strong>{count}</strong></div>
          ))}
        </div>
        {!!health?.collector_warnings?.length && (
          <details className="micro-details">
            <summary>{copy.warnings} <span>{health.collector_warnings.length}</span></summary>
            <div className="micro-warning-list">
              {health.collector_warnings.map((warning, index) => (
                <p className={warning.severity === "critical" ? "critical" : ""} key={`${warning.code}-${index}`}>
                  <strong>{warning.code}</strong> {warning.instrument ? `${warning.instrument} · ` : ""}{warning.message}
                </p>
              ))}
            </div>
          </details>
        )}
      </section>

      <section className="micro-section">
        <div className="micro-section-head">
          <div><span className="eyebrow">{copy.collection}</span><h2>{copy.collection}</h2></div>
          <div className="micro-tabs" role="tablist" aria-label={copy.collection}>
            {INSTRUMENTS.map(value => (
              <button role="tab" aria-selected={instrument === value} className={instrument === value ? "active" : ""}
                key={value} onClick={() => setInstrument(value)}>{value.split("-")[0]}</button>
            ))}
          </div>
        </div>
        <div className="micro-source-grid">
          {SOURCE_ORDER.map(source => {
            const item = selectedCoverage(source);
            const isLiquidation = source === "liquidations";
            const lag = item ? Date.now() - item.latest_ms : undefined;
            const state = isLiquidation
              ? health?.liquidation_health?.connection_status || "--"
              : lag === undefined ? "--" : lag <= 60_000 ? "LIVE" : `${Math.round(lag / 1000)}s`;
            return (
              <article key={source}>
                <div><strong>{source.replace("funding_predicted", "predicted funding")}</strong><StatusPill value={state} className={statusClass(state)} /></div>
                <span>{item ? `${item.rows.toLocaleString()} ${copy.rows.toLowerCase()}` : "--"}</span>
                <small>{copy.latest}: {formatTime(item?.latest_ms)}</small>
              </article>
            );
          })}
        </div>
        <div className="micro-settlement">
          <strong>{copy.settled}</strong>
          <StatusPill value={fundingSchedule ? fundingSchedule.overdue ? "OVERDUE" : copy.onSchedule : "--"}
            className={fundingSchedule ? fundingSchedule.overdue ? "negative" : "pass" : ""} />
          <span>{copy.latestSettlement}: <b>{formatTime(fundingSchedule?.latest_settlement_ms)}</b></span>
          <span>{copy.nextSettlement}: <b>{formatTime(fundingSchedule?.next_expected_settlement_ms)}</b></span>
        </div>
        <details className="micro-details" data-testid="all-sources-details">
          <summary>{copy.allSources}</summary>
          <div className="micro-table-scroll">
            <div className="micro-coverage-table">
              <div className="micro-coverage-row head">
                <span>{copy.source}</span><span>{t("micro.instrument")}</span><span>{copy.earliest}</span>
                <span>{copy.latest}</span><span>{copy.rows}</span><span>{copy.state}</span>
              </div>
              {coverage && Object.entries(coverage).flatMap(([source, items]) =>
                (items as CoverageItem[]).map(item => {
                  const schedule = health?.funding_schedule?.[item.instrument];
                  const state = source === "funding_settled"
                    ? schedule?.overdue ? "OVERDUE" : copy.onSchedule
                    : source === "liquidations" ? "event-based"
                      : `${Math.max(0, Math.round((Date.now() - item.latest_ms) / 1000))}s`;
                  return (
                    <div className="micro-coverage-row" key={`${source}-${item.instrument}`}>
                      <strong>{source}</strong><span>{item.instrument}</span><span>{formatTime(item.earliest_ms)}</span>
                      <span>{formatTime(item.latest_ms)}</span><span>{item.rows.toLocaleString()}</span>
                      <StatusPill value={state} className={statusClass(state)} />
                    </div>
                  );
                }),
              )}
            </div>
          </div>
        </details>
      </section>

      <section className="micro-section">
        <div className="micro-section-head">
          <div><span className="eyebrow">{copy.eligibility}</span><h2>{copy.eligibility}</h2></div>
          <div className="micro-tabs compact" role="tablist" aria-label={copy.eligibility}>
            {INSTRUMENTS.map(value => (
              <button role="tab" aria-selected={instrument === value} className={instrument === value ? "active" : ""}
                key={value} onClick={() => setInstrument(value)}>{value.split("-")[0]}</button>
            ))}
          </div>
        </div>
        <div className="micro-table-scroll">
          <div className="micro-feature-table">
            <div className="micro-feature-row head">
              <span>{copy.group}</span><span>{copy.sourceStatus}</span><span>{copy.naturalDays}</span>
              <span>{copy.usableDays}</span><span>{copy.eventStatus}</span><span>{copy.overlap}</span><span>{copy.next}</span>
            </div>
            {eligibility && Object.entries(eligibility.feature_groups).map(([group, data]) => {
              const row = data.instruments?.[instrument];
              if (!row) return null;
              return (
                <div className="micro-feature-row" key={`${group}-${instrument}`}>
                  <div><strong>{group}</strong><small title={data.features.join(", ")}>{data.features.join(" · ")}</small></div>
                  <StatusPill value={row.source_data_status} className={statusClass(row.source_data_status)} />
                  <span>{row.source_days.toFixed(2)}d</span><span>{row.gap_adjusted_usable_days.toFixed(2)}d</span>
                  <StatusPill value={row.event_study_status} className={statusClass(row.event_study_status)} />
                  <span>{row.overlap_usable_days.toFixed(2)}d / {row.event_count.toLocaleString()}</span>
                  <span className={row.blocking_reason ? "negative" : ""}>{row.blocking_reason || row.next_eligibility_date || "--"}</span>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <section className="micro-section">
        <div className="micro-section-head">
          <div><span className="eyebrow">{copy.validation}</span><h2>{copy.validation}</h2></div>
        </div>
        <div className="micro-validation-note">{validation?.disclaimer || "VALIDATION RESEARCH ONLY — NOT A TRADING SIGNAL"}</div>
        <p className="micro-muted">{validation?.chronological_partition_policy || "No persisted validation report is available yet."}</p>
        <div className="micro-validation-groups">
          <details className="micro-details validation" data-testid="funding-validation-details">
            <summary>
              <strong>Settled Funding validation · {instrument.split("-")[0]}</strong>
              <span className="micro-validation-headline">
                {fundingHeadline.classification} · calibration {fundingHeadline.calibration} · later {fundingHeadline.later} · sign {fundingHeadline.stability}
              </span>
            </summary>
            <div className="micro-validation-summary">
              {validationRows(fundingValidation).map(row => (
                <article key={row.feature}>
                  <strong>{row.feature}</strong><StatusPill value={row.classification} className={statusClass(row.classification)} />
                  <span>Calibration <b>{row.calibration}</b></span><span>Later <b>{row.later}</b></span>
                  <span>Sign stability <b>{row.stability === undefined ? "--" : row.stability.toFixed(2)}</b></span>
                </article>
              ))}
              {!validationRows(fundingValidation).length && <p className="micro-muted">--</p>}
            </div>
            <details className="micro-details nested"><summary>{copy.details}</summary><ValidationDetails item={fundingValidation} /></details>
          </details>
          <details className="micro-details validation" data-testid="basis-validation-details">
            <summary>
              <strong>BTC Basis validation</strong>
              <span className="micro-validation-headline">
                {basisHeadline.classification} · calibration {basisHeadline.calibration} · later {basisHeadline.later} · sign {basisHeadline.stability}
              </span>
            </summary>
            <div className="micro-validation-summary">
              {validationRows(basisValidation).map(row => (
                <article key={row.feature}>
                  <strong>{row.feature}</strong><StatusPill value={row.classification} className={statusClass(row.classification)} />
                  <span>Calibration <b>{row.calibration}</b></span><span>Later <b>{row.later}</b></span>
                  <span>Sign stability <b>{row.stability === undefined ? "--" : row.stability.toFixed(2)}</b></span>
                </article>
              ))}
              {!validationRows(basisValidation).length && <p className="micro-muted">--</p>}
            </div>
            <details className="micro-details nested"><summary>{copy.details}</summary><ValidationDetails item={basisValidation} /></details>
          </details>
        </div>
      </section>

      <section className="micro-section">
        <div className="micro-section-head">
          <div><span className="eyebrow">{copy.charts}</span><h2>{copy.charts}</h2></div>
          <div className="micro-tabs chart-tabs" role="tablist" aria-label={copy.charts}>
            {CHARTS.map(chart => (
              <button role="tab" aria-selected={activeChart === chart} className={activeChart === chart ? "active" : ""}
                key={chart} onClick={() => setActiveChart(chart)}>{t(`micro.${chart}` as "micro.funding")}</button>
            ))}
          </div>
        </div>
        <SimpleLineChart data={chartData[activeChart]} title={`${t(`micro.${activeChart}` as "micro.funding")} · ${instrument}`} />
      </section>
    </div>
  );
}
