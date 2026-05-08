"""事件驱动回测引擎 - 修复 look-ahead bias，支持止损和仓位管理。"""
from __future__ import annotations

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
        position_size: float = 1.0,
    ):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.stop_loss = stop_loss
        self.position_size = position_size  # 0.0 ~ 1.0, 仓位比例
        self.reset()

    def reset(self):
        """重置回测状态，便于同一实例重复运行。"""
        self.cash = self.initial_cash
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.last_prices = {}
        self.pending_signal = {}  # 待执行的信号

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

        # 先让策略处理第一根 bar，生成初始信号
        bars = list(df.itertuples(index=False))
        if not bars:
            return None

        # 预加载 bar 数据用于策略初始化
        for i, row in enumerate(bars[:20]):
            bar = self._row_to_bar(row, symbol)
            self.last_prices[symbol] = bar["close"]
            strategy.on_bar(bar)

        # 主循环: bar i 的信号在 bar i+1 的开盘价执行
        for i in range(len(bars) - 1):
            current_bar = self._row_to_bar(bars[i], symbol)
            next_bar = self._row_to_bar(bars[i + 1], symbol)

            self.last_prices[symbol] = current_bar["close"]

            # 检查止损
            self._check_stop_loss(symbol, next_bar["open"])

            # 执行上一根 bar 产生的信号
            if symbol in self.pending_signal:
                signal = self.pending_signal.pop(symbol)
                self._execute_signal(signal, next_bar, strategy)

            # 生成新信号（下一根 bar 执行）
            signal = strategy.on_bar(current_bar)
            if signal != Signal.HOLD:
                self.pending_signal[symbol] = signal

            self.equity_curve.append({"date": current_bar["date"], "value": self.get_total_value()})

        # 处理最后一根 bar
        last_bar = self._row_to_bar(bars[-1], symbol)
        self.last_prices[symbol] = last_bar["close"]
        if symbol in self.pending_signal:
            self._check_stop_loss(symbol, last_bar["open"])
            signal = self.pending_signal.pop(symbol)
            self._execute_signal(signal, last_bar, strategy)
        self.equity_curve.append({"date": last_bar["date"], "value": self.get_total_value()})

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

        entry_price = self._get_entry_price(symbol)
        if entry_price <= 0:
            return

        loss_ratio = (current_price - entry_price) / entry_price
        if loss_ratio <= -self.stop_loss:
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
        """返回标准化回测结果。"""
        final_value = self.get_total_value()
        profit = final_value - self.initial_cash
        profit_pct = profit / self.initial_cash * 100 if self.initial_cash else 0.0
        return {
            "symbol": symbol,
            "final_value": final_value,
            "profit": profit,
            "profit_pct": profit_pct,
            "trades": len(self.trades),
            "cash": self.cash,
            "positions": dict(self.positions),
            "last_prices": dict(self.last_prices),
        }

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
            strategy.set_position(symbol, self.positions[symbol])
            trade = {
                "date": bar["date"],
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "quantity": affordable_quantity,
            }
            self.trades.append(trade)
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
        """打印回测结果。"""
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
        print(f"收益率: {summary['profit_pct']:.2f}%")
        print(f"交易次数: {summary['trades']}")
        print(f"剩余现金: {summary['cash']:,.2f}")
        print(f"持仓: {position_snapshot}")
