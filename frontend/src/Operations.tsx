import { Activity, AlertTriangle, Database, RefreshCw } from "lucide-react";
import { useAsyncResource, type AsyncPhase } from "./asyncResource";
import { useLanguage } from "./i18n";

type OperationsSummary = {
  generated_at: string;
  service: {
    status: string;
    version: string;
    git_commit: string;
    uptime_seconds: number;
  };
  frontend: { status: string };
  paper_api: {
    status: string;
    collector_freshness: Record<
      string,
      { updated_at?: string; age_seconds?: number; status: string }
    >;
  };
  collector: {
    status: string;
    freshness: Record<string, Record<string, number | null>>;
    last_success_data_time?: string;
    queue_depth: number;
    writer: string;
    aggregation: string;
  };
  query_plane: {
    status: string;
    components: Record<string, string>;
  };
  database: {
    status: string;
    quick_status: string;
    logical_size_bytes: number;
    microstructure_logical_size_bytes: number;
  };
  wal_size_bytes: number;
  maintenance: { status: string };
  scheduler: {
    running: boolean;
    last_cycle_completed_at?: string;
    last_cycle_duration_ms?: number;
  };
  tasks: {
    current_count: number;
    queued_count: number;
    recent_completed: Array<{
      id: number;
      job_type: string;
      status: string;
      completed_at?: string;
    }>;
  };
  warning_count: number;
  system: { disk_percent: number; memory_percent?: number };
};

const paperApiBase = (
  window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || ""
).replace(/\/$/, "");

