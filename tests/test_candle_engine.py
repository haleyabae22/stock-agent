"""
tests/test_candle_engine.py
───────────────────────────
Tests the full intraday candle pipeline WITHOUT hitting real APIs.

All yfinance calls are monkey-patched with synthetic 10-minute OHLCV
data so the suite runs offline, fast, and deterministically.

Coverage:
  Section A — Candle construction & market-hours filter
  Section B — SMA calculation on intraday bars
  Section C — Crossover detection (candle_close mode)
  Section D — Crossover detection (crossover / live-tick mode)
  Section E — Simulator buy behavior for both modes
  Section F — Buy log storage: fields, dedup, timestamp stamping
  Section G — Display smoke-tests (print_buys / print_positions)

Run:
    python -m pytest tests/test_candle_engine.py -v
  or directly:
    python tests/test_candle_engine.py
"""

import sys
import types
import unittest
from datetime import date, datetime, timezone, timedelta
from copy import deepcopy

import pandas as pd
import pytz

# ── path setup so tests find app.* without install ──────────────
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.signals   import calculate_smas, detect_buy_signal, detect_sell_signal, get_signal, signal_summary
from app.simulator import PaperTrader, ALLOCATION_PCT, STOP_LOSS_PCT

PT = pytz.timezone("America/Los_Angeles")

# ─────────────────────────────────────────────────────────────────
# Terminal colour / table helpers
# ─────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
MAGENTA= "\033[35m"
BLUE   = "\033[34m"
WHITE  = "\033[97m"
BG_DARK= "\033[48;5;235m"


def _hr(char="─", width=90, color=DIM):
    print(f"{color}{char * width}{RESET}")


def _section_banner(title: str, subtitle: str = ""):
    print()
    _hr("═", color=CYAN)
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    if subtitle:
        print(f"{DIM}  {subtitle}{RESET}")
    _hr("═", color=CYAN)


def _subsection(title: str):
    print(f"\n{BOLD}{YELLOW}  ▶ {title}{RESET}")
    _hr(color=DIM)


def _col(val, width, align="<", color=""):
    s = str(val)
    if len(s) > width:
        s = s[:width - 1] + "…"
    formatted = f"{s:{align}{width}}"
    return f"{color}{formatted}{RESET}" if color else formatted


def _table(headers, rows, col_widths, col_colors=None, col_aligns=None,
           row_color_fn=None, title=None):
    """
    Print a neatly formatted table to stdout.

    headers     : list of header strings
    rows        : list of lists (one per row)
    col_widths  : list of ints
    col_colors  : list of ANSI color codes for each column header
    col_aligns  : list of '<' or '>' per column
    row_color_fn: callable(row) → ANSI color for the entire data row, or None
    title       : optional table title printed above
    """
    col_colors = col_colors or [""] * len(headers)
    col_aligns = col_aligns or ["<"] * len(headers)

    if title:
        print(f"\n  {BOLD}{WHITE}{title}{RESET}")

    # separator
    sep = "  ┼" + "┼".join("─" * (w + 2) for w in col_widths) + "┼"
    top = "  ┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    bot = "  └" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    # header row
    print(top)
    header_cells = []
    for h, w, c, a in zip(headers, col_widths, col_colors, col_aligns):
        cell = f"{c}{BOLD}{h:{a}{w}}{RESET}"
        header_cells.append(f" {cell} ")
    print("  │" + "│".join(header_cells) + "│")
    print(sep)

    # data rows
    for row in rows:
        rc = row_color_fn(row) if row_color_fn else ""
        cells = []
        for val, w, a in zip(row, col_widths, col_aligns):
            s = str(val)
            if len(s) > w:
                s = s[:w - 1] + "…"
            cells.append(f" {rc}{s:{a}{w}}{RESET} ")
        print("  │" + "│".join(cells) + "│")

    print(bot)


def _print_candles(df: pd.DataFrame, label: str, max_rows: int = 39):
    """Print a candle DataFrame as a terminal table."""
    _subsection(label)

    show = df.head(max_rows).copy()

    has_sma = "sma8" in show.columns and "sma20" in show.columns

    def fmt_f(v, places=2):
        if pd.isna(v):
            return f"{DIM}n/a{RESET}"
        return f"{v:.{places}f}"

    def signal_color(sig):
        return {
            "BUY":  f"{GREEN}{BOLD}BUY {RESET}",
            "SELL": f"{RED}{BOLD}SELL{RESET}",
            "HOLD": f"{DIM}HOLD{RESET}",
        }.get(sig, sig)

    if has_sma:
        headers   = ["#", "Candle", "Open", "High", "Low", "Close", "Volume",
                     "SMA8", "SMA20", "Gap%"]
        col_widths = [3, 9, 8, 8, 8, 8, 9, 9, 9, 7]
        col_aligns = [">", "<", ">", ">", ">", ">", ">", ">", ">", ">"]
        col_colors = [DIM, CYAN, "", "", "", WHITE, DIM, YELLOW, MAGENTA, GREEN]

        rows = []
        for i, (_, r) in enumerate(show.iterrows()):
            sma8  = r.get("sma8",  float("nan"))
            sma20 = r.get("sma20", float("nan"))
            gap   = ""
            if not pd.isna(sma8) and not pd.isna(sma20) and sma20 != 0:
                gap_val = (sma8 - sma20) / sma20 * 100
                gap = f"{gap_val:+.3f}"
            rows.append([
                i + 1,
                r["candle"],
                f"{r['open']:.2f}",
                f"{r['high']:.2f}",
                f"{r['low']:.2f}",
                f"{r['close']:.2f}",
                f"{int(r['volume']):,}",
                fmt_f(sma8),
                fmt_f(sma20),
                gap,
            ])

        def row_color(row):
            gap_s = str(row[9]).replace("+", "").replace("−", "-")
            try:
                v = float(gap_s)
                if v > 0.05:  return GREEN
                if v < -0.05: return RED
            except ValueError:
                pass
            return ""

        _table(headers, rows, col_widths, col_colors, col_aligns,
               row_color_fn=row_color,
               title=f"Candle data ({len(df)} bars total, showing {len(show)})")

    else:
        headers    = ["#", "Candle", "Open", "High", "Low", "Close", "Volume", "Ticker"]
        col_widths = [3, 9, 8, 8, 8, 8, 9, 6]
        col_aligns = [">", "<", ">", ">", ">", ">", ">", "<"]
        col_colors = [DIM, CYAN, "", "", "", WHITE, DIM, YELLOW]

        rows = []
        for i, (_, r) in enumerate(show.iterrows()):
            rows.append([
                i + 1,
                r["candle"],
                f"{r['open']:.2f}",
                f"{r['high']:.2f}",
                f"{r['low']:.2f}",
                f"{r['close']:.2f}",
                f"{int(r['volume']):,}",
                r.get("ticker", "TEST"),
            ])
        _table(headers, rows, col_widths, col_colors, col_aligns,
               title=f"Candle data ({len(df)} bars)")

    if len(df) > max_rows:
        print(f"  {DIM}  … {len(df) - max_rows} more rows hidden{RESET}")


