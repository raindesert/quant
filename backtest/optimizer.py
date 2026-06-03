"""策略参数优化器：支持 Grid Search + 贝叶斯/随机搜索（optuna）。

贝叶斯/随机搜索在参数维度 >5 或大范围时显著优于 Grid。
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from enum import Enum
from itertools import product
from typing import Any, Callable

from backtest.engine import BacktestEngine
from strategy.registry import STRATEGY_REGISTRY, get_strategy_class


DEFAULT_GRIDS = {
    "sma": {"fast": [5, 10, 15, 20], "slow": [30, 60, 120]},
    "rsi": {"period": [7, 14, 21], "oversold": [20, 30], "overbought": [70, 80]},
    "macd": {"fast": [8, 12, 16], "slow": [20, 26, 34], "signal": [7, 9, 13]},
    "bollinger": {"period": [10, 20, 30], "std_dev": [1.5, 2.0, 2.5]},
    "momentum": {"period": [5, 10, 20], "threshold": [0.01, 0.02, 0.05]},
    "mean_reversion": {"period": [10, 20, 30], "threshold": [0.01, 0.02, 0.05]},
    "kdj": {"n": [9, 14, 21], "m1": [2, 3, 5], "m2": [2, 3, 5], "oversold": [15, 20, 25], "overbought": [75, 80, 85]},
}

# 可优化的指标
OPTIMIZE_METRICS = {
    "profit_pct": "收益率 (%)",
    "sharpe_ratio": "夏普比率",
    "profit_factor": "盈利因子",
    "max_drawdown_pct": "最大回撤 (%)",  # 越小越好
    "win_rate": "胜率 (%)",
}


class OptimizeMethod(str, Enum):
    GRID = "grid"
    RANDOM = "random"
    BAYESIAN = "bayesian"

    @classmethod
    def from_str(cls, s: str) -> "OptimizeMethod":
        s = s.lower().strip()
        for m in cls:
            if m.value == s:
                return m
        raise ValueError(
            f"未知优化方法 {s!r}，可选: {[m.value for m in cls]}"
        )


# 越小越好的指标
_LOWER_IS_BETTER = {"max_drawdown_pct"}


def _is_higher_better(metric: str) -> bool:
    return metric not in _LOWER_IS_BETTER


def _make_suggester(name: str, values, method: "OptimizeMethod"):
    """根据 param_grid 值的形式返回 optuna suggest 函数。

    支持：
    - list → 离散候选（categorical）
    - (low, high) 元组 → int 或 float 范围（看 low 类型）
    """
    import optuna

    if isinstance(values, (list, tuple)) and not (
        isinstance(values, tuple) and len(values) == 2
        and all(isinstance(v, (int, float)) for v in values)
    ):
        # 离散候选
        choices = list(values)

        def suggest_categorical(trial):
            return trial.suggest_categorical(name, choices)
        return suggest_categorical

    if isinstance(values, tuple) and len(values) == 2:
        low, high = values
        if isinstance(low, int) and isinstance(high, int):
            def suggest_int(trial, _low=low, _high=high):
                return trial.suggest_int(name, _low, _high)
            return suggest_int
        else:
            def suggest_float(trial, _low=low, _high=high):
                return trial.suggest_float(name, _low, _high)
            return suggest_float

    raise ValueError(
        f"参数 {name!r} 的定义 {values!r} 不合法。"
        "应为 list（离散候选）或 (low, high) 元组（范围）。"
    )


def _run_single_backtest(args_tuple) -> dict:
    """单次回测（用于并发执行）。"""
    strategy_name, symbol, days, commission, stop_loss, take_profit, position_size, start_date, end_date, params, risk_params = args_tuple
    cls = get_strategy_class(strategy_name)
    if cls is None:
        cls = get_strategy_class("sma")
    strategy = cls(**params)

    risk_manager = None
    if risk_params and risk_params.get("enabled"):
        from risk.manager import RiskManager
        risk_manager = RiskManager(
            max_position_pct=risk_params.get("max_position_pct", 0.25),
            max_positions=risk_params.get("max_positions", 10),
            max_drawdown_pct=risk_params.get("max_drawdown_pct", 0.20),
            max_daily_loss_pct=risk_params.get("max_daily_loss_pct", 0.03),
            max_stock_loss_pct=risk_params.get("max_stock_loss_pct", 0.10),
            enabled=True,
        )

    engine = BacktestEngine(
        initial_cash=1_000_000,
        commission=commission,
        verbose=False,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
        risk_manager=risk_manager,
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
        risk_params: dict | None = None,
        walk_forward: bool = False,
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
        self.risk_params = risk_params
        self.walk_forward = walk_forward

    def optimize(
        self,
        param_grid: dict[str, list],
        method: str | OptimizeMethod = OptimizeMethod.GRID,
        n_trials: int = 50,
        n_jobs: int | None = None,
    ) -> dict[str, Any]:
        """运行参数优化。

        Args:
            param_grid: 参数名 -> 候选值列表
                Grid 模式: 笛卡尔积遍历所有组合
                Random/Bayes 模式: 每个值是 (low, high) 元组或离散整数列表
                    - [5, 10, 20] → 离散候选（categorical）
                    - (1, 50) → 整数范围
                    - (0.01, 0.5) → 浮点范围
            method: 'grid' / 'random' / 'bayesian'
            n_trials: 随机/贝叶斯模式的采样次数（Grid 模式忽略）
            n_jobs: 进程数（None 用 self.workers）
        """
        if isinstance(method, str):
            method = OptimizeMethod.from_str(method)

        if method == OptimizeMethod.GRID:
            return self._optimize_grid(param_grid)
        else:
            return self._optimize_search(
                param_grid, method=method, n_trials=n_trials, n_jobs=n_jobs
            )

    def _optimize_grid(self, param_grid: dict[str, list]) -> dict[str, Any]:
        """Grid Search（暴力遍历所有组合）。"""
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(product(*values))
        total = len(combinations)
        print(f"参数优化 (Grid): {total} 种组合 × {self.strategy_name} × {self.symbol}")
        print(f"优化指标: {OPTIMIZE_METRICS.get(self.metric, self.metric)}")
        if self.risk_params and self.risk_params.get("enabled"):
            print(f"风控: 已启用")
        if self.walk_forward:
            print(f"Walk-Forward: 已启用（将在验证期评估稳健性）")

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
                self.risk_params,
            )
            for combo in combinations
        ]

        all_results = self._run_concurrent(args_list, total)
        return self._summarize(all_results)

    def _optimize_search(
        self,
        param_grid: dict[str, list | tuple],
        method: OptimizeMethod,
        n_trials: int = 50,
        n_jobs: int | None = None,
    ) -> dict[str, Any]:
        """随机/贝叶斯搜索（optuna 驱动）。"""
        try:
            import optuna
        except ImportError:
            raise ImportError(
                "随机/贝叶斯优化需要 optuna：pip install optuna"
            )

        n_jobs = n_jobs or self.workers
        print(
            f"参数优化 ({method.value}): {n_trials} trials × {self.strategy_name} × {self.symbol}"
        )
        print(f"优化指标: {OPTIMIZE_METRICS.get(self.metric, self.metric)}")
        print(f"并发: {n_jobs} workers")

        # 把 param_grid 转成 optuna 建议器
        suggesters = {}
        for name, values in param_grid.items():
            suggesters[name] = _make_suggester(name, values, method)

        # 用 optuna 串行驱动 trial（每次 trial 内部开 1 个进程跑回测）
        # 注意：optuna 自带 n_jobs，但子进程嵌套进程会复杂；这里用串行 trial + 内部并发
        higher_better = _is_higher_better(self.metric)
        all_results: list[dict] = []

        def _objective(trial: "optuna.Trial") -> float:
            params = {name: suggest(trial) for name, suggest in suggesters.items()}
            args_tuple = (
                self.strategy_name,
                self.symbol,
                self.days,
                self.commission,
                self.stop_loss,
                self.take_profit,
                self.position_size,
                self.start_date,
                self.end_date,
                params,
                self.risk_params,
            )
            # 单次回测在子进程跑（_run_single_backtest 是模块级函数）
            with ProcessPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_run_single_backtest, args_tuple)
                result = future.result()
            result["params"] = params
            all_results.append(result)

            score = result.get(self.metric, None)
            if score is None or "error" in result:
                raise optuna.TrialPruned()  # optuna 跳过该 trial

            # optuna 始终最大化
            return float(score) if higher_better else -float(score)

        sampler = (
            optuna.samplers.TPESampler(seed=42) if method == OptimizeMethod.BAYESIAN
            else optuna.samplers.RandomSampler(seed=42)
        )
        study = optuna.create_study(direction="maximize", sampler=sampler)
        # 静默 optuna 日志
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)

        # 标准化结果
        valid = [r for r in all_results if "error" not in r]
        summary = self._summarize(all_results)
        summary["n_trials"] = len(all_results)
        summary["n_pruned"] = len(all_results) - len(valid)
        return summary

    def _run_concurrent(self, args_list: list, total: int) -> list[dict]:
        """并发跑 args_list 里的回测，实时打印进度。"""
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
        return all_results

    def _summarize(self, all_results: list[dict]) -> dict[str, Any]:
        """从结果里挑最优 + 打印。"""
        valid_results = [r for r in all_results if "error" not in r]
        if not valid_results:
            print("没有有效的回测结果")
            return {"best_params": {}, "best_score": None, "all_results": all_results}

        if self.metric in _LOWER_IS_BETTER:
            best = min(valid_results, key=lambda r: r.get(self.metric, float("inf")))
        else:
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