const formatBytes = (value?: number) => {
  if (value === undefined) return "暂时无法获取";
  const units = ["B", "KiB", "MiB", "GiB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
};

function phaseText(phase: AsyncPhase, errorType?: string) {
  if (phase === "LOADING") return "加载中";
  if (phase === "STALE_LAST_SUCCESS") return "显示上次成功数据 · 更新中";
  if (phase === "PERMISSION_REQUIRED") return "需要管理员权限";
  if (phase === "NO_DATA") return "暂无数据";
  if (phase === "UNAVAILABLE")
    return `暂时无法获取${errorType ? ` · ${errorType}` : ""}`;
  return "";
}

export default function Operations() {
  const { t, value } = useLanguage();
  const resource = useAsyncResource<OperationsSummary>(
    "operations-summary",
    `${paperApiBase}/api/operations/summary`,
    { intervalMs: 15_000, timeoutMs: 5_000 },
  );
  const summary = resource.data;
  const phaseMessage = phaseText(resource.phase, resource.errorType);
  const unavailable = !summary;
  const secureTransport = window.location.protocol === "https:";

  return (
    <main className="operations-workspace">
      <section className="operations-hero">
        <div>
          <span className="eyebrow">{t("operations.description")}</span>
          <h1>{t("operations.title")}</h1>
          <p>
            公开只读运行摘要。采集数据面与查询服务状态独立展示，查询延迟不会被解释为采集停止。
          </p>
        </div>
        <div className="operations-actions">
          <button className="secondary-btn" onClick={resource.refresh}>
            <RefreshCw size={14} />
            {t("common.refresh")}
          </button>
          <small>
            {secureTransport
              ? "管理操作需在独立认证会话中执行。"
              : "普通 HTTP 不接收、不保存管理员令牌；管理操作已隐藏。"}
          </small>
        </div>
      </section>

      {phaseMessage && (
        <div
          className={`research-alert ${
            resource.phase === "UNAVAILABLE" ? "error" : ""
          }`}
          data-state={resource.phase}
        >
          {phaseMessage}
          {resource.dataAsOf ? ` · 数据截至 ${resource.dataAsOf}` : ""}
        </div>
      )}

      <div className="operations-grid">
        <section className="operations-card health-overview">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.serviceHealth")}</h2>
            <span className={`status-pill ${summary?.service.status.toLowerCase() || ""}`}>
              {unavailable ? phaseText(resource.phase, resource.errorType) : value(summary?.service.status || "")}
            </span>
          </div>
          <div className="ops-metrics">
            <div><span>{t("operations.version")}</span><b>{summary?.service.version ?? "加载中"}</b></div>
            <div><span>{t("operations.gitCommit")}</span><b>{summary?.service.git_commit ?? "加载中"}</b></div>
            <div><span>{t("operations.paperApi")}</span><b>{summary?.paper_api.status ?? "加载中"}</b></div>
            <div><span>Frontend</span><b>{summary?.frontend.status ?? "加载中"}</b></div>
            <div><span>{t("operations.uptime")}</span><b>{summary ? `${summary.service.uptime_seconds}s` : "加载中"}</b></div>
            <div><span>{t("operations.disk")}</span><b>{summary ? `${summary.system.disk_percent}%` : "加载中"}</b></div>
            <div><span>{t("operations.memory")}</span><b>{summary?.system.memory_percent !== undefined ? `${summary.system.memory_percent}%` : "暂时无法获取"}</b></div>
          </div>
        </section>

        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>数据采集面</h2>
            <span className={`status-pill ${summary?.collector.status.toLowerCase() || ""}`}>
              {summary?.collector.status ?? "加载中"}
            </span>
          </div>
          <p>Writer：<b>{summary?.collector.writer ?? "加载中"}</b></p>
          <p>Aggregation：<b>{summary?.collector.aggregation ?? "加载中"}</b></p>
          <p>Queue：<b>{summary?.collector.queue_depth ?? "加载中"}</b></p>
          <small>上次成功数据：{summary?.collector.last_success_data_time ?? "加载中"}</small>
        </section>

        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>查询服务面</h2>
            <span className={`status-pill ${summary?.query_plane.status.toLowerCase() || ""}`}>
              {summary?.query_plane.status ?? "加载中"}
            </span>
          </div>
          {summary
            ? Object.entries(summary.query_plane.components).map(([name, status]) => (
                <p key={name}><span>{name}</span> <b>{status}</b></p>
              ))
            : <p>加载中</p>}
        </section>

        <section className="operations-card">
          <div className="operations-title">
            <Database size={17} />
            <h2>{t("operations.databaseStatus")}</h2>
          </div>
          <p>状态：<b>{summary?.database.status ?? "加载中"}</b></p>
          <p>Quick check：<b>{summary?.database.quick_status ?? "加载中"}</b></p>
          <p>Paper DB：<b>{formatBytes(summary?.database.logical_size_bytes)}</b></p>
          <p>Microstructure DB：<b>{formatBytes(summary?.database.microstructure_logical_size_bytes)}</b></p>
          <p>WAL：<b>{formatBytes(summary?.wal_size_bytes)}</b></p>
        </section>

        <section className="operations-card">
          <div className="operations-title">
            <RefreshCw size={17} />
            <h2>{t("operations.runtimeStatus")}</h2>
          </div>
          <p>Maintenance：<b>{summary?.maintenance.status ?? "加载中"}</b></p>
          <p>Scheduler：<b>{summary ? (summary.scheduler.running ? "RUNNING" : "STOPPED") : "加载中"}</b></p>
          <p>最近周期：<b>{summary?.scheduler.last_cycle_completed_at ?? "暂无数据"}</b></p>
          <p>周期耗时：<b>{summary?.scheduler.last_cycle_duration_ms !== undefined ? `${summary.scheduler.last_cycle_duration_ms} ms` : "暂无数据"}</b></p>
        </section>

        <section className="operations-card">
          <div className="operations-title">
            <AlertTriangle size={17} />
            <h2>任务与警告</h2>
            <span className="alert-count">{summary?.warning_count ?? 0}</span>
          </div>
          <p>当前任务：<b>{summary?.tasks.current_count ?? "加载中"}</b></p>
          <p>排队任务：<b>{summary?.tasks.queued_count ?? "加载中"}</b></p>
          {summary?.tasks.recent_completed.map((job) => (
            <small key={job.id}>#{job.id} · {job.job_type} · {job.status}</small>
          ))}
        </section>
      </div>
    </main>
  );
}
