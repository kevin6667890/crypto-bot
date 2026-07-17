import { useState } from "react";
import { phase4Api, post, pct } from "./api";
import { useLanguage } from "../i18n";
export default function RobustnessPanel() {
  const { t } = useLanguage(),
    [inputRun, setInputRun] = useState(""),
    [simulations, setSimulations] = useState(1000),
    [seed, setSeed] = useState(42),
    [runId, setRunId] = useState<number | null>(null),
    [data, setData] = useState<any>(null),
    [notice, setNotice] = useState("");
  async function run() {
    try {
      const x = await post<{ id: number }>("/api/robustness/run", {
        input_run_id: +inputRun,
        simulation_count: simulations,
        random_seed: seed,
        fee_multipliers: [0.5, 1, 1.5, 2],
        slippage_multipliers: [0.5, 1, 1.5, 2],
        missed_trade_rates: [0, 0.05, 0.1, 0.2],
        execution_delay_bars: [0, 1, 2],
      });
      setRunId(x.id);
      setNotice(t("validation.runQueued", {id: x.id}));
    } catch (e) {
      setNotice(`${t("common.runFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  async function load() {
    if (runId)
      try {
        setData(await phase4Api(`/api/robustness/${runId}`));
      } catch (e) {
        setNotice(`${t("validation.resultNotReady")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
      }
  }
  const r = data?.result || data;
  return (
    <section className="phase4-card">
      <div className="phase4-head">
        <div>
          <span className="eyebrow">{t("validation.perturbation")}</span>
          <h2>{t("validation.robustnessTitle")}</h2>
        </div>
      </div>
      <div className="phase4-controls">
        <label>
          {t("validation.inputRun")}
          <input
            value={inputRun}
            onChange={(e) => setInputRun(e.target.value)}
            placeholder={t("validation.backtestId")}
          />
        </label>
        <label>
          {t("validation.simulations")}
          <input
            type="number"
            min="1"
            max="5000"
            value={simulations}
            onChange={(e) => setSimulations(+e.target.value)}
          />
        </label>
        <label>
          {t("validation.randomSeed")}
          <input
            type="number"
            value={seed}
            onChange={(e) => setSeed(+e.target.value)}
          />
        </label>
        <button onClick={run}>{t("validation.runRobustness")}</button>
        {runId && (
          <button onClick={load}>{t("validation.refreshResult")}</button>
        )}
      </div>
      {notice && <div className="research-alert warning">{notice}</div>}
      {r?.status === "COMPLETED" ? (
        <>
          <div className="research-metrics">
            <article>
              <span>{t("validation.medianReturn")}</span>
              <strong>{pct(r.median_return)}</strong>
            </article>
            <article>
              <span>{t("validation.returnRange")}</span>
              <strong>
                {pct(r.return_percentiles?.p5)} /{" "}
                {pct(r.return_percentiles?.p95)}
              </strong>
            </article>
            <article>
              <span>{t("validation.medianDrawdown")}</span>
              <strong>{pct(r.median_drawdown)}</strong>
            </article>
            <article>
              <span>{t("validation.drawdown95")}</span>
              <strong>{pct(r.p95_drawdown)}</strong>
            </article>
            <article>
              <span>{t("validation.positiveProbability")}</span>
              <strong>{pct(r.probability_positive_return * 100)}</strong>
            </article>
            <article>
              <span>{t("validation.riskOfRuin")}</span>
              <strong>{pct(r.risk_of_ruin * 100)}</strong>
            </article>
          </div>
          <div className="distribution-strip">
            {(r.returns || []).slice(0, 300).map((x: number, i: number) => (
              <i
                key={i}
                className={x >= 0 ? "positive-bar" : "negative-bar"}
                style={{ height: `${Math.min(100, Math.abs(x) * 3 + 3)}%` }}
              />
            ))}
          </div>
        </>
      ) : (
        <div className="research-empty compact">
          {t("validation.selectTrades")}
        </div>
      )}
    </section>
  );
}
