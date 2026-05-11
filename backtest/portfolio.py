"""组合回测引擎：多股票同时持仓，计算组合层面指标。"""
from __future__ import annotations

import math
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.base import Signal


class PortfolioBacktestEngine:
    """多股票组合回测引擎。

    特点：
    - 多股票同时运行，统一资金管理
    - 每日按仓位比例分配信号（等权配仓）
    - 组合层面权益曲线、指标计算
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        max_positions: int = 5,
    ):
        self.initial_cash = initial_cash
        self.commission = commission
        self.verbose = verbose
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.max_positions = max_positions  # 最大同时持仓股票数
        self.reset()

    def reset(self):
        self.cash = self.initial_cash
        self.positions = {}  # symbol -> quantity
        self.entry_prices = {}  # symbol -> entry price
        self.trades = []  # 全局交易记录
        self.equity_curve = []  # 组合权益曲线
        self.benchmark_curve = []  # 组合基准曲线（等权买入持有）
        self.last_prices = {}  # symbol -> last close
        self.pending_signals = {}  # symbol -> signal
        self._last_actions = {}  # symbol -> action for chart annotation

    def run(
        self,
        strategy_factory,
        symbols: list[str],
        days: int = 250,
    ) -> dict | None:
        """运行组合回测。

        Args:
            strategy_factory: 返回新策略实例的函数（每个symbol独立策略）
            symbols: 股票列表
            days: 回测天数
        """
        self.reset()

        fetcher = DataFetcher()
        processor = DataProcessor()

        # 加载所有股票数据
        symbol_data = {}
        for sym in symbols:
            df = fetcher.get_history(sym, days=days)
            if df.empty:
                print(f"无法获取 {sym} 数据，跳过")
                continue
            df = processor.clean(df)
            symbol_data[sym] = df

        if not symbol_data:
            print("所有股票均无数据")
            return None

        # 对齐所有股票的日期（取交集）
        all_dates_sets = [set(df["date"]) for df in symbol_data.values()]
        common_dates = set.intersection(*all_dates_sets) if all_dates_sets else set()
        for df in symbol_data.values():
            common_dates &= set(df["date"])

        if not common_dates:
            # 取并集
            all_dates: list = []
            for df in symbol_data.values():
                all_dates.extend(df["date"].tolist())
            common_dates = sorted(set(all_dates))

        common_dates = sorted(common_dates)
        print(f"组合回测: {len(symbols)} 只股票, {len(common_dates)} 个交易日")

        # 创建每个股票的独立策略实例
        strategies = {sym: strategy_factory() for sym in symbol_data}

        # 预处理：让策略预热（前20根bar）
        warmup_bars = min(20, len(common_dates) - 1)
        for sym, df in symbol_data.items():
            bars = list(df.itertuples(index=False))
            warmup_count = min(warmup_bars, len(bars) - 1)
            for row in bars[:warmup_count]:
                bar = self._row_to_bar(row, sym)
                self.last_prices[sym] = bar["close"]
                strategies[sym].on_bar(bar)

        # 初始化基准持仓（每个股票等权分配一半资金的1/max_positions）
        benchmark_shares = {sym: 0 for sym in symbol_data}
        benchmark_cash = self.initial_cash
        first_date = common_dates[0]
        per_stock_budget = self.initial_cash * 0.5 / len(symbol_data)

        # 主循环：按日期遍历
        for di, date in enumerate(common_dates[:-1]):
            # 获取当日所有股票的bar
            bars = {}
            for sym, df in symbol_data.items():
                row = df[df["date"] == date]
                if row.empty:
                    continue
                bar = self._row_to_bar(row.iloc[0], symbol=sym)
                bars[sym] = bar
                self.last_prices[sym] = bar["close"]

                # 初始化基准持仓（首日）
                if benchmark_shares[sym] == 0 and di == 0:
                    open_p = bar["open"]
                    aff = int(per_stock_budget / (open_p * (1 + self.commission)) / 100) * 100
                    if aff > 0:
                        cost = aff * open_p * (1 + self.commission)
                        benchmark_cash -= cost
                        benchmark_shares[sym] = aff

            # 计算基准组合价值
            bm_val = benchmark_cash + sum(
                benchmark_shares.get(sym, 0) * bars.get(sym, {}).get("close", 0)
                for sym in symbol_data
            )
            self.benchmark_curve.append({"date": date, "value": bm_val})

            # 检查各持仓股票的止损/止盈
            for sym in list(self.positions.keys()):
                if sym in bars:
                    next_bar = bars[sym]
                    self._check_stop_loss(sym, next_bar["open"])
                    self._check_take_profit(sym, next_bar["open"])

            # 执行上一日信号
            for sym in list(self.pending_signals.keys()):
                if sym in bars:
                    signal = self.pending_signals.pop(sym)
                    self._execute_signal(signal, bars[sym], strategies[sym])

            # 收集各股票信号
            actions = {}
            for sym, bar in bars.items():
                sig = strategies[sym].on_bar(bar)
                if sig != Signal.HOLD:
                    self.pending_signals[sym] = sig
                    actions[sym] = sig

            # 记录组合权益
            combined_val = self.cash + sum(
                self.positions.get(sym, 0) * self.last_prices.get(sym, 0)
                for sym in self.positions
            )
            action_str = " | ".join(
                f"{sym}:{a}" for sym, a in actions.items()
            ) or None
            self.equity_curve.append({
                "date": date,
                "value": combined_val,
                "action": action_str,
            })
            self._last_actions = actions

        # 处理最后一天
        last_date = common_dates[-1]
        last_bars = {}
        for sym, df in symbol_data.items():
            row = df[df["date"] == last_date]
            if row.empty:
                continue
            bar = self._row_to_bar(row.iloc[0], sym)
            last_bars[sym] = bar
            self.last_prices[sym] = bar["close"]

        bm_val = benchmark_cash + sum(
            benchmark_shares.get(sym, 0) * last_bars.get(sym, {}).get("close", 0)
            for sym in symbol_data
        )
        self.benchmark_curve.append({"date": last_date, "value": bm_val})

        # 执行最后信号
        for sym in list(self.pending_signals.keys()):
            if sym in last_bars:
                sig = self.pending_signals.pop(sym)
                self._execute_signal(sig, last_bars[sym], strategies[sym])

        combined_val = self.cash + sum(
            self.positions.get(sym, 0) * self.last_prices.get(sym, 0)
            for sym in self.positions
        )
        self.equity_curve.append({
            "date": last_date,
            "value": combined_val,
            "action": None,
        })

        summary = self.get_summary(symbols)
        self._print_summary(summary)
        return summary

    def _row_to_bar(self, row, symbol: str) -> dict:
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
        if self.stop_loss <= 0 or symbol not in self.positions:
            return
        entry = self.entry_prices.get(symbol, 0.0)
        if entry <= 0:
            return
        if (current_price - entry) / entry <= -self.stop_loss:
            self.pending_signals[symbol] = Signal.SELL

    def _check_take_profit(self, symbol: str, current_price: float):
        if self.take_profit <= 0 or symbol not in self.positions:
            return
        entry = self.entry_prices.get(symbol, 0.0)
        if entry <= 0:
            return
        if (current_price - entry) / entry >= self.take_profit:
            self.pending_signals[symbol] = Signal.SELL

    def get_total_value(self) -> float:
        return self.cash + sum(
            qty * self.last_prices.get(sym, 0.0)
            for sym, qty in self.positions.items()
        )

    def get_summary(self, symbols: list[str]) -> dict:
        final_value = self.get_total_value()
        profit = final_value - self.initial_cash
        profit_pct = profit / self.initial_cash * 100 if self.initial_cash else 0.0

        max_dd, max_dd_pct = self._calc_max_drawdown()
        annual_return = self._calc_annual_return(profit_pct)
        sharpe = self._calc_sharpe_ratio()

        # 基准组合收益
        bm_ret = 0.0
        if self.benchmark_curve and len(self.benchmark_curve) >= 2:
            bm_start = self.benchmark_curve[0]["value"]
            bm_end = self.benchmark_curve[-1]["value"]
            bm_ret = (bm_end - bm_start) / bm_start * 100 if bm_start else 0.0

        alpha = profit_pct - bm_ret

        # 交易统计
        buy_trades = [t for t in self.trades if t["action"] == "BUY"]
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        wins, total_profit, total_loss = 0, 0.0, 0.0
        for i in range(min(len(sell_trades), len(buy_trades))):
            pnl = (sell_trades[i]["price"] - buy_trades[i]["price"]) * sell_trades[i]["quantity"]
            if pnl > 0:
                wins += 1
                total_profit += pnl
            else:
                total_loss += abs(pnl)

        win_rate = wins / len(sell_trades) * 100 if sell_trades else 0.0
        avg_win = total_profit / wins if wins else 0.0
        avg_loss = total_loss / (len(sell_trades) - wins) if sell_trades and len(sell_trades) > wins else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf") if total_profit > 0 else 0.0

        # 各股票独立结果
        symbol_results = []
        for sym in symbols:
            sym_trades = [t for t in self.trades if t["symbol"] == sym]
            sym_buy = [t for t in sym_trades if t["action"] == "BUY"]
            sym_sell = [t for t in sym_trades if t["action"] == "SELL"]
            sym_wins = 0
            for i in range(min(len(sym_sell), len(sym_buy))):
                if (sym_sell[i]["price"] - sym_buy[i]["price"]) * sym_sell[i]["quantity"] > 0:
                    sym_wins += 1
            symbol_results.append({
                "symbol": sym,
                "trades": len(sym_trades),
                "win_rate": sym_wins / len(sym_sell) * 100 if sym_sell else 0.0,
                "position": self.positions.get(sym, 0),
                "last_price": self.last_prices.get(sym, 0.0),
            })

        return {
            "symbols": symbols,
            "final_value": final_value,
            "profit": profit,
            "profit_pct": profit_pct,
            "annual_return": annual_return,
            "benchmark_return": bm_ret,
            "alpha": alpha,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "trades": len(self.trades),
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
            "symbol_results": symbol_results,
        }

    def _calc_max_drawdown(self):
        if not self.equity_curve:
            return 0.0, 0.0
        values = [e["value"] for e in self.equity_curve]
        peak = values[0]
        max_dd, max_dd_pct = 0.0, 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = peak - v
            dd_pct = dd / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd, max_dd_pct = dd, dd_pct
        return max_dd, max_dd_pct

    def _calc_annual_return(self, profit_pct: float) -> float:
        if not self.equity_curve or len(self.equity_curve) < 2:
            return 0.0
        days = len(self.equity_curve)
        years = days / 244
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
        return (mean_ret / std_ret) * math.sqrt(244)

    def _execute_signal(self, signal: str, bar: dict, strategy):
        symbol = bar["symbol"]
        price = bar["open"]
        position = self.positions.get(symbol, 0)
        lot_size = 100

        if signal == Signal.BUY:
            # 仓位分配：等权分配到各持仓股票
            current_holdings = len(self.positions)
            if current_holdings >= self.max_positions and symbol not in self.positions:
                return  # 超过最大持仓数，且不是加仓，跳过

            # 分配可用资金的 1/(max_positions) 或全部（如果已持仓）
            if symbol in self.positions:
                budget = self.cash * 0.5
            else:
                budget = self.cash * 0.5 / (current_holdings + 1) if current_holdings < self.max_positions else 0
            affordable = int(budget / (price * (1 + self.commission)) / lot_size) * lot_size
            if affordable <= 0:
                return

            cost = affordable * price * (1 + self.commission)
            self.cash -= cost
            self.positions[symbol] = position + affordable
            self.entry_prices[symbol] = price
            strategy.set_position(symbol, self.positions[symbol])
            self.trades.append({
                "date": bar["date"],
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "quantity": affordable,
            })
            if self.verbose:
                print(f"  [{bar['date']}] BUY {symbol}: {affordable} @ {price:.2f}")
            return

        if signal == Signal.SELL and position > 0:
            proceeds = position * price * (1 - self.commission)
            self.cash += proceeds
            self.positions.pop(symbol, None)
            self.entry_prices.pop(symbol, None)
            strategy.set_position(symbol, 0)
            self.trades.append({
                "date": bar["date"],
                "symbol": symbol,
                "action": "SELL",
                "price": price,
                "quantity": position,
            })
            if self.verbose:
                print(f"  [{bar['date']}] SELL {symbol}: {position} @ {price:.2f}")

    def _print_summary(self, summary: dict):
        print("\n===== 组合回测结果 =====")
        print(f"初始资金: {self.initial_cash:,.2f}")
        print(f"最终价值: {summary['final_value']:,.2f}")
        print(f"收益率:   {summary['profit_pct']:+.2f}%")
        print(f"年化收益: {summary.get('annual_return', 0):+.2f}%")
        print(f"基准收益: {summary.get('benchmark_return', 0):+.2f}%  (等权买入持有)")
        print(f"Alpha:   {summary.get('alpha', 0):+.2f}%  (相对组合基准)")
        print(f"最大回撤: {summary.get('max_drawdown', 0):,.2f}  ({summary.get('max_drawdown_pct', 0):.2f}%)")
        print(f"夏普比率: {summary.get('sharpe_ratio', 0):.2f}")
        print(f"总交易:   {summary.get('trades', 0)} 次")
        print(f"胜率:     {summary.get('win_rate', 0):.1f}%")
        print(f"盈亏比:   {summary.get('avg_win', 0):.2f} / {summary.get('avg_loss', 0):.2f}")
        print(f"盈利因子: {summary.get('profit_factor', 0):.2f}")
        print(f"剩余现金: {summary.get('cash', 0):,.2f}")
        print(f"持仓: { {s: qty for s, qty in summary.get('positions', {}).items()} }")

        if summary.get("symbol_results"):
            print("\n--- 各股票明细 ---")
            for sr in summary["symbol_results"]:
                print(f"  {sr['symbol']}: 持仓 {sr['position']} 股, 最后价 {sr['last_price']:.2f}, "
                      f"交易 {sr['trades']} 次, 胜率 {sr['win_rate']:.0f}%")
