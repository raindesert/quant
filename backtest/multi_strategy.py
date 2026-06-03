"""多策略并行组合回测 — 一个组合内同时跑 N 个独立策略。

每个子策略分配一份子资金（默认等分），分别跑回测，最后汇总：
  - 加权组合权益曲线
  - 组合级收益 / 夏普 / 回撤
  - 每个子策略的独立贡献

使用场景：
  - 同一标的上多策略叠加（不同时间周期 / 不同逻辑的策略融合）
  - 策略组合优化（找最优子策略权重）
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from backtest.engine import BacktestEngine
from strategy.registry import get_strategy_class

logger = logging.getLogger(__name__)


def _run_one(args):
    """单策略子回测（必须是模块级函数以支持 pickle）。"""
    (
        strategy_name, strategy_params, sub_cash, symbol, days,
        commission, stop_loss, take_profit, position_size, start_date, end_date,
    ) = args
    cls = get_strategy_class(strategy_name)
    if cls is None:
        cls = get_strategy_class("sma")
    strategy = cls(**(strategy_params or {}))

    engine = BacktestEngine(
        initial_cash=sub_cash,
        commission=commission,
        verbose=False,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
    )
    summary = engine.run(
        strategy, symbol, days=days, start_date=start_date, end_date=end_date
    )
    if summary is None:
        return {"strategy": strategy_name, "error": "no data"}

    # 加 strategy_name 方便汇总
    summary["strategy"] = strategy_name
    summary["strategy_params"] = strategy_params or {}
    summary["sub_cash"] = sub_cash
    return summary


class MultiStrategyEngine:
    """多策略并行组合回测。

    资金分配（默认等分）：
        weights = [0.4, 0.3, 0.3]  # 总和必须 = 1.0

    汇总：
        - 组合权益曲线 = 各子策略权益按权重加和
        - 组合级指标 = 基于加权权益计算
    """

    def __init__(
        self,
        strategies: list[str],
        symbol: str,
        days: int = 250,
        initial_cash: float = 1_000_000,
        commission: float = 0.0003,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        position_size: float = 1.0,
        weights: list[float] | None = None,
        strategy_params: list[dict] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        workers: int = 4,
    ):
        if not strategies:
            raise ValueError("strategies 不能为空")
        if weights is None:
            # 默认等分
            weights = [1.0 / len(strategies)] * len(strategies)
        else:
            if len(weights) != len(strategies):
                raise ValueError(
                    f"weights 长度 {len(weights)} 与 strategies 长度 {len(strategies)} 不一致"
                )
            if any(w < 0 for w in weights):
                raise ValueError("weights 不能为负")
            if abs(sum(weights) - 1.0) > 1e-6:
                raise ValueError(
                    f"weights 总和 {sum(weights)} 必须为 1.0（当前 {sum(weights):.4f}）"
                )

        self.strategies = list(strategies)
        self.symbol = symbol
        self.days = days
        self.initial_cash = initial_cash
        self.commission = commission
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_size = position_size
        self.weights = list(weights)
        self.strategy_params = strategy_params or [{}] * len(strategies)
        if len(self.strategy_params) != len(strategies):
            raise ValueError("strategy_params 长度必须与 strategies 一致")
        self.start_date = start_date
        self.end_date = end_date
        self.workers = workers

    def run(self) -> dict[str, Any]:
        """执行多策略组合回测。

        Returns:
            dict，包含：
              - strategies: 子策略结果列表
              - combined: 组合级汇总（equity_curve / profit_pct / sharpe_ratio / max_drawdown_pct / ...）
              - weights: 使用的权重
        """
        print(
            f"\n多策略组合: {len(self.strategies)} 个策略 × {self.symbol} × {self.days} 天"
        )
        print(f"权重: {dict(zip(self.strategies, self.weights))}")

        # 分配子资金
        sub_cash_list = [self.initial_cash * w for w in self.weights]
        for name, w, sc in zip(self.strategies, self.weights, sub_cash_list):
            print(f"  {name}: 权重 {w:.2%} 子资金 {sc:,.0f}")

        # 并发跑子策略
        args_list = [
            (
                name, params, sub_cash, self.symbol, self.days,
                self.commission, self.stop_loss, self.take_profit, self.position_size,
                self.start_date, self.end_date,
            )
            for name, params, sub_cash in zip(
                self.strategies, self.strategy_params, sub_cash_list
            )
        ]

        sub_results: list[dict] = []
        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(_run_one, args): args for args in args_list}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    sub_results.append(result)
                except Exception as e:
                    args = futures[future]
                    sub_results.append({
                        "strategy": args[0],
                        "error": str(e),
                        "strategy_params": args[1] or {},
                    })
                    logger.warning("子策略 %s 失败: %s", args[0], e)

        # 保持原顺序（as_completed 不保证）
        results_by_name = {r.get("strategy"): r for r in sub_results}
        sub_results = [results_by_name.get(n, {"strategy": n, "error": "missing"})
                       for n in self.strategies]

        # 汇总组合
        combined = self._combine(sub_results)

        print(f"\n===== 多策略组合结果 =====")
        for r in sub_results:
            if "error" in r:
                print(f"  {r['strategy']}: ERROR {r['error']}")
            else:
                print(
                    f"  {r['strategy']}: 收益 {r.get('profit_pct', 0):+.2f}% "
                    f"夏普 {r.get('sharpe_ratio', 0):.2f} "
                    f"回撤 {r.get('max_drawdown_pct', 0):.2f}%"
                )
        print(f"\n  组合: 收益 {combined['profit_pct']:+.2f}% "
              f"夏普 {combined['sharpe_ratio']:.2f} "
              f"回撤 {combined['max_drawdown_pct']:.2f}%")

        return {
            "symbol": self.symbol,
            "strategies": sub_results,
            "combined": combined,
            "weights": dict(zip(self.strategies, self.weights)),
        }

    def _combine(self, sub_results: list[dict]) -> dict[str, Any]:
        """把各子策略结果按权重汇总为组合。"""
        import numpy as np
        import pandas as pd

        # 收集有效的 equity_curve（按日期索引）
        valid = [r for r in sub_results if "error" not in r and r.get("equity_curve")]
        if not valid:
            return {
                "profit_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate": 0.0,
                "trades": 0,
                "profit_factor": 0.0,
                "final_value": float(self.initial_cash),
                "annual_return": 0.0,
                "equity_curve": [],
                "n_strategies": 0,
            }

        # 每个子策略的 equity_curve 转 Series（按日期）
        series_list = []
        valid_names = []
        for r in valid:
            eq_df = pd.DataFrame(r["equity_curve"])
            if "date" in eq_df.columns:
                eq_df["date"] = pd.to_datetime(eq_df["date"])
                eq_df = eq_df.set_index("date")
            s = eq_df["value"].astype(float)
            series_list.append(s)
            valid_names.append(r["strategy"])

        # 权重对应（用 self.strategies 顺序）
        name_to_weight = dict(zip(self.strategies, self.weights))
        valid_weights = np.array([name_to_weight[n] for n in valid_names])

        # 归一化（万一有的子策略有 error）
        valid_weights = valid_weights / valid_weights.sum()

        # 对齐日期（用所有有效子策略日期的并集，缺失用前向填充）
        combined_index = series_list[0].index
        for s in series_list[1:]:
            combined_index = combined_index.union(s.index)
        combined_index = combined_index.sort_values()

        aligned = pd.DataFrame(
            {n: s.reindex(combined_index).ffill() for n, s in zip(valid_names, series_list)}
        )

        # 加权求和：组合权益 = Σ weight_i * sub_strategy_equity_i
        combined_equity = (aligned * valid_weights).sum(axis=1)

        # 计算组合级指标
        initial = combined_equity.iloc[0]
        final = combined_equity.iloc[-1]
        profit_pct = (final / initial - 1) * 100

        # 日收益率
        daily_ret = combined_equity.pct_change().dropna()
        if len(daily_ret) == 0 or daily_ret.std() == 0:
            sharpe = 0.0
        else:
            sharpe = float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5))

        # 最大回撤
        running_max = combined_equity.cummax()
        drawdown = (combined_equity - running_max) / running_max
        max_dd = float(drawdown.min() * 100)

        # 年化
        n_days = (combined_equity.index[-1] - combined_equity.index[0]).days or 1
        annual_return = ((final / initial) ** (365.0 / n_days) - 1) * 100

        # 交易次数（加权求和）
        total_trades = sum(int(r.get("trades", 0)) for r in valid)

        # 胜率：胜率按权重平均
        if total_trades > 0:
            wr_sum = sum(
                r.get("win_rate", 0) * r.get("trades", 0)
                for r in valid
            )
            win_rate = wr_sum / total_trades
        else:
            win_rate = 0.0

        # 盈利因子
        profit_factors = [r.get("profit_factor", 0) for r in valid if r.get("profit_factor", 0) > 0]
        avg_pf = float(np.mean(profit_factors)) if profit_factors else 0.0

        # 把组合 equity_curve 转成 list[dict]（与 sub 一致）
        eq_curve = [
            {"date": d.strftime("%Y-%m-%d"), "value": float(v)}
            for d, v in combined_equity.items()
        ]

        return {
            "profit_pct": float(profit_pct),
            "sharpe_ratio": float(sharpe),
            "max_drawdown_pct": float(max_dd),
            "win_rate": float(win_rate),
            "trades": int(total_trades),
            "profit_factor": float(avg_pf),
            "final_value": float(final),
            "initial_value": float(initial),
            "annual_return": float(annual_return),
            "equity_curve": eq_curve,
            "n_strategies": len(valid),
        }
