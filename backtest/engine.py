"""事件驱动回测引擎 - 修复 look-ahead bias，支持止损/止盈和仓位管理，增强指标。"""
from __future__ import annotations

import math
import pandas as pd

from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.base import Signal


class BacktestEngine:
    """基于日线 bar 的简化回测引擎。

    修复的问题:
    - look-ahead bias: 信号在 bar N 收盘后产生，以 bar N+1 开盘价执行
    - 日期范围过滤: 支持 start_date / end_date 配置
    - 止损: 支持固定比例止损
    - 仓位管理: 支持多种仓位计算方式
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        position_size: float = 1.0,
    ):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.stop_loss = stop_loss
        self.take_profit = take_profit  # 止盈比例 (0.10 = 10%)，0 表示禁用
        self.position_size = position_size  # 0.0 ~ 1.0, 仓位比例
        self.reset()

    def reset(self):
        """重置回测状态，便于同一实例重复运行。"""
        self.cash = self.initial_cash
        self.positions = {}
        self.entry_prices = {}  # 各标的入场价格
        self.trades = []
        self.equity_curve = []
        self.benchmark_curve = []  # 基准（买入持有）曲线
        self.last_prices = {}
        self.pending_signal = {}  # 待执行的信号
        self._last_action = None  # 最近一次交易动作（用于图标标注）

    def run(
        self,
        strategy,
        symbol: str,
        days: int = 250,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict | None:
        """运行回测并返回汇总结果。

        Args:
            strategy: 交易策略实例
            symbol: 股票代码
            days: 回测天数
            start_date: 开始日期 (YYYYMMDD)，覆盖 days 参数
            end_date: 结束日期 (YYYYMMDD)
        """
        self.reset()

        fetcher = DataFetcher()
        processor = DataProcessor()

        df = fetcher.get_history(symbol, days=days)
        if df.empty:
            print(f"无法获取 {symbol} 数据")
            return None

        df = processor.clean(df)

        # 日期范围过滤
        if start_date:
            start_dt = pd.to_datetime(start_date)
            df = df[df["date"] >= start_dt]
        if end_date:
            end_dt = pd.to_datetime(end_date)
            df = df[df["date"] <= end_dt]

        if df.empty:
            print(f"指定日期范围内无数据: {start_date} ~ {end_date}")
            return None

        print(f"回测开始: {symbol}, 数据量: {len(df)}")

        bars = list(df.itertuples(index=False))
        if not bars:
            return None

        warmup_count = min(20, len(bars) - 1)

        first_bar = self._row_to_bar(bars[0], symbol)
        self.last_prices[symbol] = first_bar["close"]
        benchmark_shares = 0
        benchmark_cash = self.initial_cash

        for i, row in enumerate(bars[:warmup_count]):
            bar = self._row_to_bar(row, symbol)
            self.last_prices[symbol] = bar["close"]
            strategy.on_bar(bar)

        for i in range(warmup_count, len(bars) - 1):
            current_bar = self._row_to_bar(bars[i], symbol)
            next_bar = self._row_to_bar(bars[i + 1], symbol)

            self.last_prices[symbol] = current_bar["close"]

            if benchmark_shares == 0:
                open_price = current_bar["open"]
                affordable = int(self.initial_cash * 0.5 / (open_price * (1 + self.commission)) / 100) * 100
                if affordable > 0:
                    cost = affordable * open_price * (1 + self.commission)
                    benchmark_cash -= cost
                    benchmark_shares = affordable
            benchmark_value = benchmark_cash + benchmark_shares * current_bar["close"]
            self.benchmark_curve.append({"date": current_bar["date"], "value": benchmark_value})

            self._check_stop_loss(symbol, next_bar["open"])
            self._check_take_profit(symbol, next_bar["open"])

            if symbol in self.pending_signal:
                signal = self.pending_signal.pop(symbol)
                self._execute_signal(signal, next_bar, strategy)

            signal = strategy.on_bar(current_bar)
            if signal != Signal.HOLD:
                self.pending_signal[symbol] = signal

            self.equity_curve.append({"date": current_bar["date"], "value": self.get_total_value(), "action": self._last_action})
            self._last_action = None

        # 处理最后一根 bar
        last_bar = self._row_to_bar(bars[-1], symbol)
        self.last_prices[symbol] = last_bar["close"]
        benchmark_value = benchmark_cash + benchmark_shares * last_bar["close"]
        self.benchmark_curve.append({"date": last_bar["date"], "value": benchmark_value})
        if symbol in self.pending_signal:
            self._check_stop_loss(symbol, last_bar["open"])
            self._check_take_profit(symbol, last_bar["open"])
            signal = self.pending_signal.pop(symbol)
            self._execute_signal(signal, last_bar, strategy)
        self.equity_curve.append({"date": last_bar["date"], "value": self.get_total_value(), "action": self._last_action})

        if self.verbose:
            self._print_trades()

        summary = self.get_summary(symbol)
        self._print_summary(summary)
        return summary

    def _row_to_bar(self, row, symbol: str) -> dict:
        """将 DataFrame row 转换为 bar dict。"""
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

    def _check_stop_loss(self, symbol: str, current_price: float):
        """检查是否触发止损。"""
        if self.stop_loss <= 0 or symbol not in self.positions:
            return

        position = self.positions[symbol]
        if position <= 0:
            return

        entry_price = self.entry_prices.get(symbol, 0.0)
        if entry_price <= 0:
            entry_price = self._get_entry_price(symbol)
        if entry_price <= 0:
            return

        loss_ratio = (current_price - entry_price) / entry_price
        if loss_ratio <= -self.stop_loss:
            self.pending_signal[symbol] = Signal.SELL

    def _check_take_profit(self, symbol: str, current_price: float):
        """检查是否触发止盈。"""
        if self.take_profit <= 0 or symbol not in self.positions:
            return

        position = self.positions[symbol]
        if position <= 0:
            return

        entry_price = self.entry_prices.get(symbol, 0.0)
        if entry_price <= 0:
            entry_price = self._get_entry_price(symbol)
        if entry_price <= 0:
            return

        gain_ratio = (current_price - entry_price) / entry_price
        if gain_ratio >= self.take_profit:
            self.pending_signal[symbol] = Signal.SELL

    def _get_entry_price(self, symbol: str) -> float:
        """获取持仓的平均入场价格。"""
        for trade in reversed(self.trades):
            if trade["symbol"] == symbol and trade["action"] == "BUY":
                return trade["price"]
        return 0.0

    def get_total_value(self) -> float:
        """按最新价格计算账户总资产。"""
        positions_value = sum(
            quantity * self.last_prices.get(symbol, 0.0)
            for symbol, quantity in self.positions.items()
        )
        return self.cash + positions_value

    def get_summary(self, symbol: str) -> dict:
        """返回标准化回测结果（含增强指标）。"""
        final_value = self.get_total_value()
        profit = final_value - self.initial_cash
        profit_pct = profit / self.initial_cash * 100 if self.initial_cash else 0.0

        # 计算最大回撤
        max_drawdown, max_drawdown_pct = self._calc_max_drawdown()

        # 计算年化收益率
        annual_return = self._calc_annual_return(profit_pct)

        # 计算夏普比率
        sharpe = self._calc_sharpe_ratio()

        # 计算基准收益
        benchmark_return = 0.0
        if self.benchmark_curve:
            benchmark_start = self.benchmark_curve[0]["value"]
            benchmark_end = self.benchmark_curve[-1]["value"]
            benchmark_return = (benchmark_end - benchmark_start) / benchmark_start * 100 if benchmark_start else 0.0

        # 策略相对基准收益
        alpha = profit_pct - benchmark_return

        # 交易统计
        trades = len(self.trades)
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        win_trades = 0
        total_profit = 0.0
        total_loss = 0.0
        for t in sell_trades:
            entry_price = self.entry_prices.get(t["symbol"], 0.0)
            if entry_price <= 0:
                for bt in reversed(self.trades):
                    if bt["symbol"] == t["symbol"] and bt["action"] == "BUY" and bt["date"] <= t["date"]:
                        entry_price = bt["price"]
                        break
            pnl = (t["price"] - entry_price) * t["quantity"]
            if pnl > 0:
                win_trades += 1
                total_profit += pnl
            else:
                total_loss += abs(pnl)
        win_rate = win_trades / len(sell_trades) * 100 if sell_trades else 0.0
        avg_win = total_profit / win_trades if win_trades else 0.0
        avg_loss = total_loss / (len(sell_trades) - win_trades) if sell_trades and len(sell_trades) > win_trades else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf") if total_profit > 0 else 0.0

        return {
            "symbol": symbol,
            "final_value": final_value,
            "profit": profit,
            "profit_pct": profit_pct,
            "annual_return": annual_return,
            "benchmark_return": benchmark_return,
            "alpha": alpha,
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": sharpe,
            "trades": trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "cash": self.cash,
            "positions": dict(self.positions),
            "last_prices": dict(self.last_prices),
            "equity_curve": self.equity_curve,
            "benchmark_curve": self.benchmark_curve,
            "trades_list": list(self.trades),
        }

    def _calc_max_drawdown(self):
        """计算最大回撤。"""
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

    def _calc_annual_return(self, profit_pct: float) -> float:
        """基于回测天数估算年化收益率。"""
        if not self.equity_curve or len(self.equity_curve) < 2:
            return 0.0
        days = len(self.equity_curve)
        years = days / 244  # A股每年约244个交易日
        if years < 0.01:
            return 0.0
        # 几何年化
        total_return = self.equity_curve[-1]["value"] / self.equity_curve[0]["value"]
        annual = (total_return ** (1 / years) - 1) * 100 if total_return > 0 else 0.0
        return annual

    def _calc_sharpe_ratio(self) -> float:
        """计算夏普比率（简单版：无风险利率=0）。"""
        if len(self.equity_curve) < 10:
            return 0.0
        values = [e["value"] for e in self.equity_curve]
        # 日收益率
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
        # 年化夏普（244交易日）
        sharpe = (mean_ret / std_ret) * math.sqrt(244)
        return sharpe

    def _execute_signal(self, signal: str, bar: dict, strategy):
        """执行交易信号（以 bar 的 open 价格成交，消除 look-ahead bias）。"""
        symbol = bar["symbol"]
        # 修复: 使用 open 价格执行，避免 look-ahead bias
        price = bar["open"]
        position = self.positions.get(symbol, 0)

        if signal == Signal.BUY:
            lot_size = 100
            # 仓位管理: 按 position_size 比例使用可用资金
            available_cash = self.cash * self.position_size
            affordable_quantity = int(available_cash / (price * (1 + self.commission)) / lot_size) * lot_size
            if affordable_quantity <= 0:
                return

            cost = affordable_quantity * price * (1 + self.commission)
            self.cash -= cost
            self.positions[symbol] = position + affordable_quantity
            self.entry_prices[symbol] = price  # 记录入场价
            strategy.set_position(symbol, self.positions[symbol])
            trade = {
                "date": bar["date"],
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "quantity": affordable_quantity,
            }
            self.trades.append(trade)
            self._last_action = "buy"
            if self.verbose:
                self._print_trade(trade)
            return

        if signal == Signal.SELL and position > 0:
            proceeds = position * price * (1 - self.commission)
            self.cash += proceeds
            self.positions.pop(symbol, None)
            strategy.set_position(symbol, 0)
            trade = {
                "date": bar["date"],
                "symbol": symbol,
                "action": "SELL",
                "price": price,
                "quantity": position,
            }
            self.trades.append(trade)
            self._last_action = "sell"
            if self.verbose:
                self._print_trade(trade)

    def _print_trade(self, trade: dict):
        """打印单笔交易。"""
        date_str = trade["date"].strftime("%Y-%m-%d") if hasattr(trade["date"], "strftime") else str(trade["date"])
        action = "买入" if trade["action"] == "BUY" else "卖出"
        print(f"  [{date_str}] {action}: {trade['quantity']}股 @ {trade['price']:.2f}")

    def _print_trades(self):
        """打印所有交易。"""
        print("\n===== 交易明细 =====")
        if not self.trades:
            print("  无交易")
            return

        for index, trade in enumerate(self.trades, 1):
            date_str = trade["date"].strftime("%Y-%m-%d") if hasattr(trade["date"], "strftime") else str(trade["date"])
            action = "买入" if trade["action"] == "BUY" else "卖出"
            print(f"  {index}. [{date_str}] {action}: {trade['quantity']}股 @ {trade['price']:.2f}")

    def _print_summary(self, summary: dict):
        """打印回测结果（含增强指标）。"""
        position_snapshot = {
            symbol: {
                "quantity": quantity,
                "last_price": self.last_prices.get(symbol, 0.0),
            }
            for symbol, quantity in self.positions.items()
        }

        print("\n===== 回测结果 =====")
        print(f"初始资金: {self.initial_cash:,.2f}")
        print(f"最终价值: {summary['final_value']:,.2f}")
        print(f"收益率:   {summary['profit_pct']:+.2f}%")
        print(f"年化收益: {summary.get('annual_return', 0):+.2f}%")
        print(f"基准收益: {summary.get('benchmark_return', 0):+.2f}%  (买入持有)")
        print(f"Alpha:   {summary.get('alpha', 0):+.2f}%  (相对基准)")
        print(f"最大回撤: {summary.get('max_drawdown', 0):,.2f}  ({summary.get('max_drawdown_pct', 0):.2f}%)")
        print(f"夏普比率: {summary.get('sharpe_ratio', 0):.2f}")
        print(f"交易次数: {summary.get('trades', 0)}")
        print(f"胜率:     {summary.get('win_rate', 0):.1f}%")
        print(f"盈亏比:   {summary.get('avg_win', 0):.2f} / {summary.get('avg_loss', 0):.2f}")
        print(f"盈利因子: {summary.get('profit_factor', 0):.2f}")
        print(f"剩余现金: {summary.get('cash', 0):,.2f}")
        print(f"持仓: {position_snapshot}")
