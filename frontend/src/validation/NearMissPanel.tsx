import { useEffect, useState } from "react";
import { phase4Api } from "./api";
import { useLanguage } from "../i18n";
type Near = {
  id: number;
  signal_id: string;
  instrument: string;
  candle_close_ts: number;
  bias: string;
  score: number;
  minimum_score: number;
  score_gap: number;
  failed_gates: string[];
  regime: string;
  what_prevented_entry: string;
  what_would_have_changed: string;
  flow_alignment_state: string;
  ema_distance_gap: number;
  volume_ratio_gap: number;
  rsi_lower_gap: number;
  rsi_upper_gap: number;
  outcome?: Record<string, unknown>;
};
export default function NearMissPanel() {
  const { t, value } = useLanguage(),
    [items, setItems] = useState<Near[]>([]),
    [selected, setSelected] = useState<Near | null>(null),
    [asset, setAsset] = useState("ALL"),
    [gate, setGate] = useState(""),
    [sort, setSort] = useState("score_gap"),
    [error, setError] = useState("");
  useEffect(() => {
    const q = new URLSearchParams({ instrument: asset, gate, sort });
    phase4Api<{ items: Near[] }>(`/api/near-misses?${q}`)
      .then((x) => {
        setItems(x.items);
        setError("");
      })
      .catch((e) =>
        setError(
          `${t("validation.loadFailed")} ${t("common.technicalDetail", {
            detail: e instanceof Error ? e.message : String(e),
          })}`
        )
      );
  }, [asset, gate, sort]);
  return (
    <section className="phase4-card">
      <div className="phase4-head">
        <div>
          <span className="eyebrow">{t("validation.counterfactual")}</span>
          <h2>{t("validation.nearMissAnalysis")}</h2>
        </div>
        <div className="phase4-controls">
          <select value={asset} onChange={(e) => setAsset(e.target.value)}>
            <option>ALL</option>
            <option>BTC-USDT</option>
            <option>ETH-USDT</option>
            <option>SOL-USDT</option>
          </select>
          <input
            value={gate}
            onChange={(e) => setGate(e.target.value)}
            placeholder={t("validation.failedGate")}
          />
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="score_gap">{t("validation.scoreGap")}</option>
            <option value="time">{t("common.time")}</option>
          </select>
        </div>
      </div>
      {error && <div className="research-alert error">{error}</div>}
      {!items.length ? (
        <div className="research-empty compact">
          <strong>{t("validation.noNearMiss")}</strong>
          <span>{t("validation.nearMissHelp")}</span>
        </div>
      ) : (
        <div className="research-table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  t("common.time"),
                  t("common.asset"),
                  t("validation.bias"),
                  t("common.score"),
                  t("common.gap"),
                  t("validation.failedGates"),
                  t("validation.regime"),
                  t("validation.outcome"),
                ].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((x) => (
                <tr
                  key={x.id}
                  onClick={() =>
                    phase4Api<Near>(`/api/near-misses/${x.id}`).then(
                      setSelected
                    )
                  }
                >
                  <td>{new Date(x.candle_close_ts * 1000).toLocaleString()}</td>
                  <td>{x.instrument}</td>
                  <td>{value(x.bias)}</td>
                  <td>
                    {x.score}/{x.minimum_score}
                  </td>
                  <td>{x.score_gap.toFixed(1)}</td>
                  <td>{x.failed_gates.map(value).join("、")}</td>
                  <td>{value(x.regime)}</td>
                  <td>{value(x.outcome?.first_trigger as string) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <div className="detail-drawer">
          <button onClick={() => setSelected(null)}>{t("common.close")}</button>
          <span className="eyebrow">
            {t("decision.signal")} {selected.signal_id.slice(0, 12)}
          </span>
          <h2>{t("validation.prevented")}</h2>
          <p>
            {t("validation.nearMissPrevented", {
              gates: selected.failed_gates.map(value).join("、"),
            })}
          </p>
          <div className="detail-facts">
            <span>
              {t("validation.emaGap")}{" "}
              <b>{selected.ema_distance_gap?.toFixed(5) ?? "—"}</b>
            </span>
            <span>
              {t("validation.rsiGap")}{" "}
              <b>
                {Math.max(
                  selected.rsi_lower_gap || 0,
                  selected.rsi_upper_gap || 0
                ).toFixed(2)}
              </b>
            </span>
            <span>
              {t("validation.volumeGap")}{" "}
              <b>{selected.volume_ratio_gap?.toFixed(2) ?? "—"}</b>
            </span>
            <span>
              {t("validation.flow")}{" "}
              <b>{value(selected.flow_alignment_state)}</b>
            </span>
          </div>
          <h2>{t("validation.changed")}</h2>
          <p>{t("validation.nearMissChanged")}</p>
          <div className="research-alert warning">
            {t("validation.sensitivityCaution")}
          </div>
        </div>
      )}
    </section>
  );
}