def _print_signal_summary(s: dict):
    _subsection("Signal summary")
    sig = s["signal"]
    sig_colored = {
        "BUY":  f"{GREEN}{BOLD}BUY{RESET}",
        "SELL": f"{RED}{BOLD}SELL{RESET}",
        "HOLD": f"{DIM}HOLD{RESET}",
    }.get(sig, sig)

    rows = [
        ["Ticker",   s.get("ticker", "—")],
        ["Date",     str(s.get("date", "—"))],
        ["Close",    f"${s['close']:.4f}"],
        ["SMA8",     f"{s['sma8']:.4f}"  if s.get("sma8")  else "n/a"],
        ["SMA20",    f"{s['sma20']:.4f}" if s.get("sma20") else "n/a"],
        ["Gap%",     f"{s['gap_pct']:+.4f}%"],
        ["Signal",   sig],
    ]
    _table(["Field", "Value"], rows, [10, 18],
           col_colors=[DIM, WHITE], title="Signal summary dict")
    print(f"  → Signal: {sig_colored}")


def _print_buy_log(trader: PaperTrader, title="Buy log"):
    _subsection(title)
    if not trader._buy_log:
        print(f"  {DIM}  (empty — no buys executed){RESET}")
        return

    headers    = ["#", "Ticker", "Date", "Entry $", "Shares", "Cost $",
                  "Stop $", "Timestamp"]
    col_widths = [3, 6, 12, 9, 8, 9, 9, 10]
    col_aligns = [">", "<", "<", ">", ">", ">", ">", "<"]
    col_colors = [DIM, CYAN, DIM, WHITE, "", YELLOW, RED, DIM]

    rows = []
    for i, b in enumerate(trader._buy_log):
        rows.append([
            i + 1,
            b["ticker"],
            str(b["date"]),
            f"{b['entry_price']:.2f}",
            f"{b['shares']:.4f}",
            f"{b['cost']:.2f}",
            f"{b['stop_price']:.2f}",
            b.get("timestamp", "—"),
        ])
    _table(headers, rows, col_widths, col_colors, col_aligns,
           title=f"{title} ({len(trader._buy_log)} entries)")
    print(f"  {DIM}  Cash remaining: {RESET}{GREEN}${trader.cash:,.2f}{RESET}")


def _print_positions(trader: PaperTrader, current_prices: dict,
                     as_of=None, title="Positions"):
    _subsection(title)
    if not trader._positions:
        print(f"  {DIM}  (empty — no open positions){RESET}")
        return

    headers    = ["Ticker", "Entry $", "Current $", "Shares", "Cost $",
                  "Mkt Val $", "P&L $", "P&L %"]
    col_widths = [6, 9, 10, 8, 9, 10, 9, 8]
    col_aligns = ["<", ">", ">", ">", ">", ">", ">", ">"]
    col_colors = [CYAN, DIM, WHITE, DIM, DIM, YELLOW, "", ""]

    rows = []
    for ticker, pos in trader._positions.items():
        cur  = current_prices.get(ticker, pos.entry_price)
        mval = cur * pos.shares
        pnl  = mval - pos.cost
        pct  = (pnl / pos.cost * 100) if pos.cost else 0
        rows.append([
            ticker,
            f"{pos.entry_price:.2f}",
            f"{cur:.2f}",
            f"{pos.shares:.4f}",
            f"{pos.cost:.2f}",
            f"{mval:.2f}",
            f"{pnl:+.2f}",
            f"{pct:+.2f}%",
        ])

    def row_color(row):
        try:
            return GREEN if float(row[6]) >= 0 else RED
        except ValueError:
            return ""

    _table(headers, rows, col_widths, col_colors, col_aligns,
           row_color_fn=row_color,
           title=f"{title} (as of {as_of or 'n/a'})")

    if current_prices:
        pv  = trader.portfolio_value(current_prices)
        pnl = trader.total_pnl(current_prices)
        color = GREEN if pnl >= 0 else RED
        print(f"  {DIM}  Portfolio value: {RESET}{WHITE}${pv:,.2f}{RESET}"
              f"  │  P&L: {color}{pnl:+,.2f}{RESET}")


# ─────────────────────────────────────────────────────────────────
# Shared synthetic-data helpers
# ─────────────────────────────────────────────────────────────────

