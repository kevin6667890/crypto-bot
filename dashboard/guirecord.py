import customtkinter as ctk
import sqlite3
import pandas as pd
import numpy as np

# [Core fix 1] Force the Matplotlib backend to TkAgg; must be declared before importing pyplot to fix canvas rendering issues
import matplotlib

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from datetime import datetime, timedelta
from tkinter import messagebox
import warnings

# --- 1. Global configuration ---
warnings.filterwarnings("ignore")
# [Core fix 2] Cross-platform Chinese font fallback list, fixes macOS font errors
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti TC', 'Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

# [Core fix 3] Completely removed SCALE_FACTOR and ctk scaling code, letting macOS handle DPI scaling, fixes global white-screen issue

CAD_USDT_RATE = 1.35
EXCHANGE_RATE_CNY = 7.2


class CryptoTrackerPro(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MyCrypto Asset Management System v6.7.2 (Interactive Recovery Edition)")
        self.geometry("1200x850")
        self.db_name = "trading_data_v3.db"
        self.initial_capital = 5764.0
        self.view_mode = "USDT"
        self.exchange_rate = EXCHANGE_RATE_CNY
        self.init_db()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.create_sidebar()
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.frames = {}
        self.create_daily_input_frame()
        self.create_dashboard_frame()
        self.create_calendar_frame()

        # [Core fix 4] Defer view loading to avoid widget load failures caused by macOS UI thread deadlocks
        self.update()
        self.after(100, lambda: self.show_frame("dashboard"))

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS daily_balances (
                date TEXT PRIMARY KEY, bybit REAL, gate REAL, bitget REAL, okx REAL, 
                deposit REAL, withdrawal REAL, note TEXT)''')
        try:
            cursor.execute("ALTER TABLE daily_balances ADD COLUMN cad REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass
        cursor.execute('''CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, date_time TEXT, symbol TEXT, direction TEXT, 
                entry_price REAL, exit_price REAL, quantity REAL, volume REAL, pnl REAL)''')
        conn.commit()
        conn.close()

    def create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self.sidebar_frame, text="MyCrypto\nAsset Audit", font=("PingFang SC", 22, "bold")).grid(row=0,
                                                                                                              column=0,
                                                                                                              padx=20,
                                                                                                              pady=20)
        btn_font = ("PingFang SC", 14)
        ctk.CTkButton(self.sidebar_frame, text="Dashboard", command=lambda: self.show_frame("dashboard"), height=40,
                      font=btn_font).grid(row=1, column=0, padx=20, pady=10)
        ctk.CTkButton(self.sidebar_frame, text="Trading Calendar", command=lambda: self.show_frame("calendar"),
                      fg_color="#D35400", height=40, font=btn_font).grid(row=2, column=0, padx=20, pady=10)
        ctk.CTkButton(self.sidebar_frame, text="Balance Snapshot (incl. CAD)", command=lambda: self.show_frame("daily"),
                      fg_color="#E04F5F", height=40, font=btn_font).grid(row=3, column=0, padx=20, pady=10)

    def show_frame(self, name):
        for frame in self.frames.values():
            frame.pack_forget()
        if name == "dashboard": self.refresh_dashboard()
        if name == "calendar": self.refresh_calendar()
        self.frames[name].pack(fill="both", expand=True)

    # --- Module: Balance Entry ---
    def create_daily_input_frame(self):
        frame = ctk.CTkFrame(self.main_frame)
        self.frames["daily"] = frame
        center_box = ctk.CTkFrame(frame)
        center_box.pack(pady=30, padx=50)
        ctk.CTkLabel(center_box, text="Asset Snapshot Entry", font=("PingFang SC", 24, "bold")).pack(pady=15)
        input_grid = ctk.CTkFrame(center_box, fg_color="transparent")
        input_grid.pack(pady=10)
        self.entries_daily = {}
        labels = ["Date (YYYY-MM-DD)", "Bybit Balance (U)", "Gate Balance (U)", "Bitget Balance (U)", "OKX Balance (U)",
                  "Local CAD", "Today's Deposit (+U)", "Today's Withdrawal (-U)"]
        keys = ["date", "bybit", "gate", "bitget", "okx", "cad", "deposit", "withdrawal"]
        for i, (text, key) in enumerate(zip(labels, keys)):
            ctk.CTkLabel(input_grid, text=text, font=("PingFang SC", 14)).grid(row=i, column=0, padx=15, pady=6,
                                                                               sticky="e")
            entry = ctk.CTkEntry(input_grid, width=200)
            entry.grid(row=i, column=1, padx=15, pady=6)
            entry.insert(0, datetime.now().strftime("%Y-%m-%d") if key == "date" else "0")
            self.entries_daily[key] = entry
        ctk.CTkButton(center_box, text="Save Snapshot", command=self.save_daily, width=200, height=40, fg_color="green",
                      font=("PingFang SC", 15, "bold")).pack(pady=25)

    def save_daily(self):
        d = {k: v.get() for k, v in self.entries_daily.items()}
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT note FROM daily_balances WHERE date=?", (d['date'],))
            res = cursor.fetchone()
            existing_note = res[0] if res else ""
            conn.execute(
                'INSERT OR REPLACE INTO daily_balances (date, bybit, gate, bitget, okx, cad, deposit, withdrawal, note) VALUES (?,?,?,?,?,?,?,?,?)',
                (d['date'], d['bybit'], d['gate'], d['bitget'], d['okx'], d['cad'], d['deposit'], d['withdrawal'],
                 existing_note))
            conn.commit()
            conn.close()
            messagebox.showinfo("Success", "Snapshot saved")
            self.show_frame("dashboard")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # --- Module: Dashboard ---
    def create_dashboard_frame(self):
        frame = ctk.CTkFrame(self.main_frame)
        self.frames["dashboard"] = frame
        self.toolbar = ctk.CTkFrame(frame, fg_color="transparent", height=40)
        self.toolbar.pack(fill="x", padx=20, pady=(10, 0))
        self.currency_switch = ctk.CTkSwitch(self.toolbar, text="Show CNY", command=self.refresh_dashboard,
                                             onvalue="CNY", offvalue="USDT")
        self.currency_switch.pack(side="right")
        self.cards_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=10, pady=5)
        self.card_labels = {}
        card_config = [("Total Combined Assets", "total_combined"), ("Total USDT", "total_usdt"), ("CAD Balance", "total_cad"),
                       ("Total PnL (U)", "total_pnl"), ("Today's PnL (U)", "today_pnl"), ("Weekly PnL (7d)", "week_pnl")]
        for i, (title, key) in enumerate(card_config):
            card = ctk.CTkFrame(self.cards_frame, fg_color="#212121", corner_radius=12)
            card.grid(row=i // 3, column=i % 3, padx=8, pady=8, sticky="nsew")
            self.cards_frame.grid_columnconfigure(i % 3, weight=1)
            ctk.CTkLabel(card, text=title, text_color="#AAAAAA", font=("PingFang SC", 13)).pack(pady=(12, 2))
            lbl = ctk.CTkLabel(card, text="--", font=("DIN Condensed", 32))
            lbl.pack(pady=(0, 12))
            self.card_labels[key] = lbl
        self.chart_frame = ctk.CTkFrame(frame)
        self.chart_frame.pack(fill="both", expand=True, padx=20, pady=10)

    def refresh_dashboard(self):
        self.view_mode = self.currency_switch.get()
        rate_cny = self.exchange_rate if self.view_mode == "CNY" else 1.0
        symbol = "¥ " if self.view_mode == "CNY" else "$"
        for w in self.chart_frame.winfo_children(): w.destroy()

        conn = sqlite3.connect(self.db_name)
        df = pd.read_sql_query("SELECT * FROM daily_balances", conn)
        conn.close()

        # [Core fix 5] Intercept empty data and show UI feedback, preventing the chart render function from silently returning and leaving an empty chart frame
        if df.empty:
            ctk.CTkLabel(self.chart_frame, text="[No Data] Please click 'Balance Snapshot (incl. CAD)' on the left to enter initial data",
                         text_color="gray", font=("PingFang SC", 16)).pack(expand=True)
            return

        cols = ['bybit', 'gate', 'bitget', 'okx', 'cad', 'deposit', 'withdrawal']
        for c in cols: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
        if 'note' not in df.columns:
            df['note'] = ""
        else:
            df['note'] = df['note'].fillna("")

        df['date_obj'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date_obj']).sort_values('date_obj')

        # Second-stage check: intercept malformed date data
        if df.empty:
            ctk.CTkLabel(self.chart_frame, text="Failed to parse date format, please check the data", text_color="red",
                         font=("PingFang SC", 16)).pack(expand=True)
            return

        df['total_u'] = df['bybit'] + df['gate'] + df['bitget'] + df['okx']
        current = df.iloc[-1]
        u_sum = current['total_u']
        cad_sum = current['cad']
        combined_usdt = u_sum + (cad_sum / CAD_USDT_RATE)

        # Update cards
        self.card_labels["total_combined"].configure(text=f"{symbol}{combined_usdt * rate_cny:,.2f}")
        self.card_labels["total_usdt"].configure(text=f"{u_sum:,.2f} U")
        self.card_labels["total_cad"].configure(text=f"{cad_sum:,.2f} CAD")

        net_flow = df['deposit'].sum() - df['withdrawal'].sum()
        total_pnl_u = u_sum - (self.initial_capital + net_flow)
        roi = (total_pnl_u / (self.initial_capital + net_flow) * 100) if (self.initial_capital + net_flow) > 0 else 0
        self.set_color_label("total_pnl", total_pnl_u * rate_cny, symbol, roi=roi)

        if len(df) >= 2:
            prev = df.iloc[-2]
            day_pnl = (u_sum - prev['total_u']) - (current['deposit'] - current['withdrawal'])
            self.set_color_label("today_pnl", day_pnl * rate_cny, symbol)

        # Calculate PnL for the last 7 days (week_pnl)
        if len(df) >= 1:
            latest_date = df['date_obj'].iloc[-1]
            seven_days_ago = latest_date - timedelta(days=7)
            week_df = df[df['date_obj'] >= seven_days_ago]
            if len(week_df) >= 2:
                start_total = week_df['total_u'].iloc[0]
                end_total = week_df['total_u'].iloc[-1]
                week_deposit = week_df['deposit'].sum() - week_df['withdrawal'].sum()
                week_pnl = (end_total - start_total) - week_deposit
                self.set_color_label("week_pnl", week_pnl * rate_cny, symbol)
            elif len(week_df) == 1:
                week_pnl = week_df['total_u'].iloc[0] - (self.initial_capital + week_df['deposit'].iloc[0] - week_df['withdrawal'].iloc[0])
                self.set_color_label("week_pnl", week_pnl * rate_cny, symbol)

        # Draw chart
        plot_data = df['total_u'] * rate_cny
        fig, ax = plt.subplots(figsize=(10, 4), facecolor='#2b2b2b')
        line, = ax.plot(df['date_obj'], plot_data, color='#00ff00', marker='o', linewidth=2, markersize=6, picker=8)

        ax.set_facecolor('#2b2b2b')
        ax.tick_params(colors='white')
        ax.grid(alpha=0.2)
        ax.set_title("Equity Growth Curve (click a point for review)", color='white')
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

        annot = ax.annotate("", xy=(0, 0), xytext=(-20, 20), textcoords="offset points",
                            bbox=dict(boxstyle="round", fc="black", ec="white", alpha=0.8),
                            arrowprops=dict(arrowstyle="->", color="white"), color="white")
        annot.set_visible(False)

        def update_annot(ind):
            idx = ind["ind"][0]
            pos = line.get_xydata()[idx]
            annot.xy = pos
            date_str = df['date'].iloc[idx]
            money_val = plot_data.iloc[idx]
            note_val = df['note'].iloc[idx]
            text = f"{date_str}\n{symbol}{money_val:,.2f}\nNote: {note_val if note_val else 'None'}"
            annot.set_text(text)

        def hover(event):
            if event.inaxes == ax:
                cont, ind = line.contains(event)
                if cont:
                    update_annot(ind)
                    annot.set_visible(True)
                    canvas.draw_idle()
                else:
                    if annot.get_visible():
                        annot.set_visible(False)
                        canvas.draw_idle()

        def on_pick(event):
            if event.artist != line: return
            idx = event.ind[0]
            date_str = df['date'].iloc[idx]
            new_note = ctk.CTkInputDialog(text=f"Edit review note for {date_str}:", title="Trade Review").get_input()
            if new_note is not None:
                try:
                    conn = sqlite3.connect(self.db_name)
                    conn.execute("UPDATE daily_balances SET note=? WHERE date=?", (new_note, date_str))
                    conn.commit()
                    conn.close()
                    self.refresh_dashboard()
                except Exception as e:
                    messagebox.showerror("Error", str(e))

        canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        canvas.mpl_connect("motion_notify_event", hover)
        canvas.mpl_connect("pick_event", on_pick)

    def set_color_label(self, key, val, sym, roi=None):
        color = "#00FF00" if val >= 0 else "#FF3333"
        txt = f"{'+' if val >= 0 else ''}{sym}{abs(val):,.2f}"
        if roi is not None: txt += f" ({roi:+.1f}%)"
        self.card_labels[key].configure(text=txt, text_color=color)

    def create_calendar_frame(self):
        frame = ctk.CTkFrame(self.main_frame)
        self.frames["calendar"] = frame
        ctk.CTkLabel(frame, text="Trading Calendar Heatmap", font=("PingFang SC", 20, "bold")).pack(pady=(20, 5))

        # Month navigation bar
        nav_frame = ctk.CTkFrame(frame, fg_color="transparent")
        nav_frame.pack(pady=5)
        ctk.CTkButton(nav_frame, text="◀ Prev", command=lambda: self._cal_nav(-1), width=100).pack(side="left", padx=10)
        self.cal_month_label = ctk.CTkLabel(nav_frame, text="", font=("PingFang SC", 18, "bold"))
        self.cal_month_label.pack(side="left", padx=20)
        ctk.CTkButton(nav_frame, text="Next ▶", command=lambda: self._cal_nav(1), width=100).pack(side="left", padx=10)
        ctk.CTkButton(nav_frame, text="Today", command=lambda: self._cal_nav(0), width=80).pack(side="left", padx=10)

        # Legend
        legend_frame = ctk.CTkFrame(frame, fg_color="transparent")
        legend_frame.pack(pady=2)
        for lbl, color in [("Big Win +", "#00FF00"), ("Small Win", "#55aa00"), ("Small Loss", "#aa4400"), ("Big Loss -", "#FF3333"), ("No Data", "#333333")]:
            c = ctk.CTkFrame(legend_frame, width=16, height=16, fg_color=color, corner_radius=3)
            c.pack(side="left", padx=(10, 2))
            ctk.CTkLabel(legend_frame, text=lbl, font=("PingFang SC", 11)).pack(side="left", padx=(0, 5))

        self.cal_grid = ctk.CTkFrame(frame)
        self.cal_grid.pack(fill="both", expand=True, padx=20, pady=10)

        self.cal_year = datetime.now().year
        self.cal_month = datetime.now().month

    def _cal_nav(self, delta):
        if delta == 0:
            self.cal_year = datetime.now().year
            self.cal_month = datetime.now().month
        else:
            self.cal_month += delta
            if self.cal_month > 12:
                self.cal_month = 1
                self.cal_year += 1
            elif self.cal_month < 1:
                self.cal_month = 12
                self.cal_year -= 1
        self.refresh_calendar()

    def _get_daily_pnl(self):
        """Calculate daily PnL from daily_balances: change in total_u for the day minus net deposits for the day"""
        conn = sqlite3.connect(self.db_name)
        df = pd.read_sql_query("SELECT * FROM daily_balances ORDER BY date ASC", conn)
        conn.close()
        if df.empty:
            return {}
        cols = ['bybit', 'gate', 'bitget', 'okx', 'deposit', 'withdrawal']
        for c in cols: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
        df['total_u'] = df['bybit'] + df['gate'] + df['bitget'] + df['okx']
        df['date_obj'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date_obj'])
        pnl_map = {}
        for i in range(1, len(df)):
            prev_total = df['total_u'].iloc[i - 1]
            cur_total = df['total_u'].iloc[i]
            net_in = df['deposit'].iloc[i] - df['withdrawal'].iloc[i]
            pnl = (cur_total - prev_total) - net_in
            d = df['date'].iloc[i]
            pnl_map[d] = pnl
        # The first day has no previous-day data, approximate using cumulative PnL
        first = df.iloc[0]
        pnl_map[first['date']] = first['total_u'] - self.initial_capital - (first['deposit'] - first['withdrawal'])
        return pnl_map

    def refresh_calendar(self):
        for w in self.cal_grid.winfo_children(): w.destroy()
        self.cal_month_label.configure(text=f"{self.cal_year}-{self.cal_month:02d}")

        # Get daily PnL data
        daily_pnl = self._get_daily_pnl()

        # Determine the first day, last day, and weekday of the month
        first_day = datetime(self.cal_year, self.cal_month, 1)
        if self.cal_month == 12:
            last_day = datetime(self.cal_year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(self.cal_year, self.cal_month + 1, 1) - timedelta(days=1)
        days_in_month = last_day.day
        start_weekday = first_day.weekday()  # 0=Monday, 6=Sunday -> convert to Sunday-first: (weekday + 1) % 7
        start_col = (start_weekday + 1) % 7  # 0=Sunday

        # Weekday header
        header_frame = ctk.CTkFrame(self.cal_grid, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 5))
        for i in range(7):
            header_frame.grid_columnconfigure(i, weight=1)
        for col, day_name in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
            ctk.CTkLabel(header_frame, text=day_name, width=60, font=("PingFang SC", 13, "bold"),
                         text_color="#AAAAAA").grid(row=0, column=col, padx=2, sticky="nsew")

        # Calendar grid (up to 6 rows)
        today_str = datetime.now().strftime("%Y-%m-%d")
        day_cell_frame = ctk.CTkFrame(self.cal_grid, fg_color="transparent")
        day_cell_frame.pack(fill="both", expand=True)
        for i in range(7):
            day_cell_frame.grid_columnconfigure(i, weight=1)

        day_num = 1
        row = 0
        # Determine max absolute PnL for normalizing color intensity
        all_pnls = [v for v in daily_pnl.values() if v != 0]
        max_abs_pnl = max(abs(v) for v in all_pnls) if all_pnls else 1

        while day_num <= days_in_month:
            for col in range(7):
                if row == 0 and col < start_col:
                    # Empty placeholder (previous month)
                    empty = ctk.CTkFrame(day_cell_frame, width=60, height=60, fg_color="transparent")
                    empty.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
                    continue
                if day_num > days_in_month:
                    break

                date_str = f"{self.cal_year}-{self.cal_month:02d}-{day_num:02d}"
                is_today = (date_str == today_str)

                # Determine background color
                if date_str in daily_pnl:
                    pnl = daily_pnl[date_str]
                    if pnl > 0:
                        intensity = min(pnl / max_abs_pnl, 1.0)
                        r = int(0 * (1 - intensity) + 0 * intensity)
                        g = int(85 * (1 - intensity) + 255 * intensity)
                        b = int(0 * (1 - intensity) + 0 * intensity)
                        bg = f"#{r:02x}{g:02x}{b:02x}"
                        pnl_text = f"{'+' if pnl >= 0 else ''}{pnl:.0f}"
                    elif pnl < 0:
                        intensity = min(abs(pnl) / max_abs_pnl, 1.0)
                        r = int(170 * (1 - intensity) + 255 * intensity)
                        g = int(68 * (1 - intensity) + 51 * intensity)
                        b = int(0 * (1 - intensity) + 0 * intensity)
                        bg = f"#{r:02x}{g:02x}{b:02x}"
                        pnl_text = f"{pnl:.0f}"
                    else:
                        bg = "#4a4a4a"
                        pnl_text = "0"
                else:
                    bg = "#2a2a2a" if not is_today else "#1a3a5a"
                    pnl_text = ""

                # Create cell
                cell = ctk.CTkFrame(day_cell_frame, width=60, height=60, fg_color=bg, corner_radius=6,
                                    border_width=2 if is_today else 0, border_color="#FFD700")
                cell.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
                cell.grid_propagate(False)

                day_lbl = ctk.CTkLabel(cell, text=str(day_num), font=("DIN Condensed", 16, "bold"),
                                       text_color="white" if not is_today else "#FFD700")
                day_lbl.pack(anchor="nw", padx=4, pady=(2, 0))

                if pnl_text:
                    pnl_lbl = ctk.CTkLabel(cell, text=pnl_text,
                                           font=("DIN Condensed", 11),
                                           text_color="#FFFFFF")
                    pnl_lbl.pack(anchor="se", padx=4, pady=(0, 2))

                day_num += 1
            row += 1


if __name__ == "__main__":
    app = CryptoTrackerPro()
    app.mainloop()