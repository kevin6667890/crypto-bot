import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Database, RefreshCw } from "lucide-react";
import { useLanguage } from "./i18n";

type Job = {
  id: number;
  job_type: string;
  status: string;
  progress: number;
  progress_message?: string;
  message_code?: string;
  message_params?: Record<string, string | number | boolean>;
  queue_position?: number;
  error?: string;
  created_at: string;
  started_at?: string;
};
type Alert = {
  id: number;
  alert_type: string;
  severity: string;
  status: string;
  component: string;
  instrument?: string;
  message: string;
  message_code?: string;
  message_params?: Record<string, string | number | boolean>;
  last_seen: string;
  occurrence_count: number;
};
type Coverage = {
  instrument: string;
  timeframe: string;
  rows: number;
  first_ts: number;
  last_ts: number;
};
type Health = {
  status: string;
  version: string;
  git_commit: string;
  uptime_seconds: number;
  database_status: string;
  database_size_bytes: number;
  database_integrity_status: string;
  paper_scheduler_running: boolean;
  last_cycle_completed_at?: string;
  last_cycle_duration_ms?: number;
  collector_freshness: Record<
    string,
    { updated_at?: string; age_seconds?: number; status: string }
  >;
  active_job?: Job;
  queued_jobs: number;
  deepseek_configured: boolean;
  disk_usage: { percent: number };
  memory_usage: { percent?: number };
  shadow_scheduler_status?: string;
  active_shadow_strategies?: number;
  phase4_database_rows?: number;
  promotion_audit_alerts?: number;
  validation_job_types?: string[];
};
const paperApiBase = (
  window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || ""
).replace(/\/$/, "");

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${paperApiBase}${path}`, options);
  const text = await response.text();
  let body: (T & { error?: string }) | undefined;

  try {
    body = text ? (JSON.parse(text) as T & { error?: string }) : undefined;
  } catch {
    throw new Error(`接口返回的不是 JSON 数据（HTTP ${response.status}）。`);
  }

  if (!response.ok) throw new Error(body?.error || `HTTP ${response.status}`);
  if (!body) throw new Error(`接口返回为空（HTTP ${response.status}）。`);
  return body;
}
export default function Operations() {
  const { t, message, value } = useLanguage();
  const [health, setHealth] = useState<Health | null>(null),
    [jobs, setJobs] = useState<Job[]>([]),
    [alerts, setAlerts] = useState<Alert[]>([]),
    [coverage, setCoverage] = useState<Coverage[]>([]),
    [error, setError] = useState("");
  const [adminToken, setAdminToken] = useState(
    () => sessionStorage.getItem("crypto_bot_admin_token") || ""
  );
  async function refresh() {
    try {
      const [h, j, a, c] = await Promise.all([
        api<Health>("/api/health/details"),
        api<{ items: Job[] }>("/api/jobs"),
        api<{ items: Alert[] }>("/api/alerts"),
        api<{ items: Coverage[] }>("/api/data-coverage"),
      ]);
      setHealth(h);
      setJobs(j.items);
      setAlerts(a.items);
      setCoverage(c.items);
      setError("");
    } catch (e) {
      setError(`${t("operations.refreshFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);
  const open = useMemo(
    () => alerts.filter((a) => a.status === "open"),
    [alerts]
  );
  const headers = (): Record<string, string> => {
    const token = sessionStorage.getItem("crypto_bot_admin_token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  };
  async function action(path: string) {
    try {
      await api(path, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: "{}",
      });
      await refresh();
    } catch (e) {
      setError(`${t("operations.actionFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  const dash = "—";
  return (
    <main className="operations-workspace">
      <section className="operations-hero">
        <div>
          <span className="eyebrow">{t("operations.description")}</span>
          <h1>{t("operations.title")}</h1>
          <p>{t("operations.help")}</p>
        </div>
        <div className="operations-actions">
          <label>
            {t("operations.adminToken")}
            <input
              type="password"
              value={adminToken}
              placeholder={t("operations.tokenOptional")}
              onChange={(e) => {
                setAdminToken(e.target.value);
                if (e.target.value)
                  sessionStorage.setItem(
                    "crypto_bot_admin_token",
                    e.target.value
                  );
                else sessionStorage.removeItem("crypto_bot_admin_token");
              }}
            />
          </label>
          <button className="secondary-btn" onClick={refresh}>
            <RefreshCw size={14} />
            {t("common.refresh")}
          </button>
          <small>{t("operations.httpWarning")}</small>
        </div>
      </section>
      {error && <div className="research-alert error">{error}</div>}
      <div className="operations-grid">
        <section className="operations-card health-overview">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.serviceHealth")}</h2>
            <span className={`status-pill ${health?.status}`}>
              {value(health?.status || "unknown")}
            </span>
          </div>
          <div className="ops-metrics">
            {[
              [t("operations.version"), health?.version],
              [t("operations.gitCommit"), health?.git_commit],
              [
                t("operations.uptime"),
                health ? t("common.minutesShort", {count: Math.floor(health.uptime_seconds / 60)}) : dash,
              ],
              [t("operations.disk"), health ? `${health.disk_usage.percent}%` : dash],
              [
                t("operations.memory"),
                health?.memory_usage.percent !== undefined
                  ? `${health.memory_usage.percent}%`
                  : t("common.notAvailable"),
              ],
            ].map(([l, v]) => (
              <article key={l}>
                <span>{l}</span>
                <b>{v}</b>
              </article>
            ))}
          </div>
        </section>
        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.collectorFreshness")}</h2>
          </div>
          <div className="collector-list">
            {Object.entries(health?.collector_freshness || {}).map(
              ([asset, x]) => (
                <div key={asset}>
                  <b>{asset}</b>
                  <span className={`status-pill ${x.status}`}>
                    {value(x.status)}
                  </span>
                  <small>
                    {x.age_seconds !== undefined
                      ? t("common.secondsShort", {count: Math.round(x.age_seconds)})
                      : t("common.notAvailable")}
                  </small>
                </div>
              )
            )}
          </div>
        </section>
        <section className="operations-card">
          <div className="operations-title">
            <Database size={17} />
            <h2>{t("operations.databaseStatus")}</h2>
          </div>
          <div className="ops-facts">
            <span>
              {t("common.status")}{" "}
              <b>{value(health?.database_status) || dash}</b>
            </span>
            <span>
              {t("operations.integrity")}{" "}
              <b>{value(health?.database_integrity_status) || dash}</b>
            </span>
            <span>
              {t("operations.size")}{" "}
              <b>
                {health
                  ? `${(health.database_size_bytes / 1048576).toFixed(2)} MiB`
                  : dash}
              </b>
            </span>
          </div>
        </section>
        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.runtimeStatus")}</h2>
          </div>
          <div className="ops-facts">
            <span>
              {t("operations.paperScheduler")}{" "}
              <b>
                {value(health?.paper_scheduler_running ? "RUNNING" : "STOPPED")}
              </b>
            </span>
            <span>
              {t("operations.lastCycle")}{" "}
              <b>
                {health?.last_cycle_completed_at
                  ? new Date(health.last_cycle_completed_at).toLocaleString()
                  : dash}
              </b>
            </span>
            <span>
              {t("operations.cycleDuration")}{" "}
              <b>{health?.last_cycle_duration_ms ?? dash} ms</b>
            </span>
            <span>
              {t("operations.ai")}{" "}
              <b>
                {value(health?.deepseek_configured ? "CONFIGURED" : "DISABLED")}
              </b>
            </span>
          </div>
        </section>
      </div>
      <div className="operations-grid">
        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.shadowStatus")}</h2>
          </div>
          <div className="ops-facts">
            <span>
              {t("operations.scheduler")}{" "}
              <b>
                {value(health?.shadow_scheduler_status) ||
                  t("common.notAvailable")}
              </b>
            </span>
            <span>
              {t("operations.activeCandidates")}{" "}
              <b>{health?.active_shadow_strategies ?? 0}</b>
            </span>
            <span>
              {t("operations.shadowFreshness")}{" "}
              <b>
                {health?.last_cycle_completed_at
                  ? new Date(
                      health.last_cycle_completed_at
                    ).toLocaleTimeString()
                  : t("common.notAvailable")}
              </b>
            </span>
          </div>
        </section>
        <section className="operations-card">
          <div className="operations-title">
            <Database size={17} />
            <h2>{t("operations.validation")}</h2>
          </div>
          <div className="ops-facts">
            <span>
              {t("operations.jobTypes")}{" "}
              <b>
                {health?.validation_job_types?.join(", ") ||
                  t("common.notAvailable")}
              </b>
            </span>
            <span>
              {t("operations.phaseRows")}{" "}
              <b>{health?.phase4_database_rows ?? 0}</b>
            </span>
            <span>
              {t("operations.auditAlerts")}{" "}
              <b>{health?.promotion_audit_alerts ?? 0}</b>
            </span>
          </div>
        </section>
      </div>
      <div className="operations-grid">
        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.activeJob")}</h2>
          </div>
          {health?.active_job ? (
            <div className="ops-facts">
              <span>
                {t("operations.job")}{" "}
                <b>
                  #{health.active_job.id} · {value(health.active_job.job_type)}
                </b>
              </span>
              <span>
                {t("operations.progress")} <b>{health.active_job.progress}%</b>
              </span>
            </div>
          ) : (
            <p className="research-empty compact">
              {t("operations.noActiveJob")}
            </p>
          )}
        </section>
        <section className="operations-card">
          <div className="operations-title">
            <Activity size={17} />
            <h2>{t("operations.recentJobs")}</h2>
          </div>
          <div className="ops-facts">
            {jobs
              .filter((j) =>
                ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(
                  j.status
                )
              )
              .slice(0, 4)
              .map((j) => (
                <span key={j.id}>
                  #{j.id} · {value(j.job_type)}
                  <b>{value(j.status)}</b>
                </span>
              ))}
          </div>
        </section>
      </div>
      <section className="operations-card wide">
        <div className="operations-title">
          <Activity size={17} />
          <h2>{t("operations.jobQueue")}</h2>
          <span>{health?.queued_jobs || 0}</span>
        </div>
        <div className="research-table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("operations.job")}</th>
                <th>{t("common.type")}</th>
                <th>{t("common.status")}</th>
                <th>{t("common.queue")}</th>
                <th>{t("operations.progress")}</th>
                <th>{t("common.messageOrError")}</th>
                <th>{t("common.created")}</th>
                <th>{t("common.action")}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.slice(0, 30).map((j) => (
                <tr key={j.id}>
                  <td>#{j.id}</td>
                  <td>{value(j.job_type)}</td>
                  <td>{value(j.status)}</td>
                  <td>{j.queue_position || dash}</td>
                  <td>{j.progress}%</td>
                  <td>
                    {message(
                        j.message_code,
                        j.message_params,
                        j.progress_message
                      ) || j.error ||
                      dash}
                  </td>
                  <td>{new Date(j.created_at).toLocaleString()}</td>
                  <td>
                    {["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(
                      j.status
                    ) && (
                      <button
                        onClick={() => action(`/api/jobs/${j.id}/cancel`)}
                      >
                        {t("common.cancel")}
                      </button>
                    )}
                    {["FAILED", "CANCELLED", "INTERRUPTED"].includes(
                      j.status
                    ) && (
                      <button onClick={() => action(`/api/jobs/${j.id}/retry`)}>
                        {t("common.retry")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="operations-card wide">
        <div className="operations-title">
          <AlertTriangle size={17} />
          <h2>{t("operations.alertCenter")}</h2>
          <span className="alert-count">{open.length}</span>
        </div>
        <div className="alert-list">
          {alerts.length ? (
            alerts.map((a) => (
              <article key={a.id} className={`alert-row ${a.severity}`}>
                <span className={`status-pill ${a.severity}`}>
                  {value(a.severity)}
                </span>
                <div>
                  <b>
                    {value(a.alert_type)}
                    {a.instrument ? ` · ${a.instrument}` : ""}
                  </b>
                  <p>{message(a.message_code, a.message_params, a.message)}</p>
                  <small>
                    {a.component} · {new Date(a.last_seen).toLocaleString()} ·{" "}
                    {a.occurrence_count} · {value(a.status)}
                  </small>
                </div>
                {a.status === "open" && (
                  <button
                    onClick={() => action(`/api/alerts/${a.id}/acknowledge`)}
                  >
                    {t("operations.acknowledge")}
                  </button>
                )}
              </article>
            ))
          ) : (
            <p className="research-empty compact">{t("operations.noAlerts")}</p>
          )}
        </div>
      </section>
      <section className="operations-card wide">
        <div className="operations-title">
          <Database size={17} />
          <h2>{t("operations.dataCoverage")}</h2>
        </div>
        <div className="research-table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("common.instrument")}</th>
                <th>{t("common.timeframe")}</th>
                <th>{t("operations.confirmedRows")}</th>
                <th>{t("operations.firstCandle")}</th>
                <th>{t("operations.lastCandle")}</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((x) => (
                <tr key={`${x.instrument}-${x.timeframe}`}>
                  <td>{x.instrument}</td>
                  <td>{x.timeframe}</td>
                  <td>{x.rows}</td>
                  <td>{new Date(x.first_ts * 1000).toLocaleString()}</td>
                  <td>{new Date(x.last_ts * 1000).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