def _make_candles(
    closes: list,
    start_pt: str = "2024-01-15 06:30",
    freq_min: int = 10,
) -> pd.DataFrame:
    start = PT.localize(datetime.strptime(start_pt, "%Y-%m-%d %H:%M"))
    timestamps_pt  = [start + timedelta(minutes=freq_min * i) for i in range(len(closes))]
    timestamps_utc = [t.astimezone(timezone.utc) for t in timestamps_pt]

    df = pd.DataFrame({
        "datetime":   pd.to_datetime([t.isoformat() for t in timestamps_utc], utc=True),
        "candle":     [t.strftime("%H:%M PT") for t in timestamps_pt],
        "open":       closes,
        "high":       [c * 1.002 for c in closes],
        "low":        [c * 0.998 for c in closes],
        "close":      closes,
        "volume":     [500_000] * len(closes),
        "ticker":     "TEST",
        "fetched_at": datetime.utcnow(),
    })
    return df


def _golden_cross_closes(n_before: int = 22, spike: float = 15.0) -> list:
    declining  = [100.0 - i * 0.3 for i in range(n_before)]
    cross_bar1 = declining[-1] + spike * 0.6
    cross_bar2 = declining[-1] + spike
    return declining + [cross_bar1, cross_bar2]


def _death_cross_closes(n_before: int = 22, drop: float = 15.0) -> list:
    rising     = [80.0 + i * 0.3 for i in range(n_before)]
    cross_bar1 = rising[-1] - drop * 0.6
    cross_bar2 = rising[-1] - drop
    return rising + [cross_bar1, cross_bar2]


# ─────────────────────────────────────────────────────────────────
# Section A  — Candle construction & market-hours filter
# ─────────────────────────────────────────────────────────────────

