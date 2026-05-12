"""Walk-Forward 验证模块 — 滚动窗口回测，检测过拟合。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.engine import BacktestEngine
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.registry import get_strategy_class

logger = logging.getLogger("quant")


@dataclass
class WalkForwardWindow:
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_result: dict = field(default_factory=dict)
    test_result: dict = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    strategy_name: str
    symbol: str
    windows: list[WalkForwardWindow] = field(default_factory=list)
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    avg_train_sharpe: float = 0.0
    avg_test_sharpe: float = 0.0
    degradation_ratio: float = 0.0
    is_overfit: bool = False

    def summary(self) -> str:
        lines = [
            f"\n{'=' * 60}",
            f"Walk-Forward 验证结果: {self.strategy_name} / {self.symbol}",
            f"{'=' * 60}",
            f"窗口数量: {len(self.windows)}",
            f"训练期平均收益: {self.avg_train_return:+.2f}%",
            f"验证期平均收益: {self.avg_test_return:+.2f}%",
            f"训练期平均夏普: {self.avg_train_sharpe:.2f}",
            f"验证期平均夏普: {self.avg_test_sharpe:.2f}",
            f"衰减比(验证/训练): {self.degradation_ratio:.2f}",
            f"过拟合判断: {'⚠️ 是' if self.is_overfit else '✅ 否'}",
            f"{'─' * 60}",
        ]
        for w in self.windows:
            tr = w.train_result.get("profit_pct", 0)
            ts = w.test_result.get("profit_pct", 0)
            tsh = w.train_result.get("sharpe_ratio", 0)
            tssh = w.test_result.get("sharpe_ratio", 0)
            lines.append(
                f"  窗口{w.window_id}: "
                f"训练[{w.train_start}~{w.train_end}] {tr:+.2f}%/夏普{tsh:.2f}  "
                f"验证[{w.test_start}~{w.test_end}] {ts:+.2f}%/夏普{tssh:.2f}"
            )
        lines.append(f"{'=' * 60}")
        return "\n".join(lines)


class WalkForwardValidator:
    """Walk-Forward Analysis 滚动窗口验证。

    将历史数据按时间分为多个窗口，每个窗口包含训练期和验证期：
    - 训练期: 用于运行回测，获取策略表现
    - 验证期: 紧接训练期之后，检验策略是否过拟合

    过拟合判断标准:
    - 衰减比 = 验证期平均收益 / 训练期平均收益
    - 衰减比 < 0.5 视为过拟合（验证期收益不到训练期的一半）
    - 验证期收益为负也视为过拟合
    """

    def __init__(
        self,
        strategy_name: str,
        symbol: str,
        train_days: int = 120,
        test_days: int = 60,
        step_days: int = 60,
        commission: float = 0.0003,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        position_size: float = 1.0,
        slippage: float = 0.001,
        slippage_type: str = "percent",
        enforce_t_plus_1: bool = True,
        check_limit: bool = True,
        overfit_threshold: float = 0.5,
        strategy_params: dict | None = None,
    ):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.commission = commission
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_size = position_size
        self.slippage = slippage
        self.slippage_type = slippage_type
        self.enforce_t_plus_1 = enforce_t_plus_1
        self.check_limit = check_limit
        self.overfit_threshold = overfit_threshold
        self.strategy_params = strategy_params or {}

    def validate(self) -> WalkForwardResult:
        fetcher = DataFetcher()
        processor = DataProcessor()

        total_days = self.train_days + self.test_days + self.step_days * 5
        df = fetcher.get_history(self.symbol, days=total_days)
        if df.empty:
            logger.error("无法获取 %s 数据", self.symbol)
            return WalkForwardResult(self.strategy_name, self.symbol)

        df = processor.clean(df)
        if len(df) < self.train_days + self.test_days:
            logger.error("数据不足: 需要 %d 天，实际 %d 天", self.train_days + self.test_days, len(df))
            return WalkForwardResult(self.strategy_name, self.symbol)

        dates = sorted(df["date"].tolist())
        windows = []
        window_id = 0

        start_idx = 0
        while True:
            train_end_idx = start_idx + self.train_days - 1
            test_start_idx = train_end_idx + 1
            test_end_idx = test_start_idx + self.test_days - 1

            if test_end_idx >= len(dates):
                break

            train_start_str = pd.Timestamp(dates[start_idx]).strftime("%Y-%m-%d")
            train_end_str = pd.Timestamp(dates[train_end_idx]).strftime("%Y-%m-%d")
            test_start_str = pd.Timestamp(dates[test_start_idx]).strftime("%Y-%m-%d")
            test_end_str = pd.Timestamp(dates[test_end_idx]).strftime("%Y-%m-%d")

            window_id += 1
            wf_window = WalkForwardWindow(
                window_id=window_id,
                train_start=train_start_str,
                train_end=train_end_str,
                test_start=test_start_str,
                test_end=test_end_str,
            )

            train_result = self._run_period(
                df, train_start_str, train_end_str,
            )
            test_result = self._run_period(
                df, test_start_str, test_end_str,
            )

            wf_window.train_result = train_result or {}
            wf_window.test_result = test_result or {}
            windows.append(wf_window)

            start_idx += self.step_days

        return self._aggregate(windows)

    def _run_period(self, df: pd.DataFrame, start_date: str, end_date: str) -> dict | None:
        cls = get_strategy_class(self.strategy_name)
        if cls is None:
            cls = get_strategy_class("sma")
        strategy = cls(**self.strategy_params)

        engine = BacktestEngine(
            initial_cash=1_000_000,
            commission=self.commission,
            verbose=False,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            position_size=self.position_size,
            slippage=self.slippage,
            slippage_type=self.slippage_type,
            enforce_t_plus_1=self.enforce_t_plus_1,
            check_limit=self.check_limit,
        )

        period_df = df.copy()
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        period_df = period_df[(period_df["date"] >= start_dt) & (period_df["date"] <= end_dt)]

        if len(period_df) < 30:
            return None

        bars = list(period_df.itertuples(index=False))
        symbol = self.symbol
        warmup_count = min(20, len(bars) - 1)

        first_bar = engine._row_to_bar(bars[0], symbol)
        engine.last_prices[symbol] = first_bar["close"]
        benchmark_shares = 0
        benchmark_cash = engine.initial_cash

        for i, row in enumerate(bars[:warmup_count]):
            bar = engine._row_to_bar(row, symbol)
            engine.last_prices[symbol] = bar["close"]
            engine.last_bars[symbol] = bar
            strategy.on_bar(bar)

        for i in range(warmup_count, len(bars) - 1):
            current_bar = engine._row_to_bar(bars[i], symbol)
            next_bar = engine._row_to_bar(bars[i + 1], symbol)
            engine.last_prices[symbol] = current_bar["close"]
            engine.last_bars[symbol] = current_bar

            if benchmark_shares == 0:
                open_price = current_bar["open"]
                cost_per_100 = engine._calc_buy_cost(open_price, 100)
                affordable = int(engine.initial_cash * 0.5 / cost_per_100) * 100
                if affordable > 0:
                    cost = engine._calc_buy_cost(open_price, affordable)
                    benchmark_cash -= cost
                    benchmark_shares = affordable
            benchmark_value = benchmark_cash + benchmark_shares * current_bar["close"]
            engine.benchmark_curve.append({"date": current_bar["date"], "value": benchmark_value})

            engine._check_stop_loss(symbol, next_bar["open"], engine.pending_signal)
            engine._check_take_profit(symbol, next_bar["open"], engine.pending_signal)

            if symbol in engine.pending_signal:
                signal = engine.pending_signal.pop(symbol)
                engine._execute_signal(signal, next_bar, strategy)

            signal = strategy.on_bar(current_bar)
            if signal != "hold":
                engine.pending_signal[symbol] = signal

            engine.equity_curve.append({"date": current_bar["date"], "value": engine.get_total_value(), "action": engine._last_action})
            engine._last_action = None

        last_bar = engine._row_to_bar(bars[-1], symbol)
        engine.last_prices[symbol] = last_bar["close"]
        if symbol in engine.pending_signal:
            engine._check_stop_loss(symbol, last_bar["open"], engine.pending_signal)
            engine._check_take_profit(symbol, last_bar["open"], engine.pending_signal)
            signal = engine.pending_signal.pop(symbol)
            engine._execute_signal(signal, last_bar, strategy)
        strategy.on_bar(last_bar)
        engine.equity_curve.append({"date": last_bar["date"], "value": engine.get_total_value()})

        return engine.get_summary(symbol)

    def _aggregate(self, windows: list[WalkForwardWindow]) -> WalkForwardResult:
        result = WalkForwardResult(
            strategy_name=self.strategy_name,
            symbol=self.symbol,
            windows=windows,
        )

        valid_windows = [w for w in windows if w.train_result and w.test_result]
        if not valid_windows:
            return result

        train_returns = [w.train_result.get("profit_pct", 0) for w in valid_windows]
        test_returns = [w.test_result.get("profit_pct", 0) for w in valid_windows]
        train_sharpes = [w.train_result.get("sharpe_ratio", 0) for w in valid_windows]
        test_sharpes = [w.test_result.get("sharpe_ratio", 0) for w in valid_windows]

        result.avg_train_return = sum(train_returns) / len(train_returns)
        result.avg_test_return = sum(test_returns) / len(test_returns)
        result.avg_train_sharpe = sum(train_sharpes) / len(train_sharpes)
        result.avg_test_sharpe = sum(test_sharpes) / len(test_sharpes)

        if abs(result.avg_train_return) > 0.01:
            result.degradation_ratio = result.avg_test_return / result.avg_train_return
        elif result.avg_test_return > 0:
            result.degradation_ratio = 1.0
        else:
            result.degradation_ratio = 0.0

        result.is_overfit = (
            result.degradation_ratio < self.overfit_threshold
            or result.avg_test_return < 0
        )

        return result
