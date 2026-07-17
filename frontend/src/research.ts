export type StrategyParameters = {
  fast_ma: number; slow_ma: number; ema_pullback_period: number; ema_pullback_distance: number;
  rsi_period: number; rsi_min: number; rsi_max: number; minimum_volume_ratio: number;
  minimum_score: number; atr_period: number; stop_loss_atr_multiplier: number;
  risk_reward_ratio: number; trading_fee: number; slippage: number; cooldown_bars: number;
  enable_long: boolean; enable_short: boolean; initial_capital: number; risk_per_trade: number;
  max_open_positions: number;
};

export const DEFAULT_RESEARCH_PARAMETERS: StrategyParameters = {
  fast_ma: 60, slow_ma: 200, ema_pullback_period: 20, ema_pullback_distance: 0.0045,
  rsi_period: 14, rsi_min: 35, rsi_max: 68, minimum_volume_ratio: 1,
  minimum_score: 75, atr_period: 14, stop_loss_atr_multiplier: 1,
  risk_reward_ratio: 2, trading_fee: 0.0005, slippage: 0.0003, cooldown_bars: 16,
  enable_long: true, enable_short: true, initial_capital: 10000, risk_per_trade: 0.01,
  max_open_positions: 1,
};

export type BacktestMetrics = {
  initial_capital: number; final_equity: number; net_profit: number; total_return: number;
  annualized_return: number | null; total_trades: number; win_rate: number | null;
  profit_factor: number | null; expectancy: number | null; average_win: number | null;
  average_loss: number | null; realized_risk_reward: number | null; maximum_drawdown: number;
  sharpe_ratio: number | null; sortino_ratio: number | null; consecutive_wins: number;
  consecutive_losses: number; fees_paid: number; long_trades: number; short_trades: number;
  average_holding_seconds: number | null; sample_note: string | null;
};
export type BacktestTrade = {
  trade_id: number; instrument: string; entry_ts: number; exit_ts: number; entry_time: string; exit_time: string;
  side: "LONG" | "SHORT"; entry_price: number; exit_price: number; stop_loss: number; take_profit: number;
  position_size: number; pnl: number; pnl_pct: number; result_r: number; fees: number; exit_reason: string;
  holding_seconds: number; signal_score: number; signal_ts: number;
};
export type EquityPoint = { ts: number; equity: number };
export type BacktestResult = {
  metrics: BacktestMetrics; drawdown: Array<{ ts: number; drawdown: number }>;
  monthly_returns: Array<{ month: string; return: number }>;
  candles: Array<{ ts: number; open: number; high: number; low: number; close: number }>;
  signal_count: number; execution_model: string; indicator_model: string;
  validation: { split: number; split_ts: number; in_sample: BacktestMetrics; out_of_sample: BacktestMetrics; overfitting_warning: boolean; message: string };
};
export type BacktestRun = {
  id: number; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED"; progress: number;
  progress_message?: string; instrument: string; timeframe: string; start_date: string; end_date: string;
  parameters: StrategyParameters; result?: BacktestResult; error?: string; data_quality?: Record<string, unknown>;
  created_at: string;
};
export type StrategyConfig = { id: number; name: string; parameters: StrategyParameters; instrument: string; timeframe: string; start_date?: string; end_date?: string; latest_summary?: BacktestMetrics & { run_id: number; instrument: string; timeframe: string; start_date: string; end_date: string }; created_at: string; updated_at: string };
export type Reconciliation = { paper_trades: number; backtest_trades: number; paper_signal_count: number; backtest_signal_count: number; signal_count_difference: number; paper_win_rate: number | null; backtest_win_rate: number | null; paper_profit_factor: number | null; backtest_profit_factor: number | null; missed_signals: number; unexpected_signals: number; drift_status: "Normal" | "Watch" | "Diverging"; limitations: string[] };

const apiBase = (window.__PAPER_API_URL__ || import.meta.env.VITE_PAPER_API_URL || "").replace(/\/$/, "");
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, options);
  const payload = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}
export const researchApi = {
  strategies: async () => (await request<{ items: StrategyConfig[] }>("/api/strategies")).items,
  saveStrategy: (payload: Partial<StrategyConfig>, id?: number) => request<StrategyConfig>(id ? `/api/strategies/${id}` : "/api/strategies", { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  deleteStrategy: (id: number) => request<{ deleted: boolean }>(`/api/strategies/${id}`, { method: "DELETE" }),
  duplicateStrategy: (id: number) => request<StrategyConfig>(`/api/strategies/${id}/duplicate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }),
  run: (payload: object) => request<BacktestRun>("/api/backtest/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  getRun: (id: number) => request<BacktestRun>(`/api/backtest/${id}`),
  trades: async (id: number) => (await request<{ items: BacktestTrade[] }>(`/api/backtest/${id}/trades`)).items,
  equity: async (id: number) => (await request<{ items: EquityPoint[] }>(`/api/backtest/${id}/equity`)).items,
  history: async () => (await request<{ items: BacktestRun[] }>("/api/backtest/history")).items,
  compare: async (runIds: number[]) => (await request<{ items: Array<Record<string, number | string | null>> }>("/api/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_ids: runIds }) })).items,
  compareStrategies: async (strategyIds: number[]) => (await request<{ items: Array<Record<string, number | string | null>> }>("/api/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ strategy_ids: strategyIds }) })).items,
  walkForward: (payload: object) => request<{ windows: Array<Record<string, unknown>>; note: string }>("/api/walk-forward", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  reconciliation: (runId: number) => request<Reconciliation>(`/api/reconciliation?run_id=${runId}`),
};
