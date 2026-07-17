import { useEffect, useState } from "react";
import { phase4Api, post, pct } from "./api";
import { useLanguage } from "../i18n";
type Gate = {
  gate: string;
  label: string;
  evaluated_count: number;
  pass_count: number;
  fail_count: number;
  sequential_pass_count: number;
  pass_rate: number | null;
  conditional_pass_rate: number | null;
  signals_lost: number;
  exclusive_failure_count: number;
  combined_failure_count: number;
  long_pass_rate: number | null;
  short_pass_rate: number | null;
  per_asset_pass_rate: Record<string, number>;
};
type Funnel = {
  decision_count: number;
  gates: Gate[];
  top_rejection_reasons: Array<{ gate: string; label: string; count: number }>;
  score_distribution: Array<{ bucket: string; count: number }>;
  daily_rejection_timeline: Array<{ date: string; total: number }>;
  summary?: Funnel;
};
export default function GateFunnel() {
  const { t, value } = useLanguage(),
    [data, setData] = useState<Funnel | null>(null),
    [instrument, setInstrument] = useState("ALL"),
    [source, setSource] = useState("ALL"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function load() {
    try {
      setData(
        await phase4Api<Funnel>(
          `/api/validation/gates?${new URLSearchParams({ instrument, source })}`
        )
      );
      setError("");
    } catch (e) {
      setError(`${t("validation.loadFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  useEffect(() => {
    load();
  }, [instrument, source]);
  async function aggregate() {
    setBusy(true);
    try {
      const run = await post<{ id: number }>("/api/validation/gates/run", {
        instrument,
        source,
      });
      setError(t("validation.gateQueued", { id: run.id }));
    } catch (e) {
      setError(`${t("common.runFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    } finally {
      setBusy(false);
    }
  }
  const view = data?.summary || data;
  return (
    <div className="phase4-stack">
      <section className="phase4-card">
        <div className="phase4-head">
          <div>
            <span className="eyebrow">{t("validation.completePayload")}</span>
            <h2>{t("validation.gateFunnel")}</h2>
          </div>
          <div className="phase4-controls">
            <select
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
            >
              <option>ALL</option>
              <option>BTC-USDT</option>
              <option>ETH-USDT</option>
              <option>SOL-USDT</option>
            </select>
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option>ALL</option>
              <option>PAPER</option>
              <option>BACKTEST</option>
            </select>
            <button onClick={aggregate} disabled={busy}>
              {busy ? t("common.loading") : t("common.refresh")}
            </button>
          </div>
        </div>
        {error && <div className="research-alert warning">{error}</div>}
        {!view?.decision_count ? (
          <div className="research-empty compact">
            <strong>{t("validation.noPayload")}</strong>
            <span>{t("validation.noProxy")}</span>
          </div>
        ) : (
          <>
            <div className="funnel-chart">
              {view.gates.map((g) => (
                <div key={g.gate}>
                  <span>{value(g.gate)}</span>
                  <i>
                    <b style={{ width: `${g.conditional_pass_rate || 0}%` }} />
                  </i>
                  <strong>{g.sequential_pass_count ?? g.pass_count}</strong>
                </div>
              ))}
            </div>
            <div className="research-table-wrap">
              <table>
                <thead>
                  <tr>
                    {["Gate", "#", "✓", "×", "%", "Δ", "LONG", "SHORT"].map(
                      (x) => (
                        <th key={x}>{x}</th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {view.gates.map((g) => (
                    <tr key={g.gate}>
                      <td>{value(g.gate)}</td>
                      <td>{g.evaluated_count}</td>
                      <td>{g.pass_count}</td>
                      <td>{g.fail_count}</td>
                      <td>{pct(g.conditional_pass_rate)}</td>
                      <td>{g.signals_lost}</td>
                      <td>{pct(g.long_pass_rate)}</td>
                      <td>{pct(g.short_pass_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      <div className="phase4-grid">
        <section className="phase4-card">
          <h2>{t("validation.perAsset")}</h2>
          {view?.gates.slice(0, 15).map((g) => (
            <div className="comparison-row" key={g.gate}>
              <span>{value(g.gate)}</span>
              {Object.entries(g.per_asset_pass_rate || {}).map(
                ([asset, rate]) => (
                  <small key={asset}>
                    {asset.replace("-USDT", "")} {pct(rate)}
                  </small>
                )
              )}
            </div>
          ))}
        </section>
        <section className="phase4-card">
          <h2>{t("validation.rejectionReasons")}</h2>
          {view?.top_rejection_reasons?.length ? (
            view.top_rejection_reasons.slice(0, 10).map((x) => (
              <div className="comparison-row" key={x.gate}>
                <span>{value(x.gate)}</span>
                <b>{x.count}</b>
              </div>
            ))
          ) : (
            <div className="research-empty compact">
              {t("validation.noRejections")}
            </div>
          )}
        </section>
        <section className="phase4-card">
          <h2>{t("validation.scoreDistribution")}</h2>
          <div className="mini-bars">
            {view?.score_distribution?.map((x) => (
              <div
                key={x.bucket}
                title={`${x.bucket}: ${x.count}`}
                style={{
                  height: `${Math.max(
                    4,
                    (x.count /
                      Math.max(
                        ...view.score_distribution.map((y) => y.count)
                      )) *
                      100
                  )}%`,
                }}
              >
                <span>{x.bucket}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="phase4-card">
          <h2>{t("validation.rejectionTimeline")}</h2>
          <div className="timeline-list">
            {view?.daily_rejection_timeline?.slice(-14).map((x) => (
              <div key={x.date}>
                <span>{x.date}</span>
                <i style={{ width: `${Math.min(100, x.total)}%` }} />
                <b>{x.total}</b>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
