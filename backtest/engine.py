"""事件驱动回测引擎 - 支持止损/止盈、仓位管理、A股真实佣金。"""
from __future__ import annotations

import pandas as pd

from backtest.base import BaseBacktestEngine
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.base import Signal


class BacktestEngine(BaseBacktestEngine):
    """基于日线 bar 的回测引擎。

    特点:
    - look-ahead bias 修复: 信号在 bar N 收盘后产生，以 bar N+1 开盘价执行
    - A股真实佣金: 佣金(万三)+印花税(千一，卖出)+最低佣金(5元)
    - 止损/止盈: 支持固定比例
    - 仓位管理: 支持仓位比例
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        position_size: float = 1.0,
        slippage: float = 0.0,
        slippage_type: str = "percent",
        enforce_t_plus_1: bool = True,
        check_limit: bool = True,
    ):
        super().__init__(
            initial_cash=initial_cash,
            commission=commission,
            verbose=verbose,
            stop_loss=stop_loss,
            take_profit=take_profit,
            slippage=slippage,
            slippage_type=slippage_type,
            enforce_t_plus_1=enforce_t_plus_1,
            check_limit=check_limit,
        )
        self.position_size = position_size

    def run(
        self,
        strategy,
        symbol: str,
        days: int = 250,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict | None:
        self.reset()

        fetcher = DataFetcher()
        processor = DataProcessor()

        df = fetcher.get_history(symbol, days=days)
        if df.empty:
            print(f"无法获取 {symbol} 数据")
            return None

        df = processor.clean(df)

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
            self.last_bars[symbol] = bar
            strategy.on_bar(bar)

        for i in range(warmup_count, len(bars) - 1):
            current_bar = self._row_to_bar(bars[i], symbol)
            next_bar = self._row_to_bar(bars[i + 1], symbol)

            self.last_prices[symbol] = current_bar["close"]
            self.last_bars[symbol] = current_bar

            if benchmark_shares == 0:
                open_price = current_bar["open"]
                cost_per_100 = self._calc_buy_cost(open_price, 100)
                affordable = int(self.initial_cash * 0.5 / cost_per_100) * 100
                if affordable > 0:
                    cost = self._calc_buy_cost(open_price, affordable)
                    benchmark_cash -= cost
                    benchmark_shares = affordable
            benchmark_value = benchmark_cash + benchmark_shares * current_bar["close"]
            self.benchmark_curve.append({"date": current_bar["date"], "value": benchmark_value})

            self._check_stop_loss(symbol, next_bar["open"], self.pending_signal)
            self._check_take_profit(symbol, next_bar["open"], self.pending_signal)

            if symbol in self.pending_signal:
                signal = self.pending_signal.pop(symbol)
                self._execute_signal(signal, next_bar, strategy)

            signal = strategy.on_bar(current_bar)
            if signal != Signal.HOLD:
                self.pending_signal[symbol] = signal

            self.equity_curve.append({"date": current_bar["date"], "value": self.get_total_value(), "action": self._last_action})
            self._last_action = None

        last_bar = self._row_to_bar(bars[-1], symbol)
        self.last_prices[symbol] = last_bar["close"]
        benchmark_value = benchmark_cash + benchmark_shares * last_bar["close"]
        self.benchmark_curve.append({"date": last_bar["date"], "value": benchmark_value})

        if symbol in self.pending_signal:
            self._check_stop_loss(symbol, last_bar["open"], self.pending_signal)
            self._check_take_profit(symbol, last_bar["open"], self.pending_signal)
            signal = self.pending_signal.pop(symbol)
            self._execute_signal(signal, last_bar, strategy)

        strategy.on_bar(last_bar)

        self.equity_curve.append({"date": last_bar["date"], "value": self.get_total_value(), "action": self._last_action})

        if self.verbose:
            self._print_trades()

        summary = self.get_summary(symbol)
        self._print_summary(summary)
        return summary

    def _execute_signal(self, signal: str, bar: dict, strategy):
        symbol = bar["symbol"]
        price = bar["open"]
        position = self.positions.get(symbol, 0)

        if signal == Signal.BUY:
            if self._is_limit_up(symbol, bar):
                return

            actual_price = self._apply_slippage(price, "buy")
            lot_size = 100
            available_cash = self.cash * self.position_size
            affordable_quantity = int(available_cash / (actual_price * (1 + self.commission)) / lot_size) * lot_size
            if affordable_quantity <= 0:
                return

            cost = self._calc_buy_cost(actual_price, affordable_quantity)
            if cost > self.cash:
                return
            self.cash -= cost
            old_qty = self.positions.get(symbol, 0)
            new_qty = old_qty + affordable_quantity
            self.positions[symbol] = new_qty
            if old_qty > 0:
                old_entry = self.entry_prices.get(symbol, actual_price)
                self.entry_prices[symbol] = (old_entry * old_qty + actual_price * affordable_quantity) / new_qty
            else:
                self.entry_prices[symbol] = actual_price
                self.entry_dates[symbol] = bar["date"]
            strategy.set_position(symbol, new_qty)
            commission_cost = max(actual_price * affordable_quantity * self.commission, self.min_commission)
            slippage_cost = (actual_price - price) * affordable_quantity
            trade = {
                "date": bar["date"],
                "symbol": symbol,
                "action": "BUY",
                "price": actual_price,
                "quantity": affordable_quantity,
                "entry_price": self.entry_prices[symbol],
                "commission_cost": commission_cost,
                "slippage_cost": slippage_cost,
            }
            self.trades.append(trade)
            self._last_action = "buy"
            if self.verbose:
                self._print_trade(trade)
            return

        if signal == Signal.SELL and position > 0:
            if not self._check_t_plus_1(symbol, bar["date"]):
                return

            if self._is_limit_down(symbol, bar):
                return

            actual_price = self._apply_slippage(price, "sell")
            proceeds = self._calc_sell_proceeds(actual_price, position)
            commission_cost = max(actual_price * position * self.commission, self.min_commission)
            stamp_cost = actual_price * position * self.stamp_tax
            entry_price = self.entry_prices.get(symbol, actual_price)
            self.cash += proceeds
            self.positions.pop(symbol, None)
            self.entry_prices.pop(symbol, None)
            self.entry_dates.pop(symbol, None)
            strategy.set_position(symbol, 0)
            slippage_cost = (price - actual_price) * position
            trade = {
                "date": bar["date"],
                "symbol": symbol,
                "action": "SELL",
                "price": actual_price,
                "quantity": position,
                "entry_price": entry_price,
                "commission_cost": commission_cost + stamp_cost,
                "slippage_cost": slippage_cost,
            }
            self.trades.append(trade)
            self._last_action = "sell"
            if self.verbose:
                self._print_trade(trade)

    def _print_trade(self, trade: dict):
        date_str = trade["date"].strftime("%Y-%m-%d") if hasattr(trade["date"], "strftime") else str(trade["date"])
        action = "买入" if trade["action"] == "BUY" else "卖出"
        print(f"  [{date_str}] {action}: {trade['quantity']}股 @ {trade['price']:.2f}")

    def _print_trades(self):
        print("\n===== 交易明细 =====")
        if not self.trades:
            print("  无交易")
            return
        for index, trade in enumerate(self.trades, 1):
            self._print_trade(trade)

    def get_summary(self, symbol: str) -> dict:
        final_value = self.get_total_value()
        profit = final_value - self.initial_cash
        profit_pct = profit / self.initial_cash * 100 if self.initial_cash else 0.0

        max_drawdown, max_drawdown_pct = self._calc_max_drawdown()
        annual_return = self._calc_annual_return()
        sharpe = self._calc_sharpe_ratio()
        benchmark_return = self._calc_benchmark_return()
        alpha = profit_pct - benchmark_return
        stats = self._calc_trade_stats()

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
            "cash": self.cash,
            "positions": dict(self.positions),
            "last_prices": dict(self.last_prices),
            "equity_curve": self.equity_curve,
            "benchmark_curve": self.benchmark_curve,
            "trades_list": list(self.trades),
            **stats,
        }

    def _print_summary(self, summary: dict):
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
        print(f"总佣金:   {summary.get('total_commission', 0):,.2f}")
        print(f"总印花税: {summary.get('total_stamp_tax', 0):,.2f}")
        print(f"总滑点:   {summary.get('total_slippage_cost', 0):,.2f}")
        print(f"平均持仓: {summary.get('avg_holding_days', 0):.1f} 天")
        print(f"剩余现金: {summary.get('cash', 0):,.2f}")
        print(f"持仓: {position_snapshot}")
