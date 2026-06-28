"""ETH/USDT Trading Signal System — Streamlit Dashboard (TradingView Theme)."""

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ETH/USDT Trading Signal System",
    layout="wide",
)

# ---------------------------------------------------------------------------
# TradingView visual style
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
/* ── Base ─────────────────────────────────────────── */
.stApp {
    background-color: #131722 !important;
    color: #d1d4dc;
    font-family: 'Trebuchet MS', sans-serif;
    font-size: 13px;
}
/* ── Sidebar ───────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #1e222d !important;
    border-right: 1px solid #2a2e39;
}
[data-testid="stSidebarContent"] {
    background-color: #1e222d !important;
}
.sidebar-brand {
    color: #787b86;
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
    font-weight: 600;
    padding: 16px 0 6px 0;
}
.status-line {
    display: flex;
    align-items: center;
    gap: 6px;
    color: #26a69a;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
    padding-bottom: 14px;
}
.status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: #26a69a;
    display: inline-block;
    box-shadow: 0 0 5px #26a69a;
    flex-shrink: 0;
}
/* ── Typography ────────────────────────────────────── */
h1, h2, h3 {
    color: #d1d4dc !important;
    font-family: 'Trebuchet MS', sans-serif !important;
}
.section-label {
    color: #787b86;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    display: block;
    margin-bottom: 8px;
}
/* ── Compact stats bar ─────────────────────────────── */
.stats-bar {
    display: flex;
    align-items: center;
    background-color: #1e222d;
    border: 1px solid #2a2e39;
    border-radius: 3px;
    padding: 10px 20px;
    margin-bottom: 16px;
}
.stat-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
    min-width: 0;
}
.stat-label {
    color: #787b86;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 3px;
    white-space: nowrap;
}
.stat-value {
    color: #d1d4dc;
    font-size: 16px;
    font-weight: 600;
    white-space: nowrap;
}
.stat-value.green { color: #26a69a; }
.stat-value.red   { color: #ef5350; }
.stat-divider {
    width: 1px;
    height: 36px;
    background-color: #2a2e39;
    margin: 0 14px;
    flex-shrink: 0;
}
/* ── ETH price header ──────────────────────────────── */
.price-header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
    background-color: #1e222d;
    border: 1px solid #2a2e39;
    border-radius: 3px;
    padding: 8px 18px;
    margin-bottom: 12px;
}
.price-symbol {
    color: #d1d4dc;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
.price-value {
    font-size: 18px;
    font-weight: 700;
}
.price-meta {
    color: #787b86;
    font-size: 12px;
}
.price-meta span {
    color: #d1d4dc;
}
.price-sep {
    color: #2a2e39;
    font-size: 20px;
    line-height: 1;
}
.price-ts {
    color: #787b86;
    font-size: 11px;
    margin-left: auto;
}
/* ── TV-style tables ───────────────────────────────── */
.tv-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-family: 'Trebuchet MS', sans-serif;
}
.tv-table th {
    color: #787b86;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 1px;
    border-bottom: 1px solid #2a2e39;
    padding: 7px 10px;
    text-align: left;
    font-weight: 500;
    background: transparent;
}
.tv-table td {
    color: #d1d4dc;
    padding: 6px 10px;
    border-bottom: 1px solid #2a2e39;
    background: transparent;
}
.tv-table tr:last-child td { border-bottom: none; }
.tv-table tr:hover td { background-color: rgba(42,46,57,0.6); }
.tv-table .best-row td:first-child {
    border-left: 2px solid #26a69a;
    padding-left: 8px;
}
.text-green { color: #26a69a; }
.text-red   { color: #ef5350; }
.text-muted { color: #787b86; }
/* ── Info / warning divs ───────────────────────────── */
.tv-info {
    border: 1px solid #2a2e39;
    border-left: 3px solid #2962ff;
    border-radius: 2px;
    padding: 10px 14px;
    color: #787b86;
    font-size: 13px;
    margin: 10px 0;
    background: transparent;
}
.tv-warn {
    border: 1px solid #2a2e39;
    border-left: 3px solid #787b86;
    border-radius: 2px;
    padding: 10px 14px;
    color: #787b86;
    font-size: 13px;
    margin: 10px 0;
    background: transparent;
}
/* ── Divider ───────────────────────────────────────── */
.tv-divider {
    border: none;
    border-top: 1px solid #2a2e39;
    margin: 18px 0;
}
/* ── Tech pills ────────────────────────────────────── */
.tech-pill {
    display: inline-block;
    background-color: #1e222d;
    border: 1px solid #2a2e39;
    color: #d1d4dc;
    border-radius: 3px;
    padding: 6px 14px;
    text-align: center;
    font-size: 12px;
    width: 100%;
    box-sizing: border-box;
}
/* ── Chart caption ─────────────────────────────────── */
.chart-note {
    color: #787b86;
    font-size: 12px;
    margin-top: 6px;
}
/* ── Streamlit widgets override ────────────────────── */
[data-testid="stButton"] button {
    background-color: #1e222d;
    border: 1px solid #2a2e39;
    color: #787b86;
    font-size: 12px;
    font-family: 'Trebuchet MS', sans-serif;
    padding: 4px 12px;
}
[data-testid="stButton"] button:hover {
    border-color: #d1d4dc;
    color: #d1d4dc;
    background-color: #1e222d;
}
[data-testid="stMetric"] { display: none; }
#MainMenu, footer, [data-testid="stToolbar"],
.stDeployButton { display: none !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.markdown(
    '<div class="sidebar-brand">Crypto-Bot</div>'
    '<div class="status-line">'
    '<span class="status-dot"></span>System: Active'
    '</div>',
    unsafe_allow_html=True,
)
page = st.sidebar.radio(
    "Navigation",
    ["Strategy Overview", "Backtest Results", "Paper Trading Log", "Live Chart"],
    label_visibility="collapsed",
)

# ---------------------------------------------------------------------------
# Real-time ETH price header — shown on ALL pages
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30)
def fetch_eth_price() -> dict:
    try:
        ticker = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": "ETHUSDT"},
            timeout=6,
        ).json()
        klines = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "ETHUSDT", "interval": "15m", "limit": 25},
            timeout=6,
        ).json()
        closes = [float(k[4]) for k in klines]
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
        return {
            "ok": True,
            "price": float(ticker["lastPrice"]),
            "change_pct": float(ticker["priceChangePercent"]),
            "high24": float(ticker["highPrice"]),
            "low24": float(ticker["lowPrice"]),
            "ema20": ema20,
        }
    except Exception:
        return {"ok": False}


def render_price_header():
    data = fetch_eth_price()
    now_str = dt.datetime.utcnow().strftime("%H:%M:%S")
    hdr_col, btn_col = st.columns([7, 1])
    with hdr_col:
        if not data["ok"]:
            st.markdown(
                '<div class="tv-warn">Price data unavailable</div>',
                unsafe_allow_html=True,
            )
        else:
            p = data["price"]
            chg = data["change_pct"]
            ema20 = data["ema20"]
            dist_pct = (p - ema20) / ema20 * 100
            p_color = "#26a69a" if chg >= 0 else "#ef5350"
            chg_sign = "+" if chg >= 0 else ""
            dist_color = "#26a69a" if dist_pct >= 0 else "#ef5350"
            dist_sign = "+" if dist_pct >= 0 else ""
            st.markdown(
                f'<div class="price-header">'
                f'<span class="price-symbol">ETH/USDT</span>'
                f'<span class="price-sep">|</span>'
                f'<span class="price-value" style="color:{p_color};">{p:,.2f}</span>'
                f'<span style="color:{p_color};font-size:13px;">&nbsp;{chg_sign}{chg:.2f}%</span>'
                f'<span class="price-sep">|</span>'
                f'<span class="price-meta">EMA20&nbsp;<span>{ema20:,.2f}</span></span>'
                f'<span class="price-meta">Distance&nbsp;<span style="color:{dist_color};">'
                f'{dist_sign}{dist_pct:.2f}%</span></span>'
                f'<span class="price-sep">|</span>'
                f'<span class="price-meta">H&nbsp;<span>{data["high24"]:,.2f}</span></span>'
                f'<span class="price-meta">L&nbsp;<span>{data["low24"]:,.2f}</span></span>'
                f'<span class="price-ts">Updated {now_str} UTC</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    with btn_col:
        if st.button("Refresh"):
            fetch_eth_price.clear()
            st.rerun()


render_price_header()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def stats_bar(items: list):
    parts = []
    for i, (label, value, cls) in enumerate(items):
        parts.append(
            f'<div class="stat-item">'
            f'<div class="stat-label">{label}</div>'
            f'<div class="stat-value {cls}">{value}</div>'
            f'</div>'
        )
        if i < len(items) - 1:
            parts.append('<div class="stat-divider"></div>')
    st.markdown(
        '<div class="stats-bar">' + "".join(parts) + '</div>',
        unsafe_allow_html=True,
    )


def tv_divider():
    st.markdown('<hr class="tv-divider">', unsafe_allow_html=True)


def tv_info(text: str):
    st.markdown(f'<div class="tv-info">{text}</div>', unsafe_allow_html=True)


def tv_warn(text: str):
    st.markdown(f'<div class="tv-warn">{text}</div>', unsafe_allow_html=True)


def section_label(text: str):
    st.markdown(f'<span class="section-label">{text}</span>', unsafe_allow_html=True)


def tv_table(header: list, rows_html: str):
    th = "".join(f"<th>{h}</th>" for h in header)
    st.markdown(
        f'<table class="tv-table"><thead><tr>{th}</tr></thead>'
        f'<tbody>{rows_html}</tbody></table>',
        unsafe_allow_html=True,
    )


# Plotly chart base layout
CHART_LAYOUT = dict(
    paper_bgcolor="#131722",
    plot_bgcolor="#131722",
    font=dict(color="#787b86", family="Trebuchet MS", size=12),
    xaxis=dict(gridcolor="#2a2e39", zerolinecolor="#2a2e39", linecolor="#2a2e39"),
    yaxis=dict(gridcolor="#2a2e39", zerolinecolor="#2a2e39", linecolor="#2a2e39"),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#787b86")),
    margin=dict(l=0, r=0, t=24, b=0),
)

# ---------------------------------------------------------------------------
# Page 1: Strategy Overview
# ---------------------------------------------------------------------------
if page == "Strategy Overview":
    st.markdown("### Strategy Overview")

    stats_bar([
        ("Profit Factor", "2.60", "green"),
        ("Annual Return", "+46.43%", "green"),
        ("Max Drawdown", "4.14%", "red"),
        ("Win Rate", "33.8%", ""),
        ("Total Trades", "68", ""),
        ("Backtest Period", "2 Years", ""),
    ])

    tv_divider()
    section_label("Strategy Discovery Timeline")

    rows_html = ""
    for version, method, desc, pf, is_best in [
        ("V1", "Breakout", "Price breakout entry", "0.95", False),
        ("V2", "EMA20 Pullback", "EMA20 pullback entry", "1.94", False),
        ("V3", "Pullback + BE", "Pullback + breakeven stop", "3.04", False),
        ("Final (2yr)", "Full Validation", "2-year backtest validation", "2.60", True),
    ]:
        cls = "best-row" if is_best else ""
        rows_html += (
            f'<tr class="{cls}">'
            f'<td>{version}</td><td>{method}</td><td>{desc}</td>'
            f'<td class="text-green">{pf}</td>'
            f'</tr>'
        )
    tv_table(["Version", "Entry Method", "Description", "Profit Factor"], rows_html)

    tv_info(
        "The single most impactful discovery: switching from breakout to EMA20 pullback entry "
        "reduced trade frequency from 379 to 44 trades/year while improving Profit Factor from 0.95 to 1.94. "
        "Adding a breakeven stop at 1R further improved PF to 3.04."
    )

    tv_divider()
    section_label("System Architecture")
    st.code(
        """
Binance API → Strategy Engine → Signal Queue → EMA20 Pullback Detection
                                                        |
                                                        v
                                               WeChat Notification
                                               Paper Trade Opened
                                                        |
                                                        v
                                          Auto-track SL / TP / BE
                                                        |
                                                        v
                                         Close + DeepSeek AI Review
        """,
        language=None,
    )

    tv_divider()
    section_label("Tech Stack")
    pills = ["Python 3.10", "asyncio", "pandas", "Binance API", "DeepSeek AI", "SQLite"]
    cols = st.columns(len(pills))
    for c, label in zip(cols, pills):
        c.markdown(f'<div class="tech-pill">{label}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Page 2: Backtest Results
# ---------------------------------------------------------------------------
elif page == "Backtest Results":
    st.markdown("### Backtest Results — 730 Days (2024–2026)")

    try:
        st.image("docs/backtest_results.png", use_container_width=True)
    except Exception:
        tv_warn("Backtest image not found at docs/backtest_results.png")

    tv_divider()
    section_label("Strategy Comparison")

    rows_html = ""
    for name, pf, ret, dd, trades, wr, is_best in [
        ("Trend_EMA20_3R", "2.60", "+46.43%", "4.14%", "68", "33.8%", True),
        ("Range_ZLEMA_2R", "1.55", "+103.78%", "7.83%", "444", "37.2%", False),
        ("Adaptive_ADX30", "1.81", "+110.78%", "7.59%", "250", "37.2%", False),
        ("Breakout_2.0R (Baseline)", "0.95", "-11.49%", "27.33%", "379", "38.3%", False),
    ]:
        cls = "best-row" if is_best else ""
        pf_cls = "text-green" if float(pf) >= 1.5 else "text-red"
        ret_cls = "text-green" if ret.startswith("+") else "text-red"
        rows_html += (
            f'<tr class="{cls}">'
            f'<td>{name}</td>'
            f'<td class="{pf_cls}">{pf}</td>'
            f'<td class="{ret_cls}">{ret}</td>'
            f'<td>{dd}</td><td>{trades}</td><td>{wr}</td>'
            f'</tr>'
        )
    tv_table(
        ["Strategy", "Profit Factor", "Annual Return", "Max Drawdown", "Trades", "Win Rate"],
        rows_html,
    )

    tv_info(
        "Final strategy selected: Trend_EMA20_3R — highest Profit Factor (2.60) with lowest drawdown (4.14%). "
        "Range_ZLEMA showed higher returns but lower per-trade quality and higher fee sensitivity."
    )

    tv_divider()
    section_label("Simulated Equity Curve — EMA20 Pullback Strategy")

    rng = np.random.default_rng(42)
    n_days = 730
    start_capital = 7500.0
    end_capital = 10982.0
    daily_drift = (np.log(end_capital) - np.log(start_capital)) / n_days
    log_returns = rng.normal(loc=daily_drift, scale=0.012, size=n_days)
    equity = start_capital * np.exp(np.cumsum(log_returns))
    equity[-1] = end_capital
    dates = pd.date_range(end=dt.date.today(), periods=n_days, freq="D")
    equity_df = pd.DataFrame({"Date": dates, "Equity": equity})

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity_df["Date"], y=equity_df["Equity"],
        mode="lines", name="Equity",
        line=dict(color="#26a69a", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=equity_df["Date"], y=[start_capital] * n_days,
        mode="lines", name="Initial Capital",
        line=dict(color="#787b86", width=1, dash="dash"),
    ))
    fig.update_layout(**CHART_LAYOUT, height=420,
                      xaxis_title="Date", yaxis_title="Equity (USDT)")
    st.plotly_chart(fig, use_container_width=True)
    st.markdown(
        '<div class="chart-note">'
        '* Simulated based on backtest statistics. Actual trade-by-trade data not shown.'
        '</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Page 3: Paper Trading Log
# ---------------------------------------------------------------------------
elif page == "Paper Trading Log":
    st.markdown("### Paper Trading — Live Simulation")

    @st.cache_data
    def generate_paper_trades(seed: int = 7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        n = 15
        outcomes = ["WIN"] * 5 + ["BE"] * 2 + ["LOSS"] * 8
        rng.shuffle(outcomes)
        actions = rng.choice(["LONG", "SHORT"], size=n)
        rows = []
        now = dt.datetime.now()
        for i, (outcome, action) in enumerate(zip(outcomes, actions)):
            entry = round(rng.uniform(2000, 2500), 2)
            if outcome == "WIN":
                pnl_r = round(rng.uniform(2.5, 3.0), 2)
                close_reason = "TP Hit"
            elif outcome == "LOSS":
                pnl_r = round(-rng.uniform(0.95, 1.05), 2)
                close_reason = "SL Hit"
            else:
                pnl_r = 0.0
                close_reason = "Breakeven Exit"
            risk_pct = rng.uniform(0.005, 0.012)
            price_move = (
                entry * risk_pct * pnl_r if pnl_r != 0
                else entry * risk_pct * rng.choice([-1, 1])
            )
            if outcome == "BE":
                exit_price = entry
            elif action == "LONG":
                exit_price = round(entry + price_move, 2)
            else:
                exit_price = round(entry - price_move, 2)
            days_ago = 21 - (i * 21 / n)
            create_time = now - dt.timedelta(days=days_ago, hours=float(rng.uniform(0, 5)))
            rows.append({
                "#": i + 1,
                "Action": action,
                "Entry": entry,
                "Exit": exit_price,
                "Result": outcome,
                "P&L (R)": pnl_r,
                "Close Reason": close_reason,
                "Date": create_time.strftime("%Y-%m-%d %H:%M"),
            })
        return pd.DataFrame(rows)

    trades_df = generate_paper_trades()
    wins = trades_df[trades_df["Result"] == "WIN"]
    losses = trades_df[trades_df["Result"] == "LOSS"]
    total_trades = len(trades_df)
    win_rate = len(wins) / total_trades * 100
    gross_profit = wins["P&L (R)"].sum()
    gross_loss = abs(losses["P&L (R)"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_pnl_r = trades_df["P&L (R)"].sum()

    stats_bar([
        ("Total Trades", str(total_trades), ""),
        ("Win Rate", f"{win_rate:.1f}%", ""),
        ("Wins", str(len(wins)), "green"),
        ("Losses", str(len(losses)), "red"),
        ("Profit Factor", f"{profit_factor:.2f}", "green"),
        ("Total P&L", f"{'+'if total_pnl_r>=0 else ''}{total_pnl_r:.2f}R", "green" if total_pnl_r >= 0 else "red"),
    ])

    tv_divider()
    section_label("Cumulative P&L (R)")

    cum_df = trades_df.copy()
    cum_df["Cumulative R"] = cum_df["P&L (R)"].cumsum()
    line_color = "#26a69a" if cum_df["Cumulative R"].iloc[-1] >= 0 else "#ef5350"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=cum_df["#"], y=cum_df["Cumulative R"],
        mode="lines+markers",
        line=dict(color=line_color, width=2),
        marker=dict(size=5, color=line_color),
        name="Cumulative R",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="#2a2e39")
    fig.update_layout(**CHART_LAYOUT, height=320,
                      xaxis_title="Trade #", yaxis_title="Cumulative R")
    st.plotly_chart(fig, use_container_width=True)

    tv_divider()
    section_label("Trade Log")

    rows_html = ""
    for _, row in trades_df.iterrows():
        r = row["Result"]
        pnl = row["P&L (R)"]
        r_cls = "text-green" if r == "WIN" else ("text-red" if r == "LOSS" else "text-muted")
        pnl_cls = "text-green" if pnl > 0 else ("text-red" if pnl < 0 else "text-muted")
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        rows_html += (
            f'<tr>'
            f'<td>{int(row["#"])}</td>'
            f'<td>{row["Action"]}</td>'
            f'<td>{row["Entry"]:.2f}</td>'
            f'<td>{row["Exit"]:.2f}</td>'
            f'<td class="{r_cls}">{r}</td>'
            f'<td class="{pnl_cls}">{pnl_str}</td>'
            f'<td class="text-muted">{row["Close Reason"]}</td>'
            f'<td class="text-muted">{row["Date"]}</td>'
            f'</tr>'
        )
    tv_table(
        ["#", "Direction", "Entry", "Exit", "Result", "P&L (R)", "Close Reason", "Date"],
        rows_html,
    )

    tv_warn(
        "Paper trading data shown is simulated for demonstration purposes. "
        "Live paper trading is tracked in SQLite on the production server."
    )

# ---------------------------------------------------------------------------
# Page 4: Live Chart
# ---------------------------------------------------------------------------
else:
    st.markdown("### Live Chart")

    SYMBOLS = [
        "BINANCE:ETHUSDT",
        "BINANCE:BTCUSDT",
        "BINANCE:SOLUSDT",
        "NASDAQ:AAPL",
        "NASDAQ:NVDA",
    ]
    TF_LABELS = ["1m", "5m", "15m", "1H", "4H", "1D"]
    TF_VALUES = ["1", "5", "15", "60", "240", "D"]

    col_sym, col_tf = st.columns([2, 3])
    with col_sym:
        selected_symbol = st.selectbox(
            "Symbol", SYMBOLS, index=0, label_visibility="collapsed"
        )
    with col_tf:
        selected_tf_label = st.radio(
            "Timeframe", TF_LABELS, index=2,
            horizontal=True, label_visibility="collapsed",
        )
    selected_interval = TF_VALUES[TF_LABELS.index(selected_tf_label)]

    components.html(
        f"""
<div class="tradingview-widget-container" style="height:600px;width:100%">
  <div id="tradingview_eth"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
  new TradingView.widget({{
    "width": "100%",
    "height": 580,
    "symbol": "{selected_symbol}",
    "interval": "{selected_interval}",
    "timezone": "America/Toronto",
    "theme": "dark",
    "style": "1",
    "locale": "en",
    "toolbar_bg": "#131722",
    "enable_publishing": false,
    "allow_symbol_change": true,
    "studies": ["MASimple@tv-built-ins","RSI@tv-built-ins","ATR@tv-built-ins"],
    "container_id": "tradingview_eth"
  }});
  </script>
</div>
""",
        height=650,
    )

    st.markdown(
        '<div class="chart-note">'
        'Chart powered by TradingView. EMA20, RSI, ATR pre-loaded.'
        '</div>',
        unsafe_allow_html=True,
    )
