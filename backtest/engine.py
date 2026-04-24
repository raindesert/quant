"""事件驱动回测引擎。"""
from __future__ import annotations

from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.base import Signal


class BacktestEngine:
    """基于日线 bar 的简化回测引擎。"""

    def __init__(self, initial_cash: float = 1_000_000, commission: float = 0.0003, verbose: bool = False):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.reset()

    def reset(self):
        """重置回测状态，便于同一实例重复运行。"""
        self.cash = self.initial_cash
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.last_prices = {}

    def run(self, strategy, symbol: str, days: int = 250) -> dict | None:
        """运行回测并返回汇总结果。"""
        self.reset()

        fetcher = DataFetcher()
        processor = DataProcessor()

        df = fetcher.get_history(symbol, days=days)
        if df.empty:
            print(f"无法获取 {symbol} 数据")
            return None

        df = processor.clean(df)
        print(f"回测开始: {symbol}, 数据量: {len(df)}")

        for row in df.itertuples(index=False):
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
            self.last_prices[symbol] = row.close

            signal = strategy.on_bar(bar)
            self._execute_signal(signal, bar, strategy)
            self.equity_curve.append({"date": row.date, "value": self.get_total_value()})

        if self.verbose:
            self._print_trades()

        summary = self.get_summary(symbol)
        self._print_summary(summary)
        return summary

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
        """执行交易信号。"""
        symbol = bar["symbol"]
        price = bar["close"]
        position = self.positions.get(symbol, 0)

        if signal == Signal.BUY:
            lot_size = 100
            affordable_quantity = int(self.cash / (price * (1 + self.commission)) / lot_size) * lot_size
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
