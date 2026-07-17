import { useEffect, useState } from "react";
import { DEFAULT_RESEARCH_PARAMETERS } from "../research";
import { phase4Api, post, pct } from "../validation/api";
import { useLanguage } from "../i18n";
type Shadow = {
  shadow_strategy_id: string;
  name: string;
  status: string;
  strategy_version: string;
  config_hash: string;
  started_at: string | null;
  open_positions: Record<string, unknown>;
  closed_trades: number;
  total_r: number;
  fees: number;
  drawdown: number;
  current_equity: number;
  virtual_initial_capital: number;
};
const presets: Record<string, Record<string, unknown>> = {
  Conservative: { ...DEFAULT_RESEARCH_PARAMETERS, minimum_score: 85 },
  Balanced: DEFAULT_RESEARCH_PARAMETERS,
  Aggressive: { ...DEFAULT_RESEARCH_PARAMETERS, minimum_score: 70 },
};
export default function ShadowExperiments() {
  const { t, value } = useLanguage(),
    [items, setItems] = useState<Shadow[]>([]),
    [notice, setNotice] = useState("");
  async function load() {
    try {
      setItems(
        (await phase4Api<{ items: Shadow[] }>("/api/shadow-strategies")).items
      );
      setNotice("");
    } catch (e) {
      setNotice(`${t("shadow.operationFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, []);
  async function create(name: string) {
    try {
      await post("/api/shadow-strategies", {
        name,
        parameters: presets[name],
        instruments: ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        virtual_initial_capital: 10000,
      });
      await load();
    } catch (e) {
      setNotice(`${t("shadow.operationFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  async function action(item: Shadow, verb: string) {
    try {
      await post(
        `/api/shadow-strategies/${item.shadow_strategy_id}/${verb}`,
        {}
      );
      await load();
    } catch (e) {
      setNotice(`${t("shadow.operationFailed")} ${t("common.technicalDetail", {detail:e instanceof Error?e.message:String(e)})}`);
    }
  }
  return (
    <>
      <section className="research-command">
        <div>
          <span className="eyebrow">{t("shadow.description")}</span>
          <h1>{t("shadow.title")}</h1>
          <p>{t("shadow.safety")}</p>
        </div>
        <div className="phase4-controls">
          {Object.keys(presets).map((name) => (
            <button key={name} onClick={() => create(name)}>
              {t("common.new")} {t(`shadow.preset.${name}` as "shadow.preset.Conservative"|"shadow.preset.Balanced"|"shadow.preset.Aggressive")}
            </button>
          ))}
        </div>
      </section>
      {notice && <div className="research-alert warning">{notice}</div>}
      <section className="phase4-card">
        <div className="phase4-head">
          <div>
            <span className="eyebrow">{t("shadow.candidateComparison")}</span>
            <h2>{t("shadow.active")}</h2>
          </div>
          <button onClick={load}>{t("common.refresh")}</button>
        </div>
        {!items.length ? (
          <div className="research-empty">
            <strong>{t("shadow.noData")}</strong>
            <span>{t("shadow.createHelp")}</span>
          </div>
        ) : (
          <div className="research-table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    t("shadow.candidate"),
                    t("common.status"),
                    t("shadow.versionConfig"),
                    t("common.start"),
                    t("paper.open"),
                    t("common.trades"),
                    t("common.return"),
                    "R",
                    t("common.drawdown"),
                    t("common.fees"),
                    t("common.value"),
                    t("common.action"),
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((x) => (
                  <tr key={x.shadow_strategy_id}>
                    <td>{x.name}</td>
                    <td>{value(x.status)}</td>
                    <td>
                      {x.strategy_version}
                      <small className="hash">
                        {x.config_hash.slice(0, 12)}
                      </small>
                    </td>
                    <td>
                      {x.started_at
                        ? new Date(x.started_at).toLocaleString()
                        : "—"}
                    </td>
                    <td>
                      {
                        Object.keys(x.open_positions || {}).filter(
                          (k) => !k.endsWith(":pending")
                        ).length
                      }
                    </td>
                    <td>{x.closed_trades}</td>
                    <td>
                      {pct(
                        (x.current_equity / x.virtual_initial_capital - 1) * 100
                      )}
                    </td>
                    <td>{x.total_r?.toFixed(2) ?? "0.00"}</td>
                    <td>{pct(x.drawdown)}</td>
                    <td>${x.fees?.toFixed(2) ?? "0.00"}</td>
                    <td>${x.current_equity?.toFixed(2)}</td>
                    <td>
                      <div className="row-actions">
                        {x.status === "DRAFT" && (
                          <button onClick={() => action(x, "start")}>
                            {t("common.start")}
                          </button>
                        )}
                        {x.status === "RUNNING" && (
                          <button onClick={() => action(x, "pause")}>
                            {t("common.pause")}
                          </button>
                        )}
                        {x.status === "PAUSED" && (
                          <button onClick={() => action(x, "resume")}>
                            {t("common.resume")}
                          </button>
                        )}
                        {["RUNNING", "PAUSED"].includes(x.status) && (
                          <button onClick={() => action(x, "stop")}>
                            {t("common.stop")}
                          </button>
                        )}
                        {["DRAFT", "STOPPED"].includes(x.status) && (
                          <button onClick={() => action(x, "archive")}>
                            {t("common.archive")}
                          </button>
                        )}
                        <button onClick={() => action(x, "duplicate")}>
                          {t("common.duplicate")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="research-alert warning">{t("shadow.disclaimer")}</div>
    </>
  );
}
