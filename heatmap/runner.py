"""参数敏感性热图运行器。

支持 6 个策略 (SMA/RSI/MACD/Bollinger/Momentum/MeanReversion) 的二维参数网格回测。
- 并发执行 (ProcessPoolExecutor, 最多 8 worker)
- 文件缓存 (~/.quant_heatmap_cache/<sha1>.json) — 同一参数组合不重复算
- 进度回调
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from main import _run_single_backtest
from strategy import (
    SMAStrategy,
    RSIStrategy,
    MACDStrategy,
    BollingerStrategy,
    MomentumStrategy,
    MeanReversionStrategy,
)


# 6 策略 × 默认参数网格（用户可改起止 step）
STRATEGY_GRID: dict[str, dict[str, Any]] = {
    "sma": {
        "cls": SMAStrategy,
        "params": {
            "fast": {"label": "短周期", "min": 3, "max": 30, "step": 1, "default": 5},
            "slow": {"label": "长周期", "min": 10, "max": 120, "step": 1, "default": 20},
        },
    },
    "rsi": {
        "cls": RSIStrategy,
        "params": {
            "period": {"label": "RSI 周期", "min": 5, "max": 30, "step": 1, "default": 14},
            "oversold": {"label": "超卖阈值", "min": 10, "max": 40, "step": 1, "default": 30},
        },
        "default": {},
    },
    "macd": {
        "cls": MACDStrategy,
        "params": {
            "fast": {"label": "快线", "min": 5, "max": 20, "step": 1, "default": 12},
            "slow": {"label": "慢线", "min": 15, "max": 40, "step": 1, "default": 26},
        },
        "default": {},
    },
    "bollinger": {
        "cls": BollingerStrategy,
        "params": {
            "period": {"label": "周期", "min": 5, "max": 60, "step": 1, "default": 20},
            "std_dev": {"label": "标准差倍数", "min": 1.0, "max": 3.0, "step": 0.1, "default": 2.0},
        },
        "default": {},
    },
    "momentum": {
        "cls": MomentumStrategy,
        "params": {
            "period": {"label": "回看周期", "min": 5, "max": 60, "step": 1, "default": 20},
            "threshold": {"label": "动量阈值", "min": 0.005, "max": 0.10, "step": 0.005, "default": 0.02},
        },
        "default": {},
    },
    "mean_reversion": {
        "cls": MeanReversionStrategy,
        "params": {
            "period": {"label": "MA 周期", "min": 5, "max": 60, "step": 1, "default": 20},
            "threshold": {"label": "偏离阈值", "min": 0.005, "max": 0.15, "step": 0.005, "default": 0.05},
        },
        "default": {},
    },
}


CACHE_DIR = Path.home() / ".quant_heatmap_cache"


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(
    strategy: str,
    symbol: str,
    params: dict,
    days: int,
    initial_cash: float,
    commission: float,
    stop_loss: float,
    take_profit: float,
    position_size: float,
    slippage: float,
) -> str:
    """缓存键 — 包含所有影响回测结果的参数。"""
    payload = {
        "strategy": strategy,
        "symbol": symbol,
        "params": params,
        "days": days,
        "initial_cash": initial_cash,
        "commission": commission,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": position_size,
        "slippage": slippage,
    }
    s = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(s.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    """读缓存 — 文件存在且 mtime < 24h 算有效。"""
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > 86400:  # 24h 过期
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _cache_put(key: str, data: dict) -> None:
    _ensure_cache_dir()
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(data, default=str))


def enumerate_grid(
    strategy: str,
    x_param: str,
    y_param: str,
    x_range: tuple[float, float, float],
    y_range: tuple[float, float, float],
) -> list[dict]:
    """生成 (x_values × y_values) 的参数组合。

    x_range / y_range = (start, end, step)。start 闭、end 含（按 step 走）。
    """
    info = STRATEGY_GRID[strategy]
    x_meta = info["params"][x_param]
    y_meta = info["params"][y_param]

    # user 传入的 step (x_range[2]/y_range[2]) 优先；缺省回退到 meta step
    x_step = x_range[2] if len(x_range) >= 3 else x_meta["step"]
    y_step = y_range[2] if len(y_range) >= 3 else y_meta["step"]
    x_vals = _frange(x_range[0], x_range[1], x_step)
    y_vals = _frange(y_range[0], y_range[1], y_step)

    combos = []
    for yv in y_vals:
        for xv in x_vals:
            # MACD 必须 fast < slow；其他策略无此约束
            if strategy == "macd" and xv >= yv:
                continue
            # SMA fast < slow
            if strategy == "sma" and x_param == "fast" and y_param == "slow" and xv >= yv:
                continue
            if strategy == "sma" and x_param == "slow" and y_param == "fast" and yv >= xv:
                continue
            params = {x_param: _round(xv, x_step), y_param: _round(yv, y_step)}
            combos.append(params)
    return combos


def _frange(start: float, end: float, step: float) -> list[float]:
    """含 end 的浮点范围。"""
    if step <= 0:
        return [start]
    vals = []
    v = start
    # 浮点容差
    while v <= end + step * 0.001:
        vals.append(round(v, 10))  # 去掉二进制尾巴
        v += step
    return vals


def _round(v: float, step: float) -> int | float:
    """step 整数则返回 int，否则保留 step 的有效位数。

    例：step=1 → int; step=0.5 → 1 位小数; step=0.05 → 2 位小数; step=0.005 → 3 位小数.
    """
    if isinstance(step, int) or (isinstance(step, float) and step.is_integer()):
        return int(round(v))
    # 把 step 字符串化算小数位，避开浮点 log10
    s = f"{step:.10g}".rstrip("0").rstrip(".")
    if "." in s:
        decimals = len(s.split(".")[1])
    else:
        decimals = 0
    return round(v, decimals)


def run_grid(
    strategy: str,
    symbol: str,
    x_param: str,
    y_param: str,
    x_range: tuple,
    y_range: tuple,
    days: int = 250,
    initial_cash: float = 1_000_000,
    commission: float = 0.0003,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    position_size: float = 1.0,
    slippage: float = 0.001,
    slippage_type: str = "percent",
    max_workers: int = 8,
    use_cache: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """跑参数网格，返回 {param_key: summary, ...}。

    param_key 形如 "fast=5,slow=20"。
    progress_callback(done, total)。
    """
    combos = enumerate_grid(strategy, x_param, y_param, x_range, y_range)
    total = len(combos)
    if total == 0:
        return {"results": {}, "total": 0, "cached": 0, "computed": 0}

    # 拆 cache hit / miss
    tasks_to_run = []
    cache_results = {}
    for params in combos:
        key = _cache_key(strategy, symbol, params, days, initial_cash, commission,
                         stop_loss, take_profit, position_size, slippage)
        if use_cache:
            cached = _cache_get(key)
            if cached is not None:
                cache_results[_param_combo_key(params)] = cached
                continue
        tasks_to_run.append((key, params))

    results = dict(cache_results)
    cached_count = len(cache_results)
    computed_count = 0
    t0 = time.time()

    if not tasks_to_run:
        return {
            "results": results,
            "total": total,
            "cached": cached_count,
            "computed": computed_count,
            "elapsed": time.time() - t0,
        }

    # 构造 _run_single_backtest 接受的元组
    base_args = (strategy, symbol, days, initial_cash, commission,
                 stop_loss, take_profit, position_size, None, None,
                 False, slippage, slippage_type, True, True, None, "day")
    tasks = []
    for key, params in tasks_to_run:
        tasks.append(base_args + (params,))

    done = 0
    with ProcessPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        future_to_key = {executor.submit(_run_single_backtest, t): (key, params)
                         for t, (key, params) in zip(tasks, tasks_to_run)}
        for future in as_completed(future_to_key):
            done += 1
            key, params = future_to_key[future]
            try:
                summary = future.result()
                if summary is not None:
                    if use_cache:
                        _cache_put(key, summary)
                    results[_param_combo_key(params)] = summary
                    computed_count += 1
            except Exception as exc:
                # 单个失败不影响其他 — 记 None 占位
                results[_param_combo_key(params)] = None
            if progress_callback:
                progress_callback(done, len(tasks))

    return {
        "results": results,
        "total": total,
        "cached": cached_count,
        "computed": computed_count,
        "elapsed": time.time() - t0,
    }


def _param_combo_key(params: dict) -> str:
    """参数组合的可读 key：'fast=5,slow=20'。"""
    return ",".join(f"{k}={v}" for k, v in sorted(params.items()))


def list_strategy_param_keys(strategy: str) -> list[str]:
    """返回该策略的可调参数名列表。"""
    return list(STRATEGY_GRID[strategy]["params"].keys())


def get_param_meta(strategy: str, param: str) -> dict:
    return STRATEGY_GRID[strategy]["params"][param]
