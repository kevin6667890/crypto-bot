"""ETH/USDT Trading Signal System — Streamlit Dashboard."""

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ETH/USDT Trading Signal System",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Dark theme styling
# ---------------------------------------------------------------------------
ACCENT = "#00d4aa"

st.markdown(
    f"""
    <style>
        .stApp {{
            background-color: #0e1117;
            color: #e6e6e6;
        }}
        [data-testid="stSidebar"] {{
            background-color: #161a23;
        }}
        h1, h2, h3 {{
            color: {ACCENT} !important;
        }}
        [data-testid="stMetric"] {{
            background-color: #1a1f2b;
            border: 1px solid #2a3142;
            border-radius: 10px;
            padding: 14px 16px;
        }}
        [data-testid="stMetricLabel"] {{
            color: #9aa4b8 !important;
        }}
        [data-testid="stMetricValue"] {{
            color: {ACCENT} !important;
        }}
        .tech-pill {{
            display: inline-block;
            background-color: #1a1f2b;
            border: 1px solid {ACCENT};
            color: {ACCENT};
            border-radius: 20px;
            padding: 8px 18px;
            margin: 4px 0;
            text-align: center;
            font-weight: 600;
            width: 100%;
            box-sizing: border-box;
        }}
        .row-win {{
            background-color: rgba(0, 212, 170, 0.15);
        }}
        .row-loss {{
            background-color: rgba(255, 75, 75, 0.15);
        }}
        .row-be {{
            background-color: rgba(150, 150, 150, 0.15);
        }}
        thead tr th {{
            background-color: #1a1f2b !important;
            color: {ACCENT} !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
st.sidebar.title("📈 Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Strategy Overview", "Backtest Results", "Paper Trading Log"],
)

# ---------------------------------------------------------------------------
# Page 1: Strategy Overview
# ---------------------------------------------------------------------------
if page == "Strategy Overview":
    st.title("ETH/USDT Quantitative Trading Signal System")
    st.subheader(
        "Production-deployed algorithmic trading system "
        "with AI-powered analysis"
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Profit Factor", "2.60")
    col2.metric("Annual Return", "+46.43%")
    col3.metric("Max Drawdown", "4.14%")
    col4.metric("Win Rate", "33.8%")

    st.markdown("---")

    st.subheader("Strategy Discovery Timeline")
    timeline_df = pd.DataFrame(
        [
            ["V1 Breakout", "Price breakout entry", 0.95, "Losing — fees exceeded profits"],
            ["V2 Pullback", "EMA20 pullback entry", 1.94, "Positive expectancy found"],
            ["V3 Pullback + BE", "Pullback + breakeven stop", 3.04, "Major improvement"],
            ["Final (2yr)", "Full validation", 2.60, "Robust & reproducible"],
        ],
        columns=["Version", "Entry Method", "Profit Factor", "Result"],
    )
    st.dataframe(timeline_df, hide_index=True, use_container_width=True)

    st.info(
        "The single most impactful discovery: switching from breakout to "
        "EMA20 pullback entry reduced trade frequency from 379 to 44 trades/year "
        "while improving Profit Factor from 0.95 to 1.94. Adding a breakeven "
        "stop at 1R further improved PF to 3.04."
    )

    st.markdown("---")

    st.subheader("System Architecture")
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

    st.markdown("---")

    st.subheader("Tech Stack")
    pills = ["Python 3.10", "asyncio", "pandas", "Binance API", "DeepSeek AI", "SQLite"]
    cols = st.columns(len(pills))
    for c, label in zip(cols, pills):
        c.markdown(f'<div class="tech-pill">{label}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Page 2: Backtest Results
# ---------------------------------------------------------------------------
elif page == "Backtest Results":
    st.title("Backtest Results — 730 Days (2024–2026)")

    try:
        st.image("docs/backtest_results.png", use_container_width=True)
    except Exception:
        st.warning("Backtest image not found at docs/backtest_results.png")

    st.markdown("---")

    st.subheader("Strategy Comparison")
    results_df = pd.DataFrame(
        [
            ["Trend_EMA20_3R (Final)", 2.60, "+46.43%", "4.14%", 68, "33.8%"],
            ["Range_ZLEMA_2R", 1.55, "+103.78%", "7.83%", 444, "37.2%"],
            ["Adaptive_ADX30", 1.81, "+110.78%", "7.59%", 250, "37.2%"],
            ["Breakout_2.0R (Baseline)", 0.95, "-11.49%", "27.33%", 379, "38.3%"],
        ],
        columns=["Strategy", "Profit Factor", "Annual Return", "Max Drawdown", "Trades", "Win Rate"],
    )

    def highlight_final(row):
        if "Final" in row["Strategy"]:
            return ["background-color: rgba(0, 212, 170, 0.25)"] * len(row)
        return [""] * len(row)

    st.dataframe(
        results_df.style.apply(highlight_final, axis=1),
        hide_index=True,
        use_container_width=True,
    )

    st.info(
        "Final strategy selected: Trend_EMA20_3R — highest Profit Factor (2.60) "
        "with lowest drawdown (4.14%). Range_ZLEMA showed higher returns but "
        "lower per-trade quality and higher fees sensitivity."
    )

    st.markdown("---")

    st.subheader("Simulated Equity Curve — EMA20 Pullback Strategy")

    rng = np.random.default_rng(42)
    n_days = 730
    start_capital = 7500.0
    end_capital = 10982.0

    daily_drift = (np.log(end_capital) - np.log(start_capital)) / n_days
    daily_vol = 0.012
    log_returns = rng.normal(loc=daily_drift, scale=daily_vol, size=n_days)
    equity = start_capital * np.exp(np.cumsum(log_returns))
    equity[-1] = end_capital  # anchor final value to known result

    dates = pd.date_range(end=dt.date.today(), periods=n_days, freq="D")
    equity_df = pd.DataFrame({"Date": dates, "Equity": equity})

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=equity_df["Date"],
            y=equity_df["Equity"],
            mode="lines",
            name="Equity",
            line=dict(color=ACCENT, width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=equity_df["Date"],
            y=[start_capital] * n_days,
            mode="lines",
            name="Initial Capital",
            line=dict(color="#888888", width=1.5, dash="dash"),
        )
    )
    fig.update_layout(
        title="Simulated Equity Curve — EMA20 Pullback Strategy",
        xaxis_title="Date",
        yaxis_title="Equity (USDT)",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#e6e6e6"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "* Simulated based on backtest statistics. "
        "Actual trade-by-trade data not shown."
    )

# ---------------------------------------------------------------------------
# Page 3: Paper Trading Log
# ---------------------------------------------------------------------------
else:
    st.title("Paper Trading — Live Simulation")

    @st.cache_data
    def generate_paper_trades(seed: int = 7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        n = 15

        # ~33% win rate -> roughly 5 wins, 2 BE, 8 losses
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
            price_move = entry * risk_pct * pnl_r if pnl_r != 0 else entry * risk_pct * rng.choice([-1, 1])
            if outcome == "BE":
                exit_price = entry
            elif action == "LONG":
                exit_price = round(entry + price_move, 2)
            else:
                exit_price = round(entry - price_move, 2)

            # Spread create_time over the last 3 weeks, oldest first
            days_ago = 21 - (i * 21 / n)
            create_time = now - dt.timedelta(days=days_ago, hours=float(rng.uniform(0, 5)))
            close_time = create_time + dt.timedelta(hours=float(rng.uniform(2, 30)))

            rows.append(
                {
                    "#": i + 1,
                    "Action": action,
                    "Entry": entry,
                    "Exit": exit_price,
                    "Result": outcome,
                    "P&L (R)": pnl_r,
                    "Close Reason": close_reason,
                    "create_time": create_time,
                    "close_time": close_time,
                    "Date": create_time.strftime("%Y-%m-%d %H:%M"),
                }
            )

        return pd.DataFrame(rows)

    trades_df = generate_paper_trades()

    total_trades = len(trades_df)
    wins = trades_df[trades_df["Result"] == "WIN"]
    losses = trades_df[trades_df["Result"] == "LOSS"]
    win_rate = len(wins) / total_trades * 100

    gross_profit = wins["P&L (R)"].sum()
    gross_loss = abs(losses["P&L (R)"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_pnl_r = trades_df["P&L (R)"].sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Trades", total_trades)
    col2.metric("Win Rate", f"{win_rate:.1f}%")
    col3.metric("Profit Factor", f"{profit_factor:.2f}")
    col4.metric("Total P&L (R)", f"{total_pnl_r:+.2f}R")

    st.markdown("---")

    st.subheader("Cumulative P&L (R)")
    cum_df = trades_df.copy()
    cum_df["Cumulative R"] = cum_df["P&L (R)"].cumsum()

    line_color = ACCENT if cum_df["Cumulative R"].iloc[-1] >= 0 else "#ff4b4b"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=cum_df["#"],
            y=cum_df["Cumulative R"],
            mode="lines+markers",
            line=dict(color=line_color, width=2),
            marker=dict(size=6),
            name="Cumulative R",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#888888")
    fig.update_layout(
        xaxis_title="Trade #",
        yaxis_title="Cumulative R",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#e6e6e6"),
        height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    st.subheader("Trade Log")

    display_df = trades_df[
        ["#", "Action", "Entry", "Exit", "Result", "P&L (R)", "Close Reason", "Date"]
    ]

    def highlight_result(row):
        if row["Result"] == "WIN":
            style = "background-color: rgba(0, 212, 170, 0.18)"
        elif row["Result"] == "LOSS":
            style = "background-color: rgba(255, 75, 75, 0.18)"
        else:
            style = "background-color: rgba(150, 150, 150, 0.18)"
        return [style] * len(row)

    st.dataframe(
        display_df.style.apply(highlight_result, axis=1),
        hide_index=True,
        use_container_width=True,
    )

    st.warning(
        "Paper trading data shown is simulated for demonstration purposes. "
        "Live paper trading is tracked in SQLite on the production server."
    )
