"""组合回测引擎：多股票同时持仓，计算组合层面指标。"""
from __future__ import annotations

import logging

from backtest.base import BaseBacktestEngine
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from risk.manager import RiskManager
from strategy.base import Signal

logger = logging.getLogger("quant")


class PortfolioBacktestEngine(BaseBacktestEngine):
    """多股票组合回测引擎。

    特点：
    - 多股票同时运行，统一资金管理
    - 每日按仓位比例分配信号（等权配仓）
    - 组合层面权益曲线、指标计算
    - A股真实佣金: 佣金+印花税(卖出)+最低佣金
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        verbose: bool = False,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        max_positions: int = 5,
        slippage: float = 0.0,
        slippage_type: str = "percent",
        enforce_t_plus_1: bool = True,
        check_limit: bool = True,
        risk_manager: RiskManager | None = None,
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
            risk_manager=risk_manager,
        )
        self.max_positions = max_positions

    def reset(self):
        super().reset()
        self._last_actions = {}

    def run(
        self,
        strategy_factory,
        symbols: list[str],
        days: int = 250,
    ) -> dict | None:
        self.reset()

        fetcher = DataFetcher()
        processor = DataProcessor()

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

        all_dates_sets = [set(df["date"]) for df in symbol_data.values()]
        common_dates = sorted(set.intersection(*all_dates_sets)) if all_dates_sets else []

        if not common_dates:
            all_dates: list = []
            for df in symbol_data.values():
                all_dates.extend(df["date"].tolist())
            common_dates = sorted(set(all_dates))

        print(f"组合回测: {len(symbols)} 只股票, {len(common_dates)} 个交易日")

        strategies = {sym: strategy_factory() for sym in symbol_data}

        symbol_date_index = {}
        for sym, df in symbol_data.items():
            symbol_date_index[sym] = {row.date: row for row in df.itertuples(index=False)}

        warmup_count = min(20, len(common_dates) - 1)
        warmup_dates = common_dates[:warmup_count]
        for sym in symbol_data:
            for date in warmup_dates:
                row = symbol_date_index[sym].get(date)
                if row is None:
                    continue
                bar = self._row_to_bar(row, sym)
                self.last_prices[sym] = bar["close"]
                self.last_bars[sym] = bar
                strategies[sym].on_bar(bar)

        benchmark_shares = {sym: 0 for sym in symbol_data}
        benchmark_cash = self.initial_cash
        per_stock_budget = self.initial_cash * 0.5 / len(symbol_data)

        trading_dates = common_dates[warmup_count:]
        for di, date in enumerate(trading_dates[:-1]):
            bars = {}
            for sym in symbol_data:
                row = symbol_date_index[sym].get(date)
                if row is None:
                    continue
                bar = self._row_to_bar(row, symbol=sym)
                bars[sym] = bar
                self.last_prices[sym] = bar["close"]
                self.last_bars[sym] = bar

                if benchmark_shares[sym] == 0 and di == 0:
                    open_p = bar["open"]
                    cost_per_100 = self._calc_buy_cost(open_p, 100)
                    aff = int(per_stock_budget / cost_per_100) * 100
                    if aff > 0:
                        cost = self._calc_buy_cost(open_p, aff)
                        benchmark_cash -= cost
                        benchmark_shares[sym] = aff

            bm_val = benchmark_cash + sum(
                benchmark_shares.get(sym, 0) * bars.get(sym, {}).get("close", 0)
                for sym in symbol_data
            )
            self.benchmark_curve.append({"date": date, "value": bm_val})

            for sym in list(self.positions.keys()):
                if sym in bars:
                    self._check_stop_loss(sym, bars[sym]["open"], self.pending_signal)
                    self._check_take_profit(sym, bars[sym]["open"], self.pending_signal)

            for sym in list(self.pending_signal.keys()):
                if sym in bars:
                    signal = self.pending_signal.pop(sym)
                    self._execute_signal(signal, bars[sym], strategies[sym])

            actions = {}
            for sym, bar in bars.items():
                sig = strategies[sym].on_bar(bar)
                if sig != Signal.HOLD:
                    self.pending_signal[sym] = sig
                    actions[sym] = sig

            combined_val = self.get_total_value()
            action_str = " | ".join(
                f"{sym}:{a}" for sym, a in actions.items()
            ) or None
            self.equity_curve.append({
                "date": date,
                "value": combined_val,
                "action": action_str,
            })
            self._last_actions = actions

        last_date = trading_dates[-1]
        last_bars = {}
        for sym in symbol_data:
            row = symbol_date_index[sym].get(last_date)
            if row is None:
                continue
            bar = self._row_to_bar(row, sym)
            last_bars[sym] = bar
            self.last_prices[sym] = bar["close"]

        bm_val = benchmark_cash + sum(
            benchmark_shares.get(sym, 0) * last_bars.get(sym, {}).get("close", 0)
            for sym in symbol_data
        )
        self.benchmark_curve.append({"date": last_date, "value": bm_val})

        for sym in list(self.pending_signal.keys()):
            if sym in last_bars:
                sig = self.pending_signal.pop(sym)
                self._execute_signal(sig, last_bars[sym], strategies[sym])

        for sym, bar in last_bars.items():
            strategies[sym].on_bar(bar)

        combined_val = self.get_total_value()
        self.equity_curve.append({
            "date": last_date,
            "value": combined_val,
            "action": None,
        })

        summary = self.get_summary(symbols)
        self._print_summary(summary)
        return summary

    def _execute_signal(self, signal: str, bar: dict, strategy):
        symbol = bar["symbol"]
        price = bar["open"]
        position = self.positions.get(symbol, 0)
        lot_size = 100

        if bar["date"] != self._prev_date:
            if self._prev_date is not None and self.risk_manager is not None:
                self.risk_manager.on_new_day(bar["date"])
            self._prev_date = bar["date"]

        if signal == Signal.BUY:
            if self._is_limit_up(symbol, bar):
                return

            actual_price = self._apply_slippage(price, "buy")
            current_holdings = len(self.positions)
            if current_holdings >= self.max_positions and symbol not in self.positions:
                return

            if symbol in self.positions:
                budget = self.cash * 0.5
            else:
                budget = self.cash * 0.5 / (current_holdings + 1) if current_holdings < self.max_positions else 0
            affordable = int(budget / (actual_price * (1 + self.commission)) / lot_size) * lot_size
            if affordable <= 0:
                return

            if self.risk_manager is not None:
                total_value = self.get_total_value()
                risk_result = self.risk_manager.check_buy(
                    symbol=symbol,
                    price=actual_price,
                    quantity=affordable,
                    total_value=total_value,
                    cash=self.cash,
                    positions=self.positions,
                    last_prices=self.last_prices,
                )
                if not risk_result.allowed:
                    return
                affordable = risk_result.adjusted_quantity
                if affordable <= 0:
                    return

            cost = self._calc_buy_cost(actual_price, affordable)
            if cost > self.cash:
                return
            self.cash -= cost
            old_qty = self.positions.get(symbol, 0)
            new_qty = old_qty + affordable
            self.positions[symbol] = new_qty
            if old_qty > 0:
                old_entry = self.entry_prices.get(symbol, actual_price)
                self.entry_prices[symbol] = (old_entry * old_qty + actual_price * affordable) / new_qty
            else:
                self.entry_prices[symbol] = actual_price
                self.entry_dates[symbol] = bar["date"]
            strategy.set_position(symbol, new_qty)
            commission_cost = max(actual_price * affordable * self.commission, self.min_commission)
            slippage_cost = (actual_price - price) * affordable
            self.trades.append({
                "date": bar["date"],
                "symbol": symbol,
                "action": "BUY",
                "price": actual_price,
                "quantity": affordable,
                "entry_price": self.entry_prices[symbol],
                "commission_cost": commission_cost,
                "slippage_cost": slippage_cost,
            })
            if self.verbose:
                print(f"  [{bar['date']}] BUY {symbol}: {affordable} @ {actual_price:.2f}")
            return

        if signal == Signal.SELL and position > 0:
            if not self._check_t_plus_1(symbol, bar["date"]):
                return

            if self._is_limit_down(symbol, bar):
                return

            actual_price = self._apply_slippage(price, "sell")
            entry_price = self.entry_prices.get(symbol, actual_price)

            if self.risk_manager is not None:
                total_value = self.get_total_value()
                risk_result = self.risk_manager.check_sell(
                    symbol=symbol,
                    price=actual_price,
                    quantity=position,
                    entry_price=entry_price,
                    total_value=total_value,
                )
                if not risk_result.allowed:
                    return

            proceeds = self._calc_sell_proceeds(actual_price, position)
            commission_cost = max(actual_price * position * self.commission, self.min_commission)
            stamp_cost = actual_price * position * self.stamp_tax
            self.cash += proceeds
            self.positions.pop(symbol, None)
            self.entry_prices.pop(symbol, None)
            self.entry_dates.pop(symbol, None)
            strategy.set_position(symbol, 0)
            slippage_cost = (price - actual_price) * position
            self.trades.append({
                "date": bar["date"],
                "symbol": symbol,
                "action": "SELL",
                "price": actual_price,
                "quantity": position,
                "entry_price": entry_price,
                "commission_cost": commission_cost + stamp_cost,
                "slippage_cost": slippage_cost,
            })
            if self.verbose:
                print(f"  [{bar['date']}] SELL {symbol}: {position} @ {actual_price:.2f}")

    def get_summary(self, symbols: list[str]) -> dict:
        final_value = self.get_total_value()
        profit = final_value - self.initial_cash
        profit_pct = profit / self.initial_cash * 100 if self.initial_cash else 0.0

        max_dd, max_dd_pct = self._calc_max_drawdown()
        annual_return = self._calc_annual_return()
        sharpe = self._calc_sharpe_ratio()
        benchmark_return = self._calc_benchmark_return()
        alpha = profit_pct - benchmark_return
        stats = self._calc_trade_stats()

        symbol_results = []
        for sym in symbols:
            sym_trades = [t for t in self.trades if t["symbol"] == sym]
            sym_sell = [t for t in sym_trades if t["action"] == "SELL"]
            sym_wins = 0
            for t in sym_sell:
                entry_price = t.get("entry_price", 0.0)
                pnl = (t["price"] - entry_price) * t["quantity"]
                if pnl > 0:
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
            "benchmark_return": benchmark_return,
            "alpha": alpha,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "cash": self.cash,
            "positions": dict(self.positions),
            "last_prices": dict(self.last_prices),
            "equity_curve": self.equity_curve,
            "benchmark_curve": self.benchmark_curve,
            "trades_list": list(self.trades),
            "symbol_results": symbol_results,
            **stats,
        }

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
        print(f"总佣金:   {summary.get('total_commission', 0):,.2f}")
        print(f"总印花税: {summary.get('total_stamp_tax', 0):,.2f}")
        print(f"总滑点:   {summary.get('total_slippage_cost', 0):,.2f}")
        print(f"平均持仓: {summary.get('avg_holding_days', 0):.1f} 天")
        print(f"剩余现金: {summary.get('cash', 0):,.2f}")
        print(f"持仓: { {s: qty for s, qty in summary.get('positions', {}).items()} }")

        if summary.get("symbol_results"):
            print("\n--- 各股票明细 ---")
            for sr in summary["symbol_results"]:
                print(f"  {sr['symbol']}: 持仓 {sr['position']} 股, 最后价 {sr['last_price']:.2f}, "
                      f"交易 {sr['trades']} 次, 胜率 {sr['win_rate']:.0f}%")
