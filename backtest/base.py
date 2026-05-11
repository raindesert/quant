"""回测引擎公共基类，提取指标计算和账户管理等共享逻辑。"""
from __future__ import annotations

import math
from strategy.base import Signal


class BaseBacktestEngine:
    """回测引擎公共基类。

    提供账户管理、指标计算、止损止盈检查等共享功能。
    """

    TRADING_DAYS_PER_YEAR = 244

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        stamp_tax: float = 0.001,
        min_commission: float = 5.0,
    ):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.stamp_tax = stamp_tax
        self.min_commission = min_commission
        self.reset()

    def reset(self):
        self.cash = self.initial_cash
        self.positions = {}
        self.entry_prices = {}
        self.trades = []
        self.equity_curve = []
        self.benchmark_curve = []
        self.last_prices = {}
        self.pending_signal = {}
        self._last_action = None

    @staticmethod
    def _row_to_bar(row, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "date": row.date,
            "timestamp": row.date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "last_price": row.close,
            "volume": row.volume,
        }

    def _calc_buy_cost(self, price: float, quantity: int) -> float:
        commission = max(price * quantity * self.commission, self.min_commission)
        return price * quantity + commission

    def _calc_sell_proceeds(self, price: float, quantity: int) -> float:
        commission = max(price * quantity * self.commission, self.min_commission)
        stamp = price * quantity * self.stamp_tax
        return price * quantity - commission - stamp

    def _check_stop_loss(self, symbol: str, current_price: float, pending: dict):
        if self.stop_loss <= 0 or symbol not in self.positions:
            return
        if self.positions[symbol] <= 0:
            return
        entry = self.entry_prices.get(symbol, 0.0)
        if entry <= 0:
            return
        if (current_price - entry) / entry <= -self.stop_loss:
            pending[symbol] = Signal.SELL

    def _check_take_profit(self, symbol: str, current_price: float, pending: dict):
        if self.take_profit <= 0 or symbol not in self.positions:
            return
        if self.positions[symbol] <= 0:
            return
        entry = self.entry_prices.get(symbol, 0.0)
        if entry <= 0:
            return
        if (current_price - entry) / entry >= self.take_profit:
            pending[symbol] = Signal.SELL

    def get_total_value(self) -> float:
        positions_value = sum(
            qty * self.last_prices.get(sym, 0.0)
            for sym, qty in self.positions.items()
        )
        return self.cash + positions_value

    def _calc_max_drawdown(self) -> tuple[float, float]:
        if not self.equity_curve:
            return 0.0, 0.0
        values = [e["value"] for e in self.equity_curve]
        peak = values[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = peak - v
            dd_pct = dd / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
        return max_dd, max_dd_pct

    def _calc_annual_return(self) -> float:
        if not self.equity_curve or len(self.equity_curve) < 2:
            return 0.0
        days = len(self.equity_curve)
        years = days / self.TRADING_DAYS_PER_YEAR
        if years < 0.01:
            return 0.0
        total_ret = self.equity_curve[-1]["value"] / self.equity_curve[0]["value"]
        return (total_ret ** (1 / years) - 1) * 100 if total_ret > 0 else 0.0

    def _calc_sharpe_ratio(self) -> float:
        if len(self.equity_curve) < 10:
            return 0.0
        values = [e["value"] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                ret = (values[i] - values[i - 1]) / values[i - 1]
                returns.append(ret)
        if not returns:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0.0
        if std_ret == 0:
            return 0.0
        return (mean_ret / std_ret) * math.sqrt(self.TRADING_DAYS_PER_YEAR)

    def _calc_benchmark_return(self) -> float:
        if not self.benchmark_curve:
            return 0.0
        start = self.benchmark_curve[0]["value"]
        end = self.benchmark_curve[-1]["value"]
        return (end - start) / start * 100 if start else 0.0

    def _calc_trade_stats(self) -> dict:
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        win_trades = 0
        total_profit = 0.0
        total_loss = 0.0
        for t in sell_trades:
            entry_price = self.entry_prices.get(t["symbol"], 0.0)
            pnl = (t["price"] - entry_price) * t["quantity"] - t.get("commission_cost", 0.0)
            if pnl > 0:
                win_trades += 1
                total_profit += pnl
            else:
                total_loss += abs(pnl)
        total = len(sell_trades)
        win_rate = win_trades / total * 100 if total else 0.0
        avg_win = total_profit / win_trades if win_trades else 0.0
        avg_loss = total_loss / (total - win_trades) if total and total > win_trades else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf") if total_profit > 0 else 0.0
        return {
            "trades": len(self.trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
        }