class TestCandleConstruction(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION A — Candle construction & market-hours filter",
            "Verifies shape, timezone, label format, and hour-filter logic"
        )

    def test_candle_has_required_columns(self):
        df = _make_candles([100.0] * 5)
        required = {"datetime", "candle", "open", "high", "low", "close", "volume"}
        _print_candles(df, "Required-columns check (5 bars)")
        print(f"  Columns present: {CYAN}{sorted(df.columns.tolist())}{RESET}")
        print(f"  Required:        {CYAN}{sorted(required)}{RESET}")
        self.assertTrue(required.issubset(set(df.columns)),
                        f"Missing: {required - set(df.columns)}")

    def test_candle_timestamps_are_utc_aware(self):
        df = _make_candles([100.0] * 3)
        tz = df["datetime"].dt.tz
        print(f"\n  Detected timezone: {CYAN}{tz}{RESET}  (expected UTC)")
        self.assertIsNotNone(tz)
        self.assertEqual(str(tz), "UTC")

    def test_candle_labels_are_pt(self):
        df = _make_candles([100.0] * 3, start_pt="2024-01-15 06:30")
        labels = df["candle"].tolist()
        print(f"\n  Candle labels: {CYAN}{labels}{RESET}")
        self.assertEqual(labels[0], "06:30 PT")
        self.assertEqual(labels[1], "06:40 PT")
        self.assertEqual(labels[2], "06:50 PT")

    def test_candle_count_matches_input(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        df = _make_candles(closes)
        print(f"\n  Input length: {len(closes)}  │  DataFrame rows: {len(df)}")
        self.assertEqual(len(df), len(closes))

    def test_market_hours_filter_simulation(self):
        """
        Simulate the market-hours filter that fetch_intraday applies.
        Bars before 06:30 PT or at/after 13:00 PT should be excluded.
        """
        all_closes = [100.0 + i for i in range(44)]   # 44 × 10-min bars
        df = _make_candles(all_closes, start_pt="2024-01-15 06:00")

        pt_times = df["datetime"].dt.tz_convert(PT).dt.time
        import datetime as dt
        mask     = (pt_times >= dt.time(6, 30)) & (pt_times < dt.time(13, 0))
        filtered = df[mask].reset_index(drop=True)

        _subsection("Market-hours filter (06:30–12:50 PT)")
        # Show a compact view: pre-filter vs post-filter counts
        pre_post = [
            ["Total bars (06:00–13:10)", str(len(df)), ""],
            ["After filter (06:30–12:50)", str(len(filtered)),
             f"{GREEN}✓ 39 expected{RESET}" if len(filtered) == 39
             else f"{RED}✗ expected 39{RESET}"],
        ]
        _table(["Description", "Count", "Status"], pre_post, [28, 7, 20],
               title="Market-hours filter result")

        # Show first/last 3 filtered bars
        sample_rows = []
        for _, r in filtered.head(3).iterrows():
            sample_rows.append([r["candle"], f"{r['close']:.1f}", "✓ in range"])
        sample_rows.append(["…", "…", "…"])
        for _, r in filtered.tail(3).iterrows():
            sample_rows.append([r["candle"], f"{r['close']:.1f}", "✓ in range"])
        _table(["Candle", "Close", "Status"], sample_rows, [10, 8, 12],
               title="Filtered candle sample (first 3 + last 3)")

        self.assertEqual(len(filtered), 39,
                         f"Expected 39 market-hours bars, got {len(filtered)}")

    def test_last_candle_label(self):
        closes = [100.0] * 39
        df = _make_candles(closes, start_pt="2024-01-15 06:30")
        last = df["candle"].iloc[-1]
        print(f"\n  Last candle label: {CYAN}{last}{RESET}  (expected 12:50 PT)")
        self.assertEqual(last, "12:50 PT")


# ─────────────────────────────────────────────────────────────────
# Section B  — SMA calculation on intraday bars
# ─────────────────────────────────────────────────────────────────

class TestIntradaySMA(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION B — SMA calculation on intraday bars",
            "SMA8 & SMA20 warm-up periods, manual cross-check, edge cases"
        )

        # Print the full SMA table for a 25-bar series once for the whole section
        _subsection("Full SMA table — 25 flat bars at $150 (warm-up visualisation)")
        df = _make_candles([150.0] * 25)
        df = calculate_smas(df)
        _print_candles(df, "SMA table (flat $150)", max_rows=25)

        _subsection("Full SMA table — 25-bar ascending series (1,2,…,25)")
        closes = list(range(1, 26))
        df2 = _make_candles(closes)
        df2 = calculate_smas(df2)
        _print_candles(df2, "SMA table (ascending)", max_rows=25)

    def test_sma8_needs_8_bars(self):
        df = _make_candles([100.0] * 25)
        df = calculate_smas(df)
        nan_count = df["sma8"].iloc[:7].isna().sum()
        print(f"\n  SMA8 NaN count in first 7 bars: {nan_count}  (expected 7)")
        print(f"  SMA8 bar-8 value: {df['sma8'].iloc[7]:.4f}  (should not be NaN)")
        self.assertTrue(df["sma8"].iloc[:7].isna().all())
        self.assertFalse(pd.isna(df["sma8"].iloc[7]))

    def test_sma20_needs_20_bars(self):
        df = _make_candles([100.0] * 25)
        df = calculate_smas(df)
        nan_count = df["sma20"].iloc[:19].isna().sum()
        print(f"\n  SMA20 NaN count in first 19 bars: {nan_count}  (expected 19)")
        print(f"  SMA20 bar-20 value: {df['sma20'].iloc[19]:.4f}  (should not be NaN)")
        self.assertTrue(df["sma20"].iloc[:19].isna().all())
        self.assertFalse(pd.isna(df["sma20"].iloc[19]))

    def test_sma_values_match_manual(self):
        closes = list(range(1, 26))   # 1,2,…,25
        df = _make_candles(closes)
        df = calculate_smas(df)

        expected_sma8  = sum(range(1, 9)) / 8
        expected_sma20 = sum(range(1, 21)) / 20
        actual_sma8    = df["sma8"].dropna().iloc[0]
        actual_sma20   = df["sma20"].dropna().iloc[0]

        check_rows = [
            ["SMA8",  "sum(1..8)/8",   f"{expected_sma8:.4f}",  f"{actual_sma8:.4f}",
             f"{GREEN}✓{RESET}" if abs(actual_sma8 - expected_sma8) < 1e-4 else f"{RED}✗{RESET}"],
            ["SMA20", "sum(1..20)/20", f"{expected_sma20:.4f}", f"{actual_sma20:.4f}",
             f"{GREEN}✓{RESET}" if abs(actual_sma20 - expected_sma20) < 1e-4 else f"{RED}✗{RESET}"],
        ]
        _table(["Metric", "Formula", "Expected", "Actual", "Match"],
               check_rows, [6, 14, 10, 10, 6],
               title="SMA manual cross-check")

        self.assertAlmostEqual(actual_sma8,  expected_sma8,  places=4)
        self.assertAlmostEqual(actual_sma20, expected_sma20, places=4)

    def test_flat_closes_sma8_equals_sma20(self):
        df = _make_candles([150.0] * 25)
        df = calculate_smas(df)
        valid = df.dropna(subset=["sma8", "sma20"])
        max_diff = (valid["sma8"] - valid["sma20"]).abs().max()
        print(f"\n  Max |SMA8 - SMA20| on flat series: {max_diff:.8f}  (expected ~0)")
        for _, row in valid.iterrows():
            self.assertAlmostEqual(row["sma8"], row["sma20"], places=6)

    def test_calculate_smas_accepts_mixed_case(self):
        df = _make_candles([100.0] * 25)
        df = df.rename(columns={"close": "Close", "open": "Open"})
        print(f"\n  Columns before normalisation: {list(df.columns)}")
        try:
            result = calculate_smas(df)
            print(f"  Columns after  normalisation: {list(result.columns)}")
            self.assertIn("sma8",  result.columns)
            self.assertIn("sma20", result.columns)
        except ValueError:
            self.fail("calculate_smas raised ValueError on Title Case columns")

    def test_insufficient_bars_returns_no_signal(self):
        df = _make_candles([100.0] * 15)
        df = calculate_smas(df)
        sig = get_signal(df)
        nan_count_sma20 = df["sma20"].isna().sum()
        print(f"\n  Bars: 15  │  SMA20 all-NaN: {nan_count_sma20 == 15}  │  Signal: {sig}")
        self.assertEqual(sig, "HOLD")


# ─────────────────────────────────────────────────────────────────
# Section C  — Crossover detection  (candle_close mode)
# ─────────────────────────────────────────────────────────────────

class TestCandleCloseCrossover(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION C — Crossover detection (candle_close mode)",
            "Golden cross, death cross, hold, and no-double-fire checks"
        )

        # Golden cross visual
        _subsection("Golden-cross series (full SMA table)")
        closes = _golden_cross_closes()
        df = _make_candles(closes)
        df = calculate_smas(df)
        _print_candles(df, "Golden-cross candles", max_rows=len(df))
        s = signal_summary("TEST", df)
        _print_signal_summary(s)

        # Death cross visual
        _subsection("Death-cross series (full SMA table)")
        closes2 = _death_cross_closes()
        df2 = _make_candles(closes2)
        df2 = calculate_smas(df2)
        _print_candles(df2, "Death-cross candles", max_rows=len(df2))
        s2 = signal_summary("TEST", df2)
        _print_signal_summary(s2)

    def _build_signal_df(self, closes):
        df = _make_candles(closes)
        return calculate_smas(df)

    def test_golden_cross_detected(self):
        closes = _golden_cross_closes()
        df = self._build_signal_df(closes)
        sig = get_signal(df)
        buy = detect_buy_signal(df)
        print(f"\n  detect_buy_signal: {buy}  │  get_signal: {sig}")
        self.assertTrue(buy, "Expected BUY on golden cross")
        self.assertEqual(sig, "BUY")

    def test_death_cross_detected(self):
        closes = _death_cross_closes()
        df = self._build_signal_df(closes)
        sig  = get_signal(df)
        sell = detect_sell_signal(df)
        print(f"\n  detect_sell_signal: {sell}  │  get_signal: {sig}")
        self.assertTrue(sell, "Expected SELL on death cross")
        self.assertEqual(sig, "SELL")

    def test_hold_when_no_crossover(self):
        df = self._build_signal_df([100.0] * 25)
        sig = get_signal(df)
        print(f"\n  Flat series signal: {sig}  (expected HOLD)")
        self.assertEqual(sig, "HOLD")

    def test_no_double_signal_after_cross(self):
        base  = _golden_cross_closes()
        extra = base[-1] + 0.5
        df = self._build_signal_df(base + [extra])
        buy = detect_buy_signal(df)
        sig = get_signal(df)
        print(f"\n  One bar after cross  detect_buy_signal: {buy}  get_signal: {sig}")
        self.assertFalse(buy, "Should not re-fire BUY one bar after the cross")
        self.assertEqual(sig, "HOLD")

    def test_crossover_at_bar_minus1(self):
        closes  = _golden_cross_closes()
        closes2 = closes + [closes[-1] + 1.0]
        df = self._build_signal_df(closes2)
        sig = get_signal(df)
        print(f"\n  Cross on penultimate bar signal: {sig}  (expected HOLD)")
        self.assertEqual(sig, "HOLD")

    def test_signal_summary_keys(self):
        closes = _golden_cross_closes()
        df = self._build_signal_df(closes)
        s  = signal_summary("AAPL", df)
        required = {"ticker", "date", "close", "sma8", "sma20", "gap_pct", "signal"}
        print(f"\n  signal_summary keys returned: {sorted(s.keys())}")
        print(f"  Expected keys:                {sorted(required)}")
        self.assertEqual(required, set(s.keys()))
        self.assertEqual(s["signal"], "BUY")
        self.assertGreater(s["gap_pct"], 0)

    def test_signal_summary_gap_pct_direction(self):
        closes = _death_cross_closes()
        df = self._build_signal_df(closes)
        s  = signal_summary("AAPL", df)
        print(f"\n  Death-cross gap_pct: {s['gap_pct']:+.4f}%  (should be negative)")
        self.assertLess(s["gap_pct"], 0)

    def test_candle_close_uses_last_bar_price(self):
        closes = _golden_cross_closes()
        df = self._build_signal_df(closes)
        expected_price = float(df["close"].iloc[-1])
        s = signal_summary("TEST", df)
        print(f"\n  Last bar close: {expected_price:.4f}"
              f"  │  summary.close: {s['close']:.4f}")
        self.assertAlmostEqual(s["close"], expected_price, places=4)


# ─────────────────────────────────────────────────────────────────
# Section D  — Crossover detection  (live / crossover mode)
# ─────────────────────────────────────────────────────────────────

class TestCrossoverModeLogic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION D — Crossover detection (live-tick / crossover mode)",
            "Injecting a live price as the last bar's close before SMA recalc"
        )

        # Show before/after injection for a borderline series
        declining = [100.0 - i * 0.2 for i in range(24)]
        df_base   = _make_candles(declining)
        df_base   = calculate_smas(df_base)
        live_tick = 115.0
        df_live   = df_base.copy()
        df_live.loc[df_live.index[-1], "close"] = live_tick
        df_live   = calculate_smas(df_live)

        _subsection("Before injection — 24 declining bars")
        _print_candles(df_base, "Baseline (no injection)", max_rows=len(df_base))
        s_base = signal_summary("TEST", df_base)
        _print_signal_summary(s_base)

        _subsection(f"After injecting live tick = {live_tick}")
        _print_candles(df_live, "After injection", max_rows=len(df_live))
        s_live = signal_summary("TEST", df_live)
        _print_signal_summary(s_live)

        change_rows = [
            ["Baseline close",   f"{declining[-1]:.2f}", s_base["signal"]],
            ["Injected close",   f"{live_tick:.2f}",     s_live["signal"]],
        ]
        _table(["Scenario", "Last Close", "Signal"],
               change_rows, [16, 11, 8],
               title="Injection effect comparison")

    def _inject_live_price(self, df, live_price):
        df_eval = df.copy()
        df_eval.loc[df_eval.index[-1], "close"] = live_price
        return df_eval

    def test_live_price_injection_triggers_buy(self):
        declining = [100.0 - i * 0.2 for i in range(24)]
        df_base   = _make_candles(declining)
        df_base   = calculate_smas(df_base)
        base_sig  = get_signal(df_base)

        live_tick = 115.0
        df_live   = self._inject_live_price(df_base, live_tick)
        df_live   = calculate_smas(df_live)
        live_sig  = get_signal(df_live)

        print(f"\n  Before: {base_sig}  →  After injection: {live_sig}")
        self.assertEqual(base_sig, "HOLD", "Baseline should be HOLD before injection")
        self.assertEqual(live_sig, "BUY",  "Injected live price should trigger BUY")

    def test_live_price_below_does_not_trigger_buy(self):
        rising  = [80.0 + i * 0.5 for i in range(25)]
        df_base = _make_candles(rising)
        df_base = calculate_smas(df_base)

        df_live = self._inject_live_price(df_base, rising[-1] + 0.1)
        df_live = calculate_smas(df_live)
        sig     = get_signal(df_live)

        print(f"\n  Rising series + tiny tick → signal: {sig}  (should NOT be BUY)")
        self.assertNotEqual(sig, "BUY")

    def test_crossover_mode_price_is_live_not_bar_close(self):
        closes    = _golden_cross_closes()
        df_base   = _make_candles(closes)
        live_tick = 999.99
        df_live   = df_base.copy()
        df_live.loc[df_live.index[-1], "close"] = live_tick
        df_live   = calculate_smas(df_live)
        s         = signal_summary("TEST", df_live)

        print(f"\n  Injected live tick:  {live_tick}")
        print(f"  summary['close']:    {s['close']:.2f}")
        self.assertAlmostEqual(s["close"], live_tick, places=2,
                               msg="summary.close should reflect injected live price")


