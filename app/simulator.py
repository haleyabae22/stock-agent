"""
app/simulator.py
----------------
Paper trading engine. Tracks BUY orders and shows what was purchased.

Tracks:
  - Cash balance
  - Open positions (entry price, shares held) — unlimited

No selling. No summary totals. Shows:
  - What the agent bought (print_buys)
  - Live P&L on all holdings (print_positions)

Public API:
    trader = PaperTrader(starting_cash=10_000)
    trader.handle_signal(ticker, signal, current_price, date)
    trader.open_positions()
    trader.portfolio_value(current_prices)    ← cash + market value of holdings
    trader.total_pnl(current_prices)          ← unrealized P&L vs starting cash
    trader.total_pnl_pct(current_prices)      ← percent return on portfolio
    trader.print_buys()                       ← table of everything bought
    trader.print_positions(current_prices)    ← live P&L on holdings

Run directly to self-test:
    python -m app.simulator
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

ALLOCATION_PCT = 0.10   # spend 10% of current cash per BUY signal
STOP_LOSS_PCT  = 0.07   # stop price shown in table (informational)


# ─────────────────────────────────────────────
# ANSI colour helpers
# ─────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_WHITE  = "\033[97m"

def _pnl_color(value: float) -> str:
    return _GREEN if value >= 0 else _RED


# ─────────────────────────────────────────────
# Data class
# ─────────────────────────────────────────────

@dataclass
class Position:
    ticker:      str
    shares:      float
    entry_price: float
    entry_date:  date
    cost:        float            # dollars spent at entry
    stop_price:  float = field(init=False)

    def __post_init__(self):
        self.stop_price = round(self.entry_price * (1 - STOP_LOSS_PCT), 4)

    def current_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_pnl(self, price: float) -> float:
        return self.shares * (price - self.entry_price)

    def unrealized_pnl_pct(self, price: float) -> float:
        return (price - self.entry_price) / self.entry_price * 100

    def days_held(self, as_of: date) -> int:
        return (as_of - self.entry_date).days


# ─────────────────────────────────────────────
# Paper trading engine
# ─────────────────────────────────────────────

class PaperTrader:
    def __init__(self, starting_cash: float = 10_000.0):
        self.starting_cash: float               = starting_cash
        self.cash:          float               = starting_cash
        self._positions:    dict[str, Position] = {}
        self._buy_log:      list[dict]          = []   # ordered record of every buy

    # ── Public accessors ──────────────────────

    def open_positions(self) -> dict[str, Position]:
        """Return a copy of the current open positions dict."""
        return dict(self._positions)

    def portfolio_value(self, current_prices: dict[str, float]) -> float:
        """
        Total account value: cash + market value of all open positions.
        Falls back to entry price for any ticker missing from current_prices.
        """
        holdings = sum(
            pos.current_value(current_prices.get(ticker, pos.entry_price))
            for ticker, pos in self._positions.items()
        )
        return self.cash + holdings

    def total_pnl(self, current_prices: dict[str, float]) -> float:
        """Total unrealized profit/loss versus starting cash."""
        return self.portfolio_value(current_prices) - self.starting_cash

    def total_pnl_pct(self, current_prices: dict[str, float]) -> float:
        """Percent return on the full starting portfolio."""
        return self.total_pnl(current_prices) / self.starting_cash * 100

    # ── Signal handler ────────────────────────

    def handle_signal(
        self,
        ticker:        str,
        signal:        str,   # "BUY" | "HOLD"
        current_price: float,
        as_of:         date = None,
    ) -> Optional[str]:
        """
        Act on a BUY signal. HOLD signals are ignored.

        Returns: "BOUGHT" | "SKIPPED" | "HELD"
        """
        as_of = as_of or date.today()
        if signal == "BUY":
            return self._execute_buy(ticker, current_price, as_of)
        return "HELD"

    # ── Display tables ────────────────────────

    def print_buys(self) -> None:
        """
        Print every BUY the agent has made, in order, with entry details.

        Columns: #, Ticker, Date, Entry $, Shares, Cost $, Stop $
        """
        if not self._buy_log:
            print(f"\n  {_DIM}No buys yet.{_RESET}\n")
            return

        W = 72
        print()
        print(f"  {_BOLD}{_CYAN}{'BUYS — SIGNAL TRIGGERED':^{W}}{_RESET}")
        print(f"  {'═' * W}")
        print(
            f"  {_BOLD}"
            f"{'#':<4}{'Ticker':<8}{'Date':<13}{'Entry $':>8}"
            f"{'Shares':>10}{'Cost $':>10}{'Stop $':>9}"
            f"{_RESET}"
        )
        print(f"  {'─' * W}")

        for i, b in enumerate(self._buy_log, 1):
            print(
                f"  {i:<4}"
                f"{_BOLD}{_WHITE}{b['ticker']:<8}{_RESET}"
                f"{str(b['date']):<13}"
                f"${b['entry_price']:>7.2f}"
                f"{b['shares']:>10.4f}"
                f"${b['cost']:>9.2f}"
                f"${b['stop_price']:>8.2f}"
            )

        total_cost = sum(b["cost"] for b in self._buy_log)
        print(f"  {'─' * W}")
        print(
            f"  {_BOLD}{'TOTAL':<4}{'':<8}{'':<13}{'':>8}{'':>10}"
            f"${total_cost:>9.2f}{'':>9}{_RESET}"
        )
        print(f"  {'═' * W}")
        print(f"  {_DIM}{len(self._buy_log)} buy(s)  "
              f"Cash remaining: ${self.cash:,.2f}{_RESET}\n")

    def print_positions(self, current_prices: dict[str, float], as_of: date = None) -> None:
        """
        Print open positions with live P&L.

        Columns: #, Ticker, Entry $, Now $, Shares, P&L $, P&L %, Stop $, Days
        """
        as_of = as_of or date.today()

        if not self._positions:
            print(f"\n  {_DIM}No open positions.{_RESET}\n")
            return

        W = 74
        print()
        print(f"  {_BOLD}{_CYAN}{'OPEN POSITIONS':^{W}}{_RESET}")
        print(f"  {'═' * W}")
        print(
            f"  {_BOLD}"
            f"{'#':<4}{'Ticker':<8}{'Entry $':>8}{'Now $':>8}"
            f"{'Shares':>10}{'P&L $':>10}{'P&L %':>8}{'Stop $':>8}{'Days':>6}"
            f"{_RESET}"
        )
        print(f"  {'─' * W}")

        for i, (ticker, pos) in enumerate(sorted(self._positions.items()), 1):
            price   = current_prices.get(ticker, pos.entry_price)
            pnl     = pos.unrealized_pnl(price)
            pnl_pct = pos.unrealized_pnl_pct(price)
            days    = pos.days_held(as_of)
            col     = _pnl_color(pnl)

            print(
                f"  {i:<4}"
                f"{_BOLD}{_WHITE}{ticker:<8}{_RESET}"
                f"${pos.entry_price:>7.2f}"
                f"${price:>7.2f}"
                f"{pos.shares:>10.4f}"
                f"{col}{pnl:>+10.2f}{_RESET}"
                f"{col}{pnl_pct:>+7.1f}%{_RESET}"
                f"${pos.stop_price:>7.2f}"
                f"{days:>6}d"
            )

        print(f"  {'═' * W}")
        print(f"  {_DIM}{len(self._positions)} open position(s){_RESET}\n")

    # ── Internal execution ────────────────────

    def _execute_buy(self, ticker: str, price: float, as_of: date) -> str:
        if ticker in self._positions:
            return "SKIPPED"   # already holding

        if self.cash <= 0:
            print(f"  ⚠ No cash remaining — skipping {ticker}")
            return "SKIPPED"

        amount = self.cash * ALLOCATION_PCT
        shares = amount / price
        self.cash -= amount

        pos = Position(
            ticker=ticker,
            shares=shares,
            entry_price=price,
            entry_date=as_of,
            cost=round(amount, 4),
        )
        self._positions[ticker] = pos
        self._buy_log.append({
            "ticker":      ticker,
            "date":        as_of,
            "entry_price": price,
            "shares":      shares,
            "cost":        round(amount, 4),
            "stop_price":  pos.stop_price,
        })

        print(f"  ✓ BUY  {ticker:<6}  {shares:.4f} shares @ ${price:.2f}"
              f"  (cost=${amount:.2f}  stop=${pos.stop_price:.2f})")
        return "BOUGHT"


# ─────────────────────────────────────────────
# Self-tests
# ─────────────────────────────────────────────

def _test_buy_on_signal():
    print("  test_buy_on_signal ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 2))
    assert "AAPL" in t._positions
    assert abs(t.cash - 9_000) < 0.01
    assert len(t._buy_log) == 1
    print("PASS")


def _test_hold_ignored():
    print("  test_hold_ignored ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "HOLD", 100.0, date(2024, 1, 2))
    assert len(t._positions) == 0
    assert t.cash == 10_000
    print("PASS")


def _test_no_duplicate_buy():
    print("  test_no_duplicate_buy ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 2))
    cash_after = t.cash
    t.handle_signal("AAPL", "BUY", 105.0, date(2024, 1, 3))
    assert t.cash == cash_after
    assert len(t._buy_log) == 1
    print("PASS")


def _test_unlimited_positions():
    print("  test_unlimited_positions ... ", end="")
    t = PaperTrader(starting_cash=100_000)
    for i in range(20):
        t.handle_signal(f"T{i}", "BUY", 50.0, date(2024, 1, 2))
    assert len(t._positions) == 20
    print("PASS")


def _test_buy_log_fields():
    print("  test_buy_log_fields ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("TSLA", "BUY", 200.0, date(2024, 1, 5))
    b = t._buy_log[0]
    assert set(b.keys()) == {"ticker", "date", "entry_price", "shares", "cost", "stop_price"}
    assert b["ticker"] == "TSLA"
    assert abs(b["cost"] - 1_000.0) < 0.01
    assert abs(b["stop_price"] - 200.0 * (1 - STOP_LOSS_PCT)) < 0.01
    print("PASS")


def _test_portfolio_value():
    print("  test_portfolio_value ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 2))   # spent $1,000, holds 10 shares
    # price doubles → holdings worth $2,000, cash $9,000 → total $11,000
    assert abs(t.portfolio_value({"AAPL": 200.0}) - 11_000.0) < 0.01
    print("PASS")


def _test_total_pnl():
    print("  test_total_pnl ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 100.0, date(2024, 1, 2))
    assert abs(t.total_pnl({"AAPL": 200.0}) - 1_000.0) < 0.01
    assert abs(t.total_pnl_pct({"AAPL": 200.0}) - 10.0) < 0.01
    print("PASS")


def _test_print_buys_runs():
    print("  test_print_buys_runs ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 182.0, date(2024, 1, 2))
    t.handle_signal("NVDA", "BUY", 495.0, date(2024, 1, 3))
    t.print_buys()   # must not raise
    print("PASS")


def _test_print_positions_runs():
    print("  test_print_positions_runs ... ", end="")
    t = PaperTrader(starting_cash=10_000)
    t.handle_signal("AAPL", "BUY", 182.0, date(2024, 1, 2))
    t.handle_signal("NVDA", "BUY", 495.0, date(2024, 1, 3))
    t.print_positions({"AAPL": 195.0, "NVDA": 520.0}, as_of=date(2024, 1, 10))
    print("PASS")


def run_tests():
    print("\n=== simulator.py tests ===\n")
    _test_buy_on_signal()
    _test_hold_ignored()
    _test_no_duplicate_buy()
    _test_unlimited_positions()
    _test_buy_log_fields()
    _test_portfolio_value()
    _test_total_pnl()
    _test_print_buys_runs()
    _test_print_positions_runs()
    print("\n✓ All simulator tests passed\n")


# ─────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_tests()

    print("=== Demo: agent buys ===\n")
    trader = PaperTrader(starting_cash=10_000)

    buys = [
        (date(2024, 1,  2), "AAPL",  182.00),
        (date(2024, 1,  3), "TSLA",  248.00),
        (date(2024, 1,  8), "NVDA",  495.00),
        (date(2024, 1,  9), "META",  370.00),
        (date(2024, 1, 10), "GOOG",  140.00),
        (date(2024, 1, 11), "AMZN",  178.00),
    ]
    for d, ticker, price in buys:
        trader.handle_signal(ticker, "BUY", price, as_of=d)

    trader.print_buys()

    final_prices = {
        "AAPL": 191.00, "TSLA": 238.00, "NVDA": 540.00,
        "META": 390.00, "GOOG": 145.00, "AMZN": 175.00,
    }
    trader.print_positions(final_prices, as_of=date(2024, 1, 20))

    print(f"Portfolio value : ${trader.portfolio_value(final_prices):,.2f}")
    print(f"Total P&L       : ${trader.total_pnl(final_prices):+,.2f}")
    print(f"Total P&L %     : {trader.total_pnl_pct(final_prices):+.2f}%")