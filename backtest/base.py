"""回测引擎公共基类，提取指标计算和账户管理等共享逻辑。"""
from __future__ import annotations

import math
from strategy.base import Signal
from risk.manager import RiskManager

LIMIT_UP_THRESHOLD_ST = 0.05
LIMIT_UP_THRESHOLD_MAIN = 0.10
LIMIT_UP_THRESHOLD_NEW = 0.20


class BaseBacktestEngine:
    """回测引擎公共基类。

    提供账户管理、指标计算、止损止盈检查等共享功能。
    支持: T+1约束、滑点模型、涨跌停判断、风控管理。
    """

    TRADING_DAYS_PER_YEAR = 244
    RISK_FREE_RATE_DAILY = 0.03 / 244

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        stamp_tax: float = 0.001,
        min_commission: float = 5.0,
        slippage: float = 0.0,
        slippage_type: str = "percent",
        enforce_t_plus_1: bool = True,
        check_limit: bool = True,
        risk_manager: RiskManager | None = None,
    ):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.stamp_tax = stamp_tax
        self.min_commission = min_commission
        self.slippage = slippage
        self.slippage_type = slippage_type
        self.enforce_t_plus_1 = enforce_t_plus_1
        self.check_limit = check_limit
        self.risk_manager = risk_manager
        self.reset()

    def reset(self):
        self.cash = self.initial_cash
        self.positions = {}
        self.entry_prices = {}
        self.entry_dates = {}
        self.trades = []
        self.equity_curve = []
        self.benchmark_curve = []
        self.last_prices = {}
        self.last_bars = {}
        self.pending_signal = {}
        self._last_action = None
        self._prev_date = None
        if self.risk_manager is not None:
            self.risk_manager.reset()

    @staticmethod
    def _row_to_bar(row, symbol: str) -> dict:
        bar = {
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
        indicator_fields = [
            "ma5", "ma10", "ma20", "ma60",
            "bb_mid", "bb_upper", "bb_lower", "bb_std",
            "rsi",
            "macd_dif", "macd_dea", "macd_hist",
            "atr",
            "k", "d", "j",
        ]
        for field in indicator_fields:
            val = getattr(row, field, None)
            if val is not None and not (isinstance(val, float) and (val != val)):
                bar[field] = val
        return bar

    def _apply_slippage(self, price: float, direction: str) -> float:
        if self.slippage <= 0:
            return price
        if self.slippage_type == "fixed":
            return price + self.slippage if direction == "buy" else price - self.slippage
        slip_amount = price * self.slippage
        return price + slip_amount if direction == "buy" else max(price - slip_amount, 0.01)

    def _is_limit_up(self, symbol: str, bar: dict) -> bool:
        if not self.check_limit:
            return False
        prev_close = self._get_prev_close(symbol)
        if prev_close is None or prev_close <= 0:
            return False
        threshold = self._get_limit_threshold(symbol)
        return bar["close"] >= prev_close * (1 + threshold - 0.001)

    def _is_limit_down(self, symbol: str, bar: dict) -> bool:
        if not self.check_limit:
            return False
        prev_close = self._get_prev_close(symbol)
        if prev_close is None or prev_close <= 0:
            return False
        threshold = self._get_limit_threshold(symbol)
        return bar["close"] <= prev_close * (1 - threshold + 0.001)

    def _get_prev_close(self, symbol: str) -> float | None:
        last_bar = self.last_bars.get(symbol)
        if last_bar is not None:
            return last_bar.get("close")
        return None

    @staticmethod
    def _get_limit_threshold(symbol: str) -> float:
        code = symbol.split(".")[0] if "." in symbol else symbol
        if code.startswith("688"):
            return LIMIT_UP_THRESHOLD_NEW
        if code.startswith("300") or code.startswith("301"):
            return LIMIT_UP_THRESHOLD_NEW
        if code.startswith("00"):
            return LIMIT_UP_THRESHOLD_MAIN
        if code.startswith("60"):
            return LIMIT_UP_THRESHOLD_MAIN
        return LIMIT_UP_THRESHOLD_MAIN

    def _check_t_plus_1(self, symbol: str, current_date) -> bool:
        if not self.enforce_t_plus_1:
            return True
        entry_date = self.entry_dates.get(symbol)
        if entry_date is None:
            return True
        try:
            return (current_date - entry_date).days >= 1
        except Exception:
            return True

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
        excess_returns = [r - self.RISK_FREE_RATE_DAILY for r in returns]
        mean_excess = sum(excess_returns) / len(excess_returns)
        std_ret = math.sqrt(sum((r - mean_excess) ** 2 for r in excess_returns) / len(excess_returns)) if len(excess_returns) > 1 else 0.0
        if std_ret < 1e-10:
            return 0.0
        return (mean_excess / std_ret) * math.sqrt(self.TRADING_DAYS_PER_YEAR)

    def _calc_benchmark_return(self) -> float:
        if not self.benchmark_curve:
            return 0.0
        start = self.benchmark_curve[0]["value"]
        end = self.benchmark_curve[-1]["value"]
        return (end - start) / start * 100 if start else 0.0

    def _calc_beta(self) -> float:
        if len(self.equity_curve) < 10 or not self.benchmark_curve:
            return 0.0
        strategy_returns = []
        benchmark_returns = []
        for i in range(1, len(self.equity_curve)):
            s_prev = self.equity_curve[i - 1]["value"]
            s_curr = self.equity_curve[i]["value"]
            if s_prev > 0:
                strategy_returns.append((s_curr - s_prev) / s_prev)
            bm_date = self.equity_curve[i]["date"]
            bm_prev = next((b["value"] for b in self.benchmark_curve if b["date"] == self.equity_curve[i - 1]["date"]), None)
            bm_curr = next((b["value"] for b in self.benchmark_curve if b["date"] == bm_date), None)
            if bm_prev and bm_prev > 0 and bm_curr:
                benchmark_returns.append((bm_curr - bm_prev) / bm_prev)
        if len(strategy_returns) != len(benchmark_returns) or len(strategy_returns) < 2:
            return 0.0
        n = len(strategy_returns)
        mean_s = sum(strategy_returns) / n
        mean_b = sum(benchmark_returns) / n
        cov = sum((strategy_returns[i] - mean_s) * (benchmark_returns[i] - mean_b) for i in range(n)) / n
        var_b = sum((benchmark_returns[i] - mean_b) ** 2 for i in range(n)) / n
        if var_b < 1e-10:
            return 0.0
        return cov / var_b

    def _calc_information_ratio(self) -> float:
        if len(self.equity_curve) < 10 or not self.benchmark_curve:
            return 0.0
        strategy_returns = []
        benchmark_returns = []
        for i in range(1, len(self.equity_curve)):
            s_prev = self.equity_curve[i - 1]["value"]
            s_curr = self.equity_curve[i]["value"]
            if s_prev > 0:
                strategy_returns.append((s_curr - s_prev) / s_prev)
            bm_date = self.equity_curve[i]["date"]
            bm_prev = next((b["value"] for b in self.benchmark_curve if b["date"] == self.equity_curve[i - 1]["date"]), None)
            bm_curr = next((b["value"] for b in self.benchmark_curve if b["date"] == bm_date), None)
            if bm_prev and bm_prev > 0 and bm_curr:
                benchmark_returns.append((bm_curr - bm_prev) / bm_prev)
        if len(strategy_returns) != len(benchmark_returns) or len(strategy_returns) < 2:
            return 0.0
        n = len(strategy_returns)
        excess = [strategy_returns[i] - benchmark_returns[i] for i in range(n)]
        mean_excess = sum(excess) / n
        std_excess = math.sqrt(sum((e - mean_excess) ** 2 for e in excess) / n) if n > 1 else 0.0
        if std_excess < 1e-10:
            return 0.0
        return (mean_excess / std_excess) * math.sqrt(self.TRADING_DAYS_PER_YEAR)

    def _calc_annual_volatility(self) -> float:
        if len(self.equity_curve) < 10:
            return 0.0
        values = [e["value"] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                returns.append((values[i] - values[i - 1]) / values[i - 1])
        if not returns:
            return 0.0
        std_ret = math.sqrt(sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0.0
        return std_ret * math.sqrt(self.TRADING_DAYS_PER_YEAR) * 100

    def _calc_calmar_ratio(self) -> float:
        annual_return = self._calc_annual_return()
        _, max_dd_pct = self._calc_max_drawdown()
        if max_dd_pct <= 0:
            return 0.0
        return annual_return / max_dd_pct

    def _calc_sortino_ratio(self) -> float:
        if len(self.equity_curve) < 10:
            return 0.0
        values = [e["value"] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                returns.append((values[i] - values[i - 1]) / values[i - 1])
        if not returns:
            return 0.0
        excess_returns = [r - self.RISK_FREE_RATE_DAILY for r in returns]
        mean_excess = sum(excess_returns) / len(excess_returns)
        downside_returns = [r for r in returns if r < 0]
        downside_std = math.sqrt(sum(r ** 2 for r in downside_returns) / len(returns)) if downside_returns else 0.0
        if downside_std < 1e-10:
            return 0.0
        return (mean_excess / downside_std) * math.sqrt(self.TRADING_DAYS_PER_YEAR)

    def _calc_monthly_returns(self) -> dict:
        if not self.equity_curve:
            return {}
        monthly = {}
        for e in self.equity_curve:
            try:
                month_key = e["date"].strftime("%Y-%m")
            except Exception:
                continue
            if month_key not in monthly:
                monthly[month_key] = {"start": e["value"], "end": e["value"]}
            monthly[month_key]["end"] = e["value"]

        result = {}
        for month, data in monthly.items():
            if data["start"] > 0:
                result[month] = (data["end"] - data["start"]) / data["start"] * 100
        return result

    def _calc_quantile_stats(self) -> dict:
        if not self.equity_curve or len(self.equity_curve) < 2:
            return {}
        values = [e["value"] for e in self.equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                returns.append((values[i] - values[i - 1]) / values[i - 1] * 100)
        if not returns:
            return {}
        sorted_returns = sorted(returns)
        n = len(sorted_returns)
        return {
            "best_day": sorted_returns[-1] if n > 0 else 0.0,
            "worst_day": sorted_returns[0] if n > 0 else 0.0,
            "p25": sorted_returns[n // 4] if n > 3 else 0.0,
            "p50": sorted_returns[n // 2] if n > 1 else 0.0,
            "p75": sorted_returns[n * 3 // 4] if n > 3 else 0.0,
        }

    def _calc_trade_stats(self) -> dict:
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        win_trades = 0
        total_profit = 0.0
        total_loss = 0.0
        total_commission = 0.0
        total_stamp_tax = 0.0
        total_slippage_cost = 0.0
        for t in self.trades:
            cc = t.get("commission_cost", 0.0)
            total_commission += cc
            sc = t.get("slippage_cost", 0.0)
            total_slippage_cost += sc
            if t["action"] == "SELL":
                stamp = t["price"] * t["quantity"] * self.stamp_tax
                total_stamp_tax += stamp
        for t in sell_trades:
            entry_price = t.get("entry_price", 0.0)
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

        holding_days = []
        buy_date_map = {}
        for t in self.trades:
            sym = t["symbol"]
            if t["action"] == "BUY":
                buy_date_map[sym] = t["date"]
            elif t["action"] == "SELL" and sym in buy_date_map:
                bd = buy_date_map.pop(sym)
                try:
                    delta = (t["date"] - bd).days
                    holding_days.append(delta)
                except Exception:
                    pass
        avg_holding_days = sum(holding_days) / len(holding_days) if holding_days else 0.0

        return {
            "trades": len(self.trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_commission": total_commission,
            "total_stamp_tax": total_stamp_tax,
            "total_slippage_cost": total_slippage_cost,
            "avg_holding_days": avg_holding_days,
            "annual_volatility": self._calc_annual_volatility(),
            "calmar_ratio": self._calc_calmar_ratio(),
            "sortino_ratio": self._calc_sortino_ratio(),
            "monthly_returns": self._calc_monthly_returns(),
            "quantile_stats": self._calc_quantile_stats(),
        }