# ─────────────────────────────────────────────────────────────────
# Section E  — Simulator buy behavior for both modes
# ─────────────────────────────────────────────────────────────────

class TestSimulatorBuyBehavior(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION E — Simulator buy behavior",
            f"ALLOCATION_PCT={ALLOCATION_PCT*100:.0f}%  |  STOP_LOSS_PCT={STOP_LOSS_PCT*100:.0f}%"
        )

        _subsection("Cash compounding walkthrough (3 successive buys from $10,000)")
        trader = PaperTrader(starting_cash=10_000)
        steps  = []
        for sym in ["A", "B", "C"]:
            before = trader.cash
            trader.handle_signal(sym, "BUY", 50.0, date(2024, 1, 15))
            spent  = before - trader.cash
            steps.append([sym, f"${before:,.2f}", f"${spent:,.2f}", f"${trader.cash:,.2f}"])
        _table(["Ticker", "Cash before", "Spent (10%)", "Cash after"],
               steps, [7, 12, 13, 12],
               title=f"Compounding at {ALLOCATION_PCT*100:.0f}% of remaining cash")
        print(f"\n  Expected final cash: ${10_000 * 0.9**3:,.2f}"
              f"  │  Actual: ${trader.cash:,.2f}")

    def test_candle_close_buy_uses_bar_close_price(self):
        closes = _golden_cross_closes()
        df     = calculate_smas(_make_candles(closes))
        s      = signal_summary("AAPL", df)

        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", s["signal"], s["close"], date(2024, 1, 15))

        ep = trader._positions["AAPL"].entry_price
        print(f"\n  summary.close: {s['close']:.4f}  │  entry_price: {ep:.4f}")
        self.assertIn("AAPL", trader._positions)
        self.assertAlmostEqual(ep, s["close"], places=2)

    def test_candle_close_buy_cost_is_10pct_of_cash(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 150.0, date(2024, 1, 15))
        expected_spent = 10_000 * ALLOCATION_PCT
        print(f"\n  Starting cash: $10,000  │  Expected spend: ${expected_spent:,.2f}"
              f"  │  Remaining: ${trader.cash:,.2f}")
        self.assertAlmostEqual(trader.cash, 10_000 - expected_spent, places=2)

    def test_candle_close_no_duplicate_buy(self):
        trader = PaperTrader(starting_cash=10_000)
        r1 = trader.handle_signal("AAPL", "BUY", 150.0, date(2024, 1, 15))
        r2 = trader.handle_signal("AAPL", "BUY", 155.0, date(2024, 1, 15))

        dup_rows = [
            ["1st signal", "150.00", r1],
            ["2nd signal (dup)", "155.00", r2],
        ]
        _table(["Attempt", "Price", "Result"], dup_rows, [18, 7, 8],
               title="Duplicate buy dedup check")

        self.assertEqual(r1, "BOUGHT")
        self.assertEqual(r2, "SKIPPED")
        self.assertEqual(len(trader._buy_log), 1)

    def test_crossover_mode_buy_uses_live_price(self):
        live_tick = 182.37
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("TSLA", "BUY", live_tick, date(2024, 1, 15))
        ep = trader._positions["TSLA"].entry_price
        print(f"\n  Live tick: {live_tick}  │  Recorded entry_price: {ep:.2f}")
        self.assertAlmostEqual(ep, live_tick, places=2)

    def test_crossover_mode_multiple_tickers(self):
        trader  = PaperTrader(starting_cash=100_000)
        signals = [("AAPL", 182.00), ("TSLA", 248.00), ("NVDA", 495.00)]
        results = []
        for ticker, price in signals:
            result = trader.handle_signal(ticker, "BUY", price, date(2024, 1, 15))
            results.append([ticker, f"${price:.2f}", result])
            self.assertEqual(result, "BOUGHT", f"{ticker} should be BOUGHT")

        _table(["Ticker", "Price", "Result"], results, [7, 8, 8],
               title="Multi-ticker buy results")
        self.assertEqual(len(trader._positions), 3)
        self.assertEqual(len(trader._buy_log), 3)

    def test_hold_signal_never_buys(self):
        trader = PaperTrader(starting_cash=10_000)
        result = trader.handle_signal("MSFT", "HOLD", 374.00, date(2024, 1, 15))
        print(f"\n  HOLD signal result: {result}  │  Positions: {len(trader._positions)}"
              f"  │  Cash: ${trader.cash:,.2f}")
        self.assertEqual(result, "HELD")
        self.assertEqual(len(trader._positions), 0)
        self.assertEqual(trader.cash, 10_000)

    def test_buy_compounding_cash_shrinks(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("A", "BUY", 50.0, date(2024, 1, 15))
        trader.handle_signal("B", "BUY", 50.0, date(2024, 1, 15))
        trader.handle_signal("C", "BUY", 50.0, date(2024, 1, 15))
        expected = 10_000 * (0.9 ** 3)
        print(f"\n  Expected cash (0.9^3 × $10k): ${expected:,.2f}"
              f"  │  Actual: ${trader.cash:,.2f}")
        self.assertAlmostEqual(trader.cash, expected, places=2)


# ─────────────────────────────────────────────────────────────────
# Section F  — Buy log storage: fields, dedup, timestamp stamping
# ─────────────────────────────────────────────────────────────────

class TestBuyLogStorage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION F — Buy log storage",
            "Fields, stop-price math, dedup across candles, timestamp stamping"
        )

        # Print a sample buy log for a 3-ticker session
        _subsection("Sample buy log — 3 tickers (AAPL, TSLA, NVDA)")
        trader = PaperTrader(starting_cash=100_000)
        entries = [("AAPL", 182.00), ("TSLA", 248.00), ("NVDA", 495.00)]
        for t, p in entries:
            trader.handle_signal(t, "BUY", p, date(2024, 1, 15))
            trader._buy_log[-1]["timestamp"] = "09:30 PT"
        _print_buy_log(trader, "Sample buy log")

        # Stop price verification table
        _subsection(f"Stop-price cross-check  (STOP_LOSS_PCT = {STOP_LOSS_PCT*100:.1f}%)")
        stop_rows = []
        for b in trader._buy_log:
            expected = round(b["entry_price"] * (1 - STOP_LOSS_PCT), 4)
            match = abs(b["stop_price"] - expected) < 1e-4
            stop_rows.append([
                b["ticker"],
                f"${b['entry_price']:.2f}",
                f"${expected:.4f}",
                f"${b['stop_price']:.4f}",
                f"{GREEN}✓{RESET}" if match else f"{RED}✗{RESET}",
            ])
        _table(["Ticker", "Entry", "Expected Stop", "Actual Stop", "OK"],
               stop_rows, [7, 8, 15, 13, 4],
               title="Stop-price verification")

    def test_buy_log_required_fields(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 182.00, date(2024, 1, 15))
        b = trader._buy_log[0]
        required = {"ticker", "date", "entry_price", "shares", "cost", "stop_price"}
        print(f"\n  Fields present: {sorted(b.keys())}")
        print(f"  Required:       {sorted(required)}")
        self.assertEqual(required, set(b.keys()))

    def test_buy_log_stop_price_correct(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("TSLA", "BUY", 200.0, date(2024, 1, 15))
        b  = trader._buy_log[0]
        ex = round(200.0 * (1 - STOP_LOSS_PCT), 4)
        print(f"\n  Entry: $200.00  │  Expected stop: ${ex:.4f}"
              f"  │  Actual stop: ${b['stop_price']:.4f}")
        self.assertAlmostEqual(b["stop_price"], ex, places=4)

    def test_buy_log_shares_match_cost_over_price(self):
        trader = PaperTrader(starting_cash=10_000)
        price  = 250.0
        trader.handle_signal("NVDA", "BUY", price, date(2024, 1, 15))
        b  = trader._buy_log[0]
        ex = b["cost"] / price
        print(f"\n  cost/price = {b['cost']:.2f}/{price:.2f} = {ex:.6f}"
              f"  │  recorded shares: {b['shares']:.6f}")
        self.assertAlmostEqual(b["shares"], ex, places=6)

    def test_candle_timestamp_can_be_added(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 182.0, date(2024, 1, 15))
        trader._buy_log[-1]["timestamp"] = "09:30 PT"
        ts = trader._buy_log[-1]["timestamp"]
        print(f"\n  Stamped timestamp: {ts}")
        self.assertEqual(ts, "09:30 PT")

    def test_buy_log_preserves_order(self):
        trader  = PaperTrader(starting_cash=100_000)
        tickers = ["AAPL", "TSLA", "NVDA", "META", "GOOG"]
        for t in tickers:
            trader.handle_signal(t, "BUY", 100.0, date(2024, 1, 15))
        logged = [b["ticker"] for b in trader._buy_log]
        order_rows = [[str(i+1), exp, act,
                       f"{GREEN}✓{RESET}" if exp == act else f"{RED}✗{RESET}"]
                      for i, (exp, act) in enumerate(zip(tickers, logged))]
        _table(["#", "Expected", "Actual", "OK"], order_rows, [3, 8, 8, 4],
               title="Buy log order check")
        self.assertEqual(logged, tickers)

    def test_dedup_across_candles_same_session(self):
        trader = PaperTrader(starting_cash=10_000)
        r1 = trader.handle_signal("AAPL", "BUY", 182.0, date(2024, 1, 15))
        r2 = trader.handle_signal("AAPL", "BUY", 183.5, date(2024, 1, 15))
        dup_rows = [
            ["1st candle", "182.00", r1],
            ["2nd candle (dup)", "183.50", r2],
        ]
        _table(["Candle", "Price", "Result"], dup_rows, [17, 7, 8],
               title="Same-session dedup check")
        self.assertEqual(len(trader._buy_log), 1)

    def test_candle_close_mode_entry_price_matches_summary(self):
        closes = _golden_cross_closes()
        df     = calculate_smas(_make_candles(closes))
        s      = signal_summary("AAPL", df)

        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", s["signal"], s["close"], date(2024, 1, 15))

        if s["signal"] == "BUY":
            ep = trader._buy_log[0]["entry_price"]
            print(f"\n  summary.close: {s['close']:.4f}  │  log entry_price: {ep:.4f}")
            self.assertAlmostEqual(ep, s["close"], places=4)

    def test_crossover_mode_entry_price_matches_live_tick(self):
        closes    = _golden_cross_closes()
        df_base   = _make_candles(closes)
        live_tick = 123.45
        df_live   = df_base.copy()
        df_live.loc[df_live.index[-1], "close"] = live_tick
        df_live   = calculate_smas(df_live)
        s         = signal_summary("TEST", df_live)
        s["close"] = live_tick

        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("TEST", "BUY", s["close"], date(2024, 1, 15))
        ep = trader._buy_log[0]["entry_price"]
        print(f"\n  Injected live tick: {live_tick}  │  log entry_price: {ep:.2f}")
        self.assertAlmostEqual(ep, live_tick, places=2)


# ─────────────────────────────────────────────────────────────────
# Section G  — Display smoke-tests
# ─────────────────────────────────────────────────────────────────

class TestDisplayOutput(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _section_banner(
            "SECTION G — Display smoke-tests & portfolio math",
            "print_buys / print_positions / portfolio_value / total_pnl"
        )

        # Build a 3-position trader and display everything
        trader = PaperTrader(starting_cash=10_000)
        entries = [("AAPL", 182.00), ("TSLA", 248.00), ("NVDA", 495.00)]
        for t, p in entries:
            trader.handle_signal(t, "BUY", p, date(2024, 1, 15))
            trader._buy_log[-1]["timestamp"] = "09:30 PT"

        current_prices = {"AAPL": 190.0, "TSLA": 255.0, "NVDA": 510.0}

        _print_buy_log(trader, "Section G — sample buy log")
        _print_positions(trader, current_prices, as_of=date(2024, 1, 15),
                         title="Section G — open positions (mark-to-market)")

        pv  = trader.portfolio_value(current_prices)
        pnl = trader.total_pnl(current_prices)
        math_rows = [
            ["Starting cash",    "$10,000.00", ""],
            ["portfolio_value()", f"${pv:,.2f}", "cash + mark-to-market positions"],
            ["total_pnl()",       f"${pnl:+,.2f}", "portfolio_value − starting_cash"],
        ]
        _table(["Metric", "Value", "Notes"], math_rows, [18, 12, 34],
               title="Portfolio math summary")

    def _build_trader(self):
        trader = PaperTrader(starting_cash=10_000)
        entries = [("AAPL", 182.00), ("TSLA", 248.00), ("NVDA", 495.00)]
        for ticker, price in entries:
            trader.handle_signal(ticker, "BUY", price, date(2024, 1, 15))
            trader._buy_log[-1]["timestamp"] = "09:30 PT"
        return trader

    def test_print_buys_does_not_raise(self):
        trader = self._build_trader()
        try:
            trader.print_buys()
        except Exception as exc:
            self.fail(f"print_buys() raised: {exc}")

    def test_print_positions_does_not_raise(self):
        trader = self._build_trader()
        current_prices = {"AAPL": 190.0, "TSLA": 255.0, "NVDA": 510.0}
        try:
            trader.print_positions(current_prices, as_of=date(2024, 1, 15))
        except Exception as exc:
            self.fail(f"print_positions() raised: {exc}")

    def test_print_buys_empty_does_not_raise(self):
        trader = PaperTrader(starting_cash=10_000)
        try:
            trader.print_buys()
        except Exception as exc:
            self.fail(f"print_buys() on empty log raised: {exc}")

    def test_print_positions_empty_does_not_raise(self):
        trader = PaperTrader(starting_cash=10_000)
        try:
            trader.print_positions({})
        except Exception as exc:
            self.fail(f"print_positions() on empty positions raised: {exc}")

    def test_portfolio_value_with_gains(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 15))
        trader.handle_signal("TSLA", "BUY", 200.0, date(2024, 1, 15))
        prices = {"AAPL": 120.0, "TSLA": 250.0}
        val    = trader.portfolio_value(prices)
        print(f"\n  Portfolio value (positions up): ${val:,.2f}  │  Starting: $10,000.00")
        self.assertGreater(val, 10_000)

    def test_portfolio_value_with_losses(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 15))
        val = trader.portfolio_value({"AAPL": 80.0})
        print(f"\n  Portfolio value (position down): ${val:,.2f}  │  Starting: $10,000.00")
        self.assertLess(val, 10_000)

    def test_total_pnl_accuracy(self):
        trader = PaperTrader(starting_cash=10_000)
        trader.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 15))
        prices   = {"AAPL": 110.0}
        expected = trader.portfolio_value(prices) - 10_000
        actual   = trader.total_pnl(prices)
        print(f"\n  portfolio_value − 10k = {expected:+,.4f}"
              f"  │  total_pnl() = {actual:+,.4f}")
        self.assertAlmostEqual(actual, expected, places=4)


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    sections = [
        ("A — Candle construction & market-hours filter", TestCandleConstruction),
        ("B — SMA calculation on intraday bars",          TestIntradaySMA),
        ("C — Crossover detection (candle_close mode)",   TestCandleCloseCrossover),
        ("D — Crossover detection (crossover/live mode)", TestCrossoverModeLogic),
        ("E — Simulator buy behavior",                    TestSimulatorBuyBehavior),
        ("F — Buy log storage",                           TestBuyLogStorage),
        ("G — Display smoke-tests",                       TestDisplayOutput),
    ]

    RESET = "\033[0m"; BOLD = "\033[1m"; CYAN = "\033[36m"
    GREEN = "\033[32m"; RED = "\033[31m"; DIM = "\033[2m"
    W = 90

    total_passed = total_failed = 0

    for label, cls in sections:
        s      = loader.loadTestsFromTestCase(cls)
        result = unittest.TextTestRunner(
            verbosity=0, stream=open(os.devnull, "w")
        ).run(s)

        passed = s.countTestCases() - len(result.failures) - len(result.errors)
        failed = len(result.failures) + len(result.errors)
        total_passed += passed
        total_failed += failed

        # Print result banner for this section
        print(f"\n{BOLD}  ── {label} ──{RESET}")
        status = f"{GREEN}✓ {passed} passed{RESET}"
        if failed:
            status += f"  {RED}✗ {failed} failed{RESET}"
        print(f"     {status}")

        for test, msg in result.failures + result.errors:
            print(f"  {RED}  FAIL: {test}{RESET}")
            for line in msg.strip().splitlines():
                if "AssertionError" in line or "Error" in line:
                    print(f"  {DIM}       {line.strip()}{RESET}")
                    break

    print(f"\n{BOLD}{'─' * W}{RESET}")
    if total_failed == 0:
        print(f"{BOLD}{GREEN}  ✓ All {total_passed} tests passed{RESET}")
    else:
        print(f"{BOLD}{RED}  ✗ {total_failed} failed  "
              f"{GREEN}{total_passed} passed{RESET}")
    print(f"{BOLD}{'═' * W}{RESET}\n")

    sys.exit(1 if total_failed else 0)