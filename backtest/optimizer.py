"""策略参数优化器：Grid Search 暴力搜索最优参数组合。"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from typing import Any, Callable

from backtest.engine import BacktestEngine
from strategy.examples import (
    BollingerStrategy,
    MACDStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    RSIStrategy,
    SMAStrategy,
)


STRATEGY_MAP = {
    "sma": SMAStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}

# 各策略的默认参数范围（用于 --auto-tune）
DEFAULT_GRIDS = {
    "sma": {"fast": [5, 10, 15, 20], "slow": [30, 60, 120]},
    "rsi": {"period": [7, 14, 21], "oversold": [20, 30], "overbought": [70, 80]},
    "macd": {"fast": [8, 12, 16], "slow": [20, 26, 34], "signal": [7, 9, 13]},
    "bollinger": {"period": [10, 20, 30], "std_dev": [1.5, 2.0, 2.5]},
    "momentum": {"period": [5, 10, 20], "threshold": [0.01, 0.02, 0.05]},
    "mean_reversion": {"period": [10, 20, 30], "threshold": [0.01, 0.02, 0.05]},
}

# 可优化的指标
OPTIMIZE_METRICS = {
    "profit_pct": "收益率 (%)",
    "sharpe_ratio": "夏普比率",
    "profit_factor": "盈利因子",
    "max_drawdown_pct": "最大回撤 (%)",  # 越小越好
    "win_rate": "胜率 (%)",
}


def _run_single_backtest(args_tuple) -> dict:
    """单次回测（用于并发执行）。"""
    strategy_name, symbol, days, commission, stop_loss, take_profit, position_size, start_date, end_date, params = args_tuple
    cls = STRATEGY_MAP.get(strategy_name.lower(), SMAStrategy)
    strategy = cls(**params)

    engine = BacktestEngine(
        initial_cash=1_000_000,
        commission=commission,
        verbose=False,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
    )
    summary = engine.run(
        strategy,
        symbol,
        days=days,
        start_date=start_date,
        end_date=end_date,
    )
    if summary is None:
        return {"params": params, "error": "no data"}

    result = {
        "params": params,
        "profit_pct": summary.get("profit_pct", 0),
        "sharpe_ratio": summary.get("sharpe_ratio", 0),
        "profit_factor": summary.get("profit_factor", 0),
        "max_drawdown_pct": summary.get("max_drawdown_pct", 0),
        "win_rate": summary.get("win_rate", 0),
        "trades": summary.get("trades", 0),
        "final_value": summary.get("final_value", 0),
        "annual_return": summary.get("annual_return", 0),
        "summary": summary,
    }
    return result


class StrategyOptimizer:
    """策略参数 Grid Search 优化器。"""

    def __init__(
        self,
        strategy_name: str,
        symbol: str,
        days: int = 250,
        commission: float = 0.0003,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        position_size: float = 1.0,
        start_date: str | None = None,
        end_date: str | None = None,
        metric: str = "sharpe_ratio",
        workers: int = 4,
    ):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.days = days
        self.commission = commission
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_size = position_size
        self.start_date = start_date
        self.end_date = end_date
        self.metric = metric
        self.workers = workers

    def optimize(self, param_grid: dict[str, list]) -> dict[str, Any]:
        """运行 Grid Search。

        Args:
            param_grid: 参数名 -> 候选值列表，如 {"fast": [5,10], "slow": [30,60]}

        Returns:
            包含 best_params, best_score, all_results 的字典
        """
        # 生成所有参数组合
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(product(*values))
        total = len(combinations)
        print(f"参数优化: {total} 种组合 × {self.strategy_name} × {self.symbol}")
        print(f"优化指标: {OPTIMIZE_METRICS.get(self.metric, self.metric)}")

        # 准备并发任务
        args_list = [
            (
                self.strategy_name,
                self.symbol,
                self.days,
                self.commission,
                self.stop_loss,
                self.take_profit,
                self.position_size,
                self.start_date,
                self.end_date,
                dict(zip(keys, combo)),
            )
            for combo in combinations
        ]

        all_results = []
        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(_run_single_backtest, args): args for args in args_list}
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    result = future.result()
                    all_results.append(result)
                    score = result.get(self.metric, None)
                    score_str = f"{score:.4f}" if isinstance(score, (int, float)) else str(score)
                    params_str = ", ".join(f"{k}={v}" for k, v in result["params"].items())
                    print(f"  [{i}/{total}] {params_str} → {score_str}")
                except Exception as e:
                    args = futures[future]
                    all_results.append({"params": args[-1], "error": str(e)})
                    print(f"  [{i}/{total}] {args[-1]} → ERROR: {e}")

        # 找最优（对于 max_drawdown_pct 越小越好）
        valid_results = [r for r in all_results if "error" not in r]
        if not valid_results:
            print("没有有效的回测结果")
            return {"best_params": {}, "best_score": None, "all_results": all_results}

        if self.metric in ("max_drawdown_pct",):
            # 越小越好
            best = min(valid_results, key=lambda r: r.get(self.metric, float("inf")))
        else:
            # 越大越好
            best = max(valid_results, key=lambda r: r.get(self.metric, float("-inf")))

        best_score = best.get(self.metric, None)
        print(f"\n最优参数: {best['params']}")
        print(f"最优分数: {best_score}")
        if "error" not in best:
            print(f"  收益率: {best.get('profit_pct', 0):+.2f}%")
            print(f"  夏普比率: {best.get('sharpe_ratio', 0):.2f}")
            print(f"  最大回撤: {best.get('max_drawdown_pct', 0):.2f}%")
            print(f"  交易次数: {best.get('trades', 0)}")

        return {
            "best_params": best["params"],
            "best_score": best_score,
            "best_result": best,
            "all_results": all_results,
        }

    def print_leaderboard(self, all_results: list[dict], top: int = 10):
        """打印参数组合排行榜。"""
        valid = [r for r in all_results if "error" not in r]
        if not valid:
            print("无有效结果")
            return

        # 排序
        if self.metric in ("max_drawdown_pct",):
            sorted_results = sorted(valid, key=lambda r: r.get(self.metric, float("inf")))
        else:
            sorted_results = sorted(valid, key=lambda r: r.get(self.metric, float("-inf")), reverse=True)

        metric_label = OPTIMIZE_METRICS.get(self.metric, self.metric)
        print(f"\n===== 参数排行榜 (top {top}) =====")
        print(f"{'排名':<4} {metric_label:>12} {'收益率':>10} {'夏普':>8} {'回撤%':>8} {'交易数':>6}  参数组合")
        print("-" * 90)
        for i, r in enumerate(sorted_results[:top], 1):
            params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
            print(
                f"{i:<4} {r.get(self.metric, 0):>12.4f} "
                f"{r.get('profit_pct', 0):>+10.2f}% {r.get('sharpe_ratio', 0):>8.2f} "
                f"{r.get('max_drawdown_pct', 0):>7.2f}% {r.get('trades', 0):>6d}  {params_str}"
            )
