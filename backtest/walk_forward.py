"""Walk-Forward 验证模块 — 滚动窗口回测，检测过拟合。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.engine import BacktestEngine
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from strategy.base import Signal
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
    skipped_windows: int = 0

    def summary(self) -> str:
        lines = [
            f"\n{'=' * 60}",
            f"Walk-Forward 验证结果: {self.strategy_name} / {self.symbol}",
            f"{'=' * 60}",
            f"窗口数量: {len(self.windows)}（有效 {len(self.windows) - self.skipped_windows}，跳过 {self.skipped_windows}）",
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

    训练期与验证期的数据长度口径一致，衰减比才可比。

    过拟合判断标准:
    - 衰减比 = 验证期平均收益 / 训练期平均收益
    - 衰减比 < 0.5 视为过拟合（验证期收益不到训练期的一半）
    - 验证期收益为负也视为过拟合
    """

    # 指标预热所需 bar 数，与 BacktestEngine 内部 warmup 保持一致
    WARMUP_BARS = 20
    # 单个窗口至少需要的交易日数，低于此值样本无统计意义
    MIN_TRADING_BARS = 10

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
        df = DataProcessor.add_all_indicators(df)
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
            if train_result is None or test_result is None:
                logger.warning(
                    "窗口 %d [%s~%s] 训练/验证期数据不足，已计入但标记为不可用",
                    window_id, train_start_str, train_end_str,
                )
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

        # 业界标准做法：切片时向前多取 WARMUP 根 bar 供指标预热，
        # 再用 trade_start 让引擎从窗口首日才开始交易与计入统计。
        # 否则引擎内部固定丢弃前 20 根 bar，train_days=120 实际只剩 100 根参与交易，
        # test_days=60 只剩 40 根，degradation_ratio 的分子分母不可比。
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        prior = df[df["date"] < start_dt].tail(self.WARMUP_BARS)
        window = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        if len(window) < self.MIN_TRADING_BARS:
            logger.warning(
                "窗口 %s ~ %s 交易日仅 %d 根（少于 %d），跳过",
                start_date, end_date, len(window), self.MIN_TRADING_BARS,
            )
            return None

        period_df = pd.concat([prior, window], ignore_index=True)

        return engine._run_with_data(
            strategy, period_df, self.symbol, trade_start=start_dt
        )

    def _aggregate(self, windows: list[WalkForwardWindow]) -> WalkForwardResult:
        result = WalkForwardResult(
            strategy_name=self.strategy_name,
            symbol=self.symbol,
            windows=windows,
        )

        valid_windows = [w for w in windows if w.train_result and w.test_result]
        result.skipped_windows = len(windows) - len(valid_windows)
        if not valid_windows:
            logger.error(
                "所有 %d 个窗口的数据均不足，无法判定过拟合（可增大 --wf-train-days/--wf-test-days）",
                len(windows),
            )
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
